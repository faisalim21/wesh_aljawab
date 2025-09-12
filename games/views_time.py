# === أضِف/تأكّد من هذه الاستيرادات أعلى الملف ===
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseNotAllowed
from django.utils.crypto import get_random_string
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.utils import timezone
from datetime import timedelta

from .models import GamePackage, GameSession, TimeGameProgress, TimeRiddle


# === مساعد: فحص انتهاء الصلاحية ===
def _is_session_expired(session: GameSession) -> bool:
    expiry = session.created_at + (
        timedelta(hours=1) if session.package and session.package.is_free else timedelta(hours=72)
    )
    return timezone.now() >= expiry


# === صفحة المقدم (المتحكم) ===
def time_host(request, session_id):
    session = get_object_or_404(GameSession, id=session_id, game_type='time')
    if _is_session_expired(session) or not session.is_active:
        # صفحة انتهاء الصلاحية العامة لديك
        return render(request, 'games/session_expired.html', {
            'session_type': 'مجانية' if (session.package and session.package.is_free) else 'مدفوعة',
            'message': 'انتهت صلاحية الجلسة.',
            'upgrade_message': 'يمكنك إنشاء جلسة جديدة أو شراء حزمة.'
        }, status=410)
    # مبدئيًا نعرض قالب (سننشئه لاحقًا)
    return render(request, 'games/time/time_host.html', {
        'session': session,
        'page_title': f'المقدم — {session.team1_name} ضد {session.team2_name}',
    })


# === شاشة العرض (عندك قالبها time_display.html جاهز) ===
def time_display(request, display_link):
    session = get_object_or_404(GameSession, display_link=display_link, game_type='time')
    if _is_session_expired(session) or not session.is_active:
        return render(request, 'games/session_expired.html', {
            'session_type': 'مجانية' if (session.package and session.package.is_free) else 'مدفوعة',
            'message': 'انتهت صلاحية الجلسة.',
            'upgrade_message': 'يمكنك إنشاء جلسة جديدة أو شراء حزمة.'
        }, status=410)
    return render(request, 'games/time/time_display.html', {
        'session': session,
        'page_title': f'{session.team1_name} ضد {session.team2_name} — شاشة العرض',
    })


# === صفحة المتسابقين (سنكمل قالبها لاحقًا) ===
def time_contestants(request, contestants_link):
    session = get_object_or_404(GameSession, contestants_link=contestants_link, game_type='time')
    if _is_session_expired(session) or not session.is_active:
        return render(request, 'games/session_expired.html', {
            'session_type': 'مجانية' if (session.package and session.package.is_free) else 'مدفوعة',
            'message': 'انتهت صلاحية الجلسة.',
            'upgrade_message': 'يمكنك إنشاء جلسة جديدة أو شراء حزمة.'
        }, status=410)
    return render(request, 'games/time/time_contestants.html', {
        'session': session,
        'page_title': f'المتسابقون — {session.team1_name} ضد {session.team2_name}',
    })


# === API البدء/المزامنة الأولية (يستدعيه قالب العرض) ===
@require_GET
def api_time_get_current(request):
    session_id = request.GET.get('session_id')
    session = get_object_or_404(GameSession, id=session_id, game_type='time')

    if _is_session_expired(session) or not session.is_active:
        return JsonResponse({'detail': 'expired'}, status=410)

    # تأكيد/تهيئة التقدّم
    progress, _ = TimeGameProgress.objects.get_or_create(
        session=session,
        defaults={'current_index': 1, 'active_team': 'team1', 'team1_ms': 60000, 'team2_ms': 60000}
    )

    riddles = list(
        TimeRiddle.objects.filter(package=session.package)
        .order_by('order')
        .values('image_url', 'hint', 'answer')
    )
    total = len(riddles)
    idx = progress.current_index
    cur = riddles[idx - 1] if (1 <= idx <= total) else {}

    return JsonResponse({
        'success': True,
        'active_team': progress.active_team,
        'team1_ms': progress.team1_ms,
        'team2_ms': progress.team2_ms,
        'current_index': idx,
        'count': total,
        'current': cur,
    })


from django.http import HttpResponse
from django.template import TemplateDoesNotExist

# صفحة/home لتحدّي الوقت (الحزم/التصنيفات)
def time_home(request):
    context = {
        "page_title": "تحدّي الوقت — الحزم",
        # ممكن لاحقًا تمرر free_package/paid_packages الخ...
    }
    try:
        # لو جهّزنا القالب: games/time/packages.html
        return render(request, "games/time/packages.html", context)
    except TemplateDoesNotExist:
        # مؤقّتًا: نص بسيط عشان ما يطيح السيرفر لو القالب مو جاهز
        return HttpResponse("تحدّي الوقت — صفحة الحزم (قريبًا).", content_type="text/plain")
