# games/views.py 
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from datetime import timedelta
import json
import uuid
import logging
import random
import secrets
from django.core.cache import cache
from django.views.decorators.http import require_POST
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from games.models import GameSession
from django.shortcuts import render
from django.utils import timezone
from django.db.models import Q
from .models import GamePackage, UserPurchase
from games.utils_letters import (
    get_session_order, set_session_order,
    get_paid_order_fresh, get_free_order
)
from .models import (
    GamePackage, GameSession, UserPurchase, LettersGameProgress,
    LettersGameQuestion, Contestant
)

logger = logging.getLogger('games')

# ===============================
# Helpers: انتهاء الجلسة/الوقت
# ===============================
def _expired_text(session):
    return 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)' if session.package.is_free else 'انتهت صلاحية الجلسة (72 ساعة)'

def is_session_expired(session):
    """انتهاء صلاحية الجلسة أو تعطيلها: المجاني 1 ساعة، المدفوع 72 ساعة."""
    if not session or not getattr(session, "created_at", None):
        return True
    hours = 1 if (getattr(session, "package", None) and session.package.is_free) else 72
    expiry_time = session.created_at + timedelta(hours=hours)
    return (timezone.now() >= expiry_time) or (not session.is_active)

# للتوافق مع أي استخدام سابق
_session_expired = is_session_expired



def get_session_time_remaining(session):
    if not session.package.is_free:
        expiry_time = session.created_at + timedelta(hours=72)
    else:
        expiry_time = session.created_at + timedelta(hours=1)
    now = timezone.now()
    if now >= expiry_time:
        return timedelta(0)
    return expiry_time - now

def get_session_expiry_info(session):
    now = timezone.now()
    is_free = session.package.is_free
    if is_free:
        duration_hours = 1
        duration_text = "ساعة واحدة"
        expiry_time = session.created_at + timedelta(hours=1)
    else:
        duration_hours = 72
        duration_text = "72 ساعة (3 أيام)"
        expiry_time = session.created_at + timedelta(hours=72)

    time_remaining = expiry_time - now if now < expiry_time else timedelta(0)
    is_expired = now >= expiry_time

    total_duration = timedelta(hours=duration_hours)
    remaining_percentage = (time_remaining.total_seconds() / total_duration.total_seconds() * 100) if time_remaining.total_seconds() > 0 else 0

    warning_message, warning_level = None, "info"
    if not is_expired and time_remaining.total_seconds() > 0:
        if is_free:
            remaining_minutes = int(time_remaining.total_seconds() // 60)
            if remaining_minutes <= 5:
                warning_message = f"🚨 باقي {remaining_minutes} دقائق فقط!"
                warning_level = "danger"
            elif remaining_minutes <= 10:
                warning_message = f"⚠️ باقي {remaining_minutes} دقيقة على انتهاء الجلسة"
                warning_level = "warning"
            elif remaining_minutes <= 30:
                warning_message = f"ℹ️ باقي {remaining_minutes} دقيقة"
                warning_level = "info"
        else:
            remaining_hours = int(time_remaining.total_seconds() // 3600)
            remaining_days = remaining_hours // 24
            if remaining_hours <= 3:
                warning_message = f"🚨 باقي {remaining_hours} ساعات فقط!"
                warning_level = "danger"
            elif remaining_hours <= 12:
                warning_message = f"⚠️ باقي {remaining_hours} ساعة على انتهاء الصلاحية"
                warning_level = "warning"
            elif remaining_days == 1:
                warning_message = "ℹ️ باقي يوم واحد على انتهاء الصلاحية"
                warning_level = "info"
            elif remaining_days == 2:
                warning_message = "ℹ️ باقي يومان"
                warning_level = "info"
            elif remaining_days >= 3:
                warning_message = f"ℹ️ باقي {remaining_days} أيام"
                warning_level = "info"

    return {
        'is_free': is_free,
        'session_type': 'مجانية' if is_free else 'مدفوعة',
        'duration_text': duration_text,
        'duration_hours': duration_hours,
        'expiry_time': expiry_time,
        'time_remaining': time_remaining,
        'is_expired': is_expired,
        'remaining_percentage': remaining_percentage,
        'warning_message': warning_message,
        'warning_level': warning_level,
        'created_at': session.created_at,
    }

# ===============================
# Helpers: الحروف للجلسة
# ===============================

ALL_ARABIC_LETTERS = [
    'أ', 'ب', 'ت', 'ث', 'ج', 'ح', 'خ', 'د', 'ذ', 'ر',
    'ز', 'س', 'ش', 'ص', 'ض', 'ط', 'ظ', 'ع', 'غ', 'ف',
    'ق', 'ك', 'ل', 'م', 'ن', 'هـ', 'و', 'ي'
]

# قائمة ثابتة للحزمة المجانية رقم 0 (25 حرفًا – لا تتغير)
FIXED_FREE_0_LETTERS = [
    'أ', 'ب', 'ت', 'ث', 'ج',
    'ح', 'خ', 'د', 'ذ', 'ر',
    'ز', 'س', 'ش', 'ص', 'ض',
    'ط', 'ظ', 'ع', 'غ', 'ف',
    'ق', 'ك', 'ل', 'م', 'ن',
]

def get_letters_for_session(session):
    """
    المصدر الوحيد لترتيب حروف الجلسة.
    - يقرأ من utils_letters.get_session_order
    - لو ما لقى ترتيب مخزّن (جلسة قديمة/أول مرة) ينشئه وفق نوع الحزمة ويحفظه.
    """
    is_free = session.package.is_free
    letters = get_session_order(session.id, is_free)
    if letters and isinstance(letters, (list, tuple)) and len(letters) > 0:
        return list(letters)

    # ترتيب ابتدائي بحسب نوع الحزمة
    letters = get_free_order() if is_free else get_paid_order_fresh()
    set_session_order(session.id, letters, is_free=is_free)
    return list(letters)

# ===============================
# Helpers: أهلية الجلسات المجانية
# ===============================

def check_free_session_eligibility(user, game_type):
    """
    يسمح لكل مستخدم بجلسة مجانية واحدة فقط  لكل نوع لعبة.

    """
    if not user or not user.is_authenticated:
        return False, "يرجى تسجيل الدخول للاستفادة من الجلسة المجانية", 0

    try:
        # عدد الجلسات المجانية السابقة لهذا النوع
        sessions_count = GameSession.objects.filter(
            host=user, game_type=game_type, package__is_free=True
        ).count()
    except Exception:
        sessions_count = 0

    if sessions_count >= 1:
        # استنفد الجلسة المجانية
        return False, "لقد استخدمت الجلسة المجانية الخاصة بك.", sessions_count

    return True, "", sessions_count




# ===============================
# Helpers: توكن المضيف
# ===============================

def _host_token_key(session_id):
    return f"host_token_{session_id}"

def _put_host_token(session, token=None):
    token = token or secrets.token_urlsafe(16)
    remaining = get_session_time_remaining(session)
    ttl = max(1, int(remaining.total_seconds())) if remaining else 3600
    cache.set(_host_token_key(session.id), token, timeout=ttl)
    return token

def _require_host_token(request, session_id):
    """
    التحقّق من توكن المضيف لأوامر HTTP الحساسة.
    يقبل من:
      - Header: X-Host-Token
      - أو JSON body: {"host_token": "..."} كحل بديل
    """
    expected = cache.get(_host_token_key(session_id))
    if not expected:
        return False

    # Header
    provided = request.META.get('HTTP_X_HOST_TOKEN')
    if provided and provided == expected:
        return True

    # JSON body fallback
    try:
        body = json.loads(request.body or "{}")
        if body.get('host_token') == expected:
            return True
    except Exception:
        pass

    return False

# ===============================
# Views
# ===============================

def games_home(request):
    return render(request, 'home.html', {
        'letters_available': True,
        'images_available': False,
        'quiz_available': False,
    })


def letters_game_home(request):
    # كل الحزم الفعّالة لخلية الحروف
    packages_qs = GamePackage.objects.filter(
        game_type='letters', is_active=True
    ).order_by('is_free', 'package_number')

    free_package = packages_qs.filter(is_free=True).order_by('package_number').first()
    paid_packages = packages_qs.filter(is_free=False)

    # 👇 متغيرات القالب
    user_purchases = set()   # حزم نشِطة (مشتراة ولم تنتهِ) → يظهر "ابدأ اللعب"
    used_before_ids = set()  # حزم استُخدمت/انتهت سابقًا → يظهر شريط وتنبيه قبل الشراء

    if request.user.is_authenticated:
        now = timezone.now()
        # إجلب كل مشتريات المستخدم لهذه اللعبة
        purchases = (UserPurchase.objects
                     .select_related('package')
                     .filter(user=request.user, package__game_type='letters')
                     .order_by('-purchase_date'))

        # أولاً: فعّال DB-wise (غير مكتمل + لم تنتهِ صلاحيتها)
        active_ids_db = purchases.filter(
            is_completed=False,
            expires_at__gt=now
        ).values_list('package_id', flat=True)

        user_purchases = set(active_ids_db)

        # باقي السجلات إمّا مكتملة أو منتهية (أو expires_at قديم/فارغ)
        # نستعمل خاصية is_expired لضمان الدقة حتى لو expires_at كان None بس اتضبط بالحفظ.
        for p in purchases:
            if p.package_id in user_purchases:
                continue
            if p.is_completed or p.is_expired:
                used_before_ids.add(p.package_id)

        # (اختياري) لو حاب تحدّث قاعدة البيانات تلقائيًا لو انتهت
        # for p in purchases:
        #     p.mark_expired_if_needed(auto_save=True)

    # أهلية المجاني (لو عندك دالة جاهزة)
    free_session_eligible = False
    free_session_message = ""
    # مثال: لو عندك خدمة/دالة للأهلية استدعها هنا
    # free_session_eligible, free_session_message = check_free_eligibility(request.user, game_type='letters')

    context = {
        'free_package': free_package,
        'paid_packages': paid_packages,
        'user_purchases': user_purchases,
        'used_before_ids': used_before_ids,  # 👈 هذا المطلوب للقالب
        'free_session_eligible': free_session_eligible,
        'free_session_message': free_session_message,
    }
    return render(request, 'games/letters/packages.html', context)


def create_letters_session(request):
    """
    إنشاء جلسة خلية الحروف:
    - المجانية: تتطلب تسجيل دخول + أهلية جلسة مجانية واحدة.
    - المدفوعة: تتطلب تسجيل دخول + شراء نشط غير مستهلَك.
    - تثبيت ترتيب الحروف الابتدائي عبر utils_letters (ثابت داخل الجلسة).
    """
    if request.method != 'POST':
        return redirect('games:letters_home')

    package_id = request.POST.get('package_id')
    package = get_object_or_404(GamePackage, id=package_id, game_type='letters')

    lock_key = None
    try:
        # ===== تحقق الأهلية/الشراء حسب نوع الحزمة =====
        if package.is_free:
            # يجب تسجيل الدخول
            if not request.user.is_authenticated:
                messages.info(request, 'يرجى تسجيل الدخول لإنشاء جلسة لعب')
                return redirect(f'/accounts/login/?next={request.path}')

            eligible, anti_cheat_message, sessions_count = check_free_session_eligibility(
                request.user, 'letters'
            )
            if not eligible:
                messages.error(request, anti_cheat_message or 'غير مؤهل للجلسة المجانية حاليًا')
                logger.warning(f'Free session creation blocked for user {request.user.username}: {sessions_count} previous sessions')
                return redirect('games:letters_home')
        else:
            # مدفوعة: تسجيل دخول + شراء نشط غير مستهلَك
            if not request.user.is_authenticated:
                messages.error(request, 'يرجى تسجيل الدخول لشراء الحزم المدفوعة')
                return redirect(f'/accounts/login/?next={request.path}')

            purchase = UserPurchase.objects.filter(
                user=request.user, package=package, is_completed=False
            ).order_by('-purchase_date').first()

            if not purchase:
                messages.error(request, 'يجب شراء هذه الحزمة أولًا')
                return redirect('games:letters_home')

            # في حال الشراء منتهي الصلاحية
            if purchase.mark_expired_if_needed(auto_save=True):
                messages.error(request, 'انتهت صلاحية الشراء السابق. لإعادة اللعب تحتاج شراء جديد.')
                return redirect('games:letters_home')

        # ===== أسماء الفرق (مع تفضيلات المستخدم إن وُجدت) =====
        team1_name = request.POST.get('team1_name', 'الفريق الأخضر')
        team2_name = request.POST.get('team2_name', 'الفريق البرتقالي')
        if request.user.is_authenticated and hasattr(request.user, 'preferences'):
            team1_name = request.user.preferences.default_team1_name or team1_name
            team2_name = request.user.preferences.default_team2_name or team2_name

        # ===== قفل خفيف لمنع الإنشاء المزدوج (3 ثوانٍ) =====
        lock_owner = request.user.id if request.user.is_authenticated else request.META.get('REMOTE_ADDR', 'anon')
        lock_key = f"letters_create_lock:{lock_owner}"
        if cache.get(lock_key):
            messages.info(request, '⏳ يتم إنشاء الجلسة الآن، انتظر لحظات...')
            return redirect('games:letters_home')
        cache.set(lock_key, 1, timeout=3)

        # ===== إنشاء الجلسة =====
        session = GameSession.objects.create(
            host=request.user if request.user.is_authenticated else None,
            package=package,
            game_type='letters',
            team1_name=team1_name,
            team2_name=team2_name,
        )

        # ===== تثبيت ترتيب الحروف الابتدائي في هذه الجلسة =====
        if package.is_free:
            letters = get_free_order()          # 25 حرف للمجاني (ثابتة وفق دالتك)
        else:
            letters = get_paid_order_fresh()    # 28 حرف كاملة بترتيب جديد
        set_session_order(session.id, letters, is_free=package.is_free)

        # ===== إنشاء تقدّم اللعبة (حالة الخلايا + الحروف المستخدمة) =====
        LettersGameProgress.objects.create(
            session=session,
            cell_states={},
            used_letters=[],
        )

        # ===== تسجيل نشاط (اختياري) =====
        if request.user.is_authenticated:
            try:
                from accounts.models import UserActivity
                UserActivity.objects.create(
                    user=request.user,
                    activity_type='game_created',
                    description=f'إنشاء جلسة خلية الحروف - {package.get_game_type_display()} ({"مجانية" if package.is_free else "مدفوعة"})',
                    game_type='letters',
                    session_id=str(session.id)
                )
            except Exception:
                pass

        # ===== رسالة نجاح وتوجيه =====
        if package.is_free:
            messages.success(request, '🎉 تم إنشاء جلستك المجانية بنجاح! ⏰ صالحة لمدة ساعة واحدة.')
        else:
            messages.success(request, 'تم إنشاء الجلسة المدفوعة بنجاح! استمتع باللعب 🎉')

        logger.info(f'New letters session created: {session.id} by {(request.user.username if request.user.is_authenticated else "anon")} ({"FREE" if package.is_free else "PAID"})')
        return redirect('games:letters_session', session_id=session.id)

    except Exception as e:
        logger.error(f'Error creating letters session: {e}')
        messages.error(request, 'حدث خطأ أثناء إنشاء الجلسة، يرجى المحاولة مرة أخرى')
        return redirect('games:letters_home')

    finally:
        # فك القفل دائمًا
        try:
            if lock_key:
                cache.delete(lock_key)
        except Exception:
            pass


def letters_session(request, session_id):
    session = get_object_or_404(GameSession, id=session_id)

    if is_session_expired(session):
        messages.error(request, f'⏰ {_expired_text(session)}')
        return redirect('games:letters_home')

    # اقرأ ترتيب الحروف من المصدر الموحّد (مخزون في الكاش/DB عبر utils_letters)
    arabic_letters = get_session_order(session.id, session.package.is_free) or []
    if not arabic_letters:
        arabic_letters = get_letters_for_session(session)  # احتياط

    # تنظيم الأسئلة
    questions = session.package.letters_questions.all().order_by('letter', 'question_type')
    questions_by_letter = {}
    for q in questions:
        questions_by_letter.setdefault(q.letter, {})[q.question_type] = q

    arabic_letters_json = json.dumps(arabic_letters)

    time_remaining = get_session_time_remaining(session)
    is_free_session = session.package.is_free
    free_session_warning = None
    if is_free_session and time_remaining:
        remaining_minutes = int(time_remaining.total_seconds() // 60)
        if remaining_minutes <= 10:
            free_session_warning = f"⚠️ باقي {remaining_minutes} دقيقة فقط على انتهاء الجلسة المجانية!"

    return render(request, 'games/letters/letters_session.html', {
        'session': session,
        'arabic_letters': arabic_letters,
        'arabic_letters_json': arabic_letters_json,
        'questions_by_letter': questions_by_letter,
        'time_remaining': time_remaining,
        'is_free_session': is_free_session,
        'free_session_warning': free_session_warning,
        'display_url': request.build_absolute_uri(reverse('games:letters_display', args=[session.display_link])),
        'contestants_url': request.build_absolute_uri(reverse('games:letters_contestants', args=[session.contestants_link])),
    })




def letters_display(request, display_link):
    session = get_object_or_404(GameSession, display_link=display_link, is_active=True)
    if is_session_expired(session):
        return render(request, 'games/session_expired.html', {
            'message': _expired_text(session),
            'session_type': 'مجانية' if session.package.is_free else 'مدفوعة',
            'upgrade_message': 'للاستمتاع بجلسات غير محدودة، تصفح الحزم المدفوعة!'
        })

    arabic_letters = get_session_order(session.id, session.package.is_free) or []
    if not arabic_letters:
        arabic_letters = get_letters_for_session(session)  # احتياط

    time_remaining = get_session_time_remaining(session)

    logger.info(f'Display page accessed for session: {session.id}')

    return render(request, 'games/letters/letters_display.html', {
        'session': session,
        'arabic_letters': arabic_letters,
        'time_remaining': time_remaining,
        'is_free_session': session.package.is_free,
    })



def letters_contestants(request, contestants_link):
    session = get_object_or_404(GameSession, contestants_link=contestants_link, is_active=True)

    if is_session_expired(session):
        return render(request, 'games/session_expired.html', {
            'message': _expired_text(session),
            'session_type': 'مجانية' if session.package.is_free else 'مدفوعة',
            'upgrade_message': 'للاستمتاع بجلسات غير محدودة، تصفح الحزم المدفوعة!'
        })

    time_remaining = get_session_time_remaining(session)
    logger.info(f'Contestants page accessed for session: {session.id}')

    return render(request, 'games/letters/letters_contestants.html', {
        'session': session,
        'time_remaining': time_remaining,
        'is_free_session': session.package.is_free,
    })


# ===============================
# باقي الألعاب (الواجهات)
# ===============================

def images_game_home(request):
    free_package = GamePackage.objects.filter(
        game_type='images',
        is_free=True,
        is_active=True
    ).first()

    paid_packages = GamePackage.objects.filter(
        game_type='images',
        is_free=False,
        is_active=True
    ).order_by('package_number')

    user_purchases = []
    free_session_eligible = True
    free_session_message = ""
    user_free_sessions_count = 0

    if request.user.is_authenticated:
        user_purchases = UserPurchase.objects.filter(
            user=request.user,
            package__game_type='images'
        ).values_list('package_id', flat=True)

        free_session_eligible, free_session_message, user_free_sessions_count = check_free_session_eligibility(
            request.user, 'images'
        )

    return render(request, 'games/images/home.html', {
        'free_package': free_package,
        'paid_packages': paid_packages,
        'user_purchases': user_purchases,
        'free_session_eligible': free_session_eligible,
        'free_session_message': free_session_message,
        'user_free_sessions_count': user_free_sessions_count,
    })

def quiz_game_home(request):
    free_package = GamePackage.objects.filter(
        game_type='quiz',
        is_free=True,
        is_active=True
    ).first()

    paid_packages = GamePackage.objects.filter(
        game_type='quiz',
        is_free=False,
        is_active=True
    ).order_by('package_number')

    user_purchases = []
    free_session_eligible = True
    free_session_message = ""
    user_free_sessions_count = 0

    if request.user.is_authenticated:
        user_purchases = UserPurchase.objects.filter(
            user=request.user,
            package__game_type='quiz'
        ).values_list('package_id', flat=True)

        free_session_eligible, free_session_message, user_free_sessions_count = check_free_session_eligibility(
            request.user, 'quiz'
        )

    return render(request, 'games/quiz/home.html', {
        'free_package': free_package,
        'paid_packages': paid_packages,
        'user_purchases': user_purchases,
        'free_session_eligible': free_session_eligible,
        'free_session_message': free_session_message,
        'user_free_sessions_count': user_free_sessions_count,
    })

# ===============================
# APIs
# ===============================

@require_http_methods(["GET"])
def api_check_free_session_eligibility(request):
    # ✅ تغيّر: غير مسجّل → غير مؤهل، ويُلزم تسجيل الدخول
    if not request.user.is_authenticated:
        return JsonResponse({
            'success': True,
            'eligible': False,
            'message': 'يرجى تسجيل الدخول للاستفادة من الجلسة المجانية',
            'sessions_count': 0
        })

    game_type = request.GET.get('game_type')
    if not game_type:
        return JsonResponse({'success': False, 'error': 'نوع اللعبة مطلوب'}, status=400)

    try:
        eligible, message, sessions_count = check_free_session_eligibility(request.user, game_type)
        return JsonResponse({
            'success': True,
            'eligible': eligible,
            'message': message or ('مؤهل لجلسة مجانية' if eligible else 'لقد استخدمت الجلسة المجانية الخاصة بك.'),
            'sessions_count': sessions_count,
            'user_id': request.user.id,
            'username': request.user.username,
            'game_type': game_type
        })
    except Exception as e:
        logger.error(f'Error checking free session eligibility: {e}')
        return JsonResponse({'success': False, 'error': 'خطأ في التحقق من الأهلية'}, status=500)


@require_http_methods(["GET"])
def get_question(request):
    letter = request.GET.get('letter')
    session_id = request.GET.get('session_id')

    if not letter or not session_id:
        return JsonResponse({'success': False, 'error': 'المعاملات مطلوبة'}, status=400)

    try:
        session = GameSession.objects.get(id=session_id, is_active=True)

        if is_session_expired(session):
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True
            }, status=410)

        # تأكد أن الحرف ضمن ترتيب الجلسة الفعلي (بعد أي جولة جديدة)
        letters = get_session_order(session.id, session.package.is_free) or get_letters_for_session(session)
        if letter not in letters:
            return JsonResponse({'success': False, 'error': f'الحرف {letter} غير متاح في هذه الجلسة'}, status=400)

        is_free_pkg = session.package.is_free
        question_types = ['main', 'alt1', 'alt2'] if is_free_pkg else ['main', 'alt1', 'alt2', 'alt3', 'alt4']

        questions = {}
        for qtype in question_types:
            try:
                q = LettersGameQuestion.objects.get(
                    package=session.package,
                    letter=letter,
                    question_type=qtype
                )
                questions[qtype] = {'question': q.question, 'answer': q.answer, 'category': q.category}
            except LettersGameQuestion.DoesNotExist:
                questions[qtype] = {'question': f'لا يوجد سؤال {qtype} للحرف {letter}', 'answer': 'غير متاح', 'category': 'غير محدد'}

        return JsonResponse({
            'success': True,
            'questions': questions,
            'letter': letter,
            'session_info': {
                'team1_name': session.team1_name,
                'team2_name': session.team2_name,
                'package_name': f"{session.package.get_game_type_display()} - حزمة {session.package.package_number}",
                'is_free_package': is_free_pkg
            }
        })

    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة أو غير نشطة'}, status=404)
    except Exception as e:
        logger.error(f'Error fetching question: {e}')
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)


@require_http_methods(["GET"])
def get_session_letters(request):
    session_id = request.GET.get('session_id')
    if not session_id:
        return JsonResponse({'success': False, 'error': 'معرف الجلسة مطلوب'}, status=400)

    try:
        session = GameSession.objects.get(id=session_id, is_active=True)
        if is_session_expired(session):
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True,
                'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
            }, status=410)

        # اقرأ الترتيب الحالي المخزّن (يتحدث بعد كل جولة جديدة)
        letters = get_session_order(session.id, session.package.is_free) or []
        if not letters:
            letters = get_letters_for_session(session)  # احتياط

        return JsonResponse({
            'success': True,
            'letters': letters,
            'session_info': {
                'is_free_package': session.package.is_free,
                'package_number': session.package.package_number,
                'total_letters': len(letters)
            }
        })

    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة أو غير نشطة'}, status=404)
    except Exception as e:
        logger.error(f'Error fetching session letters: {e}')
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def update_cell_state(request):
    """
    تحديث حالة خلية (team1 / team2 / normal) وتخزينها في LettersGameProgress
    + التحقق من صلاحية الجلسة والحرف
    + بثّ التغيير لكل العملاء عبر WebSocket
    """
    # --- قراءة JSON بأمان ---
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'بيانات JSON غير صحيحة'}, status=400)

    session_id = data.get('session_id')
    letter = data.get('letter')
    state = data.get('state')

    # --- تحقق من المعاملات ---
    if not session_id or not letter or state is None:
        return JsonResponse({'success': False, 'error': 'جميع المعاملات مطلوبة'}, status=400)

    state = str(state)
    if state not in ('normal', 'team1', 'team2'):
        return JsonResponse({'success': False, 'error': 'حالة الخلية غير صحيحة'}, status=400)

    # --- الجلسة ---
    try:
        session = GameSession.objects.get(id=session_id, is_active=True)
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة أو غير نشطة'}, status=404)

    if is_session_expired(session):
        return JsonResponse({
            'success': False,
            'error': 'انتهت صلاحية الجلسة',
            'session_expired': True,
            'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
        }, status=410)

    # --- تحقق من الحرف ضمن حروف هذه الجلسة ---
    letters = get_letters_for_session(session)
    if letter not in letters:
        return JsonResponse({'success': False, 'error': f'الحرف {letter} غير متاح في هذه الجلسة'}, status=400)

    # --- حفظ حالة الخلية + تتبّع الحروف المستخدمة ---
    try:
        progress, _ = LettersGameProgress.objects.get_or_create(
            session=session,
            defaults={'cell_states': {}, 'used_letters': []}
        )

        if not isinstance(progress.cell_states, dict):
            progress.cell_states = {}
        progress.cell_states[letter] = state

        if not isinstance(progress.used_letters, list):
            progress.used_letters = []
        if letter not in progress.used_letters:
            progress.used_letters.append(letter)

        progress.save(update_fields=['cell_states', 'used_letters'])

        # --- بثّ التغيير عبر WS (سيحوّله الكونسومر إلى payload type: 'cell_state_updated') ---
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"letters_session_{session_id}",
                    {
                        "type": "broadcast_cell_state",  # اسم الميثود داخل الكونسومر
                        "letter": letter,
                        "state": state,
                    }
                )
        except Exception as e:
            logger.error(f'WS broadcast error (cell_state): {e}')

        logger.info(f'Cell state updated: {letter} -> {state} in session {session_id}')
        return JsonResponse({'success': True, 'message': 'تم تحديث حالة الخلية', 'letter': letter, 'state': state})

    except Exception as e:
        logger.error(f'Error updating cell state: {e}')
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def update_scores(request):
    """تحديث نقاط الفريقين + بثّ التحديث عبر WS"""
    try:
        data = json.loads(request.body or "{}")
        session_id = data.get('session_id')
        team1_score = data.get('team1_score', 0)
        team2_score = data.get('team2_score', 0)

        if not session_id:
            return JsonResponse({'success': False, 'error': 'معرف الجلسة مطلوب'}, status=400)

        try:
            team1_score = max(0, int(team1_score))
            team2_score = max(0, int(team2_score))
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'قيم النقاط يجب أن تكون أرقام صحيحة'}, status=400)

        session = GameSession.objects.get(id=session_id, is_active=True)
        if is_session_expired(session):
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True,
                'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
            }, status=410)

        session.team1_score = team1_score
        session.team2_score = team2_score

        winning_score = 10
        if session.team1_score >= winning_score and session.team1_score > session.team2_score:
            session.winner_team = 'team1'
            session.is_completed = True
        elif session.team2_score >= winning_score and session.team2_score > session.team1_score:
            session.winner_team = 'team2'
            session.is_completed = True

        session.save(update_fields=['team1_score', 'team2_score', 'winner_team', 'is_completed'])

        # بثّ فوري لكل العملاء
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"letters_session_{session_id}",
                    {
                        "type": "broadcast_scores",   # الكونسومر يرسلها للمتصفح كـ type: 'scores_updated'
                        "team1_score": session.team1_score,
                        "team2_score": session.team2_score,
                        "winner": session.winner_team,
                        "is_completed": session.is_completed,
                    }
                )
        except Exception as e:
            logger.error(f'WS broadcast error (scores): {e}')

        logger.info(f'Scores updated in session {session_id}: Team1={team1_score}, Team2={team2_score}')
        return JsonResponse({
            'success': True,
            'message': 'تم تحديث النقاط',
            'team1_score': session.team1_score,
            'team2_score': session.team2_score,
            'winner': session.winner_team,
            'is_completed': session.is_completed
        })

    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة أو غير نشطة'}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'بيانات JSON غير صحيحة'}, status=400)
    except Exception as e:
        logger.error(f'Error updating scores: {e}')
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)


def session_state(request):
    sid = request.GET.get("session_id")
    if not sid:
        return HttpResponseBadRequest("missing session_id")

    session = get_object_or_404(GameSession, id=sid)
    if is_session_expired(session):
        return JsonResponse({"detail": "expired"}, status=410)

    # حالة الخلايا
    progress = LettersGameProgress.objects.filter(session=session).only("cell_states").first()
    cell_states = progress.cell_states if (progress and isinstance(progress.cell_states, dict)) else {}

    # الوقت المتبقي للمجاني
    time_remaining_seconds = None
    if session.package.is_free:
        end_at = session.created_at + timedelta(hours=1)
        left = int((end_at - timezone.now()).total_seconds())
        time_remaining_seconds = max(0, left)

    # ترتيب الحروف من المصدر الموحّد
    letters = get_session_order(session.id, session.package.is_free) or []

    return JsonResponse({
        "team1_score": session.team1_score,
        "team2_score": session.team2_score,
        "cell_states": cell_states,
        "time_remaining_seconds": time_remaining_seconds,
        "arabic_letters": letters,
    })


@csrf_exempt
@require_http_methods(["POST"])
def add_contestant(request):
    try:
        data = json.loads(request.body or "{}")
        session_id = data.get('session_id')
        name = (data.get('name') or '').strip()
        team = data.get('team')

        if not all([session_id, name, team]):
            return JsonResponse({'success': False, 'error': 'جميع المعاملات مطلوبة'}, status=400)

        if team not in ['team1', 'team2']:
            return JsonResponse({'success': False, 'error': 'الفريق يجب أن يكون team1 أو team2'}, status=400)

        if len(name) > 50:
            return JsonResponse({'success': False, 'error': 'اسم المتسابق طويل جداً'}, status=400)

        session = GameSession.objects.get(id=session_id, is_active=True)
        if is_session_expired(session):
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True,
                'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
            }, status=410)

        existing = Contestant.objects.filter(session=session, name=name).first()
        if existing:
            if existing.team != team:
                existing.team = team
                existing.save(update_fields=['team'])
        else:
            Contestant.objects.create(session=session, name=name, team=team)

        logger.info(f'New contestant ensured: {name} -> {team} in session {session_id}')
        return JsonResponse({
            'success': True,
            'message': 'تم إضافة المتسابق بنجاح',
            'contestant': {'name': name, 'team': team}
        })

    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة أو غير نشطة'}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'بيانات JSON غير صحيحة'}, status=400)
    except Exception as e:
        logger.error(f'Error adding contestant: {e}')
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)

# -------------------------------
# إحصائيات المستخدم (نسخة واحدة)
# -------------------------------
@login_required
@require_http_methods(["GET"])
def api_user_session_stats(request):
    try:
        user = request.user
        stats = {}

        for game_type, game_name in [('letters', 'خلية الحروف'), ('images', 'تحدي الصور'), ('quiz', 'سؤال وجواب')]:
            free_sessions = GameSession.objects.filter(host=user, game_type=game_type, package__is_free=True)
            paid_sessions = GameSession.objects.filter(host=user, game_type=game_type, package__is_free=False)
            eligible, message, used_count = check_free_session_eligibility(user, game_type)

            stats[game_type] = {
                'game_name': game_name,
                'free_sessions': {
                    'used': used_count,
                    'allowed': 1,
                    'eligible_for_new': eligible,
                    'latest_session': free_sessions.order_by('-created_at').first().created_at.isoformat() if free_sessions.exists() else None
                },
                'paid_sessions': {
                    'total': paid_sessions.count(),
                    'active': paid_sessions.filter(is_active=True).count(),
                    'completed': paid_sessions.filter(is_completed=True).count()
                },
                'purchased_packages': UserPurchase.objects.filter(user=user, package__game_type=game_type).count()
            }

        return JsonResponse({
            'success': True,
            'user_id': user.id,
            'username': user.username,
            'stats': stats,
            'summary': {
                'total_free_sessions_used': sum([stats[gt]['free_sessions']['used'] for gt in stats]),
                'total_paid_sessions': sum([stats[gt]['paid_sessions']['total'] for gt in stats]),
                'total_packages_purchased': sum([stats[gt]['purchased_packages'] for gt in stats]),
            }
        })
    except Exception as e:
        logger.error(f'User session stats API error: {e}')
        return JsonResponse({'success': False, 'error': 'حدث خطأ في جلب الإحصائيات'}, status=500)

# -------------------------------
# معلومات صلاحية الجلسة
# -------------------------------
@require_http_methods(["GET"])
def api_session_expiry_info(request):
    session_id = request.GET.get('session_id')
    if not session_id:
        return JsonResponse({'success': False, 'error': 'معرف الجلسة مطلوب'}, status=400)
    try:
        session = GameSession.objects.get(id=session_id)
        expiry_info = get_session_expiry_info(session)
        return JsonResponse({
            'success': True,
            'session_id': str(session.id),
            'expiry_info': {
                'is_free': expiry_info['is_free'],
                'session_type': expiry_info['session_type'],
                'duration_text': expiry_info['duration_text'],
                'duration_hours': expiry_info['duration_hours'],
                'expiry_time': expiry_info['expiry_time'].isoformat(),
                'time_remaining_seconds': int(expiry_info['time_remaining'].total_seconds()),
                'is_expired': expiry_info['is_expired'],
                'remaining_percentage': expiry_info['remaining_percentage'],
                'warning_message': expiry_info['warning_message'],
                'warning_level': expiry_info['warning_level'],
                'created_at': expiry_info['created_at'].isoformat(),
            },
            'package_info': {
                'name': f"{session.package.get_game_type_display()} - حزمة {session.package.package_number}",
                'is_free': session.package.is_free,
                'price': str(session.package.price)
            }
        })
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة'}, status=404)
    except Exception as e:
        logger.error(f'Session expiry info API error: {e}')
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)

# -------------------------------
# زر الطنطيط عبر HTTP (3 ثوانٍ)
# -------------------------------
@csrf_exempt
@require_http_methods(["POST"])
def api_contestant_buzz_http(request):
    """
    زر الطنطيط عبر HTTP بقفل ذرّي (3 ثواني).
    يقبل أول محاولة فقط خلال مدة القفل، والبقية تُرفض برسالة 'محجوز'.
    """
    try:
        data = json.loads(request.body or "{}")
        session_id = data.get('session_id')
        contestant_name = (data.get('contestant_name') or '').strip()
        team = data.get('team')
        timestamp = data.get('timestamp')

        if not all([session_id, contestant_name, team]):
            return JsonResponse({'success': False, 'error': 'جميع المعاملات مطلوبة'}, status=400)

        try:
            session = GameSession.objects.get(id=session_id, is_active=True)
        except GameSession.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة'}, status=404)

        # انتهاء الصلاحية
        if is_session_expired(session):
            return JsonResponse({'success': False, 'error': 'انتهت صلاحية الجلسة', 'session_expired': True}, status=410)

        buzz_lock_key = f"buzz_lock_{session_id}"
        lock_payload = {
            'name': contestant_name,
            'team': team,
            'timestamp': timestamp,
            'session_id': session_id,
            'method': 'HTTP',
        }

        # قفل ذرّي: True إذا تم الإنشاء، False إذا كان موجود
        try:
            added = cache.add(buzz_lock_key, lock_payload, timeout=3)
        except Exception:
            added = False

        if not added:
            current_buzzer = cache.get(buzz_lock_key) or {}
            return JsonResponse({
                'success': False,
                'message': f'الزر محجوز من {current_buzzer.get("name","مشارك")}',
                'locked_by': current_buzzer.get('name'),
                'locked_team': current_buzzer.get('team')
            })

        # ثبّت المتسابق/الفريق في قاعدة البيانات
        contestant, created = Contestant.objects.get_or_create(
            session=session,
            name=contestant_name,
            defaults={'team': team}
        )
        if not created and contestant.team != team:
            contestant.team = team
            contestant.save(update_fields=['team'])

        # بث موحّد إلى المجموعة (يتوافق مع consumer)
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            channel_layer = get_channel_layer()
            if channel_layer:
                group_name = f"letters_session_{session_id}"
                team_display = session.team1_name if team == 'team1' else session.team2_name
                async_to_sync(channel_layer.group_send)(group_name, {
                    'type': 'broadcast_buzz_event',
                    'contestant_name': contestant_name,
                    'team': team,
                    'team_display': team_display,
                    'timestamp': timestamp,
                    'action': 'buzz_accepted',
                })
        except Exception as e:
            logger.error(f"Error sending HTTP buzz to WebSocket: {e}")

        logger.info(f"HTTP Buzz accepted (atomic): {contestant_name} from {team} in session {session_id}")
        return JsonResponse({
            'success': True,
            'message': f'تم تسجيل إجابتك يا {contestant_name}!',
            'contestant_name': contestant_name,
            'team': team,
            'method': 'HTTP'
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'بيانات JSON غير صحيحة'}, status=400)
    except Exception as e:
        logger.error(f'HTTP Buzz error: {e}')
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)

# games/.py



@csrf_exempt
@require_http_methods(["POST"])
def letters_new_round(request):
    """
    بدء جولة جديدة للحزم المدفوعة فقط، بدون أي توكن.
    - أي شخص يملك رابط المقدم يقدر يشغّلها.
    - إذا الجلسة مجانية → 403
    - إذا منتهية الصلاحية → 410
    - تبث التغيير عبر WebSocket وتفرّغ تقدم الخلايا.
    """
    # قراءة JSON
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'بيانات JSON غير صحيحة'}, status=400)

    sid = payload.get("session_id")
    if not sid:
        return JsonResponse({'success': False, 'error': 'معرف الجلسة مطلوب'}, status=400)

    # الجلسة
    session = get_object_or_404(GameSession, id=sid, is_active=True)

    # انتهاء الصلاحية
    if is_session_expired(session):
        return JsonResponse({'success': False, 'error': 'انتهت صلاحية الجلسة', 'session_expired': True}, status=410)

    # منع المجاني
    if session.package.is_free:
        return JsonResponse({'success': False, 'error': 'الميزة متاحة للحزم المدفوعة فقط'}, status=403)

    # توليد ترتيب جديد للحروف وتثبيته
    new_letters = get_paid_order_fresh()
    set_session_order(session.id, new_letters, is_free=False)

    # تصفير تقدم الخلايا/الحروف المستخدمة (اختياري لكنه أنظف)
    try:
        progress = LettersGameProgress.objects.filter(session=session).first()
        if progress:
            progress.cell_states = {}
            progress.used_letters = []
            progress.save(update_fields=['cell_states', 'used_letters'])
    except Exception:
        pass

    # بث التغيير لكل العملاء
    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f"letters_session_{session.id}",
                {"type": "broadcast_letters_replace", "letters": new_letters, "reset_progress": True}
            )
    except Exception as e:
        logger.error(f"WS broadcast error (new round): {e}")

    return JsonResponse({'success': True, 'letters': new_letters, 'reset_progress': True})
