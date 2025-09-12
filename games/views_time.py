# games/views_time.py

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseNotAllowed
from django.utils.crypto import get_random_string
from django.contrib.auth.decorators import login_required

from .models import GamePackage, GameSession, TimeGameProgress, TimeRiddle

# صفحة الهوم/الباقات لتحدّي الوقت
def time_home(request):
    # الحزم الخاصة بلعبة الوقت
    free_package = GamePackage.objects.filter(game_type='time', is_free=True).first()
    paid_packages = GamePackage.objects.filter(game_type='time', is_free=False).order_by('package_number')

    # فئات/تصنيفات متوفرة (اختياري للعرض في القالب)
    categories = (
        TimeRiddle.objects.values_list('category', flat=True)
        .distinct()
        .order_by('category')
    )

    ctx = {
        'page_title': 'تحدّي الوقت — اختر باقتك',
        'free_package': free_package,
        'paid_packages': paid_packages,
        'categories': categories,
        # متغيرات توافقية إن احتجتها في القالب (قابلة للتوسعة لاحقًا)
        'free_active_session': None,
        'free_session_eligible': True,
        'free_session_message': '',
        'used_before_ids': [],
        'user_purchases': [],
    }
    return render(request, 'games/time/packages.html', ctx)


# إنشاء جلسة لتحدّي الوقت
@login_required
def create_time_session(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    package_id = request.POST.get('package_id')
    package = get_object_or_404(GamePackage, id=package_id, game_type='time')

    # TODO: هنا لاحقًا نتحقق من الأهلية/الدفع (مثل بقية الألعاب)
    # حالياً ننشئ الجلسة مباشرة للربط مع القوالب

    display_link = get_random_string(12)
    contestants_link = get_random_string(12)

    session = GameSession.objects.create(
        user=request.user,
        package=package,
        game_type='time',
        team1_name='اللاعب A',
        team2_name='اللاعب B',
        display_link=display_link,
        contestants_link=contestants_link,
        is_active=True,
        is_completed=False,
    )

    # تهيئة تقدّم اللعبة (مؤقّتات 60 ثانية لكل لاعب كبداية)
    TimeGameProgress.objects.get_or_create(
        session=session,
        defaults={
            'current_index': 1,
            'active_team': 'team1',
            'team1_ms': 60000,
            'team2_ms': 60000,
        }
    )

    # وجّه المقدم لصفحة التحكم
    return redirect('games:time_host', session_id=session.id)
