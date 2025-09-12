# games/views_time.py
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_GET
from django.template import TemplateDoesNotExist

from .models import GameSession

# موديلات إضافية (اختيارية حسب مشروعك)
try:
    from .models import GamePackage  # إن ما تستعمل باقات الآن، عادي تكون None
except Exception:
    GamePackage = None

try:
    from .models import TimeGameProgress, TimeRiddle
except Exception:
    TimeGameProgress = None
    TimeRiddle = None


# ======================== Helpers ========================

def _is_session_expired(session: GameSession) -> bool:
    """تحقق انتهاء الصلاحية بحسب نوع الباقة."""
    expiry = session.created_at + (
        timedelta(hours=1) if session.package and session.package.is_free else timedelta(hours=72)
    )
    return timezone.now() >= expiry


def _gen_code(n=12) -> str:
    """مولّد أكواد قصيرة للروابط العامة (عرض/متسابقين)."""
    return get_random_string(n=n, allowed_chars="abcdefghijklmnopqrstuvwxyz0123456789")


# ======================== Home (Packages/Categories) ========================

def time_home(request):
    """
    صفحة اختيار الفئات/الباقات لتحدّي الوقت.
    القالب: games/time/packages.html
    """
    context = {
        "page_title": "تحدّي الوقت — الحزم",
        # مبدئيًا: لو عندك قائمة فئات/باقات، مرّرها هنا:
        # "categories": categories_qs_or_list,
        "categories": [],
        "bundle_size": 8,
        "per_cat_price": 5,
        "bundle_discount_pct": 0,
    }
    try:
        return render(
            request,
            "games/time/packages.html",
            context,
            content_type="text/html; charset=utf-8",  # يحسم الترميز ويزيل مشاكل الـ mojibake
        )
    except TemplateDoesNotExist:
        # احتياط لو القالب غير موجود
        return HttpResponse("تحدّي الوقت — صفحة الحزم (قريبًا).", content_type="text/plain; charset=utf-8")


# ======================== Create Session ========================

@login_required
def create_time_session(request):
    """
    إنشاء جلسة (تحدّي الوقت) والانتقال مباشرةً لصفحة المقدم.
    يدعم تمرير: team1_name / team2_name و package_id (اختياري).
    """
    if request.method != "POST":
        return HttpResponseBadRequest("طريقة الطلب غير صحيحة")

    # أسماء الفرق من الفورم (أو قيم افتراضية)
    team1 = (request.POST.get("team1_name") or "الفريق A").strip()
    team2 = (request.POST.get("team2_name") or "الفريق B").strip()

    # اختيار باقة (اختياري)
    package_obj = None
    if GamePackage:
        package_id = request.POST.get("package_id")
        if package_id:
            try:
                package_obj = GamePackage.objects.get(id=package_id)
            except GamePackage.DoesNotExist:
                package_obj = None

    # يمكنك التقاط selected_category_ids لو حبيت تخزّنها لاحقًا
    # selected_ids = (request.POST.get("selected_category_ids") or "").split(",")

    session = GameSession.objects.create(
        user=request.user,
        game_type="time",
        package=package_obj,
        team1_name=team1,
        team2_name=team2,
        display_link=_gen_code(12),
        contestants_link=_gen_code(12),
        is_active=True,
    )

    # تهيئة تقدّم افتراضي
    if TimeGameProgress:
        try:
            TimeGameProgress.objects.get_or_create(
                session=session,
                defaults={
                    "current_index": 1,
                    "active_team": "team1",
                    "team1_ms": 60000,  # 60 ثانية
                    "team2_ms": 60000,
                },
            )
        except Exception:
            pass

    return redirect("games:time_host", session_id=session.id)


# ======================== Session Pages ========================

def time_host(request, session_id):
    """صفحة المقدم (المتحكم)."""
    session = get_object_or_404(GameSession, id=session_id, game_type="time")
    if _is_session_expired(session) or not session.is_active:
        return render(
            request,
            "games/session_expired.html",
            {
                "session_type": "مجانية" if (session.package and session.package.is_free) else "مدفوعة",
                "message": "انتهت صلاحية الجلسة.",
                "upgrade_message": "يمكنك إنشاء جلسة جديدة أو شراء حزمة.",
            },
            status=410,
        )
    # القالب النهائي سنبنيه لاحقًا
    try:
        return render(
            request,
            "games/time/time_host.html",
            {
                "session": session,
                "page_title": f"المقدم — {session.team1_name} ضد {session.team2_name}",
            },
            content_type="text/html; charset=utf-8",
        )
    except TemplateDoesNotExist:
        return HttpResponse("واجهة المقدم (قريبًا).", content_type="text/plain; charset=utf-8")


def time_display(request, display_link):
    """شاشة العرض (للمشاهدين)."""
    session = get_object_or_404(GameSession, display_link=display_link, game_type="time")
    if _is_session_expired(session) or not session.is_active:
        return render(
            request,
            "games/session_expired.html",
            {
                "session_type": "مجانية" if (session.package and session.package.is_free) else "مدفوعة",
                "message": "انتهت صلاحية الجلسة.",
                "upgrade_message": "يمكنك إنشاء جلسة جديدة أو شراء حزمة.",
            },
            status=410,
        )
    return render(
        request,
        "games/time/time_display.html",
        {
            "session": session,
            "page_title": f"{session.team1_name} ضد {session.team2_name} — شاشة العرض",
        },
        content_type="text/html; charset=utf-8",
    )


def time_contestants(request, contestants_link):
    """صفحة المتسابقين (زر التبديل وعرض المؤقت لكل لاعب أمامه)."""
    session = get_object_or_404(GameSession, contestants_link=contestants_link, game_type="time")
    if _is_session_expired(session) or not session.is_active:
        return render(
            request,
            "games/session_expired.html",
            {
                "session_type": "مجانية" if (session.package and session.package.is_free) else "مدفوعة",
                "message": "انتهت صلاحية الجلسة.",
                "upgrade_message": "يمكنك إنشاء جلسة جديدة أو شراء حزمة.",
            },
            status=410,
        )
    try:
        return render(
            request,
            "games/time/time_contestants.html",
            {
                "session": session,
                "page_title": f"المتسابقون — {session.team1_name} ضد {session.team2_name}",
            },
            content_type="text/html; charset=utf-8",
        )
    except TemplateDoesNotExist:
        return HttpResponse("واجهة المتسابقين (قريبًا).", content_type="text/plain; charset=utf-8")


# ======================== API (initial sync) ========================

@require_GET
def api_time_get_current(request):
    """
    تُستخدم في بداية شاشة العرض لحمل:
    - الفريق النشط
    - رصيد الوقت لكل فريق بالملي ثانية
    - الصورة الحالية (من TimeRiddle على نفس الباقة)
    """
    session_id = request.GET.get("session_id")
    session = get_object_or_404(GameSession, id=session_id, game_type="time")

    if _is_session_expired(session) or not session.is_active:
        return JsonResponse({"detail": "expired"}, status=410)

    # تأكيد/تهيئة التقدّم
    progress = None
    if TimeGameProgress:
        progress, _ = TimeGameProgress.objects.get_or_create(
            session=session,
            defaults={
                "current_index": 1,
                "active_team": "team1",
                "team1_ms": 60000,
                "team2_ms": 60000,
            },
        )

    active_team = getattr(progress, "active_team", "team1")
    team1_ms = getattr(progress, "team1_ms", 60000)
    team2_ms = getattr(progress, "team2_ms", 60000)
    current_index = getattr(progress, "current_index", 1)

    # الصورة الحالية من TimeRiddle (اختياري)
    cur = {}
    total = 0
    if TimeRiddle and session.package:
        riddles = list(
            TimeRiddle.objects.filter(package=session.package)
            .order_by("order")
            .values("image_url", "hint", "answer")
        )
        total = len(riddles)
        if 1 <= current_index <= total:
            cur = riddles[current_index - 1]

    return JsonResponse(
        {
            "success": True,
            "active_team": active_team,
            "team1_ms": int(team1_ms),
            "team2_ms": int(team2_ms),
            "current_index": int(current_index),
            "count": int(total or 0),
            "current": cur or {},
        }
    )
