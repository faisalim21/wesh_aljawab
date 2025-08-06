# games/views.py - مع نظام الحروف العشوائية
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

def is_session_expired(session):
    """
    التحقق من انتهاء صلاحية الجلسة
    - الحزم المجانية: ساعة واحدة
    - الحزم المدفوعة: لا تنتهي صلاحيتها
    """
    if not session.package.is_free:
        # الحزم المدفوعة لا تنتهي صلاحيتها
        return False
    
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
    حساب الوقت المتبقي للجلسة المجانية
    """
    if not session.package.is_free:
        return None
    
    expiry_time = session.created_at + timedelta(hours=1)
    current_time = timezone.now()
    
    if current_time >= expiry_time:
        return timedelta(0)
    
    return expiry_time - current_time

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
    if request.user.is_authenticated:
        user_purchases = UserPurchase.objects.filter(
            user=request.user,
            package__game_type='letters'
        ).values_list('package_id', flat=True)
    
    return render(request, 'games/letters/home.html', {
        'free_package': free_package,
        'paid_packages': paid_packages,
        'user_purchases': user_purchases,
    })

def create_letters_session(request):
    """إنشاء جلسة لعب جديدة - يتطلب تسجيل دخول فقط للمقدم"""
    if request.method == 'POST':
        package_id = request.POST.get('package_id')
        
        # التحقق من وجود الحزمة
        package = get_object_or_404(GamePackage, id=package_id, game_type='letters')
        
        # إذا كانت الحزمة مجانية، السماح للجميع
        if package.is_free:
            # إذا لم يكن مسجل دخول، توجيهه للتسجيل
            if not request.user.is_authenticated:
                messages.info(request, 'يرجى تسجيل الدخول لإنشاء جلسة لعب')
                return redirect(f'/accounts/login/?next={request.path}')
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
                    description=f'إنشاء جلسة خلية الحروف - {package.get_game_type_display()}',
                    game_type='letters',
                    session_id=str(session.id)
                )
            
            messages.success(request, f'تم إنشاء الجلسة بنجاح! 🎉')
            logger.info(f'New letters session created: {session.id} by {request.user.username if request.user.is_authenticated else "anonymous"}')
            
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
        messages.error(request, '⏰ انتهت صلاحية الجلسة المجانية (ساعة واحدة). يرجى إنشاء جلسة جديدة.')
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

    return render(request, 'games/letters/letters_session.html', {
        'session': session,
        'arabic_letters': arabic_letters,
        'arabic_letters_json': arabic_letters_json,
        'questions_by_letter': questions_by_letter,
        'time_remaining': time_remaining,
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
            'session_type': 'مجانية'
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
    })

def letters_contestants(request, contestants_link):
    """صفحة المتسابقين لخلية الحروف - متاحة للجميع بدون تسجيل دخول"""
    session = get_object_or_404(GameSession, contestants_link=contestants_link, is_active=True)
    
    # التحقق من انتهاء صلاحية الجلسة
    if is_session_expired(session):
        return render(request, 'games/session_expired.html', {
            'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)',
            'session_type': 'مجانية'
        })
    
    # حساب الوقت المتبقي
    time_remaining = get_session_time_remaining(session)
    
    logger.info(f'Contestants page accessed for session: {session.id}')
    
    return render(request, 'games/letters/letters_contestants.html', {
        'session': session,
        'time_remaining': time_remaining,
    })

# باقي الـ views للألعاب الأخرى (مؤقتة)
def images_game_home(request):
    """صفحة لعبة تحدي الصور - قادمة قريباً"""
    return render(request, 'games/coming_soon.html', {'game_name': 'تحدي الصور'})

def quiz_game_home(request):
    """صفحة لعبة السؤال والجواب - قادمة قريباً"""
    return render(request, 'games/coming_soon.html', {'game_name': 'سؤال وجواب'})

# =============================================================================
# API ENDPOINTS - محسنة مع دعم الحروف العشوائية
# =============================================================================

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
                'message': 'انتهت صلاحية الجلسة المجانية (ساعة واحدة)'
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
        
        logger.info(f'New contestant added: {name} to {team} in session {session_id}')
        
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