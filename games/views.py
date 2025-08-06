# games/views.py - مع نظام منع التلاعب في الجلسات المجانية
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from datetime import timedelta
import json
import uuid
import logging
import random
from .models import (
    GamePackage, GameSession, UserPurchase, LettersGameProgress, 
    LettersGameQuestion, Contestant
)

# إعداد logger
logger = logging.getLogger('games')

def check_free_session_eligibility(user, game_type):
    """
    التحقق من أهلية المستخدم لإنشاء جلسة مجانية
    
    Args:
        user: المستخدم
        game_type: نوع اللعبة ('letters', 'images', 'quiz')
    
    Returns:
        tuple: (eligible: bool, message: str, previous_sessions_count: int)
    """
    if not user.is_authenticated:
        return True, "", 0
    
    # البحث عن الجلسات المجانية السابقة لهذا النوع
    previous_free_sessions = GameSession.objects.filter(
        host=user,
        game_type=game_type,
        package__is_free=True
    )
    
    sessions_count = previous_free_sessions.count()
    
    if sessions_count > 0:
        # المستخدم سبق له إنشاء جلسة مجانية لهذا النوع
        game_names = {
            'letters': 'خلية الحروف',
            'images': 'تحدي الصور', 
            'quiz': 'سؤال وجواب'
        }
        
        latest_session = previous_free_sessions.order_by('-created_at').first()
        
        message = f"""
        🚫 لقد استنفدت جلستك المجانية للعبة {game_names.get(game_type, 'هذه اللعبة')}!
        
        
        💎 للاستمرار في الاستمتاع باللعبة:
        • يمكنك شراء الحزم المدفوعة التي تحتوي على المزيد من المحتوى
        • الحزم المدفوعة لا تنتهي صلاحيتها ولا يوجد حد لعدد الجلسات
        • محتوى حصري وأسئلة أكثر تنوعاً
        
        🛒 تصفح الحزم المتاحة واختر ما يناسبك!
        """
        
        return False, message, sessions_count
    
    return True, "", 0

def is_session_expired(session):
    """
    التحقق من انتهاء صلاحية الجلسة
    - الحزم المجانية: ساعة واحدة
    - الحزم المدفوعة: 72 ساعة (3 أيام)
    """
    if not session.package.is_free:
        # الحزم المدفوعة تستمر 72 ساعة (3 أيام)
        duration_hours = 72
        expiry_time = session.created_at + timedelta(hours=duration_hours)
    else:
        # الحزم المجانية تنتهي بعد ساعة واحدة
        expiry_time = session.created_at + timedelta(hours=1)
    
    current_time = timezone.now()
    
    if current_time > expiry_time:
        # إنهاء الجلسة تلقائياً
        session.is_active = False
        session.is_completed = True
        session.save()
        return True
    
    return False

def get_session_time_remaining(session):
    """
    حساب الوقت المتبقي للجلسة
    """
    if not session.package.is_free:
        # الحزم المدفوعة: 72 ساعة
        duration_hours = 72
        expiry_time = session.created_at + timedelta(hours=duration_hours)
    else:
        # الحزم المجانية: ساعة واحدة
        expiry_time = session.created_at + timedelta(hours=1)
    
    current_time = timezone.now()
    
    if current_time >= expiry_time:
        return timedelta(0)
    
    return expiry_time - current_time

def get_session_expiry_info(session):
    """
    جلب معلومات شاملة عن انتهاء صلاحية الجلسة
    """
    current_time = timezone.now()
    is_free = session.package.is_free
    
    if is_free:
        duration_hours = 1
        duration_text = "ساعة واحدة"
        expiry_time = session.created_at + timedelta(hours=1)
    else:
        duration_hours = 72
        duration_text = "72 ساعة (3 أيام)"
        expiry_time = session.created_at + timedelta(hours=72)
    
    time_remaining = expiry_time - current_time if current_time < expiry_time else timedelta(0)
    is_expired = current_time >= expiry_time
    
    # حساب النسبة المئوية للوقت المتبقي
    total_duration = timedelta(hours=duration_hours)
    if time_remaining.total_seconds() > 0:
        remaining_percentage = (time_remaining.total_seconds() / total_duration.total_seconds()) * 100
    else:
        remaining_percentage = 0
    
    # رسائل التحذير حسب الوقت المتبقي
    warning_message = None
    warning_level = "info"  # info, warning, danger
    
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
                warning_message = f"ℹ️ باقي يوم واحد على انتهاء الصلاحية"
                warning_level = "info"
            elif remaining_days == 2:
                warning_message = f"ℹ️ باقي يومان"
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

def get_arabic_letters_for_session(package):
    """
    إرجاع 25 حرف للجلسة حسب نوع الحزمة
    - الحزمة المجانية: حروف ثابتة بترتيب عشوائي ثابت (نفس العشوائية دائماً)
    - الحزم المدفوعة: 25 حرف عشوائي من أصل 28 بترتيب عشوائي
    """
    # جميع الحروف العربية (28 حرف)
    all_arabic_letters = [
        'أ', 'ب', 'ت', 'ث', 'ج', 'ح', 'خ', 'د', 'ذ', 'ر',
        'ز', 'س', 'ش', 'ص', 'ض', 'ط', 'ظ', 'ع', 'غ', 'ف',
        'ق', 'ك', 'ل', 'م', 'ن', 'هـ', 'و', 'ي'
    ]
    
    if package.is_free:
        # الحزمة المجانية: أول 25 حرف لكن بترتيب عشوائي ثابت
        free_letters = [
            'أ', 'ب', 'ت', 'ث', 'ج', 'ح', 'خ', 'د', 'ذ', 'ر',
            'ز', 'س', 'ش', 'ص', 'ض', 'ط', 'ظ', 'ع', 'غ', 'ف',
            'ق', 'ك', 'ل', 'م', 'ن'
        ]
        
        # ترتيب عشوائي ثابت للحزمة المجانية
        random.seed(999)  # seed ثابت للحزمة المجانية
        random.shuffle(free_letters)
        random.seed()  # إعادة تعيين seed
        
        return free_letters
    else:
        # الحزم المدفوعة: اختيار 25 حرف عشوائي وترتيب عشوائي
        # استخدام رقم الحزمة كـ seed للحصول على نفس العشوائية في كل مرة للحزمة نفسها
        random.seed(f"{package.id}_{package.package_number}")
        
        # اختيار 25 حرف عشوائي من أصل 28
        selected_letters = random.sample(all_arabic_letters, 25)
        
        # ترتيب عشوائي للحروف المختارة
        random.shuffle(selected_letters)
        
        # إعادة تعيين seed للحصول على عشوائية طبيعية للمرات القادمة
        random.seed()
        
        return selected_letters

def games_home(request):
    """الصفحة الرئيسية للألعاب"""
    return render(request, 'home.html', {
        'letters_available': True,
        'images_available': False,  # سنفعلها لاحقاً
        'quiz_available': False,    # سنفعلها لاحقاً
    })

def letters_game_home(request):
    """صفحة لعبة خلية الحروف - متاحة للجميع"""
    # جلب الحزمة المجانية
    free_package = GamePackage.objects.filter(
        game_type='letters',
        is_free=True,
        is_active=True
    ).first()
    
    # جلب الحزم المدفوعة
    paid_packages = GamePackage.objects.filter(
        game_type='letters',
        is_free=False,
        is_active=True
    ).order_by('package_number')
    
    # إذا كان المستخدم مسجل دخول، تحقق من مشترياته
    user_purchases = []
    free_session_eligible = True
    free_session_message = ""
    user_free_sessions_count = 0
    
    if request.user.is_authenticated:
        user_purchases = UserPurchase.objects.filter(
            user=request.user,
            package__game_type='letters'
        ).values_list('package_id', flat=True)
        
        # التحقق من أهلية إنشاء جلسة مجانية
        free_session_eligible, free_session_message, user_free_sessions_count = check_free_session_eligibility(
            request.user, 'letters'
        )
    
    return render(request, 'games/letters/home.html', {
        'free_package': free_package,
        'paid_packages': paid_packages,
        'user_purchases': user_purchases,
        'free_session_eligible': free_session_eligible,
        'free_session_message': free_session_message,
        'user_free_sessions_count': user_free_sessions_count,
    })

def create_letters_session(request):
    """إنشاء جلسة لعب جديدة - مع التحقق من منع التلاعب"""
    if request.method == 'POST':
        package_id = request.POST.get('package_id')
        
        # التحقق من وجود الحزمة
        package = get_object_or_404(GamePackage, id=package_id, game_type='letters')
        
        # إذا كانت الحزمة مجانية، التحقق من الأهلية
        if package.is_free:
            # إذا لم يكن مسجل دخول، توجيهه للتسجيل
            if not request.user.is_authenticated:
                messages.info(request, 'يرجى تسجيل الدخول لإنشاء جلسة لعب')
                return redirect(f'/accounts/login/?next={request.path}')
            
            # التحقق من أهلية إنشاء جلسة مجانية (منع التلاعب)
            eligible, anti_cheat_message, sessions_count = check_free_session_eligibility(
                request.user, 'letters'
            )
            
            if not eligible:
                messages.error(request, anti_cheat_message)
                logger.warning(f'Free session creation blocked for user {request.user.username}: {sessions_count} previous sessions')
                return redirect('games:letters_home')
                
        else:
            # للحزم المدفوعة، يجب تسجيل الدخول والشراء
            if not request.user.is_authenticated:
                messages.error(request, 'يرجى تسجيل الدخول لشراء الحزم المدفوعة')
                return redirect(f'/accounts/login/?next={request.path}')
            
            # التحقق من الشراء
            purchase = UserPurchase.objects.filter(
                user=request.user,
                package=package
            ).first()
            if not purchase:
                messages.error(request, 'يجب شراء هذه الحزمة أولاً')
                return redirect('games:letters_home')
        
        # جلب أسماء الفرق من تفضيلات المستخدم أو الافتراضية
        team1_name = request.POST.get('team1_name', 'الفريق الأخضر')
        team2_name = request.POST.get('team2_name', 'الفريق البرتقالي')
        
        if request.user.is_authenticated and hasattr(request.user, 'preferences'):
            team1_name = request.user.preferences.default_team1_name or team1_name
            team2_name = request.user.preferences.default_team2_name or team2_name
        
        try:
            # إنشاء جلسة جديدة
            session = GameSession.objects.create(
                host=request.user,
                package=package,
                game_type='letters',
                team1_name=team1_name,
                team2_name=team2_name,
            )
            
            # إنشاء تقدم اللعبة مع حفظ الحروف المختارة للجلسة
            selected_letters = get_arabic_letters_for_session(package)
            
            LettersGameProgress.objects.create(
                session=session,
                cell_states={},
                used_letters=[],
                # حفظ الحروف المختارة في حقل JSON إضافي (يمكن إضافته للموديل لاحقاً)
            )
            
            # حفظ الحروف في session للاستخدام لاحقاً
            request.session[f'letters_{session.id}'] = selected_letters
            
            # تسجيل النشاط إذا كان مسجل دخول
            if request.user.is_authenticated:
                from accounts.models import UserActivity
                UserActivity.objects.create(
                    user=request.user,
                    activity_type='game_created',
                    description=f'إنشاء جلسة خلية الحروف - {package.get_game_type_display()} ({"مجانية" if package.is_free else "مدفوعة"})',
                    game_type='letters',
                    session_id=str(session.id)
                )
            
            # رسالة نجاح مختلفة حسب نوع الحزمة
            if package.is_free:
                success_message = f'''
                🎉 تم إنشاء جلسة مجانية بنجاح!
                
                ⏰ تذكير: هذه جلستك المجانية الوحيدة لخلية الحروف
                • صالحة لمدة ساعة واحدة فقط
                • لن تتمكن من إنشاء جلسة مجانية أخرى بعد انتهائها
                
                💎 للحصول على المزيد: تصفح الحزم المدفوعة للاستمتاع بمحتوى أكثر وجلسات غير محدودة!
                '''
                messages.success(request, success_message)
            else:
                messages.success(request, f'تم إنشاء الجلسة المدفوعة بنجاح! استمتع بجلسات غير محدودة! 🎉')
            
            logger.info(f'New letters session created: {session.id} by {request.user.username} ({"FREE" if package.is_free else "PAID"})')
            
            return redirect('games:letters_session', session_id=session.id)
            
        except Exception as e:
            logger.error(f'Error creating letters session: {e}')
            messages.error(request, 'حدث خطأ أثناء إنشاء الجلسة، يرجى المحاولة مرة أخرى')
            return redirect('games:letters_home')
    
    return redirect('games:letters_home')

@login_required
def letters_session(request, session_id):
    """صفحة جلسة خلية الحروف - للمقدم فقط"""
    session = get_object_or_404(GameSession, id=session_id, host=request.user)

    # التحقق من انتهاء صلاحية الجلسة
    if is_session_expired(session):
        messages.error(request, '⏰ انتهت صلاحية الجلسة المجانية (ساعة واحدة). يرجى شراء الحزم المدفوعة للاستمتاع بجلسات غير محدودة.')
        return redirect('games:letters_home')

    # جلب الحروف للجلسة
    arabic_letters = get_arabic_letters_for_session(session.package)

    # جلب الأسئلة وتنظيمها
    questions = session.package.letters_questions.all().order_by('letter', 'question_type')
    questions_by_letter = {}
    for q in questions:
        questions_by_letter.setdefault(q.letter, {})[q.question_type] = q

    # تحويل الحروف إلى JSON للـ JavaScript
    import json
    arabic_letters_json = json.dumps(arabic_letters)

    # حساب الوقت المتبقي للجلسة المجانية
    time_remaining = get_session_time_remaining(session)

    # معلومات إضافية للجلسة المجانية
    is_free_session = session.package.is_free
    free_session_warning = None
    if is_free_session and time_remaining:
        remaining_minutes = int(time_remaining.total_seconds() // 60)
        if remaining_minutes <= 10:  # تحذير في آخر 10 دقائق
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
    """شاشة العرض لخلية الحروف - متاحة للجميع بدون تسجيل دخول"""
    session = get_object_or_404(GameSession, display_link=display_link, is_active=True)
    
    # التحقق من انتهاء صلاحية الجلسة
    if is_session_expired(session):
        return render(request, 'games/session_expired.html', {
            'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)',
            'session_type': 'مجانية',
            'upgrade_message': 'للاستمتاع بجلسات غير محدودة، تصفح الحزم المدفوعة!'
        })
    
    # جلب الحروف للجلسة
    arabic_letters = get_arabic_letters_for_session(session.package)
    
    # حساب الوقت المتبقي
    time_remaining = get_session_time_remaining(session)
    
    logger.info(f'Display page accessed for session: {session.id}')
    
    return render(request, 'games/letters/letters_display.html', {
        'session': session,
        'arabic_letters': arabic_letters,
        'time_remaining': time_remaining,
        'is_free_session': session.package.is_free,
    })

def letters_contestants(request, contestants_link):
    """صفحة المتسابقين لخلية الحروف - متاحة للجميع بدون تسجيل دخول"""
    session = get_object_or_404(GameSession, contestants_link=contestants_link, is_active=True)
    
    # التحقق من انتهاء صلاحية الجلسة
    if is_session_expired(session):
        return render(request, 'games/session_expired.html', {
            'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)',
            'session_type': 'مجانية',
            'upgrade_message': 'للاستمتاع بجلسات غير محدودة، تصفح الحزم المدفوعة!'
        })
    
    # حساب الوقت المتبقي
    time_remaining = get_session_time_remaining(session)
    
    logger.info(f'Contestants page accessed for session: {session.id}')
    
    return render(request, 'games/letters/letters_contestants.html', {
        'session': session,
        'time_remaining': time_remaining,
        'is_free_session': session.package.is_free,
    })

# =======================================
# نفس منطق منع التلاعب لألعاب أخرى
# =======================================

def images_game_home(request):
    """صفحة لعبة تحدي الصور - مع نظام منع التلاعب"""
    # جلب الحزمة المجانية
    free_package = GamePackage.objects.filter(
        game_type='images',
        is_free=True,
        is_active=True
    ).first()
    
    # جلب الحزم المدفوعة
    paid_packages = GamePackage.objects.filter(
        game_type='images',
        is_free=False,
        is_active=True
    ).order_by('package_number')
    
    # التحقق من الأهلية للجلسة المجانية
    user_purchases = []
    free_session_eligible = True
    free_session_message = ""
    user_free_sessions_count = 0
    
    if request.user.is_authenticated:
        user_purchases = UserPurchase.objects.filter(
            user=request.user,
            package__game_type='images'
        ).values_list('package_id', flat=True)
        
        # التحقق من أهلية إنشاء جلسة مجانية
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
    """صفحة لعبة السؤال والجواب - مع نظام منع التلاعب"""
    # جلب الحزمة المجانية
    free_package = GamePackage.objects.filter(
        game_type='quiz',
        is_free=True,
        is_active=True
    ).first()
    
    # جلب الحزم المدفوعة
    paid_packages = GamePackage.objects.filter(
        game_type='quiz',
        is_free=False,
        is_active=True
    ).order_by('package_number')
    
    # التحقق من الأهلية للجلسة المجانية
    user_purchases = []
    free_session_eligible = True
    free_session_message = ""
    user_free_sessions_count = 0
    
    if request.user.is_authenticated:
        user_purchases = UserPurchase.objects.filter(
            user=request.user,
            package__game_type='quiz'
        ).values_list('package_id', flat=True)
        
        # التحقق من أهلية إنشاء جلسة مجانية
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

# =============================================================================
# API ENDPOINTS - مع دعم نظام منع التلاعب
# =============================================================================

@require_http_methods(["GET"])
def api_check_free_session_eligibility(request):
    """API للتحقق من أهلية إنشاء جلسة مجانية"""
    if not request.user.is_authenticated:
        return JsonResponse({
            'success': True,
            'eligible': True,
            'message': 'غير مسجل دخول - مسموح بجلسة مجانية',
            'sessions_count': 0
        })
    
    game_type = request.GET.get('game_type')
    if not game_type:
        return JsonResponse({
            'success': False,
            'error': 'نوع اللعبة مطلوب'
        }, status=400)
    
    try:
        eligible, message, sessions_count = check_free_session_eligibility(request.user, game_type)
        
        return JsonResponse({
            'success': True,
            'eligible': eligible,
            'message': message,
            'sessions_count': sessions_count,
            'user_id': request.user.id,
            'username': request.user.username,
            'game_type': game_type
        })
        
    except Exception as e:
        logger.error(f'Error checking free session eligibility: {e}')
        return JsonResponse({
            'success': False,
            'error': 'خطأ في التحقق من الأهلية'
        }, status=500)

# باقي API endpoints (تبقى كما هي)
@require_http_methods(["GET"])
def get_question(request):
    """جلب الأسئلة للحرف المحدد - متاح للجميع"""
    letter = request.GET.get('letter')
    session_id = request.GET.get('session_id')
    
    if not letter or not session_id:
        return JsonResponse({
            'success': False, 
            'error': 'المعاملات مطلوبة'
        }, status=400)
    
    try:
        # جلب الجلسة
        session = GameSession.objects.get(id=session_id, is_active=True)
        
        # التحقق من أن الحرف موجود في حروف الجلسة
        arabic_letters = get_arabic_letters_for_session(session.package)
        if letter not in arabic_letters:
            return JsonResponse({
                'success': False, 
                'error': f'الحرف {letter} غير متاح في هذه الجلسة'
            }, status=400)
        
        # جلب الأسئلة الثلاثة للحرف
        questions = {}
        
        for question_type in ['main', 'alt1', 'alt2']:
            try:
                question_obj = LettersGameQuestion.objects.get(
                    package=session.package, 
                    letter=letter, 
                    question_type=question_type
                )
                questions[question_type] = {
                    'question': question_obj.question, 
                    'answer': question_obj.answer,
                    'category': question_obj.category
                }
            except LettersGameQuestion.DoesNotExist:
                questions[question_type] = {
                    'question': f'لا يوجد سؤال {question_type} للحرف {letter}',
                    'answer': 'غير متاح',
                    'category': 'غير محدد'
                }
        
        logger.info(f'Questions fetched for letter {letter} in session {session_id}')
        
        return JsonResponse({
            'success': True,
            'questions': questions,
            'letter': letter,
            'session_info': {
                'team1_name': session.team1_name,
                'team2_name': session.team2_name,
                'package_name': f"{session.package.get_game_type_display()} - حزمة {session.package.package_number}",
                'is_free_package': session.package.is_free
            }
        })
        
    except GameSession.DoesNotExist:
        return JsonResponse({
            'success': False, 
            'error': 'الجلسة غير موجودة أو غير نشطة'
        }, status=404)
    except Exception as e:
        logger.error(f'Error fetching question: {e}')
        return JsonResponse({
            'success': False, 
            'error': f'خطأ داخلي: {str(e)}'
        }, status=500)

# باقي API endpoints تبقى كما هي مع إضافات بسيطة

@require_http_methods(["GET"])
def get_session_letters(request):
    """جلب حروف الجلسة - API جديد لتزامن الحروف"""
    session_id = request.GET.get('session_id')
    
    if not session_id:
        return JsonResponse({
            'success': False, 
            'error': 'معرف الجلسة مطلوب'
        }, status=400)
    
    try:
        session = GameSession.objects.get(id=session_id, is_active=True)
        
        # التحقق من انتهاء صلاحية الجلسة
        if is_session_expired(session):
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True,
                'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
            }, status=410)
        
        arabic_letters = get_arabic_letters_for_session(session.package)
        
        return JsonResponse({
            'success': True,
            'letters': arabic_letters,
            'session_info': {
                'is_free_package': session.package.is_free,
                'package_number': session.package.package_number,
                'total_letters': len(arabic_letters)
            }
        })
        
    except GameSession.DoesNotExist:
        return JsonResponse({
            'success': False, 
            'error': 'الجلسة غير موجودة أو غير نشطة'
        }, status=404)
    except Exception as e:
        logger.error(f'Error fetching session letters: {e}')
        return JsonResponse({
            'success': False, 
            'error': f'خطأ داخلي: {str(e)}'
        }, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def update_cell_state(request):
    """تحديث حالة الخلية - متاح للجميع"""
    try:
        data = json.loads(request.body)
        session_id = data.get('session_id')
        letter = data.get('letter')
        state = data.get('state')  # 'normal', 'team1', 'team2'
        
        if not all([session_id, letter, state]):
            return JsonResponse({
                'success': False, 
                'error': 'جميع المعاملات مطلوبة'
            }, status=400)
        
        if state not in ['normal', 'team1', 'team2']:
            return JsonResponse({
                'success': False, 
                'error': 'حالة الخلية غير صحيحة'
            }, status=400)
        
        # جلب الجلسة
        session = GameSession.objects.get(id=session_id, is_active=True)
        
        # التحقق من انتهاء صلاحية الجلسة
        if is_session_expired(session):
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True,
                'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
            }, status=410)
        
        # التحقق من أن الحرف موجود في حروف الجلسة
        arabic_letters = get_arabic_letters_for_session(session.package)
        if letter not in arabic_letters:
            return JsonResponse({
                'success': False, 
                'error': f'الحرف {letter} غير متاح في هذه الجلسة'
            }, status=400)
        
        # جلب أو إنشاء تقدم اللعبة
        progress, created = LettersGameProgress.objects.get_or_create(
            session=session,
            defaults={'cell_states': {}, 'used_letters': []}
        )
        
        # تحديث حالة الخلية
        if progress.cell_states is None:
            progress.cell_states = {}
            
        progress.cell_states[letter] = state
        
        # إضافة الحرف للمستخدم إذا لم يكن موجود
        if progress.used_letters is None:
            progress.used_letters = []
            
        if letter not in progress.used_letters:
            progress.used_letters.append(letter)
        
        progress.save()
        
        logger.info(f'Cell state updated: {letter} -> {state} in session {session_id}')
        
        return JsonResponse({
            'success': True,
            'message': 'تم تحديث حالة الخلية',
            'letter': letter,
            'state': state
        })
        
    except GameSession.DoesNotExist:
        return JsonResponse({
            'success': False, 
            'error': 'الجلسة غير موجودة أو غير نشطة'
        }, status=404)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False, 
            'error': 'بيانات JSON غير صحيحة'
        }, status=400)
    except Exception as e:
        logger.error(f'Error updating cell state: {e}')
        return JsonResponse({
            'success': False, 
            'error': f'خطأ داخلي: {str(e)}'
        }, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def update_scores(request):
    """تحديث نقاط الفرق - متاح للجميع"""
    try:
        data = json.loads(request.body)
        session_id = data.get('session_id')
        team1_score = data.get('team1_score', 0)
        team2_score = data.get('team2_score', 0)
        
        if not session_id:
            return JsonResponse({
                'success': False, 
                'error': 'معرف الجلسة مطلوب'
            }, status=400)
        
        # التحقق من صحة النقاط
        try:
            team1_score = max(0, int(team1_score))
            team2_score = max(0, int(team2_score))
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False, 
                'error': 'قيم النقاط يجب أن تكون أرقام صحيحة'
            }, status=400)
        
        # جلب وتحديث الجلسة
        session = GameSession.objects.get(id=session_id, is_active=True)
        
        # التحقق من انتهاء صلاحية الجلسة
        if is_session_expired(session):
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True,
                'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
            }, status=410)
        
        session.team1_score = team1_score
        session.team2_score = team2_score
        
        # تحديد الفائز إذا وصل أحد الفرق لنقاط معينة
        winning_score = 10  # يمكن تعديلها حسب قواعد اللعبة
        
        if session.team1_score >= winning_score and session.team1_score > session.team2_score:
            session.winner_team = 'team1'
            session.is_completed = True
        elif session.team2_score >= winning_score and session.team2_score > session.team1_score:
            session.winner_team = 'team2'
            session.is_completed = True
        
        session.save()
        
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
        return JsonResponse({
            'success': False, 
            'error': 'الجلسة غير موجودة أو غير نشطة'
        }, status=404)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False, 
            'error': 'بيانات JSON غير صحيحة'
        }, status=400)
    except Exception as e:
        logger.error(f'Error updating scores: {e}')
        return JsonResponse({
            'success': False, 
            'error': f'خطأ داخلي: {str(e)}'
        }, status=500)

@require_http_methods(["GET"])
def session_state(request):
    """جلب الحالة الحالية للجلسة - متاح للجميع"""
    session_id = request.GET.get('session_id')
    
    if not session_id:
        return JsonResponse({
            'success': False, 
            'error': 'معرف الجلسة مطلوب'
        }, status=400)
    
    try:
        # جلب الجلسة
        session = GameSession.objects.get(id=session_id, is_active=True)
        
        # التحقق من انتهاء الصلاحية
        if is_session_expired(session):
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True,
                'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)',
                'upgrade_message': 'للاستمتاع بجلسات غير محدودة، تصفح الحزم المدفوعة!'
            }, status=410)  # 410 Gone
        
        # جلب الحروف للجلسة
        arabic_letters = get_arabic_letters_for_session(session.package)
        
        # جلب تقدم اللعبة
        try:
            progress = LettersGameProgress.objects.get(session=session)
            cell_states = progress.cell_states or {}
            used_letters = progress.used_letters or []
        except LettersGameProgress.DoesNotExist:
            cell_states = {}
            used_letters = []
        
        # جلب المتسابقين
        contestants = []
        for contestant in session.contestants.all():
            contestants.append({
                'name': contestant.name,
                'team': contestant.team,
                'is_active': contestant.is_active,
                'joined_at': contestant.joined_at.isoformat()
            })
        
        # حساب الوقت المتبقي
        time_remaining = get_session_time_remaining(session)
        time_remaining_seconds = None
        if time_remaining:
            time_remaining_seconds = int(time_remaining.total_seconds())
        
        return JsonResponse({
            'success': True,
            'session_id': str(session.id),
            'team1_name': session.team1_name,
            'team2_name': session.team2_name,
            'team1_score': session.team1_score,
            'team2_score': session.team2_score,
            'is_active': session.is_active,
            'is_completed': session.is_completed,
            'winner_team': session.winner_team,
            'cell_states': cell_states,
            'used_letters': used_letters,
            'contestants': contestants,
            'arabic_letters': arabic_letters,  # الحروف الخاصة بالجلسة
            'package_info': {
                'name': f"{session.package.get_game_type_display()} - حزمة {session.package.package_number}",
                'is_free': session.package.is_free,
                'package_number': session.package.package_number
            },
            'time_remaining_seconds': time_remaining_seconds,
            'created_at': session.created_at.isoformat(),
            'anti_cheat_info': {
                'is_free_session': session.package.is_free,
                'session_type': 'مجانية' if session.package.is_free else 'مدفوعة'
            }
        })
        
    except GameSession.DoesNotExist:
        return JsonResponse({
            'success': False, 
            'error': 'الجلسة غير موجودة أو غير نشطة'
        }, status=404)
    except Exception as e:
        logger.error(f'Error fetching session state: {e}')
        return JsonResponse({
            'success': False, 
            'error': f'خطأ داخلي: {str(e)}'
        }, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def add_contestant(request):
    """إضافة متسابق جديد - متاح للجميع"""
    try:
        data = json.loads(request.body)
        session_id = data.get('session_id')
        name = data.get('name', '').strip()
        team = data.get('team')  # 'team1' أو 'team2'
        
        if not all([session_id, name, team]):
            return JsonResponse({
                'success': False, 
                'error': 'جميع المعاملات مطلوبة'
            }, status=400)
        
        if team not in ['team1', 'team2']:
            return JsonResponse({
                'success': False, 
                'error': 'الفريق يجب أن يكون team1 أو team2'
            }, status=400)
        
        if len(name) > 50:
            return JsonResponse({
                'success': False, 
                'error': 'اسم المتسابق طويل جداً'
            }, status=400)
        
        session = GameSession.objects.get(id=session_id, is_active=True)
        
        # التحقق من انتهاء صلاحية الجلسة
        if is_session_expired(session):
            return JsonResponse({
                'success': False,
                'error': 'انتهت صلاحية الجلسة',
                'session_expired': True,
                'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
            }, status=410)
        
        # التحقق من عدم تكرار الاسم
        if Contestant.objects.filter(session=session, name=name).exists():
            return JsonResponse({
                'success': False, 
                'error': 'اسم المتسابق موجود مسبقاً في هذه الجلسة'
            }, status=400)
        
        # إنشاء المتسابق
        contestant = Contestant.objects.create(
            session=session,
            name=name,
            team=team
        )
        
        logger.info(f'New contestant added: {name} to {team} in session {session_id} ({"FREE" if session.package.is_free else "PAID"})')
        
        return JsonResponse({
            'success': True,
            'message': 'تم إضافة المتسابق بنجاح',
            'contestant': {
                'name': contestant.name,
                'team': contestant.team,
                'team_display': contestant.get_team_display(),
                'joined_at': contestant.joined_at.isoformat()
            }
        })
        
    except GameSession.DoesNotExist:
        return JsonResponse({
            'success': False, 
            'error': 'الجلسة غير موجودة أو غير نشطة'
        }, status=404)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False, 
            'error': 'بيانات JSON غير صحيحة'
        }, status=400)
    except Exception as e:
        logger.error(f'Error adding contestant: {e}')
        return JsonResponse({
            'success': False, 
            'error': f'خطأ داخلي: {str(e)}'
        }, status=500)

# =============================================================================
# إضافة API خاص لإحصائيات نظام منع التلاعب
# =============================================================================

@login_required
@require_http_methods(["GET"])
def api_user_session_stats(request):
    """API لإحصائيات جلسات المستخدم - للمطورين والإحصائيات"""
    try:
        user = request.user
        
        # إحصائيات شاملة لجميع الألعاب
        stats = {}
        
        for game_type, game_name in [('letters', 'خلية الحروف'), ('images', 'تحدي الصور'), ('quiz', 'سؤال وجواب')]:
            # الجلسات المجانية
            free_sessions = GameSession.objects.filter(
                host=user,
                game_type=game_type,
                package__is_free=True
            )
            
            # الجلسات المدفوعة
            paid_sessions = GameSession.objects.filter(
                host=user,
                game_type=game_type,
                package__is_free=False
            )
            
            # التحقق من الأهلية
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
                'purchased_packages': UserPurchase.objects.filter(
                    user=user,
                    package__game_type=game_type
                ).count()
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
        return JsonResponse({
            'success': False,
            'error': 'حدث خطأ في جلب الإحصائيات'
        }, status=500)
    

# إضافة هذه الدوال في نهاية ملف games/views.py

@require_http_methods(["GET"])
def api_session_expiry_info(request):
    """API لجلب معلومات شاملة عن صلاحية الجلسة"""
    session_id = request.GET.get('session_id')
    
    if not session_id:
        return JsonResponse({
            'success': False, 
            'error': 'معرف الجلسة مطلوب'
        }, status=400)
    
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
        return JsonResponse({
            'success': False, 
            'error': 'الجلسة غير موجودة'
        }, status=404)
    except Exception as e:
        logger.error(f'Session expiry info API error: {e}')
        return JsonResponse({
            'success': False, 
            'error': f'خطأ داخلي: {str(e)}'
        }, status=500)

@login_required
@require_http_methods(["GET"])
def api_user_session_stats(request):
    """API لإحصائيات جلسات المستخدم - للمطورين والإحصائيات"""
    try:
        user = request.user
        
        # إحصائيات شاملة لجميع الألعاب
        stats = {}
        
        for game_type, game_name in [('letters', 'خلية الحروف'), ('images', 'تحدي الصور'), ('quiz', 'سؤال وجواب')]:
            # الجلسات المجانية
            free_sessions = GameSession.objects.filter(
                host=user,
                game_type=game_type,
                package__is_free=True
            )
            
            # الجلسات المدفوعة
            paid_sessions = GameSession.objects.filter(
                host=user,
                game_type=game_type,
                package__is_free=False
            )
            
            # التحقق من الأهلية
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
                'purchased_packages': UserPurchase.objects.filter(
                    user=user,
                    package__game_type=game_type
                ).count()
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
        return JsonResponse({
            'success': False,
            'error': 'حدث خطأ في جلب الإحصائيات'
        }, status=500)