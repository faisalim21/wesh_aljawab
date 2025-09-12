from django.shortcuts import render, get_object_or_404
from games.models import GameSession

def time_host(request, session_id):
    session = get_object_or_404(GameSession, id=session_id)
    # يُفضّل التأكد أنه من نوع time
    if session.game_type != 'time':
        return render(request, 'games/session_expired.html', {
            'session_type': 'غير متاح',
            'message': 'هذه الجلسة ليست من نوع تحدّي الوقت.'
        })
    return render(request, 'games/time/host.html', {'session': session})



# games/views_time.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone

from games.models import GameSession, TimeRiddle, TimeGameProgress

def time_display(request, display_link):
    """
    شاشة العرض لتحدّي الوقت:
    - تُظهر المؤقّتين على طرفي الشاشة (يسار/يمين) + الصورة الحالية.
    - التزامن الفعلي سيتم عبر WebSocket لاحقًا (ws/time/..).
    - مؤقتًا نعتمد API ابتدائي لجلب الحالة الأولية.
    """
    session = get_object_or_404(
        GameSession,
        display_link=display_link,
        is_active=True,
        game_type='time'  # تأكد أن نوع الحزمة/الجلسة "time"
    )
    return render(request, 'games/time/time_display.html', {
        'session': session,
    })


def api_time_get_current(request):
    """
    إرجاع الحالة الأولية:
    - الصورة الحالية (حسب current_index في TimeGameProgress)
    - من هو اللاعب النشط
    - المتبقي لكل لاعب بالمللي ثانية
    ملاحظة: نقرأ الحقول بتسامح تحاشيًا لاختلاف أسماء الحقول.
    """
    sid = request.GET.get('session_id')
    if not sid:
        return JsonResponse({'success': False, 'error': 'session_id required'}, status=400)

    try:
        session = GameSession.objects.select_related('package').get(id=sid, is_active=True, game_type='time')
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'session not found'}, status=404)

    # لو الجلسة منتهية نخلي الـ frontend يتصرف (مثلاً يرجع للصفحة الرئيسية)
    from datetime import timedelta
    expiry = session.created_at + (timedelta(hours=1) if (session.package and session.package.is_free) else timedelta(hours=72))
    if timezone.now() >= expiry:
        return JsonResponse({'success': False, 'session_expired': True}, status=410)

    # قراءة التقدّم
    prog = TimeGameProgress.objects.filter(session=session).first()

    # current_index المتاح
    current_index = getattr(prog, 'current_index', 1) if prog else 1

    # أزمنة متبقية (ms) + اللاعب النشط
    # أسماء الحقول قد تختلف لذلك نقرأ بتسامح:
    t1_ms = None
    for name in ('team1_ms', 'team1_remaining_ms', 'team1_time_ms'):
        if prog and hasattr(prog, name):
            t1_ms = getattr(prog, name)
            break
    t2_ms = None
    for name in ('team2_ms', 'team2_remaining_ms', 'team2_time_ms'):
        if prog and hasattr(prog, name):
            t2_ms = getattr(prog, name)
            break
    active_team = None
    for name in ('active_team', 'turn', 'current_turn'):
        if prog and hasattr(prog, name):
            active_team = getattr(prog, name)
            break

    # افتراضات آمنة
    t1_ms = 60000 if t1_ms is None else int(t1_ms)
    t2_ms = 60000 if t2_ms is None else int(t2_ms)
    active_team = active_team or 'team1'

    # الصورة الحالية
    riddle = TimeRiddle.objects.filter(package=session.package, order=current_index).values(
        'order', 'image_url', 'hint', 'answer'
    ).first() or {'order': 1, 'image_url': '', 'hint': '', 'answer': ''}

    # إجمالي العناصر (للمستقبل)
    total = TimeRiddle.objects.filter(package=session.package).count() or 1

    return JsonResponse({
        'success': True,
        'session_id': str(session.id),
        'team1_name': session.team1_name,
        'team2_name': session.team2_name,
        'active_team': active_team,
        'team1_ms': max(0, t1_ms),
        'team2_ms': max(0, t2_ms),
        'current_index': int(riddle.get('order') or current_index),
        'count': total,
        'current': {
            'image_url': riddle.get('image_url') or '',
            'hint': riddle.get('hint') or '',
            'answer': riddle.get('answer') or '',
        }
    })



# games/views_time.py
from django.shortcuts import render, get_object_or_404

def time_contestants(request, contestants_link):
    """
    صفحة المتسابقين:
    - مؤقت يمين للفريق الأول مع زرّه أسفله.
    - مؤقت يسار للفريق الثاني مع زرّه أسفله.
    - صورة اللغز في المنتصف.
    """
    session = get_object_or_404(
        GameSession,
        contestants_link=contestants_link,
        is_active=True,
        game_type='time'
    )
    return render(request, 'games/time/time_contestants.html', {
        'session': session,
    })
