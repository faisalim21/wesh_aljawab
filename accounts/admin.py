# accounts/admin.py
from django.contrib import admin, messages
from django.urls import path, reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.utils import timezone
from django.core.cache import cache
from django.utils.safestring import mark_safe
from django.db.models import Count, Sum, F, Case, When, DecimalField
from django.db.models.functions import Coalesce, TruncDate

from datetime import timedelta, datetime
from decimal import Decimal
import csv
import json

from django.contrib.auth.models import User
from .models import UserProfile, UserActivity, UserPreferences
from games.models import UserPurchase, GameSession, FreeTrialUsage
from payments.models import Transaction, PaymentMethod


# ============== أدوات المدى الزمني + الفلاتر ==============
def _parse_range(request):
    """
    ?range=7d|30d|90d|365d|all  (افتراضي 30d)
    أو ?start=YYYY-MM-DD&end=YYYY-MM-DD
    يعيد: (start_dt, end_dt, label)
    """
    now = timezone.now()
    r = (request.GET.get("range") or "30d").lower()
    start_str = request.GET.get("start")
    end_str = request.GET.get("end")

    if start_str and end_str:
        try:
            start = timezone.make_aware(datetime.strptime(start_str, "%Y-%m-%d"))
            end = timezone.make_aware(datetime.strptime(end_str, "%Y-%m-%d")) + timedelta(days=1)
            return start, end, f"{start_str} → {end_str}"
        except Exception:
            pass

    mapping = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}
    if r in mapping:
        days = mapping[r]
        start = now - timedelta(days=days)
        return start, now, r
    if r == "all":
        return None, None, "كل الوقت"
    start = now - timedelta(days=30)
    return start, now, "30d"


def _game_filter(request):
    """?game=letters|images|quiz|all"""
    g = (request.GET.get("game") or "all").lower()
    return g if g in {"letters", "images", "quiz"} else "all"


def _effective_price_expr():
    """سعر فعلي تقديري: إن كان discounted_price > 0 استخدمه، وإلا price."""
    return Case(
        When(
            package__discounted_price__isnull=False,
            package__discounted_price__gt=Decimal("0.00"),
            then=F("package__discounted_price"),
        ),
        default=F("package__price"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


# ============== مدراء النماذج الأساسية ==============
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name_col", "account_type", "is_host", "phone_number", "created_at")
    list_filter = ("account_type", "is_host", "created_at", "updated_at")
    search_fields = ("user__username", "user__email", "host_name", "phone_number")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")

    def display_name_col(self, obj):
        return obj.display_name
    display_name_col.short_description = "الاسم المعروض"


@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = ("user", "activity_type", "game_type", "session_id", "created_at", "desc_short")
    list_filter = ("activity_type", "game_type", "created_at")
    search_fields = ("user__username", "description", "session_id")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 30
    actions = ["purge_old_activities"]

    def desc_short(self, obj):
        if not obj.description:
            return "—"
        return obj.description if len(obj.description) <= 60 else obj.description[:60] + "…"
    desc_short.short_description = "الوصف المختصر"

    def purge_old_activities(self, request, queryset):
        cutoff = timezone.now() - timedelta(days=180)
        deleted, _ = UserActivity.objects.filter(created_at__lt=cutoff).delete()
        messages.success(request, f"تم حذف {deleted} سجل نشاط أقدم من 180 يومًا.")
    purge_old_activities.short_description = "حذف الأنشطة القديمة (> 180 يوم)"


@admin.register(UserPreferences)
class UserPreferencesAdmin(admin.ModelAdmin):
    list_display = ("user", "theme_preference", "sound_enabled", "volume_level", "quick_mode_enabled", "show_statistics")
    list_filter = ("theme_preference", "sound_enabled", "quick_mode_enabled", "show_statistics")
    search_fields = ("user__username", "user__email")


# ============== لوحة الحسابات والتحليلات (Proxy) ==============
class AccountsDashboard(User):
    class Meta:
        proxy = True
        verbose_name = "لوحة الحسابات والتحليلات"
        verbose_name_plural = "لوحة الحسابات والتحليلات"


@admin.register(AccountsDashboard)
class AccountsDashboardAdmin(admin.ModelAdmin):
    """
    لوحة تحليل سلوك العميل + الإيرادات ورسوم الدفع:
    - تحويل المجاني → المدفوع (مدى الحياة وداخل المدى)
    - من جرّب مجاني ولم يشترِ
    - عدد مجرّبي المجاني / مشترِي المدفوع (+ نسب)
    - زمن التحويل (وسيط/متوسط بالأيام)
    - ARPPU، تكرار الشراء، توزيع حسب نوع اللعبة
    - صافي الإيراد بعد الرسوم (1 ريال/معاملة + % حسب اسم الطريقة + processing_fee)
    - توصية إذا كانت رسوم فيزا تثقل الهامش
    - فلاتر: المدى الزمني + نوع اللعبة، وتصدير CSV
    """
    change_list_template = None
    list_display = ("username", "email", "last_login")  # غير مستخدمة

    # ---------- روابط ----------
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("dashboard/", self.admin_site.admin_view(self.dashboard_view), name="accounts_dashboard"),
            path("export/top-buyers.csv", self.admin_site.admin_view(self.export_top_buyers), name="accounts_export_top_buyers"),
            path("export/top-packages.csv", self.admin_site.admin_view(self.export_top_packages), name="accounts_export_top_packages"),
            path("", self.admin_site.admin_view(self.redirect_to_dashboard), name="accounts_accountsdashboard_changelist"),
        ]
        return custom + urls

    def redirect_to_dashboard(self, request):
        return HttpResponseRedirect(reverse("admin:accounts_dashboard"))

    # ---------- تصدير ----------
    def export_top_buyers(self, request):
        start, end, _ = _parse_range(request)
        game = _game_filter(request)

        purchases = UserPurchase.objects.select_related("user", "package")
        if start and end:
            purchases = purchases.filter(purchase_date__gte=start, purchase_date__lt=end)
        if game != "all":
            purchases = purchases.filter(package__game_type=game)

        eff = _effective_price_expr()
        rows = (purchases.values("user", "user__username", "user__email")
                 .annotate(purchases_count=Count("id"), total_spent=Coalesce(Sum(eff), Decimal("0")))
                 .order_by("-total_spent", "-purchases_count")[:500])

        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="top_buyers.csv"'
        w = csv.writer(resp)
        w.writerow(["user_id", "username", "email", "purchases_count", "total_spent"])
        for r in rows:
            w.writerow([r["user"], r["user__username"], r["user__email"], r["purchases_count"], str(r["total_spent"] or 0)])
        return resp

    def export_top_packages(self, request):
        start, end, _ = _parse_range(request)
        game = _game_filter(request)

        purchases = UserPurchase.objects.select_related("user", "package")
        if start and end:
            purchases = purchases.filter(purchase_date__gte=start, purchase_date__lt=end)
        if game != "all":
            purchases = purchases.filter(package__game_type=game)

        eff = _effective_price_expr()
        rows = (purchases.values("package", "package__package_number", "package__game_type")
                 .annotate(purchases_count=Count("id"), total_spent=Coalesce(Sum(eff), Decimal("0")))
                 .order_by("-purchases_count", "-total_spent")[:500])

        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="top_packages.csv"'
        w = csv.writer(resp)
        w.writerow(["package_id", "game_type", "package_number", "purchases_count", "total_spent"])
        for r in rows:
            w.writerow([r["package"], r["package__game_type"], r["package__package_number"], r["purchases_count"], str(r["total_spent"] or 0)])
        return resp

    # ---------- العرض الرئيسي ----------
    def dashboard_view(self, request):
        # نخزّن HTML فقط لتفادي ContentNotRenderedError
        cache_key = f"acc_dash:{request.GET.urlencode()}"
        cached = cache.get(cache_key)
        if cached:
            return TemplateResponse(
                request,
                "admin/base_site.html",
                context={**self.admin_site.each_context(request), "title": "لوحة الحسابات والتحليلات", "content": mark_safe(cached)},
            )

        start, end, range_label = _parse_range(request)
        game = _game_filter(request)
        gt_display = {"letters": "خلية الحروف", "images": "تحدي الصور", "quiz": "سؤال وجواب"}

        # مصادر
        purchases_qs = UserPurchase.objects.select_related("package", "user")
        sessions_qs = GameSession.objects.select_related("package", "host")
        trials_qs = FreeTrialUsage.objects.all()

        if start and end:
            purchases_qs = purchases_qs.filter(purchase_date__gte=start, purchase_date__lt=end)
            sessions_qs = sessions_qs.filter(created_at__gte=start, created_at__lt=end)
            trials_qs = trials_qs.filter(used_at__gte=start, used_at__lt=end)

        if game != "all":
            purchases_qs = purchases_qs.filter(package__game_type=game)
            sessions_qs = sessions_qs.filter(game_type=game)
            trials_qs = trials_qs.filter(game_type=game)

        # المؤشرات العامة
        total_users = User.objects.count()
        active_30d = User.objects.filter(last_login__gte=timezone.now() - timedelta(days=30)).count()
        new_users_period = 0
        if start and end:
            new_users_period = User.objects.filter(date_joined__gte=start, date_joined__lt=end).count()

        # الإيراد الإجمالي التقديري
        eff = _effective_price_expr()
        total_revenue = purchases_qs.aggregate(s=Coalesce(Sum(eff), Decimal("0")))["s"]

        # مجرّبو المجاني (مميزون)
        trial_user_ids = list(trials_qs.values_list("user_id", flat=True).distinct())
        trials_count_unique = len(trial_user_ids)

        # مشترُو المدفوع (مميزون) داخل المدى
        paid_buyers_ids = list(
            purchases_qs.filter(package__is_free=False).values_list("user_id", flat=True).distinct()
        )
        paid_buyers_unique = len(paid_buyers_ids)

        # تحويل مجاني→مدفوع (مدى الحياة + داخل المدى)
        # lifetime: كل من جرّب المجاني (game filter إن وُجد) واشترى مدفوع في أي وقت
        trials_all = FreeTrialUsage.objects.filter(user_id__in=trial_user_ids) if trial_user_ids else FreeTrialUsage.objects.none()
        if game != "all":
            trials_all = trials_all.filter(game_type=game)
        convert_lifetime_ids = set(
            UserPurchase.objects.filter(
                user_id__in=trials_all.values_list("user_id", flat=True),
                package__is_free=False,
            ).values_list("user_id", flat=True).distinct()
        )
        conv_lifetime = len(convert_lifetime_ids)
        conv_lifetime_rate = (conv_lifetime / trials_count_unique * 100) if trials_count_unique else 0

        # داخل المدى: من جرّب في المدى واشترى مدفوع ضمن نفس المدى
        convert_period_ids = set(
            purchases_qs.filter(package__is_free=False, user_id__in=trial_user_ids).values_list("user_id", flat=True).distinct()
        )
        conv_period = len(convert_period_ids)
        conv_period_rate = (conv_period / trials_count_unique * 100) if trials_count_unique else 0

        # مَن جرّب مجاني ولم يشترِ أبدًا (Non-Converters)
        non_converters_ids = set(trial_user_ids) - convert_lifetime_ids
        non_conv_count = len(non_converters_ids)
        non_conv_rate = (non_conv_count / trials_count_unique * 100) if trials_count_unique else 0

        # زمن التحويل (أيام) لمن حوّلوا (من أول تجربة مجانية لأول شراء مدفوع)
        time_deltas = []
        if trial_user_ids:
            # أول تاريخ تجربة لكل مستخدم
            first_trial = (FreeTrialUsage.objects
                           .filter(user_id__in=trial_user_ids, game_type=game if game != "all" else F("game_type"))
                           .values("user_id")
                           .annotate(t0=Coalesce(TruncDate("used_at"), TruncDate("used_at")))
                           )
            trial_map = {r["user_id"]: r["t0"] for r in first_trial}
            # أول شراء مدفوع لكل مستخدم
            first_paid = (UserPurchase.objects
                          .filter(user_id__in=trial_map.keys(), package__is_free=False)
                          .values("user_id")
                          .annotate(p0=Coalesce(TruncDate("purchase_date"), TruncDate("purchase_date"))))
            paid_map = {r["user_id"]: r["p0"] for r in first_paid}
            for uid, t0 in trial_map.items():
                if uid in paid_map and t0 and paid_map[uid]:
                    delta = (paid_map[uid] - t0).days
                    if delta >= 0:
                        time_deltas.append(delta)
        avg_days = sum(time_deltas) / len(time_deltas) if time_deltas else 0
        med_days = 0
        if time_deltas:
            srt = sorted(time_deltas)
            mid = len(srt) // 2
            med_days = (srt[mid] if len(srt) % 2 else (srt[mid - 1] + srt[mid]) / 2)

        # ARPPU: الإيراد / عدد المشترين المدفوعين (داخل المدى)
        ARPPU = (total_revenue / paid_buyers_unique) if paid_buyers_unique else Decimal("0")

        # شراء متكرر داخل المدى
        repeat_counts = (purchases_qs.filter(package__is_free=False)
                         .values("user_id").annotate(n=Count("id")).filter(n__gte=2).count())
        repeat_rate = (repeat_counts / paid_buyers_unique * 100) if paid_buyers_unique else 0

        # توزيع حسب نوع اللعبة داخل المدى
        by_game = (purchases_qs.values("package__game_type")
                   .annotate(cnt=Count("id"), revenue=Coalesce(Sum(eff), Decimal("0")))
                   .order_by("-cnt"))
        # أفضل الحزم
        top_packages = (purchases_qs.values("package__game_type", "package__package_number")
                        .annotate(purchases_count=Count("id"), total_spent=Coalesce(Sum(eff), Decimal("0")))
                        .order_by("-purchases_count", "-total_spent")[:10])
        # أفضل العملاء
        top_buyers = (purchases_qs.values("user__username", "user__email")
                      .annotate(purchases_count=Count("id"), total_spent=Coalesce(Sum(eff), Decimal("0")))
                      .order_by("-total_spent", "-purchases_count")[:10])

        # الرسم (آخر 14 يومًا)
        now = timezone.now()
        since_14 = now - timedelta(days=14)
        chart_qs = purchases_qs if (start and start >= since_14) else purchases_qs.filter(purchase_date__gte=since_14)
        per_day = (chart_qs.annotate(day=TruncDate("purchase_date"))
                   .values("day").annotate(cnt=Count("id")).order_by("day"))
        labels_json = json.dumps([p["day"].strftime("%Y-%m-%d") for p in per_day], ensure_ascii=False)
        values_json = json.dumps([p["cnt"] for p in per_day], ensure_ascii=False)

        # صافي الإيراد بعد رسوم الدفع (من معاملات الدفع)
        tx_qs = Transaction.objects.filter(status="completed")
        if start and end:
            tx_qs = tx_qs.filter(created_at__gte=start, created_at__lt=end)
        if game != "all":
            tx_qs = tx_qs.filter(package__game_type=game)

        FIXED_PER_TXN = Decimal("1.00")  # طلبك: 1 ريال لكل عملية
        PCT_MAP = {
            "visa": Decimal("2.7"), "فيزا": Decimal("2.7"),
            "mada": Decimal("1.0"), "مدى":  Decimal("1.0"),
        }

        gross_amount = Decimal("0")
        total_fees = Decimal("0")
        by_method = {}

        for tx in tx_qs.select_related("payment_method"):
            amt = tx.amount or Decimal("0")
            gross_amount += amt
            pm = tx.payment_method
            pm_name = (pm.name_ar or pm.name or "").strip().lower() if pm else "غير محدد"
            pct = PCT_MAP.get(pm_name, Decimal("0"))
            meth_fixed = pm.processing_fee if (pm and pm.processing_fee) else Decimal("0")
            fee = FIXED_PER_TXN + meth_fixed + (amt * pct / Decimal("100"))
            total_fees += fee

            bm = by_method.setdefault(pm_name or "غير محدد", {"count": 0, "amount": Decimal("0"), "fees": Decimal("0")})
            bm["count"] += 1
            bm["amount"] += amt
            bm["fees"] += fee

        net_amount = gross_amount - total_fees
        visa_pressure = 0
        if gross_amount > 0 and "فيزا" in by_method:
            visa_pressure = float((by_method["فيزا"]["fees"] / gross_amount) * 100)

        # ========== واجهة ==========
        style = """
<style>
  .dash-wrap {font-family: Tahoma, Arial; padding: 16px;}
  .kpi-grid {display: grid; grid-template-columns: repeat(4, minmax(200px,1fr)); gap: 12px; margin-bottom: 16px;}
  .card {background:#0f172a;color:#e2e8f0;border:1px solid #1f2937;border-radius:12px;padding:14px;box-shadow:0 2px 6px rgba(0,0,0,.15);}
  .card h3 {margin:0 0 8px 0;font-size:14px;color:#93c5fd;}
  .card .num {font-size:22px;font-weight:800;color:#e5e7eb;}
  .sub {color:#94a3b8;font-size:12px;}
  .grid-2 {display:grid;grid-template-columns: 2fr 1fr;gap:12px;}
  .tbl {width:100%;border-collapse:collapse;}
  .tbl th, .tbl td {border-bottom:1px solid #1f2937;padding:8px;text-align:start;}
  .tbl th {color:#93c5fd;font-weight:700;background:#0b1220;}
  .controls {margin:12px 0;}
  .links .btn {display:inline-block;background:#1d4ed8;color:#fff;padding:6px 10px;border-radius:8px;text-decoration:none;margin-inline-end:6px;}
  .links .btn.gray {background:#374151;}
  .muted {color:#94a3b8;font-size:12px;}
  .hint {font-size:12px;color:#cbd5e1;}
  .pill {background:#1d4ed8;color:#fff;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:700;}
  @media(max-width:1100px){.kpi-grid{grid-template-columns:repeat(2,1fr)}.grid-2{grid-template-columns:1fr}}
</style>
"""

        base = reverse("admin:accounts_dashboard")
        qs = request.GET.copy()
        def link_range(code, text):
            qs["range"] = code; return f'<a class="btn" href="{base}?{qs.urlencode()}">{text}</a>'
        def link_game(code, text):
            qs2 = request.GET.copy(); qs2["game"]=code
            return f'<a class="btn{" gray" if game==code else ""}" href="{base}?{qs2.urlencode()}">{text}</a>'

        links = (
            '<div class="controls"><div class="links">'
            + link_range("7d", "آخر 7 أيام")
            + link_range("30d", "آخر 30 يوم")
            + link_range("90d", "آخر 90 يوم")
            + link_range("365d", "آخر سنة")
            + link_range("all", "كل الوقت")
            + f'<a class="btn" href="{reverse("admin:accounts_export_top_buyers")}?{request.GET.urlencode()}">📤 تصدير أعلى العملاء</a>'
            + f'<a class="btn" href="{reverse("admin:accounts_export_top_packages")}?{request.GET.urlencode()}">📤 تصدير أكثر الحزم</a>'
            + '</div>'
            f'<div class="hint">المدى: <b>{range_label}</b> &nbsp;|&nbsp; النوع: '
            + link_game("all","الكل") + " "
            + link_game("letters","خلية الحروف") + " "
            + link_game("images","تحدي الصور") + " "
            + link_game("quiz","سؤال وجواب")
            + "</div></div>"
        )

        # الرسم البياني
        chart = (
            '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>'
            '<canvas id="chart1" height="110"></canvas>'
            '<script>(function(){var c=document.getElementById("chart1").getContext("2d");'
            'new Chart(c,{type:"line",data:{labels:' + labels_json +
            ',datasets:[{label:"عدد المشتريات/اليوم",data:' + values_json +
            ',fill:false}]},options:{responsive:true,plugins:{legend:{labels:{color:"#e2e8f0"}}},'
            'scales:{x:{ticks:{color:"#cbd5e1"},grid:{color:"#1f2937"}},y:{ticks:{color:"#cbd5e1"},grid:{color:"#1f2937"}}}}});})();</script>'
        )

        # جداول
        def rows_top_packages():
            if not top_packages:
                return '<tr><td colspan="4" class="muted">لا توجد بيانات</td></tr>'
            out=[]
            for r in top_packages:
                out.append(
                    f"<tr><td>{gt_display.get(r['package__game_type'], r['package__game_type'])}</td>"
                    f"<td>حزمة {r['package__package_number']}</td>"
                    f"<td>{r['purchases_count']}</td>"
                    f"<td>{r['total_spent']}</td></tr>"
                )
            return "".join(out)

        def rows_top_buyers():
            if not top_buyers:
                return '<tr><td colspan="4" class="muted">لا توجد بيانات</td></tr>'
            out=[]
            for r in top_buyers:
                out.append(
                    f"<tr><td>{r.get('user__username') or '-'}</td>"
                    f"<td>{r.get('user__email') or '-'}</td>"
                    f"<td>{r['purchases_count']}</td>"
                    f"<td>{r['total_spent']}</td></tr>"
                )
            return "".join(out)

        def rows_by_game():
            if not by_game:
                return '<tr><td colspan="3" class="muted">لا توجد بيانات</td></tr>'
            out=[]
            for r in by_game:
                out.append(
                    f"<tr><td>{gt_display.get(r['package__game_type'], r['package__game_type'])}</td>"
                    f"<td>{r['cnt']}</td><td>{r['revenue']}</td></tr>"
                )
            return "".join(out)

        def rows_methods():
            if not by_method:
                return '<tr><td colspan="4" class="muted">لا توجد معاملات دفع</td></tr>'
            out=[]
            for name, agg in by_method.items():
                out.append(
                    f"<tr><td>{name or 'غير محدد'}</td>"
                    f"<td>{agg['count']}</td>"
                    f"<td>{agg['amount']}</td>"
                    f"<td>{agg['fees']}</td></tr>"
                )
            return "".join(out)

        # بطاقات KPI
        kpi = []
        kpi.append(f'<div class="card"><h3>المستخدمون</h3><div class="num">{total_users}</div><div class="sub">نشطون 30 يوم: {active_30d} — جدد في المدى: {new_users_period}</div></div>')
        kpi.append(f'<div class="card"><h3>المجاني → المدفوع</h3><div class="num">{conv_lifetime_rate:.1f}%</div><div class="sub">مدى الحياة: {conv_lifetime}/{trials_count_unique} — داخل المدى: {conv_period_rate:.1f}%</div></div>')
        kpi.append(f'<div class="card"><h3>من جرّب ولم يشترِ</h3><div class="num">{non_conv_rate:.1f}%</div><div class="sub">عددهم: {non_conv_count}</div></div>')
        kpi.append(f'<div class="card"><h3>إيراد تقديري</h3><div class="num">{total_revenue} ﷼</div><div class="sub">ARPPU: {ARPPU:.2f} ﷼ — تكرار الشراء: {repeat_rate:.1f}%</div></div>')
        kpi.append(f'<div class="card"><h3>زمن التحويل</h3><div class="num">{med_days:.1f} يوم</div><div class="sub">متوسط: {avg_days:.1f} يوم</div></div>')
        kpi.append(f'<div class="card"><h3>صافي بعد الرسوم</h3><div class="num">{net_amount:.2f} ﷼</div><div class="sub">إجمالي: {gross_amount:.2f} ﷼ — الرسوم: {total_fees:.2f} ﷼</div></div>')

        recommend = ""
        if visa_pressure > 2.0:  # عتبة إرشادية
            recommend = (
                '<div class="card" style="border-color:#f59e0b">'
                '<h3>💡 توصية تسعيرية</h3>'
                f'<div class="sub">نسبة رسوم فيزا إلى إجمالي الدخل {visa_pressure:.1f}% — إن كانت تؤثر على الهامش جرّب الحد من فيزا أو تشجيع مدى (مثلاً خصم بسيط لمدى).</div>'
                '</div>'
            )

        # HTML
        html = []
        html.append(style)
        html.append('<div class="dash-wrap">')
        html.append('<h2 style="color:#93c5fd;margin:0 0 12px 0;">📊 لوحة الحسابات والتحليلات</h2>')
        html.append(links)

        html.append('<div class="kpi-grid">' + "".join(kpi) + '</div>')
        if recommend:
            html.append(recommend)

        html.append('<div class="grid-2">')
        html.append('<div class="card"><h3>📈 سرعة الشراء (آخر 14 يومًا)</h3>' + chart + '</div>')
        html.append('<div class="card"><h3>🎮 الجلسات النشطة حسب نوع اللعبة</h3>')
        # جلسات نشطة
        active_sessions = (sessions_qs.filter(is_active=True)
                           .values("game_type").annotate(cnt=Count("id")).order_by("-cnt"))
        if active_sessions:
            html.append('<table class="tbl"><thead><tr><th>نوع اللعبة</th><th>نشطة</th></tr></thead><tbody>')
            for r in active_sessions:
                html.append(f"<tr><td>{gt_display.get(r['game_type'], r['game_type'])}</td><td>{r['cnt']}</td></tr>")
            html.append('</tbody></table>')
        else:
            html.append('<div class="muted">لا توجد جلسات نشطة</div>')
        html.append('</div></div>')

        # جداول رئيسية
        html.append('<div class="grid-2" style="margin-top:12px;">')
        html.append('<div class="card"><h3>🧺 أكثر الحزم شراءً</h3><table class="tbl"><thead><tr><th>اللعبة</th><th>الحزمة</th><th>المشتريات</th><th>الإيراد</th></tr></thead><tbody>')
        html.append(rows_top_packages())
        html.append('</tbody></table></div>')
        html.append('<div class="card"><h3>👤 أعلى 10 عملاء</h3><table class="tbl"><thead><tr><th>المستخدم</th><th>البريد</th><th>العدد</th><th>المبلغ</th></tr></thead><tbody>')
        html.append(rows_top_buyers())
        html.append('</tbody></table></div>')
        html.append('</div>')

        # توزيع حسب نوع اللعبة
        html.append('<div class="card" style="margin-top:12px;"><h3>🏆 توزيع حسب نوع اللعبة</h3><table class="tbl"><thead><tr><th>نوع اللعبة</th><th>المشتريات</th><th>الإيراد</th></tr></thead><tbody>')
        html.append(rows_by_game())
        html.append('</tbody></table></div>')

        # طرق الدفع والرسوم
        html.append('<div class="card" style="margin-top:12px;"><h3>💳 طرق الدفع والرسوم</h3><table class="tbl"><thead><tr><th>الطريقة</th><th>عدد المعاملات</th><th>المبلغ الإجمالي</th><th>الرسوم المُحتسبة</th></tr></thead><tbody>')
        html.append(rows_methods())
        html.append('</tbody></table>')
        html.append(f'<div class="muted" style="margin-top:8px;">* الرسوم = 1 ريال/معاملة + نسبة من الاسم (فيزا 2.7%، مدى 1%) + processing_fee للطريقة إن وُجد.</div>')
        html.append('</div>')

        html.append('</div>')  # dash-wrap

        html_content = "".join(html)
        cache.set(cache_key, html_content, 60)  # نخزّن HTML فقط

        return TemplateResponse(
            request,
            "admin/base_site.html",
            context={**self.admin_site.each_context(request), "title": "لوحة الحسابات والتحليلات", "content": mark_safe(html_content)},
        )
