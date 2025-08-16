# accounts/admin.py
from django.contrib import admin, messages
from django.urls import path, reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.utils import timezone
from django.core.cache import cache
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db.models import Count, Sum, F, Case, When, DecimalField
from django.db.models.functions import Coalesce, TruncDate

from datetime import timedelta, datetime
from decimal import Decimal
import csv
import json

from django.contrib.auth.models import User

from .models import UserProfile, UserActivity, UserPreferences
from games.models import UserPurchase, GameSession


# ===========================
#  أدوات مساعدة للمدى الزمني
# ===========================
def _parse_range(request):
    """
    يدعم:
      - ?range=7d|30d|90d|365d|all (افتراضي 30d)
      - أو ?start=YYYY-MM-DD&end=YYYY-MM-DD
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


def _effective_price_expr():
    """
    سعر فعلي تقديري للمشتريات:
    - إن كان discounted_price > 0 استخدمه، وإلا price.
    """
    return Case(
        When(
            package__discounted_price__isnull=False,
            package__discounted_price__gt=Decimal("0.00"),
            then=F("package__discounted_price"),
        ),
        default=F("package__price"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def _bool_to_badge(ok, true_label="نعم", false_label="لا"):
    color = "#16a34a" if ok else "#ef4444"
    label = true_label if ok else false_label
    return format_html(
        '<span style="background:{bg};color:#fff;padding:2px 8px;border-radius:999px;font-weight:700;">{}</span>',
        label,
        bg=color,
    )


# ===========================
#  UserProfile
# ===========================
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name_col", "account_type", "is_host_col", "phone_number", "created_at")
    list_filter = ("account_type", "is_host", "created_at", "updated_at")
    search_fields = ("user__username", "user__email", "host_name", "phone_number")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")

    def display_name_col(self, obj):
        return obj.display_name
    display_name_col.short_description = "الاسم المعروض"

    def is_host_col(self, obj):
        return mark_safe(_bool_to_badge(obj.is_host, "مقدم", "مستخدم"))
    is_host_col.short_description = "نوع الحساب"


# ===========================
#  UserActivity
# ===========================
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


# ===========================
#  UserPreferences
# ===========================
@admin.register(UserPreferences)
class UserPreferencesAdmin(admin.ModelAdmin):
    list_display = ("user", "theme_preference", "sound_enabled", "volume_level", "quick_mode_enabled", "show_statistics")
    list_filter = ("theme_preference", "sound_enabled", "quick_mode_enabled", "show_statistics")
    search_fields = ("user__username", "user__email")


# ===========================
#  لوحة الحسابات والتحليلات (Proxy)
# ===========================
class AccountsDashboard(User):
    class Meta:
        proxy = True
        verbose_name = "لوحة الحسابات والتحليلات"
        verbose_name_plural = "لوحة الحسابات والتحليلات"


@admin.register(AccountsDashboard)
class AccountsDashboardAdmin(admin.ModelAdmin):
    """
    صفحة تحليلات تفاعلية داخل الأدمن (بدون قوالب خارجية):
    - إجمالي المستخدمين/المشتريات/الإيراد التقديري
    - نسبة العملاء العائدين
    - أعلى 10 عملاء، أكثر الحزم شراءً
    - توزيع حسب نوع اللعبة
    - الجلسات النشطة وأفضل المضيفين
    - رسم بياني لسرعة الشراء آخر 14 يوم
    - تصدير CSV لأعلى العملاء والحزم
    """
    change_list_template = None
    list_display = ("username", "email", "last_login")  # لن تستخدم فعليًا

    # -------- روابط مخصصة --------
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

    # -------- تصدير CSV --------
    def export_top_buyers(self, request):
        start, end, _ = _parse_range(request)
        purchases = UserPurchase.objects.select_related("user", "package")
        if start and end:
            purchases = purchases.filter(purchase_date__gte=start, purchase_date__lt=end)

        eff_price = _effective_price_expr()
        rows = (
            purchases.values("user", "user__username", "user__email")
            .annotate(purchases_count=Count("id"), total_spent=Coalesce(Sum(eff_price), Decimal("0")))
            .order_by("-total_spent", "-purchases_count")[:500]
        )

        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="top_buyers.csv"'
        w = csv.writer(resp)
        w.writerow(["user_id", "username", "email", "purchases_count", "total_spent"])
        for r in rows:
            w.writerow([r["user"], r["user__username"], r["user__email"], r["purchases_count"], str(r["total_spent"] or 0)])
        return resp

    def export_top_packages(self, request):
        start, end, _ = _parse_range(request)
        purchases = UserPurchase.objects.select_related("user", "package")
        if start and end:
            purchases = purchases.filter(purchase_date__gte=start, purchase_date__lt=end)

        eff_price = _effective_price_expr()
        rows = (
            purchases.values("package", "package__package_number", "package__game_type")
            .annotate(purchases_count=Count("id"), total_spent=Coalesce(Sum(eff_price), Decimal("0")))
            .order_by("-purchases_count", "-total_spent")[:500]
        )

        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="top_packages.csv"'
        w = csv.writer(resp)
        w.writerow(["package_id", "game_type", "package_number", "purchases_count", "total_spent"])
        for r in rows:
            w.writerow([
                r["package"], r["package__game_type"], r["package__package_number"], r["purchases_count"], str(r["total_spent"] or 0)
            ])
        return resp

    # -------- العرض الرئيسي --------
    def dashboard_view(self, request):
        # كاش خفيف 60 ثانية حسب بارامترات الرابط
        cache_key = f"acc_dash:{request.GET.urlencode()}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        start, end, range_label = _parse_range(request)
        now = timezone.now()

        purchases_qs = UserPurchase.objects.select_related("package", "user")
        sessions_qs  = GameSession.objects.select_related("package", "host")
        if start and end:
            purchases_qs = purchases_qs.filter(purchase_date__gte=start, purchase_date__lt=end)
            sessions_qs  = sessions_qs.filter(created_at__gte=start, created_at__lt=end)

        # -- مؤشرات عامة --
        total_users     = User.objects.count()
        total_profiles  = UserProfile.objects.count()
        total_purchases = purchases_qs.count()

        eff_price = _effective_price_expr()
        total_revenue = purchases_qs.aggregate(s=Coalesce(Sum(eff_price), Decimal("0")))["s"]

        # خصومات
        discounted_count = purchases_qs.filter(
            package__discounted_price__isnull=False, package__discounted_price__gt=Decimal("0.00")
        ).count()
        discount_share = round((discounted_count / total_purchases * 100), 2) if total_purchases else 0.0

        # عائد العملاء
        buyers_counts = purchases_qs.values("user").annotate(n=Count("id"))
        buyers_total  = buyers_counts.count()
        buyers_repeat = sum(1 for b in buyers_counts if b["n"] >= 2)
        repeat_rate   = round((buyers_repeat / buyers_total * 100), 2) if buyers_total else 0.0

        # أعلى مشترين / حزم
        top_buyers = (
            purchases_qs.values("user__username", "user__email")
            .annotate(purchases_count=Count("id"), total_spent=Coalesce(Sum(eff_price), Decimal("0")))
            .order_by("-total_spent", "-purchases_count")[:10]
        )
        top_packages = (
            purchases_qs.values("package__game_type", "package__package_number")
            .annotate(purchases_count=Count("id"), total_spent=Coalesce(Sum(eff_price), Decimal("0")))
            .order_by("-purchases_count", "-total_spent")[:10]
        )

        # حسب نوع اللعبة
        by_game = (
            purchases_qs.values("package__game_type")
            .annotate(cnt=Count("id"), revenue=Coalesce(Sum(eff_price), Decimal("0")))
            .order_by("-cnt")
        )

        # الجلسات النشطة + أفضل المضيفين
        active_sessions = (
            sessions_qs.filter(is_active=True)
            .values("game_type").annotate(cnt=Count("id"))
            .order_by("-cnt")
        )
        top_hosts = (
            sessions_qs.values("host__username")
            .annotate(sessions_count=Count("id"))
            .order_by("-sessions_count")[:10]
        )

        # الرسم (آخر 14 يوم)
        days_back = 14
        since_14d = now - timedelta(days=days_back)
        chart_qs = purchases_qs if (start and start >= since_14d) else purchases_qs.filter(purchase_date__gte=since_14d)
        per_day = (
            chart_qs.annotate(day=TruncDate("purchase_date"))
            .values("day").annotate(cnt=Count("id")).order_by("day")
        )
        chart_labels = [p["day"].strftime("%Y-%m-%d") for p in per_day]
        chart_values = [p["cnt"] for p in per_day]
        labels_json = json.dumps(chart_labels, ensure_ascii=False)
        values_json = json.dumps(chart_values, ensure_ascii=False)

        # خرائط عرض
        gt_display = {"letters": "خلية الحروف", "images": "تحدي الصور", "quiz": "سؤال وجواب"}

        # --- بناء صفوف الجداول ---
        def rows_top_buyers():
            if not top_buyers:
                return '<tr><td colspan="4" class="muted">لا توجد بيانات</td></tr>'
            out = []
            for r in top_buyers:
                u = r.get("user__username") or "-"
                em = r.get("user__email") or "-"
                out.append(f"<tr><td>{u}</td><td>{em}</td><td>{r['purchases_count']}</td><td>{r['total_spent']}</td></tr>")
            return "".join(out)

        def rows_top_packages():
            if not top_packages:
                return '<tr><td colspan="4" class="muted">لا توجد بيانات</td></tr>'
            out = []
            for r in top_packages:
                out.append(
                    f"<tr><td>{gt_display.get(r['package__game_type'], r['package__game_type'])}</td>"
                    f"<td>حزمة {r['package__package_number']}</td>"
                    f"<td>{r['purchases_count']}</td>"
                    f"<td>{r['total_spent']}</td></tr>"
                )
            return "".join(out)

        def rows_by_game():
            if not by_game:
                return '<tr><td colspan="3" class="muted">لا توجد بيانات</td></tr>'
            out = []
            for r in by_game:
                out.append(
                    f"<tr><td>{gt_display.get(r['package__game_type'], r['package__game_type'])}</td>"
                    f"<td>{r['cnt']}</td><td>{r['revenue']}</td></tr>"
                )
            return "".join(out)

        def rows_active_sessions():
            if not active_sessions:
                return '<tr><td colspan="2" class="muted">لا توجد جلسات نشطة</td></tr>'
            out = []
            for r in active_sessions:
                out.append(f"<tr><td>{gt_display.get(r['game_type'], r['game_type'])}</td><td>{r['cnt']}</td></tr>")
            return "".join(out)

        def rows_top_hosts():
            if not top_hosts:
                return '<tr><td colspan="2" class="muted">لا توجد بيانات</td></tr>'
            out = []
            for r in top_hosts:
                out.append(f"<tr><td>{r['host__username'] or '-'}</td><td>{r['sessions_count']}</td></tr>")
            return "".join(out)

        # --- تنسيقات (متناسقة مع ألوان الأدمن الأزرق/الأسود) ---
        style = """
<style>
  .dash-wrap {font-family: Tahoma, Arial; padding: 16px;}
  .kpi-grid {display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 12px; margin-bottom: 16px;}
  .card {background: #0f172a; color: #e2e8f0; border: 1px solid #1f2937; border-radius: 12px; padding: 14px; box-shadow: 0 2px 6px rgba(0,0,0,.15);}
  .card h3 {margin: 0 0 8px 0; font-size: 14px; color: #93c5fd;}
  .card .num {font-size: 22px; font-weight: 800; color: #e5e7eb;}
  .sub {color:#94a3b8; font-size:12px;}
  .grid-2 {display:grid; grid-template-columns: 2fr 1fr; gap: 12px;}
  .tbl {width: 100%; border-collapse:collapse;}
  .tbl th, .tbl td {border-bottom:1px solid #1f2937; padding:8px; text-align: start;}
  .tbl th {color:#93c5fd; font-weight:700; background:#0b1220;}
  .controls {margin: 12px 0;}
  .links .btn {display:inline-block; background:#1d4ed8; color:#fff; padding:6px 10px; border-radius:8px; text-decoration:none; margin-inline-end:6px;}
  .links .btn.gray {background:#374151;}
  .muted {color:#94a3b8; font-size:12px;}
  @media (max-width: 1100px) {
    .kpi-grid {grid-template-columns: repeat(2, 1fr);}
    .grid-2 {grid-template-columns: 1fr;}
  }
</style>
"""

        # --- الروابط العلوية ---
        base = reverse("admin:accounts_dashboard")
        links = (
            '<div class="controls"><div class="links">'
            f'<a class="btn" href="{base}?range=7d">آخر 7 أيام</a>'
            f'<a class="btn" href="{base}?range=30d">آخر 30 يوم</a>'
            f'<a class="btn" href="{base}?range=90d">آخر 90 يوم</a>'
            f'<a class="btn" href="{base}?range=365d">آخر سنة</a>'
            f'<a class="btn gray" href="{base}?range=all">كل الوقت</a>'
            f'<a class="btn" href="{reverse("admin:accounts_export_top_buyers")}?{request.GET.urlencode()}">📤 تصدير أعلى العملاء</a>'
            f'<a class="btn" href="{reverse("admin:accounts_export_top_packages")}?{request.GET.urlencode()}">📤 تصدير أكثر الحزم</a>'
            '</div>'
            f'<div class="muted">المدى الحالي: <b>{range_label}</b></div>'
            '</div>'
        )

        # --- الرسم البياني (Chart.js عبر CDN) بدون f-string داخل JS ---
        chart_script = (
            '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>'
            '<canvas id="purchasesChart" height="110"></canvas>'
            '<script>(function(){'
            'var ctx=document.getElementById("purchasesChart").getContext("2d");'
            'new Chart(ctx,{type:"line",data:{labels:'
            + labels_json +
            ',datasets:[{label:"عدد المشتريات/اليوم",data:'
            + values_json +
            ',fill:false}]},options:{responsive:true,plugins:{legend:{labels:{color:"#e2e8f0"}}},'
            'scales:{x:{ticks:{color:"#cbd5e1"},grid:{color:"#1f2937"}},'
            'y:{ticks:{color:"#cbd5e1"},grid:{color:"#1f2937"}}}}});})();</script>'
        )

        # --- بناء الصفحة ---
        html = []
        html.append(style)
        html.append('<div class="dash-wrap">')
        html.append('<h2 style="color:#93c5fd; margin: 0 0 12px 0;">📊 لوحة الحسابات والتحليلات</h2>')
        html.append(links)

        # KPIs
        html.append('<div class="kpi-grid">')
        html.append(f'<div class="card"><h3>إجمالي المستخدمين</h3><div class="num">{total_users}</div><div class="sub">ملفات: {total_profiles}</div></div>')
        html.append(f'<div class="card"><h3>إجمالي المشتريات</h3><div class="num">{total_purchases}</div><div class="sub">المدى: {range_label}</div></div>')
        html.append(f'<div class="card"><h3>الإيراد التقديري</h3><div class="num">{total_revenue} ﷼</div><div class="sub">{discounted_count} شراء بخصم ({discount_share}%)</div></div>')
        html.append(f'<div class="card"><h3>نسبة العملاء العائدين</h3><div class="num">{repeat_rate}%</div><div class="sub">المشترون الفريدون: {buyers_total} / العائدون: {buyers_repeat}</div></div>')
        html.append('</div>')  # kpi-grid

        # الرسم + الجلسات النشطة
        html.append('<div class="grid-2">')
        html.append('<div class="card"><h3>📈 سرعة الشراء (آخر 14 يومًا)</h3>' + chart_script + '</div>')
        html.append('<div class="card"><h3>🎮 الجلسات النشطة حسب نوع اللعبة</h3>'
                    '<table class="tbl"><thead><tr><th>نوع اللعبة</th><th>جلسات نشطة</th></tr></thead>'
                    f'<tbody>{rows_active_sessions()}</tbody></table></div>')
        html.append('</div>')  # grid-2

        # أعلى العملاء + أكثر الحزم
        html.append('<div class="grid-2" style="margin-top:12px;">')
        html.append('<div class="card"><h3>👤 أعلى 10 عملاء شراءً</h3>'
                    '<table class="tbl"><thead><tr><th>المستخدم</th><th>البريد</th><th>العدد</th><th>المبلغ</th></tr></thead>'
                    f'<tbody>{rows_top_buyers()}</tbody></table></div>')
        html.append('<div class="card"><h3>🧺 أكثر الحزم شراءً</h3>'
                    '<table class="tbl"><thead><tr><th>اللعبة</th><th>الحزمة</th><th>المشتريات</th><th>الإيراد</th></tr></thead>'
                    f'<tbody>{rows_top_packages()}</tbody></table></div>')
        html.append('</div>')  # grid-2

        # توزيع حسب نوع اللعبة
        html.append('<div class="card" style="margin-top:12px;"><h3>🏆 أكثر الألعاب انتشارًا</h3>'
                    '<table class="tbl"><thead><tr><th>نوع اللعبة</th><th>عدد المشتريات</th><th>الإيراد</th></tr></thead>'
                    f'<tbody>{rows_by_game()}</tbody></table>'
                    '<div class="muted" style="margin-top:8px;">تلميح: راقب أثر العروض/الخصومات على الانتشار والإيراد.</div>'
                    '</div>')

        html.append('<div class="muted" style="margin-top:10px;">تم تحسين الاستعلامات عبر التجميعات وحقول التعبير لتقليل الحمل والحفاظ على الأداء.</div>')
        html.append('</div>')  # dash-wrap

        resp = TemplateResponse(
            request,
            "admin/base_site.html",
            context={
                **self.admin_site.each_context(request),
                "title": "لوحة الحسابات والتحليلات",
                "content": mark_safe("".join(html)),  # نحقن المحتوى داخل القالب الأساسي للأدمن
            },
        )
        cache.set(cache_key, resp, 60)
        return resp
