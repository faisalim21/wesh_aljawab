# accounts/admin.py
from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.urls import path, reverse
from django.utils import timezone
from django.db.models import Count, Q, F
from django.db.models.functions import TruncDate
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import UserProfile, UserActivity, UserPreferences

# نعتمد على نماذج الألعاب للتحليلات المدمجة
try:
    from games.models import GameSession, Contestant, UserPurchase, FreeTrialUsage
except Exception:
    GameSession = Contestant = UserPurchase = FreeTrialUsage = None


# =========================
# أدوات واجهة (تصميم غامق)
# =========================
def _kpi(label, value, sub='', tone="info"):
    colors = {
        "ok":   ("#10b981", "#064e3b"),
        "warn": ("#f59e0b", "#7c2d12"),
        "bad":  ("#ef4444", "#7f1d1d"),
        "info": ("#3b82f6", "#1e3a8a"),
    }
    bg = colors.get(tone, colors["info"])[0]
    return f"""
    <div style="flex:1;min-width:220px;margin:8px;padding:16px;border-radius:12px;
                background:{bg}22;border:1px solid {bg};box-shadow:0 1px 2px #0002;">
      <div style="color:#cbd5e1;font-weight:700;font-size:13px;margin-bottom:8px;">{label}</div>
      <div style="color:#e0e7ff;font-size:22px;font-weight:800;letter-spacing:0.3px;">{value}</div>
      <div style="color:#94a3b8;font-size:12px;margin-top:6px;">{sub}</div>
    </div>
    """

def _table(headers, rows_html):
    head = "".join(f"<th style='padding:10px 12px;text-align:right;border-bottom:1px solid #1f2937;'>{h}</th>" for h in headers)
    body = "".join(rows_html) or f"<tr><td colspan='{len(headers)}' style='padding:12px;color:#94a3b8;'>لا توجد بيانات</td></tr>"
    return f"""
    <div class="module" style="margin:12px 0;border-radius:12px;overflow:hidden;">
      <table class="listing" style="width:100%;border-collapse:collapse;background:#0b1220;">
        <thead style="background:#0f172a;color:#cbd5e1;">{head}</thead>
        <tbody style="color:#e2e8f0;">{body}</tbody>
      </table>
    </div>
    """


# =========================
# فلاتر بسيطة للوقت
# =========================
def _parse_dt(s):
    if not s:
        return None
    try:
        # نتوقع ISO مثل 2025-08-17T08:00
        return timezone.make_aware(timezone.datetime.fromisoformat(s))
    except Exception:
        return None


# =========================
# UserProfile Admin
# =========================
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user_link", "display_name", "is_host", "account_type_badge",
                    "phone_number", "favorite_game_ar", "created_at", "updated_at")
    list_filter = ("is_host", "account_type", "favorite_game", "created_at")
    search_fields = ("user__username", "user__first_name", "user__email", "host_name", "phone_number")
    readonly_fields = ("created_at", "updated_at",)
    actions = ("open_analytics",)

    fieldsets = (
        ("المعلومات الأساسية", {
            "fields": ("user", "host_name", "phone_number", "birth_date", "is_host")
        }),
        ("التفضيلات العامة", {
            "fields": ("favorite_game", "notifications_enabled", "email_notifications")
        }),
        ("معلومات الحساب", {
            "fields": ("account_type", "created_at", "updated_at")
        }),
    )

    def user_link(self, obj):
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.username)
    user_link.short_description = "المستخدم"

    def account_type_badge(self, obj):
        color = "#10b981" if obj.account_type == "premium" else "#3b82f6"
        label = dict(UserProfile._meta.get_field("account_type").choices).get(obj.account_type, obj.account_type)
        return format_html('<span style="background:{}22;color:{};padding:2px 8px;border-radius:999px;border:1px solid {};">{}</span>',
                          color, color, color, label)
    account_type_badge.short_description = "نوع الحساب"

    def favorite_game_ar(self, obj):
        if not obj.favorite_game:
            return "—"
        return dict(UserProfile._meta.get_field("favorite_game").choices).get(obj.favorite_game, obj.favorite_game)
    favorite_game_ar.short_description = "اللعبة المفضلة"

    @admin.action(description="📊 فتح تحليلات الحسابات")
    def open_analytics(self, request, queryset):
        return HttpResponseRedirect(reverse("admin:accounts_users_analytics"))

    # روابط مخصصة
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("analytics/", self.admin_site.admin_view(self.analytics_view), name="accounts_users_analytics"),
            path("analytics.csv", self.admin_site.admin_view(self.analytics_csv), name="accounts_users_analytics_csv"),
        ]
        return custom + urls

    def analytics_view(self, request):
        """
        لوحة تحليلات الحسابات:
        - فلترة فترة: start/end (بتاريخ ووقت)
        - زر "تطبيق" + خيار "إظهار المقارنة" (فترة مماثلة سابقة)
        - إجمالي المستخدمين، المستخدمون الجدد، نسبة التغير
        - DAU/WAU/MAU (اختياري بتبديل)
        - متوسط/أقصى المشاركين في الجلسة (مضيف + متسابقين)
        - أفضل المستخدمين نشاطًا
        """
        # مدخلات
        start = _parse_dt(request.GET.get("start"))
        end = _parse_dt(request.GET.get("end"))
        compare = request.GET.get("compare") == "on"
        show_activity = request.GET.get("show_activity") == "on"

        if not end:
            end = timezone.now()
        if not start:
            start = end - timezone.timedelta(days=30)

        # نطاق المقارنة (اختياري)
        cmp_html = ""
        prev_metrics = {}

        # إجمالي المستخدمين
        total_users = User.objects.count()

        # مستخدمون جدد في الفترة
        new_users_qs = User.objects.filter(date_joined__gte=start, date_joined__lte=end)
        new_users = new_users_qs.count()

        # نسبة التغيّر = (جدد / إجمالي قبل الفترة) * 100
        users_before = User.objects.filter(date_joined__lt=start).count()
        growth_pct = (new_users / users_before * 100) if users_before else 0

        # DAU/WAU/MAU (اختياري)
        dau = wau = mau = 0
        if show_activity:
            day_start = end - timezone.timedelta(days=1)
            week_start = end - timezone.timedelta(days=7)
            month_start = end - timezone.timedelta(days=30)
            dau = UserActivity.objects.filter(created_at__gte=day_start, created_at__lte=end).values("user_id").distinct().count()
            wau = UserActivity.objects.filter(created_at__gte=week_start, created_at__lte=end).values("user_id").distinct().count()
            mau = UserActivity.objects.filter(created_at__gte=month_start, created_at__lte=end).values("user_id").distinct().count()

        # متوسط/أقصى المشاركين في الجلسة (مضيف + متسابقين)
        avg_participants = max_participants = 0
        if GameSession and Contestant:
            sessions_qs = GameSession.objects.filter(created_at__gte=start, created_at__lte=end)
            # نجمع: 1 (المضيف إذا موجود) + عدد المتسابقين
            session_sizes = []
            for s in sessions_qs.only("id", "host_id"):
                count_contestants = Contestant.objects.filter(session_id=s.id).count()
                size = (1 if s.host_id else 0) + count_contestants
                session_sizes.append(size)
            if session_sizes:
                avg_participants = round(sum(session_sizes) / len(session_sizes), 2)
                max_participants = max(session_sizes)

        # أفضل المستخدمين نشاطًا خلال الفترة
        top_active = (UserActivity.objects
                      .filter(created_at__gte=start, created_at__lte=end)
                      .values("user__username", "user__first_name")
                      .annotate(n=Count("id")).order_by("-n")[:10])

        # مقارنة (فترة سابقة مساوية الطول)
        if compare:
            delta = end - start
            prev_end = start
            prev_start = start - delta
            prev_new = User.objects.filter(date_joined__gte=prev_start, date_joined__lte=prev_end).count()
            prev_users_before = User.objects.filter(date_joined__lt=prev_start).count()
            prev_growth = (prev_new / prev_users_before * 100) if prev_users_before else 0

            prev_avg = prev_max = 0
            if GameSession and Contestant:
                ps = GameSession.objects.filter(created_at__gte=prev_start, created_at__lte=prev_end)
                sizes = []
                for s in ps.only("id", "host_id"):
                    c = Contestant.objects.filter(session_id=s.id).count()
                    sizes.append((1 if s.host_id else 0) + c)
                if sizes:
                    prev_avg = round(sum(sizes) / len(sizes), 2)
                    prev_max = max(sizes)

            prev_metrics = {
                "new": prev_new,
                "growth": prev_growth,
                "avg": prev_avg,
                "max": prev_max,
            }
            cmp_html = f"""
            <div style="margin-top:8px;color:#94a3b8;font-size:12px">
              مقارنة بالفترة السابقة ({prev_start.strftime('%Y-%m-%d %H:%M')} → {prev_end.strftime('%Y-%m-%d %H:%M')}):
              جدد: {prev_new} | نمو: {prev_growth:.1f}% | متوسط مشاركين: {prev_avg} | أقصى: {prev_max}
            </div>
            """

        # كروت KPIs
        kpis = [
            _kpi("إجمالي المستخدمين", f"{total_users:,}", "في المنصّة كلها", "info"),
            _kpi("مستخدمون جدد (الفترة)", f"{new_users:,}", f"نمو مقابل قاعدة ما قبل الفترة: {growth_pct:.1f}%", "ok" if growth_pct >= 0 else "bad"),
            _kpi("متوسط المشاركين/جلسة", str(avg_participants), f"أقصى مشاركين في جلسة: {max_participants}", "info"),
        ]
        if show_activity:
            kpis.extend([
                _kpi("DAU (نشط يوميًا)", f"{dau:,}", "", "info"),
                _kpi("WAU (نشط أسبوعيًا)", f"{wau:,}", "", "info"),
                _kpi("MAU (نشط شهريًا)", f"{mau:,}", "", "info"),
            ])

        # اتجاه يومي للمستخدمين الجدد
        by_day = (new_users_qs
                  .annotate(d=TruncDate("date_joined"))
                  .values("d").annotate(n=Count("id")).order_by("d"))
        rows_trend = []
        for r in by_day:
            rows_trend.append(
                f"<tr><td style='padding:8px 12px;border-bottom:1px solid #1f2937;'>{r['d']}</td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #1f2937;'>{r['n']}</td></tr>"
            )

        # أفضل مستخدمين نشاطًا
        rows_top = []
        for i, u in enumerate(top_active, start=1):
            name = u.get("user__first_name") or u.get("user__username") or "—"
            rows_top.append(
                f"<tr><td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{i}</td>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{name}</td>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{u['n']}</td></tr>"
            )

        # نموذج الفلترة
        start_val = start.strftime("%Y-%m-%dT%H:%M")
        end_val = end.strftime("%Y-%m-%dT%H:%M")
        cmp_checked = "checked" if compare else ""
        act_checked = "checked" if show_activity else ""
        controls = f"""
        <form method="get" style="margin:8px 0;">
          <div class="module" style="padding:12px;border-radius:12px;background:#0b1220;border:1px solid #1f2937;">
            <div style="display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;">
              <div><label>البداية (تاريخ/وقت)</label><input type="datetime-local" name="start" value="{start_val}" style="width:100%"></div>
              <div><label>النهاية (تاريخ/وقت)</label><input type="datetime-local" name="end" value="{end_val}" style="width:100%"></div>
              <div style="display:flex;gap:8px;align-items:flex-end;">
                <label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" name="compare" {cmp_checked}> إظهار المقارنة</label>
              </div>
              <div style="display:flex;gap:8px;align-items:flex-end;">
                <label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" name="show_activity" {act_checked}> عرض DAU/WAU/MAU</label>
              </div>
              <div style="display:flex;align-items:flex-end;"><button class="button" style="width:100%">تطبيق</button></div>
              <div style="display:flex;align-items:flex-end;">
                <a class="button" href="{reverse('admin:accounts_users_analytics_csv')}?start={start_val}&end={end_val}" style="width:100%;text-align:center;">تنزيل CSV</a>
              </div>
            </div>
          </div>
        </form>
        """

        html = f"""
        <div style="padding:16px 20px;">
          <h2 style="margin:0 0 10px;">👥 تحليلات الحسابات</h2>
          <div style="color:#94a3b8;margin-bottom:10px;">الفترة المختارة: {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')}</div>
          {controls}
          <div style="display:flex;flex-wrap:wrap;gap:12px;">{''.join(kpis)}</div>
          {cmp_html}
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px;">
            <div>
              <h3 style="margin:6px 0;">📅 مستخدمون جدد حسب اليوم</h3>
              {_table(["اليوم","عدد الجدد"], rows_trend)}
            </div>
            <div>
              <h3 style="margin:6px 0;">🏅 أكثر المستخدمين نشاطًا (الفترة)</h3>
              {_table(["#","المستخدم","عدد الأنشطة"], rows_top)}
            </div>
          </div>
        </div>
        """
        ctx = {**self.admin_site.each_context(request), "title": "تحليلات الحسابات", "content": mark_safe(html)}
        return TemplateResponse(request, "admin/base_site.html", ctx)

    def analytics_csv(self, request):
        start = _parse_dt(request.GET.get("start")) or (timezone.now() - timezone.timedelta(days=30))
        end = _parse_dt(request.GET.get("end")) or timezone.now()
        qs = (User.objects
              .filter(date_joined__gte=start, date_joined__lte=end)
              .values("id", "username", "first_name", "email", "date_joined"))
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = 'attachment; filename="accounts_analytics.csv"'
        import csv
        w = csv.writer(resp)
        w.writerow(["id","username","name","email","date_joined"])
        for u in qs:
            w.writerow([u["id"], u["username"], u["first_name"], u["email"], u["date_joined"].isoformat()])
        return resp


# =========================
# UserActivity Admin
# =========================
@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = ("user", "activity_type_badge", "game_type", "desc_preview", "created_at")
    list_filter = ("activity_type", "game_type", "created_at")
    search_fields = ("user__username", "activity_type", "game_type", "description")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)

    # حلّ خطأ admin.E108 بتعريف الدوال
    def activity_type_badge(self, obj):
        label = obj.get_activity_type_display() if hasattr(obj, "get_activity_type_display") else (obj.activity_type or "—")
        color = "#3b82f6"
        if obj.activity_type in ("package_purchased", "game_created", "game_completed"):
            color = "#10b981"
        elif obj.activity_type in ("profile_updated",):
            color = "#f59e0b"
        return format_html(
            '<span style="background:{0}22;color:{0};padding:2px 8px;border-radius:999px;border:1px solid {0};font-weight:700;">{1}</span>',
            color, label
        )
    activity_type_badge.short_description = "نوع النشاط"

    def desc_preview(self, obj):
        if not obj.description:
            return "—"
        s = obj.description.strip()
        return (s[:60] + "…") if len(s) > 60 else s
    desc_preview.short_description = "الوصف (مختصر)"


# =========================
# UserPreferences Admin
# =========================
@admin.register(UserPreferences)
class UserPreferencesAdmin(admin.ModelAdmin):
    list_display = ("user", "theme_preference", "sound_enabled", "volume_level",
                    "auto_start_timer", "show_answers_immediately", "quick_mode_enabled", "show_statistics")
    list_filter = ("theme_preference", "sound_enabled", "auto_start_timer", "quick_mode_enabled", "show_statistics")
    search_fields = ("user__username",)
    list_select_related = ("user",)
    fieldsets = (
        ("أساسية", {
            "fields": ("user", "theme_preference", "sound_enabled", "volume_level")
        }),
        ("اللعب", {
            "fields": ("default_team1_name", "default_team2_name", "auto_start_timer", "show_answers_immediately")
        }),
        ("التحكم والعرض", {
            "fields": ("quick_mode_enabled", "show_statistics")
        }),
    )

