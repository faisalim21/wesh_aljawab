# accounts/admin.py
from __future__ import annotations
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.urls import path, reverse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.db.models import Count, Q, F
from django.db.models.functions import TruncDate
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.contrib.admin.views.decorators import staff_member_required

from .models import UserProfile, UserActivity, UserPreferences

import csv
from datetime import timedelta, datetime
import logging

logger = logging.getLogger("accounts.admin")
User = get_user_model()

# ---------------------------------------------
# أدوات واجهة (داكنة ومتناسقة مع Django Admin)
# ---------------------------------------------
def _kpi_card(label: str, value: str, sub: str | None = None, tone: str = "info") -> str:
    colors = {
        "ok":   ("#10b981", "#064e3b"),
        "warn": ("#f59e0b", "#7c2d12"),
        "bad":  ("#ef4444", "#7f1d1d"),
        "info": ("#3b82f6", "#1e3a8a"),
    }
    base, _ = colors.get(tone, colors["info"])
    return f"""
    <div style="flex:1;min-width:220px;margin:8px;padding:16px;border-radius:12px;
                background:{base}22;border:1px solid {base};box-shadow:0 1px 2px #0002;">
      <div style="color:#cbd5e1;font-weight:700;font-size:13px;margin-bottom:8px;">{label}</div>
      <div style="color:#e5e7eb;font-size:22px;font-weight:800;letter-spacing:0.3px;">{value}</div>
      <div style="color:#94a3b8;font-size:12px;margin-top:6px;">{sub or ''}</div>
    </div>
    """

def _listing_table(headers, rows_html):
    thead = "".join(
        f"<th style='padding:10px 12px;text-align:right;border-bottom:1px solid #1f2937;'>{h}</th>"
        for h in headers
    )
    tbody = "".join(rows_html) or f"<tr><td colspan='{len(headers)}' style='padding:12px;color:#94a3b8;'>لا توجد بيانات</td></tr>"
    return f"""
    <div class="module" style="margin:12px 0;border-radius:12px;overflow:hidden;">
      <table class="listing" style="width:100%;border-collapse:collapse;background:#0b1220;">
        <thead style="background:#0f172a;color:#cbd5e1;'">{thead}</thead>
        <tbody style="color:#e2e8f0;">{tbody}</tbody>
      </table>
    </div>
    """

def _progress_bar(label, pct):
    try:
        pct = int(max(0, min(100, pct)))
    except Exception:
        pct = 0
    return f"""
    <div style="display:flex;align-items:center;gap:8px;">
      <div style="flex:1;background:#111827;border-radius:999px;overflow:hidden;height:8px;">
        <div style="width:{pct}%;height:8px;background:#3b82f6;"></div>
      </div>
      <span style="font-size:12px;color:#94a3b8;">{label}</span>
    </div>
    """

# ---------------------------------------------
# مساعدات الوقت + المقارنة
# ---------------------------------------------
def _parse_dt_local(s: str | None) -> datetime | None:
    if not s:
        return None
    # يدعم datetime-local: 2025-08-17T08:00
    try:
        return timezone.make_aware(datetime.strptime(s.strip(), "%Y-%m-%dT%H:%M"))
    except Exception:
        # يدعم تاريخ فقط (يبدأ اليوم 00:00)
        try:
            d = datetime.strptime(s.strip(), "%Y-%m-%d")
            return timezone.make_aware(datetime(d.year, d.month, d.day, 0, 0))
        except Exception:
            return None

def _get_period(request):
    """يعيد (start, end, compare_enabled). افتراضي: آخر 30 يوم حتى الآن."""
    end = _parse_dt_local(request.GET.get("end"))
    start = _parse_dt_local(request.GET.get("start"))
    if not end:
        end = timezone.now()
    if not start:
        start = end - timedelta(days=30)
    # تصحيح لو المستخدم عكس:
    if start > end:
        messages.error(request, "نطاق التاريخ غير صحيح: البداية بعد النهاية.")
        start, end = end - timedelta(days=30), end
    compare = request.GET.get("compare") in ("1", "true", "on", "yes")
    return start, end, compare

def _prev_period(start, end):
    delta = end - start
    prev_end = start
    prev_start = prev_end - delta
    return prev_start, prev_end

def _fmt_delta(curr: int, prev: int) -> tuple[str, str]:
    """يعيد (رمز السهم والنسبة كنص)."""
    if prev <= 0:
        return "—", "—"
    change = ((curr - prev) / prev) * 100.0
    arrow = "↑" if change > 0 else ("↓" if change < 0 else "→")
    return arrow, f"{change:.1f}%"

# -------------------------------------------------
# Admin: UserProfile
# -------------------------------------------------
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user_username", "user_email", "display_name", "is_host",
        "phone_number", "favorite_game_ar", "account_type", "created_at", "updated_at",
    )
    list_filter = ("is_host", "account_type", "favorite_game", "created_at", "updated_at")
    search_fields = ("user__username", "user__email", "user__first_name", "host_name", "phone_number")
    ordering = ("-created_at",)
    list_select_related = ("user",)

    actions = ("open_analytics", "enable_notifications", "disable_notifications",)

    def user_username(self, obj):
        return obj.user.username
    user_username.short_description = "اسم المستخدم"

    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = "البريد"

    def favorite_game_ar(self, obj):
        choices = dict(UserProfile._meta.get_field("favorite_game").choices)
        return choices.get(obj.favorite_game, "—")
    favorite_game_ar.short_description = "اللعبة المفضلة"

    @admin.action(description="📈 فتح تحليلات المستخدمين")
    def open_analytics(self, request, queryset):
        return HttpResponseRedirect(reverse("admin:accounts_userprofile_analytics"))

    @admin.action(description="تفعيل الإشعارات للمحدد")
    def enable_notifications(self, request, queryset):
        updated = queryset.update(notifications_enabled=True)
        messages.success(request, f"تم تفعيل الإشعارات لـ {updated} مستخدم.")

    @admin.action(description="تعطيل الإشعارات للمحدد")
    def disable_notifications(self, request, queryset):
        updated = queryset.update(notifications_enabled=False)
        messages.info(request, f"تم تعطيل الإشعارات لـ {updated} مستخدم.")

    # روابط مخصّصة
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("analytics/", self.admin_site.admin_view(self.analytics_view), name="accounts_userprofile_analytics"),
            path("analytics.csv", self.admin_site.admin_view(self.analytics_csv_view), name="accounts_userprofile_analytics_csv"),
        ]
        return custom + urls

    # -----------------------
    # شاشة التحليلات
    # -----------------------
    def analytics_view(self, request):
        start, end, compare = _get_period(request)
        prev_start, prev_end = _prev_period(start, end)

        # إجمالي المستخدمين (كل الوقت)
        total_users = User.objects.count()

        # المستخدمون الجدد
        new_users = User.objects.filter(date_joined__gte=start, date_joined__lte=end).count()
        prev_new_users = User.objects.filter(date_joined__gte=prev_start, date_joined__lte=prev_end).count() if compare else 0
        arrow_new, pct_new = _fmt_delta(new_users, prev_new_users) if compare else ("", "")

        # النشطون خلال الفترة (أي نشاط في UserActivity)
        active_user_ids = set(
            UserActivity.objects.filter(created_at__gte=start, created_at__lte=end).values_list("user_id", flat=True).distinct()
        )
        active_users = len(active_user_ids)

        # المقدّمون الفعّالون (أنشؤوا جلسات في الفترة)
        try:
            from games.models import GameSession, Contestant
            # مضيفون فعّالون
            active_hosts = (
                GameSession.objects.filter(created_at__gte=start, created_at__lte=end, host__isnull=False)
                .values("host_id").distinct().count()
            )

            # المشاركون في الجلسات (لإحصائيات المتوسط/الوسيط/الأقصى)
            sessions_qs = (
                GameSession.objects.filter(created_at__gte=start, created_at__lte=end)
                .select_related("host").annotate(contestants_count=Count("contestants"))
            )
            session_counts = []
            for s in sessions_qs:
                base = 1 if s.host_id else 0
                session_counts.append(base + (s.contestants_count or 0))

            total_sessions = len(session_counts)
            avg_participants = (sum(session_counts) / total_sessions) if total_sessions else 0
            max_participants = max(session_counts) if total_sessions else 0
            # وسيط مبسّط
            median_participants = 0
            if total_sessions:
                sorted_counts = sorted(session_counts)
                mid = total_sessions // 2
                median_participants = (sorted_counts[mid] if total_sessions % 2 == 1 else (sorted_counts[mid-1] + sorted_counts[mid]) / 2)

            # أعلى 5 جلسات
            top_sessions = (
                GameSession.objects.filter(created_at__gte=start, created_at__lte=end)
                .annotate(cc=Count("contestants"))
                .values("id", "game_type", "created_at", "host_id", "cc")
                .order_by("-cc")[:5]
            )
            top_rows = []
            for i, s in enumerate(top_sessions, start=1):
                cnt = (1 if s["host_id"] else 0) + (s["cc"] or 0)
                top_rows.append(
                    f"<tr>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{i}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{s['id']}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{s['game_type']}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{timezone.localtime(s['created_at']).strftime('%Y-%m-%d %H:%M')}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{cnt}</td>"
                    f"</tr>"
                )
            top_block = _listing_table(["#","معرّف الجلسة","اللعبة","التاريخ","عدد المشاركين"], top_rows)

        except Exception as e:
            logger.warning(f"Games data not available: {e}")
            active_hosts = 0
            avg_participants = 0
            median_participants = 0
            max_participants = 0
            top_block = _listing_table(["#","معرّف الجلسة","اللعبة","التاريخ","عدد المشاركين"], [])

        # DAU/WAU/MAU (اختيارية)
        show_activity = request.GET.get("show_activity") in ("1", "true", "on", "yes")
        dau = wau = mau = None
        if show_activity:
            # نأخذ “اليوم/الأسبوع/الشهر” المنتهي عند end
            end_floor = timezone.make_aware(datetime(end.year, end.month, end.day, 23, 59))
            day_start = end_floor - timedelta(days=1)
            week_start = end_floor - timedelta(days=7)
            month_start = end_floor - timedelta(days=30)
            try:
                dau = (
                    UserActivity.objects.filter(created_at__gt=day_start, created_at__lte=end_floor)
                    .values("user_id").distinct().count()
                )
                wau = (
                    UserActivity.objects.filter(created_at__gt=week_start, created_at__lte=end_floor)
                    .values("user_id").distinct().count()
                )
                mau = (
                    UserActivity.objects.filter(created_at__gt=month_start, created_at__lte=end_floor)
                    .values("user_id").distinct().count()
                )
            except Exception as e:
                logger.error(f"DAU/WAU/MAU error: {e}")

        # بطاقات KPIs
        kpis = [
            _kpi_card("إجمالي المستخدمين", f"{total_users:,}", "كل الوقت", "info"),
            _kpi_card("المستخدمون الجدد (الفترة)", f"{new_users:,}",
                      (f"{arrow_new} {pct_new}" if compare else "—"), "ok" if new_users else "warn"),
            _kpi_card("المستخدمون النشطون (الفترة)", f"{active_users:,}", "أي نشاط مسجّل", "info"),
            _kpi_card("المقدّمون الفعّالون", f"{active_hosts:,}", "أنشؤوا جلسات خلال الفترة", "info"),
            _kpi_card("متوسط المشاركين/جلسة", f"{avg_participants:.1f}",
                      f"وسيط: {median_participants:.1f} | أقصى: {max_participants}", "info"),
        ]
        if show_activity:
            kpis.extend([
                _kpi_card("النشطون يوميًا (DAU)", f"{(dau or 0):,}", "آخر 24 ساعة من نهاية الفترة", "info"),
                _kpi_card("النشطون أسبوعيًا (WAU)", f"{(wau or 0):,}", "آخر 7 أيام من نهاية الفترة", "info"),
                _kpi_card("النشطون شهريًا (MAU)", f"{(mau or 0):,}", "آخر 30 يومًا من نهاية الفترة", "info"),
            ])

        # نموذج التحكم العلوي
        start_val = timezone.localtime(start).strftime("%Y-%m-%dT%H:%M")
        end_val = timezone.localtime(end).strftime("%Y-%m-%dT%H:%M")
        compare_checked = "checked" if compare else ""
        show_activity_checked = "checked" if show_activity else ""
        controls_html = f"""
        <form method="get" style="margin:8px 0;">
            <div class="module" style="padding:12px;border-radius:12px;background:#0b1220;border:1px solid #1f2937;">
              <div style="display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;">
                <div><label>من</label><input type="datetime-local" name="start" value="{start_val}" style="width:100%"></div>
                <div><label>إلى</label><input type="datetime-local" name="end" value="{end_val}" style="width:100%"></div>
                <div style="display:flex;align-items:flex-end;gap:8px;">
                  <label style="display:flex;align-items:center;gap:6px;">
                    <input type="checkbox" name="compare" {compare_checked}> <span>إظهار المقارنة</span>
                  </label>
                </div>
                <div style="display:flex;align-items:flex-end;gap:8px;">
                  <label style="display:flex;align-items:center;gap:6px;">
                    <input type="checkbox" name="show_activity" {show_activity_checked}> <span>إظهار DAU/WAU/MAU</span>
                  </label>
                </div>
                <div style="display:flex;align-items:flex-end;"><button class="button" style="width:100%">تطبيق</button></div>
                <div style="display:flex;align-items:flex-end;">
                  <a class="button" href="{reverse('admin:accounts_userprofile_analytics_csv')}?start={start_val}&end={end_val}" style="width:100%;text-align:center;">تصدير CSV</a>
                </div>
              </div>
            </div>
        </form>
        """

        html = f"""
        <div style="padding:16px 20px;">
          <h2 style="margin:0 0 10px;">👥 تحليلات المستخدمين</h2>
          {controls_html}
          <div style="display:flex;flex-wrap:wrap;gap:12px;">{''.join(kpis)}</div>
          <div style="margin-top:16px;">
            <h3 style="margin:6px 0;">🏆 أعلى الجلسات حسب عدد المشاركين (خلال الفترة)</h3>
            {top_block}
          </div>
          <div style="margin-top:16px;color:#6b7280;font-size:12px;">
            * “المشاركون/جلسة” تُحسب من المضيف (إن وجد) + المتسابقين. يلزم وجود تطبيق الألعاب لظهور هذه المقاييس.
          </div>
        </div>
        """

        context = {
            **self.admin_site.each_context(request),
            "title": "تحليلات المستخدمين",
            "content": mark_safe(html),
        }
        return TemplateResponse(request, "admin/base_site.html", context)

    def analytics_csv_view(self, request):
        """تصدير بسيط للمستخدمين الجدد والنشطين في الفترة إلى CSV."""
        start, end, _ = _get_period(request)

        new_users_qs = (
            User.objects.filter(date_joined__gte=start, date_joined__lte=end)
            .values("id", "username", "email", "date_joined").order_by("-date_joined")
        )
        active_user_ids = set(
            UserActivity.objects.filter(created_at__gte=start, created_at__lte=end)
            .values_list("user_id", flat=True).distinct()
        )

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="accounts_analytics_{timezone.localtime(start).date()}_{timezone.localtime(end).date()}.csv"'
        w = csv.writer(response)
        w.writerow(["user_id", "username", "email", "is_new_in_period", "is_active_in_period", "date_joined"])
        all_ids = set(list(new_users_qs.values_list("id", flat=True))) | active_user_ids
        # لتقليل الاستعلامات
        users_map = {u["id"]: u for u in new_users_qs}
        # نضيف كل النشطين حتى لو ما كانوا جددًا
        for uid in sorted(all_ids):
            uobj = users_map.get(uid)
            if uobj:
                row = [uid, uobj["username"], uobj["email"], 1, 1 if uid in active_user_ids else 0, uobj["date_joined"].isoformat()]
            else:
                # مستخدم نشط لكنه ليس جديدًا في الفترة
                user = User.objects.filter(id=uid).values("username", "email", "date_joined").first()
                row = [uid, user["username"] if user else "", user["email"] if user else "", 0, 1, user["date_joined"].isoformat() if user else ""]
            w.writerow(row)
        return response


# -------------------------------------------------
# Admin: UserActivity
# -------------------------------------------------
@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = ("user", "activity_type_badge", "game_type", "desc_preview", "created_at")
    list_filter = ("activity_type", "game_type", "created_at")
    search_fields = ("user__username", "activity_type", "game_type")

