# games/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponseBadRequest
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils import timezone
from games.models import UserPurchase, GameSession  # ← إضافة UserPurchase (و GameSession إن احتجته بالأسفل)
from . import views_imposter

from django.db import transaction, IntegrityError
from django.db.models import Q
from django.core.cache import cache
from .models import PictureRiddle, PictureGameProgress
from django.apps import apps
from datetime import timedelta
import json
import logging
import secrets
from django.http import HttpResponse
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import (
    GamePackage, GameSession, UserPurchase, LettersGameProgress,
    LettersGameQuestion, Contestant, FreeTrialUsage
)

logger = logging.getLogger('games')

# ===============================
# Helpers: انتهاء الجلسة/الوقت
# ===============================
def _expired_text(session):
    return 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)' if session.package.is_free else 'انتهت صلاحية الجلسة (72 ساعة)'



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

from games.utils_letters import (
    get_session_order, set_session_order,
    get_paid_order_fresh, get_free_order
)

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
# Helpers: أهلية الجلسات المجانية (مُصحّح)
# ===============================

def check_free_session_eligibility(user, game_type):
    """
    جلسة مجانية واحدة لكل مستخدم/نوع لعبة.
    نعتمد على FreeTrialUsage (قيد فريد user+game_type).
    """
    if not user or not user.is_authenticated:
        return False, "يرجى تسجيل الدخول للاستفادة من الجلسة المجانية", 0

    used = FreeTrialUsage.objects.filter(user=user, game_type=game_type).exists()
    if used:
        return False, "لقد استخدمت الجلسة المجانية الخاصة بك.", 1
    return True, "", 0

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

from datetime import timedelta
from django.utils import timezone
from django.db.models import Q
from django.shortcuts import render


from functools import lru_cache
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

@lru_cache(maxsize=None)
def _pick_model(app_label: str, candidates: tuple[str, ...]):
    """
    يرجّع أول موديل موجود من بين الأسماء المرشّحة.
    يرفع رسالة واضحة لو ما لقى أي اسم.
    """
    for name in candidates:
        try:
            m = apps.get_model(app_label, name)
            if m is not None:
                return m
        except LookupError:
            continue

    # أسماء الموديلات المتاحة في هذا التطبيق (للتشخيص)
    available = [m.__name__ for m in apps.get_app_config(app_label).get_models()]
    raise ImproperlyConfigured(
        f"لم يتم العثور على أي من الأسماء {candidates} داخل '{app_label}'. "
        f"الموديلات المتاحة: {available}"
    )

# ✅ عرّف الموديلات التي تحتاجها هنا بأسماء مرشّحة (حط البدائل المحتملة)
LettersPackage = _pick_model("games", ("LettersPackage", "LetterPackage", "LettersPkg"))
LettersSession = _pick_model("games", ("LettersSession", "LetterSession", "LettersGameSession"))

def _get_model(app_label: str, model_name_candidates):
    """
    نحاول جلب الموديل بالأسماء المحتملة.
    يعيد الكلاس أو يرفع ValueError برسالة واضحة فيها الأسماء المتاحة.
    """
    if isinstance(model_name_candidates, str):
        model_name_candidates = [model_name_candidates]

    for name in model_name_candidates:
        try:
            m = apps.get_model(app_label, name)
            if m is not None:
                return m
        except Exception:
            pass

    # لو ما وجدناه، نطبع قائمة الموديلات المتاحة لمساعدة التصحيح
    all_models = sorted([m.__name__ for m in apps.get_app_config(app_label).get_models()])
    raise ValueError(
        f"لم أستطع العثور على أي من الأسماء {model_name_candidates} داخل تطبيق '{app_label}'. "
        f"الأسماء المتاحة في هذا التطبيق: {all_models}"
    )


    
def letters_game_home(request):
    """
    صفحة اختيار الحزم:
    - زر ابدأ اللعب يظهر فقط للحزم ذات شراء فعال (داخل 72 ساعة).
    - شارة "سبق لك شراء هذه الحزمة" تظهر للحزم التي اشتراها المستخدم مسبقًا لكن انتهت صلاحيتها.
    - زر شراء متاح دائمًا إلا إذا كانت الحزمة مفعلة حالياً.
    """

    now = timezone.now()

    # =========================
    # الحزمة المجانية
    # =========================
    free_package = (
        LettersPackage.objects.filter(
            is_free=True,
            is_active=True,
            game_type='letters'
        )
        .order_by('-id')
        .first()
    )

    free_active_session = None
    free_session_eligible = False
    free_session_message = ""

    if request.user.is_authenticated and free_package:

        # آخر جلسة مجانية للمستخدم
        last_free_session = (
            LettersSession.objects.filter(
                package=free_package,
                host=request.user
            )
            .order_by('-created_at')
            .first()
        )

        # جلسة مجانية نشطة (غير منتهية)
        if last_free_session and not last_free_session.is_time_expired:
            free_active_session = last_free_session
            free_session_eligible = False
            free_session_message = "لديك جلسة مجانية سارية."
        else:
            # انتهت أو لا توجد جلسة → نتحقق هل سبق استخدامها
            if last_free_session:
                free_session_eligible = False
                free_session_message = "لقد استخدمت الجلسة المجانية الخاصة بك."
            else:
                free_session_eligible = True

    # =========================
    # الحزم المدفوعة
    # =========================
    paid_qs = LettersPackage.objects.filter(
        is_active=True,
        is_free=False,
        game_type='letters'
    )

    paid_packages_mixed = paid_qs.filter(
        question_theme='mixed'
    ).order_by('package_number')

    paid_packages_sports = paid_qs.filter(
        question_theme='sports'
    ).order_by('package_number')

    # =========================
    # منطق الشراء المصحّح
    # =========================
    active_packages_ids = set()       # شراء مكتمل وصالح → "ابدأ اللعب"
    completed_packages_ids = set()    # شراء مكتمل وانتهى → "سبق شراء"
    expired_packages_ids = set()
    used_before_ids = set()

    if request.user.is_authenticated:
        purchases = UserPurchase.objects.filter(
            user=request.user,
            package__game_type='letters'
        )

        for p in purchases:
            used_before_ids.add(p.package_id)

            # شراء غير مكتمل → تجاهل
            if not p.is_completed:
                continue

            # شراء مكتمل وصالح
            if p.expires_at and p.expires_at > now:
                active_packages_ids.add(p.package_id)
                continue

            # شراء مكتمل وانتهت صلاحيته
            completed_packages_ids.add(p.package_id)
            expired_packages_ids.add(p.package_id)

    # =========================
    # تمرير البيانات للقالب
    # =========================
    context = {
        "free_package": free_package,
        "free_active_session": free_active_session,
        "free_session_eligible": free_session_eligible,
        "free_session_message": free_session_message,

        "paid_packages_mixed": paid_packages_mixed,
        "paid_packages_sports": paid_packages_sports,

        "active_packages_ids": active_packages_ids,
        "completed_packages_ids": completed_packages_ids,
        "expired_packages_ids": expired_packages_ids,
        "used_before_ids": used_before_ids,
    }

    return render(request, "games/letters/packages.html", context)



from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.urls import reverse
import random
from django.views.decorators.csrf import csrf_exempt

from games.models import (
    GamePackage,
    GameSession,
    UserPurchase,
    LettersGameQuestion,   # ✅ الموديل الصحيح
)
@csrf_exempt
@login_required
@require_POST
def create_letters_session(request):
    package_id = request.POST.get("package_id")

    if not package_id:
        return JsonResponse({"success": False, "error": "package_id مفقود"}, status=400)

    package = get_object_or_404(
        GamePackage,
        id=package_id,
        game_type="letters",
        is_active=True
    )

    # ===== التحقق من الشراء (نفس منطقك) =====
    if not package.is_free:
        purchase = UserPurchase.objects.filter(
            user=request.user,
            package=package,
            is_completed=True,          # ✔️ شراء مكتمل
            expires_at__gt=timezone.now()
        ).first()


        if not purchase:
            return JsonResponse({
                "success": False,
                "error": "لا تملك هذه الحزمة"
            }, status=403)
    else:
        purchase = None

    # ===== جلب الأسئلة =====
    questions_qs = LettersGameQuestion.objects.filter(
        package=package
    )

    if not questions_qs.exists():
        return JsonResponse({
            "success": False,
            "error": "لا توجد أسئلة لهذه الحزمة"
        }, status=400)

    questions = list(questions_qs)
    random.shuffle(questions)

    # ===== إنشاء الجلسة (نفس نظامك) =====
    session = GameSession.objects.create(
        host=request.user,
        package=package,
        purchase=purchase,
        game_type="letters",
        is_active=True
    )

    # ===== تخزين الجلسة =====
    request.session[f"letters_{session.id}"] = {
        "questions": [q.id for q in questions],
        "current_index": 0,
    }
    request.session.modified = True

    return JsonResponse({
        "success": True,
        "session_url": reverse("games:letters_session", args=[session.id])
    })

        

def letters_session(request, session_id):
    session = get_object_or_404(GameSession, id=session_id)

    if session.is_time_expired:
        messages.error(request, f'⏰ {_expired_text(session)}')
        return redirect('games:letters_home')

    # اقرأ ترتيب الحروف من المصدر الموحّد
    arabic_letters = get_session_order(session.id, session.package.is_free) or []
    if not arabic_letters:
        arabic_letters = get_letters_for_session(session)

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
    if session.is_time_expired:
        return render(request, 'games/session_expired.html', {
            'message': _expired_text(session),
            'session_type': 'مجانية' if session.package.is_free else 'مدفوعة',
            'upgrade_message': 'للاستمتاع بجلسات غير محدودة، تصفح الحزم المدفوعة!'
        })

    arabic_letters = get_session_order(session.id, session.package.is_free) or []
    if not arabic_letters:
        arabic_letters = get_letters_for_session(session)

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

    if session.is_time_expired:
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
    now = timezone.now()

    # الحزمة المجانية
    free_package = GamePackage.objects.filter(
        game_type='images', is_free=True, is_active=True
    ).first()

    # الحزم المدفوعة
    paid_packages = GamePackage.objects.filter(
        game_type='images', is_free=False, is_active=True
    ).order_by('package_number')

    # ============================
    #   منطق الشراء الجديد (المصحّح)
    # ============================
    active_packages_ids = set()        # شراء مكتمل وصالح → "ابدأ اللعب"
    expired_packages_ids = set()       # شراء مكتمل وانتهى وقته → "سبق شراء"
    used_before_ids = set()            # كل الحزم التي سبق شراؤها (للزينة فقط)
    
    free_session_eligible = False
    free_session_message = ""
    free_active_session = None

    if request.user.is_authenticated:

        # تحقق من أهلية الجولة المجانية
        free_session_eligible, free_session_message, _ = check_free_session_eligibility(
            request.user, 'images'
        )

        # جلسة مجانية نشطة
        if free_package:
            candidate = GameSession.objects.filter(
                host=request.user,
                package=free_package,
                is_active=True,
                game_type='images'
            ).order_by('-created_at').first()

            if candidate and not candidate.is_time_expired:
                free_active_session = candidate

        # مشتريات المستخدم
        purchases = UserPurchase.objects.filter(
            user=request.user,
            package__game_type='images'
        )

        for p in purchases:
            used_before_ids.add(p.package_id)

            # ---- تجاهل المشتريات غير المكتملة (لا تظهر كسبق شراء) ----
            if not p.is_completed:
                continue

            # ---- شراء مكتمل وصالح ----
            if p.expires_at and p.expires_at > now:
                active_packages_ids.add(p.package_id)
                continue

            # ---- شراء مكتمل وانتهت صلاحيته ----
            expired_packages_ids.add(p.package_id)

    context = {
        'page_title': 'وش الجواب - تحدي الصور',
        'free_package': free_package,
        'paid_packages': paid_packages,

        # القوائم المهمة
        'active_packages_ids': active_packages_ids,
        'expired_packages_ids': expired_packages_ids,
        'used_before_ids': used_before_ids,

        # المجانية
        'free_session_eligible': free_session_eligible,
        'free_session_message': free_session_message,
        'free_active_session': free_active_session,
    }

    return render(request, 'games/images/packages.html', context)


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
    # غير مسجّل → غير مؤهل، ويُلزم تسجيل الدخول
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

    # تطبيع الهاء: نقبل ه/هـ
    def _variants(ltr: str):
        return ['ه', 'هـ'] if ltr in ('ه', 'هـ') else [ltr]

    try:
        session = GameSession.objects.get(id=session_id, is_active=True)

        if session.is_time_expired:
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True
            }, status=410)

        # تأكد أن الحرف ضمن ترتيب الجلسة (مع قبول ه/هـ)
        letters = get_session_order(session.id, session.package.is_free) or get_letters_for_session(session)

        # حدّد الشكل المعتمد داخل ترتيب الجلسة (إن وُجد)
        chosen_in_session = None
        for v in _variants(letter):
            if v in letters:
                chosen_in_session = v
                break
        if not chosen_in_session:
            return JsonResponse({'success': False, 'error': f'الحرف {letter} غير متاح في هذه الجلسة'}, status=400)

        is_free_pkg = session.package.is_free
        question_types = ['main', 'alt1', 'alt2'] if is_free_pkg else ['main', 'alt1', 'alt2', 'alt3', 'alt4']

        # جلب السؤال مع محاولة fallback على الشكل الآخر للهاء
        def _get_q(qtype):
            from django.core.exceptions import ObjectDoesNotExist
            try:
                q = LettersGameQuestion.objects.get(
                    package=session.package,
                    letter=chosen_in_session,
                    question_type=qtype
                )
                return {'question': q.question, 'answer': q.answer, 'category': q.category}
            except ObjectDoesNotExist:
                # جرّب الشكل الآخر إن كان الحرف ه/هـ
                for alt in _variants('هـ' if chosen_in_session == 'ه' else 'ه'):
                    if alt == chosen_in_session:
                        continue
                    try:
                        q = LettersGameQuestion.objects.get(
                            package=session.package,
                            letter=alt,
                            question_type=qtype
                        )
                        return {'question': q.question, 'answer': q.answer, 'category': q.category}
                    except ObjectDoesNotExist:
                        pass
                # ما وجدنا شيء
                return {'question': f'لا يوجد سؤال {qtype} للحرف {chosen_in_session}', 'answer': 'غير متاح', 'category': 'غير محدد'}

        questions = {qt: _get_q(qt) for qt in question_types}

        return JsonResponse({
            'success': True,
            'questions': questions,
            # نُرجع الشكل المعتمد داخل الجلسة لأجل العرض/التظليل
            'letter': chosen_in_session,
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
        if session.is_time_expired:
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True,
                'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
            }, status=410)

        letters = get_session_order(session.id, session.package.is_free) or []
        if not letters:
            letters = get_letters_for_session(session)

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
    + التحقق من صلاحية الجلسة والحرف (مع قبول ه/هـ)
    + بثّ التغيير لكل العملاء عبر WebSocket
    """
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'بيانات JSON غير صحيحة'}, status=400)

    session_id = data.get('session_id')
    letter_in = (data.get('letter') or '').strip()
    state = data.get('state')

    if not session_id or not letter_in or state is None:
        return JsonResponse({'success': False, 'error': 'جميع المعاملات مطلوبة'}, status=400)

    state = str(state)
    if state not in ('normal', 'team1', 'team2'):
        return JsonResponse({'success': False, 'error': 'حالة الخلية غير صحيحة'}, status=400)

    # تطبيع الهاء
    def _variants(ltr: str):
        return ['ه', 'هـ'] if ltr in ('ه', 'هـ') else [ltr]

    try:
        session = GameSession.objects.get(id=session_id, is_active=True)
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة أو غير نشطة'}, status=404)

    if session.is_time_expired:
        return JsonResponse({
            'success': False,
            'error': 'انتهت صلاحية الجلسة',
            'session_expired': True,
            'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
        }, status=410)

    # تأكد من الحرف داخل ترتيب الجلسة بأي من الشكلين، وثبّت الشكل المعتمد
    letters = get_letters_for_session(session)
    chosen_in_session = None
    for v in _variants(letter_in):
        if v in letters:
            chosen_in_session = v
            break
    if not chosen_in_session:
        return JsonResponse({'success': False, 'error': f'الحرف {letter_in} غير متاح في هذه الجلسة'}, status=400)

    try:
        progress, _ = LettersGameProgress.objects.get_or_create(
            session=session,
            defaults={'cell_states': {}, 'used_letters': []}
        )

        if not isinstance(progress.cell_states, dict):
            progress.cell_states = {}
        # خزّن على الحرف بالشكل المعتمد داخل ترتيب الجلسة
        progress.cell_states[chosen_in_session] = state

        if not isinstance(progress.used_letters, list):
            progress.used_letters = []
        if chosen_in_session not in progress.used_letters:
            progress.used_letters.append(chosen_in_session)

        progress.save(update_fields=['cell_states', 'used_letters'])

        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"letters_session_{session_id}",
                    {
                        "type": "broadcast_cell_state",
                        "letter": chosen_in_session,
                        "state": state,
                    }
                )
        except Exception as e:
            logger.error(f'WS broadcast error (cell_state): {e}')

        logger.info(f'Cell state updated: {chosen_in_session} -> {state} in session {session_id}')
        return JsonResponse({'success': True, 'message': 'تم تحديث حالة الخلية', 'letter': chosen_in_session, 'state': state})

    except Exception as e:
        logger.error(f'Error updating cell state: {e}')
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)

# games/views.py
@csrf_exempt
@require_http_methods(["POST"])
def update_scores(request):
    """
    تحديث نقاط الفريقين + بثّ التحديث عبر WS (متوافق مع Letters/Pictures)
    - قفل صف الجلسة بـ select_for_update لمنع سباقات الكتابة
    - إعادة ضبط winner_team/is_completed عند عدم تحقق شرط الفوز
    - البث باسم موحّد: broadcast_score_update
    """
    # 1) قراءة JSON بأمان
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'بيانات JSON غير صحيحة'}, status=400)

    session_id = data.get('session_id')
    if not session_id:
        return JsonResponse({'success': False, 'error': 'معرف الجلسة مطلوب'}, status=400)

    # 2) تحويل القيم لأعداد صحيحة غير سالبة
    try:
        t1_in = max(0, int(data.get('team1_score', 0)))
        t2_in = max(0, int(data.get('team2_score', 0)))
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'قيم النقاط يجب أن تكون أرقام صحيحة'}, status=400)

    # 3) التحقق من وجود الجلسة ونشاطها
    try:
        base_session = GameSession.objects.get(id=session_id, is_active=True)
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة أو غير نشطة'}, status=404)

    if base_session.is_time_expired:
        return JsonResponse({
            'success': False,
            'error': 'انتهت صلاحية الجلسة',
            'session_expired': True,
            'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
        }, status=410)


    # 4) حفظ من داخل معاملة مع قفل الصف لمنع السباقات
    from django.db import transaction
    try:
        with transaction.atomic():
            session = GameSession.objects.select_for_update().get(id=session_id, is_active=True)

            session.team1_score = t1_in
            session.team2_score = t2_in

            winning_score = 10
            if session.team1_score >= winning_score and session.team1_score > session.team2_score:
                session.winner_team = 'team1'
                session.is_completed = True
            elif session.team2_score >= winning_score and session.team2_score > session.team1_score:
                session.winner_team = 'team2'
                session.is_completed = True
            else:
                # لو ما تحقق شرط الفوز، نتأكد من إلغاء أي حالة فوز/اكتمال سابقة
                session.winner_team = None
                session.is_completed = False

            session.save(update_fields=['team1_score', 'team2_score', 'winner_team', 'is_completed'])
    except Exception as e:
        logger.error(f'DB update error (scores) for session {session_id}: {e}')
        return JsonResponse({'success': False, 'error': 'خطأ داخلي أثناء تحديث النقاط'}, status=500)

    # 5) بثّ التحديث باسم موحّد تدعمه المستهلكات (letters/images): broadcast_score_update
    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f"{session.game_type}_session_{session_id}",
                {
                    "type": "broadcast_score_update",
                    "team1_score": session.team1_score,
                    "team2_score": session.team2_score,
                    "winner": session.winner_team,
                    "is_completed": session.is_completed,
                }
            )
    except Exception as e:
        logger.error(f'WS broadcast error (scores) for session {session_id}: {e}')

    logger.info(f'Scores updated in session {session_id}: Team1={session.team1_score}, Team2={session.team2_score}')
    return JsonResponse({
        'success': True,
        'message': 'تم تحديث النقاط',
        'team1_score': session.team1_score,
        'team2_score': session.team2_score,
        'winner': session.winner_team,
        'is_completed': session.is_completed
    })


def session_state(request):
    sid = request.GET.get("session_id")
    if not sid:
        return HttpResponseBadRequest("missing session_id")

    session = get_object_or_404(GameSession, id=sid)
    if session.is_time_expired:
        return JsonResponse({"detail": "expired"}, status=410)

    progress = LettersGameProgress.objects.filter(session=session).only("cell_states").first()
    cell_states = progress.cell_states if (progress and isinstance(progress.cell_states, dict)) else {}

    time_remaining_seconds = None
    if session.package.is_free:
        end_at = session.created_at + timedelta(hours=1)
        left = int((end_at - timezone.now()).total_seconds())
        time_remaining_seconds = max(0, left)

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
        if session.is_time_expired:
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

        if session.is_time_expired:
            return JsonResponse({'success': False, 'error': 'انتهت صلاحية الجلسة', 'session_expired': True}, status=410)

        buzz_lock_key = f"buzz_lock_{session_id}"
        lock_payload = {
            'name': contestant_name,
            'team': team,
            'timestamp': timestamp,
            'session_id': session_id,
            'method': 'HTTP',
        }

        try:
            added = cache.add(buzz_lock_key, lock_payload, timeout=4)

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

        contestant, created = Contestant.objects.get_or_create(
            session=session,
            name=contestant_name,
            defaults={'team': team}
        )
        if not created and contestant.team != team:
            contestant.team = team
            contestant.save(update_fields=['team'])

        try:
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

# -------------------------------
# بدء جولة جديدة (مدفوعة فقط) — لا تلمس النقاط أبدًا
# -------------------------------
@csrf_exempt
@require_http_methods(["POST"])
def letters_new_round(request):
    """
    بدء جولة جديدة للحزم المدفوعة فقط، بدون أي تعديل على النقاط إطلاقًا.
    - لا يغير نقاط الفريقين مطلقًا.
    - يبدّل ترتيب الحروف فقط ويفرّغ تقدم الخلايا.
    - يبث عبر WebSocket: letters_updated لاستبدال الحروف فقط (بدون أي بث للنقاط).
    - يعيد JSON يحتوي الحروف كـ fallback إن لم يكن WebSocket متاحًا.
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'بيانات JSON غير صحيحة'}, status=400)

    sid = payload.get("session_id")
    if not sid:
        return JsonResponse({'success': False, 'error': 'معرف الجلسة مطلوب'}, status=400)

    session = get_object_or_404(GameSession, id=sid, is_active=True)

    if session.is_time_expired:
        return JsonResponse({'success': False, 'error': 'انتهت صلاحية الجلسة', 'session_expired': True}, status=410)

    if session.package.is_free:
        return JsonResponse({'success': False, 'error': 'الميزة متاحة للحزم المدفوعة فقط'}, status=403)

    # 1) بدّل ترتيب الحروف (لا علاقة للنقاط هنا)
    new_letters = get_paid_order_fresh()
    set_session_order(session.id, new_letters, is_free=False)

    # 2) تصفير تقدّم الخلايا فقط
    try:
        progress = LettersGameProgress.objects.filter(session=session).first()
        if progress:
            progress.cell_states = {}
            progress.used_letters = []
            progress.save(update_fields=['cell_states', 'used_letters'])
    except Exception:
        pass

    # 3) بثّ: استبدال الحروف فقط (بدون أي بث للنقاط)
    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f"letters_session_{session.id}",
                {"type": "broadcast_letters_replace", "letters": new_letters, "reset_progress": True}
            )
    except Exception as e:
        logger.error(f"WS broadcast error (new round): {e}")

    # 4) الاستجابة: نعيد الحروف كـ fallback فقط (لا نعيد/نثبت النقاط)
    return JsonResponse({
        'success': True,
        'letters': new_letters,
        'reset_progress': True
    })



# --- NEW: بثّ حرف مختار من المقدم إلى شاشة العرض ---
@csrf_exempt
@require_http_methods(["POST"])
def api_letters_select_letter(request):
    """
    يستقبل session_id + letter من المقدم، ويتحقق أن الحرف ضمن ترتيب الجلسة،
    ثم يبثّ حدث letter_selected عبر WebSocket لشاشة العرض/الجميع.
    لا يغيّر أي حالة (نقاط/تلوين)، فقط إشعار بصري.
    """
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'بيانات JSON غير صحيحة'}, status=400)

    sid   = payload.get("session_id")
    letter = payload.get("letter")
    if not sid or not letter:
        return JsonResponse({'success': False, 'error': 'session_id و letter مطلوبة'}, status=400)

    try:
        session = GameSession.objects.get(id=sid, is_active=True, game_type='letters')
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة أو غير نشطة'}, status=404)

    if session.is_time_expired:
        return JsonResponse({'success': False, 'error': 'انتهت صلاحية الجلسة', 'session_expired': True}, status=410)

    # تأكد أن الحرف موجود في ترتيب هذه الجلسة
    letters = get_session_order(session.id, session.package.is_free) or get_letters_for_session(session)
    if letter not in letters:
        return JsonResponse({'success': False, 'error': f'الحرف {letter} غير متاح في هذه الجلسة'}, status=400)

    # بثّ إلى المجموعة
    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f"letters_session_{session.id}",
                {
                    "type": "broadcast_letter_selected",  # سيحوّلها الـ consumer إلى payload لعملاء WS
                    "letter": letter,
                }
            )
    except Exception as e:
        logger.error(f'WS broadcast error (letter_selected): {e}')

    return JsonResponse({'success': True, 'message': 'تم بثّ الحرف', 'letter': letter})



# تحدي الصور

# تحدي الصور

from threading import Thread

def _broadcast_images_index_async(session_id, idx, count):
    """
    بثّ WebSocket في خيط منفصل حتى لا يعلّق استجابة الـ HTTP.
    """
    try:
        layer = get_channel_layer()
        if not layer:
            return
        async_to_sync(layer.group_send)(
            f"images_session_{session_id}",
            {"type": "broadcast_image_index", "current_index": idx, "count": count}
        )
    except Exception as e:
        logger.error(f'WS broadcast async (images) error: {e}')


def _clamp_index(idx, total):
    try:
        i = int(idx)
    except Exception:
        i = 1
    if total <= 0:
        return 1
    return max(1, min(i, total))


def _get_riddles_qs(session):
    return PictureRiddle.objects.filter(package=session.package).order_by('order') \
            .values('order', 'image_url', 'hint', 'answer')


def _json_current_payload(session, riddles, idx):
    """
    يبني حمولة موحّدة: الحالي + عدد الألغاز + روابط الجيران (للتحميل المُسبق).
    """
    total = len(riddles)
    idx = _clamp_index(idx, total)
    empty = {'order': 1, 'image_url': '', 'hint': '', 'answer': ''}
    cur = riddles[idx - 1] if total else empty
    prev_url = riddles[idx - 2]['image_url'] if (idx - 2) >= 0 and total else None
    next_url = riddles[idx]['image_url'] if (idx) < total and total else None
    return {
        'success': True,
        'current_index': idx,
        'count': total,
        'current': cur,
        'prev_image_url': prev_url,
        'next_image_url': next_url,
    }


@require_http_methods(["POST"])
def create_images_session(request):
    """
    إنشاء جلسة تحدي الصور:
    - المجانية: تسجيل FreeTrialUsage('images') + جلسة صالحة ساعة.
    - المدفوعة: التحقق من شراء نشط وربط الجلسة به، صالحة 72 ساعة.
    - يهيّئ PictureGameProgress(current_index=1).
    """
    if request.method != 'POST':
        return redirect('games:images_home')

    package_id = request.POST.get('package_id')
    package = get_object_or_404(GamePackage, id=package_id, game_type='images')

    # قفل خفيف ضد الدبل-ضغط
    lock_owner = request.user.id if request.user.is_authenticated else request.META.get('REMOTE_ADDR', 'anon')
    lock_key = f"images_create_lock:{lock_owner}"
    if cache.get(lock_key):
        messages.info(request, '⏳ يتم إنشاء الجلسة الآن...')
        return redirect('games:images_home')
    cache.set(lock_key, 1, timeout=3)

    try:
        # لازم يكون فيه ألغاز
        riddles_qs = PictureRiddle.objects.filter(package=package).order_by('order')
        if not riddles_qs.exists():
            messages.error(request, 'هذه الحزمة لا تحتوي ألغاز صور بعد.')
            return redirect('games:images_home')

        # ========= مجاني =========
        if package.is_free:
            if not request.user.is_authenticated:
                messages.error(request, 'يرجى تسجيل الدخول للاستفادة من الجلسة المجانية')
                return redirect(f'/accounts/login/?next={request.path}')
            try:
                with transaction.atomic():
                    FreeTrialUsage.objects.create(user=request.user, game_type='images')
            except IntegrityError:
                messages.error(request, 'لقد استخدمت الجلسة المجانية لتحدي الصور.')
                return redirect('games:images_home')

            # لو عنده جلسة مجانية نشطة لنفس الحزمة رجّعه لها
            existing = (GameSession.objects
                        .filter(host=request.user, package=package, is_active=True)
                        .order_by('-created_at').first())
            if existing and not existing.is_time_expired:
                messages.success(request, 'تم توجيهك إلى جلستك المجانية النشطة.')
                return redirect('games:images_session', session_id=existing.id)


            session = GameSession.objects.create(
                host=request.user,
                package=package,
                game_type='images',
                purchase=None,
            )
            PictureGameProgress.objects.get_or_create(session=session, defaults={'current_index': 1})
            messages.success(request, '🎉 تم إنشاء الجلسة المجانية! صالحة لمدة ساعة.')
            return redirect('games:images_session', session_id=session.id)

        # ========= مدفوع =========
        if not request.user.is_authenticated:
            messages.error(request, 'يرجى تسجيل الدخول للحزم المدفوعة')
            return redirect(f'/accounts/login/?next={request.path}')

        with transaction.atomic():
            now = timezone.now()
            purchase = (UserPurchase.objects
                        .select_for_update()
                        .filter(user=request.user, package=package, is_completed=False, expires_at__gt=now)
                        .order_by('-purchase_date').first())

            if not purchase:
                # حدّث أي شراء قديم
                stale = (UserPurchase.objects.select_for_update()
                         .filter(user=request.user, package=package, is_completed=False)
                         .order_by('-purchase_date').first())
                if stale:
                    stale.mark_expired_if_needed(auto_save=True)
                messages.error(request, 'يجب شراء هذه الحزمة أولًا أو أن شراءك السابق انتهت صلاحيته.')
                return redirect('games:images_home')

            if purchase.mark_expired_if_needed(auto_save=True):
                messages.error(request, 'انتهت صلاحية الشراء السابق. تحتاج شراء جديد.')
                return redirect('games:images_home')

            # جلسة مرتبطة بنفس الشراء؟
            existing = GameSession.objects.filter(purchase=purchase, is_active=True).first()
            if existing and not existing.is_time_expired:
                messages.info(request, 'لديك جلسة نشطة لهذه الحزمة — تم توجيهك لها.')
                return redirect('games:images_session', session_id=existing.id)


            # أو جلسة نشطة لنفس الحزمة بعد وقت الشراء
            existing2 = (GameSession.objects
                         .filter(host=request.user, package=package, is_active=True,
                                 created_at__gte=purchase.purchase_date)
                         .order_by('-created_at').first())
            if existing2 and not existing2.is_time_expired:
                if existing2.purchase_id is None:
                    existing2.purchase = purchase
                    existing2.full_clean()
                    existing2.save(update_fields=['purchase'])
                messages.info(request, 'تم ربط جلستك الحالية بالشراء وإعادتك لها.')
                return redirect('games:images_session', session_id=existing2.id)


            # إنشاء جديدة
            try:
                session = GameSession.objects.create(
                    host=request.user,
                    package=package,
                    game_type='images',
                    purchase=purchase,
                )
            except IntegrityError:
                session = GameSession.objects.get(purchase=purchase)

            PictureGameProgress.objects.get_or_create(session=session, defaults={'current_index': 1})

        messages.success(request, 'تم إنشاء الجلسة المدفوعة بنجاح! 🎉')
        return redirect('games:images_session', session_id=session.id)

    except Exception as e:
        logger.error(f'Error creating images session: {e}')
        messages.error(request, 'حدث خطأ أثناء إنشاء الجلسة، جرّب مرة أخرى.')
        return redirect('games:images_home')
    finally:
        try: cache.delete(lock_key)
        except Exception: pass



@login_required
@require_http_methods(["GET"])
def images_create(request):
    package_id = request.GET.get("package_id")
    if not package_id:
        messages.error(request, "لم يتم العثور على الحزمة.")
        return redirect("games:images_home")

    package = get_object_or_404(GamePackage, id=package_id, game_type="images")

    # شراء فعّال؟
    purchase = UserPurchase.objects.filter(
        user=request.user,
        package=package,
        is_completed=True,
        expires_at__gt=timezone.now()
    ).order_by("-purchase_date").first()

    if not purchase:
        messages.error(request, "لا يوجد شراء صالح لهذه الحزمة.")
        return redirect("games:images_home")

    # لو لديه جلسة قديمة مرتبطة بنفس الشراء
    existing_session = GameSession.objects.filter(
        purchase=purchase,
        is_active=True,
        game_type="images"
    ).first()

    if existing_session and not existing_session.is_time_expired:
        return redirect("games:images_session", session_id=existing_session.id)

    # إنشاء جلسة جديدة
    session = GameSession.objects.create(
        host=request.user,
        package=package,
        purchase=purchase,
        game_type="images"
    )

    PictureGameProgress.objects.get_or_create(
        session=session,
        defaults={"current_index": 1}
    )

    return redirect("games:images_session", session_id=session.id)



def images_display(request, display_link):
    session = get_object_or_404(GameSession, display_link=display_link, is_active=True, game_type='images')
    if session.is_time_expired:
        return render(request, 'games/session_expired.html', {
            'message': _expired_text(session),
            'session_type': 'مجانية' if session.package.is_free else 'مدفوعة',
            'upgrade_message': 'للاستمتاع بجلسات أطول، تصفح الحزم المدفوعة.'
        })

    riddles = list(PictureRiddle.objects.filter(package=session.package).order_by('order')
                   .values('order', 'image_url'))
    progress = PictureGameProgress.objects.filter(session=session).first()
    current_index = progress.current_index if progress else 1
    current_index = max(1, min(current_index, len(riddles)))

    return render(request, 'games/images/images_display.html', {
        'session': session,
        'riddles_count': len(riddles),
        'current_index': current_index,
        'time_remaining': get_session_time_remaining(session),
    })


def images_contestants(request, contestants_link):
    """صفحة المتسابقين (نفس زر الطنطيط والفرق)؛ العرض الفعلي للصورة على شاشة العرض."""
    session = get_object_or_404(GameSession, contestants_link=contestants_link, is_active=True, game_type='images')
    if session.is_time_expired:
        return render(request, 'games/session_expired.html', {
            'message': _expired_text(session),
            'session_type': 'مجانية' if session.package.is_free else 'مدفوعة',
        })

    return render(request, 'games/images/images_contestants.html', {
        'session': session,
        'time_remaining': get_session_time_remaining(session),
        'is_free_session': session.package.is_free,
    })


@require_http_methods(["GET"])
def api_images_get_current(request):
    sid = request.GET.get("session_id")
    if not sid:
        return JsonResponse({'success': False, 'error': 'session_id مطلوب'}, status=400)

    session = get_object_or_404(GameSession, id=sid, is_active=True, game_type='images')
    if session.is_time_expired:
        return JsonResponse({'success': False, 'error': 'انتهت صلاحية الجلسة', 'session_expired': True}, status=410)

    riddles = list(_get_riddles_qs(session))
    if not riddles:
        return JsonResponse({'success': False, 'error': 'لا توجد ألغاز في هذه الحزمة'}, status=400)

    progress = PictureGameProgress.objects.filter(session=session).first()
    idx = progress.current_index if progress else 1
    payload = _json_current_payload(session, riddles, idx)
    return JsonResponse(payload)


@csrf_exempt
@require_http_methods(["POST"])
def api_images_set_index(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON غير صحيح'}, status=400)

    sid = payload.get("session_id")
    idx = payload.get("index")
    if not sid or idx is None:
        return JsonResponse({'success': False, 'error': 'session_id و index مطلوبة'}, status=400)

    session = get_object_or_404(GameSession, id=sid, is_active=True, game_type='images')
    if session.is_time_expired:
        return JsonResponse({'success': False, 'error': 'انتهت صلاحية الجلسة', 'session_expired': True}, status=410)

    riddles = list(_get_riddles_qs(session))
    total = len(riddles)
    if total == 0:
        return JsonResponse({'success': False, 'error': 'لا ألغاز'}, status=400)

    idx = _clamp_index(idx, total)
    progress, _ = PictureGameProgress.objects.get_or_create(session=session, defaults={'current_index': 1})
    progress.current_index = idx
    progress.save(update_fields=['current_index'])

    payload = _json_current_payload(session, riddles, idx)

    # بثّ غير حاجب
    try:
        Thread(target=_broadcast_images_index_async, args=(session.id, payload['current_index'], payload['count']), daemon=True).start()
    except Exception:
        pass

    return JsonResponse(payload)


@csrf_exempt
@require_http_methods(["POST"])
def api_images_next(request):
    try:
        payload = json.loads(request.body or "{}")
        sid = payload.get("session_id")
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON غير صحيح'}, status=400)

    if not sid:
        return JsonResponse({'success': False, 'error': 'session_id مطلوب'}, status=400)

    session = get_object_or_404(GameSession, id=sid, is_active=True, game_type='images')
    if session.is_time_expired:
        return JsonResponse({'success': False, 'error': 'انتهت صلاحية الجلسة', 'session_expired': True}, status=410)

    riddles = list(_get_riddles_qs(session))
    total = len(riddles)
    if total == 0:
        return JsonResponse({'success': False, 'error': 'لا ألغاز'}, status=400)

    progress, _ = PictureGameProgress.objects.get_or_create(session=session, defaults={'current_index': 1})
    new_idx = _clamp_index(progress.current_index + 1, total)
    progress.current_index = new_idx
    progress.save(update_fields=['current_index'])

    payload = _json_current_payload(session, riddles, new_idx)

    # بثّ غير حاجب
    try:
        Thread(target=_broadcast_images_index_async, args=(session.id, payload['current_index'], payload['count']), daemon=True).start()
    except Exception:
        pass

    return JsonResponse(payload)


@csrf_exempt
@require_http_methods(["POST"])
def api_images_prev(request):
    try:
        payload = json.loads(request.body or "{}")
        sid = payload.get("session_id")
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON غير صحيح'}, status=400)

    if not sid:
        return JsonResponse({'success': False, 'error': 'session_id مطلوب'}, status=400)

    session = get_object_or_404(GameSession, id=sid, is_active=True, game_type='images')
    if session.is_time_expired:
        return JsonResponse({'success': False, 'error': 'انتهت صلاحية الجلسة', 'session_expired': True}, status=410)

    riddles = list(_get_riddles_qs(session))
    total = len(riddles)
    if total == 0:
        return JsonResponse({'success': False, 'error': 'لا ألغاز'}, status=400)

    progress, _ = PictureGameProgress.objects.get_or_create(session=session, defaults={'current_index': 1})
    new_idx = _clamp_index(progress.current_index - 1, total)
    progress.current_index = new_idx
    progress.save(update_fields=['current_index'])

    payload = _json_current_payload(session, riddles, new_idx)

    # بثّ غير حاجب
    try:
        Thread(target=_broadcast_images_index_async, args=(session.id, payload['current_index'], payload['count']), daemon=True).start()
    except Exception:
        pass

    return JsonResponse(payload)


from django.shortcuts import get_object_or_404, render
from django.urls import reverse

def images_session(request, session_id):
    """
    صفحة المضيف لتحدي الصور.
    تطابق اسم القالب الموجود عندك: games/images/images_session.html
    وتزوّد القالب بمعلومات الجلسة + الروابط للشاشتين.
    """
    session = get_object_or_404(GameSession, id=session_id, is_active=True, game_type='images')

    # انتهاء الصلاحية
    if session.is_time_expired:
        return render(request, 'games/session_expired.html', {
            'message': _expired_text(session),
            'session_type': 'مجانية' if session.package.is_free else 'مدفوعة',
            'upgrade_message': 'للاستمتاع بجلسات أطول، تصفح الحزم المدفوعة.'
        })

    # عدد الألغاز + الفهرس الحالي (لو احتاجه القالب)
    riddles = list(PictureRiddle.objects.filter(package=session.package).order_by('order')
                   .values('order', 'image_url'))
    progress = PictureGameProgress.objects.filter(session=session).first()
    current_index = progress.current_index if progress else 1
    current_index = max(1, min(current_index, len(riddles) or 1))

    return render(request, 'games/images/images_session.html', {
        'session': session,
        'riddles_count': len(riddles),
        'current_index': current_index,
        'time_remaining': get_session_time_remaining(session),

        # روابط الشاشات
        'display_url': request.build_absolute_uri(reverse('games:images_display', args=[session.display_link])),
        'contestants_url': request.build_absolute_uri(reverse('games:images_contestants', args=[session.contestants_link])),
    })




def imposter_packages(request):
    packages = GamePackage.objects.filter(
        game_type="imposter",
        is_active=True
    ).order_by("package_number")

    return render(request, "imposter/packages.html", {
        "packages": packages,
        "page_title": "حزم لعبة امبوستر"
    })