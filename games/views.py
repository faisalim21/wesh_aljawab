# games/views.py - الملف الكامل مع إصلاح بسيط
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
import json
import uuid
from .models import (
    GamePackage, GameSession, UserPurchase, LettersGameProgress, 
    LettersGameQuestion, Contestant
)

def games_home(request):
    """الصفحة الرئيسية للألعاب"""
    return render(request, 'home.html', {  # إصلاح المسار هنا
        'letters_available': True,
        'images_available': False,  # سنفعلها لاحقاً
        'quiz_available': False,    # سنفعلها لاحقاً
    })

def letters_game_home(request):
    """صفحة لعبة خلية الحروف"""
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

@login_required
def create_letters_session(request):
    """إنشاء جلسة لعب جديدة لخلية الحروف"""
    if request.method == 'POST':
        package_id = request.POST.get('package_id')
        team1_name = request.POST.get('team1_name', 'الفريق الأخضر')
        team2_name = request.POST.get('team2_name', 'الفريق البرتقالي')
        
        # التحقق من وجود الحزمة والصلاحية
        package = get_object_or_404(GamePackage, id=package_id, game_type='letters')
        
        # التحقق من أن المستخدم يملك هذه الحزمة
        if not package.is_free:
            purchase = UserPurchase.objects.filter(
                user=request.user,
                package=package
            ).first()
            if not purchase:
                messages.error(request, 'يجب شراء هذه الحزمة أولاً')
                return redirect('games:letters_home')
        
        # إنشاء جلسة جديدة
        session = GameSession.objects.create(
            host=request.user,
            package=package,
            game_type='letters',
            team1_name=team1_name,
            team2_name=team2_name,
        )
        
        # إنشاء تقدم اللعبة
        LettersGameProgress.objects.create(
            session=session,
            cell_states={},
            used_letters=[]
        )
        
        messages.success(request, 'تم إنشاء الجلسة بنجاح!')
        return redirect('games:letters_session', session_id=session.id)
    
    return redirect('games:letters_home')

@login_required
def letters_host(request, session_id):
    """صفحة المقدم لخلية الحروف"""
    session = get_object_or_404(GameSession, id=session_id, host=request.user)
    
    # جلب الأسئلة للحزمة
    questions = session.package.letters_questions.all().order_by('letter', 'question_type')
    
    # تنظيم الأسئلة حسب الحرف
    questions_by_letter = {}
    for question in questions:
        if question.letter not in questions_by_letter:
            questions_by_letter[question.letter] = {}
        questions_by_letter[question.letter][question.question_type] = question
    
    # الحروف العربية (25 حرف)
    arabic_letters = [
        'أ', 'ب', 'ت', 'ث', 'ج',
        'ح', 'خ', 'د', 'ذ', 'ر',
        'ز', 'س', 'ش', 'ص', 'ض',
        'ط', 'ظ', 'ع', 'غ', 'ف',
        'ق', 'ك', 'ل', 'م', 'ن'
    ]
    
    return render(request, 'games/letters/host.html', {
        'session': session,
        'questions_by_letter': questions_by_letter,
        'arabic_letters': arabic_letters,
        'display_url': request.build_absolute_uri(
            reverse('games:letters_display', args=[session.display_link])
        ),
        'contestants_url': request.build_absolute_uri(
            reverse('games:letters_contestants', args=[session.contestants_link])
        ),
    })

def letters_display(request, display_link):
    """شاشة العرض لخلية الحروف"""
    session = get_object_or_404(GameSession, display_link=display_link, is_active=True)
    
    # الحروف العربية مرتبة في شكل الخلية
    arabic_letters = [
        'أ', 'ب', 'ت', 'ث', 'ج',
        'ح', 'خ', 'د', 'ذ', 'ر',
        'ز', 'س', 'ش', 'ص', 'ض',
        'ط', 'ظ', 'ع', 'غ', 'ف',
        'ق', 'ك', 'ل', 'م', 'ن'
    ]
    
    return render(request, 'games/letters/letters_display.html', {
        'session': session,
        'arabic_letters': arabic_letters,
    })

def letters_contestants(request, contestants_link):
    """صفحة المتسابقين لخلية الحروف"""
    session = get_object_or_404(GameSession, contestants_link=contestants_link, is_active=True)
    
    return render(request, 'games/letters/letters_contestants.html', {
        'session': session,
    })

@login_required
def letters_session(request, session_id):
    """صفحة جلسة خلية الحروف"""
    session = get_object_or_404(GameSession, id=session_id, host=request.user)

    arabic_letters = [
        'أ', 'ب', 'ت', 'ث', 'ج', 'ح', 'خ', 'د', 'ذ', 'ر',
        'ز', 'س', 'ش', 'ص', 'ض', 'ط', 'ظ', 'ع', 'غ', 'ف',
        'ق', 'ك', 'ل', 'م', 'ن'
    ]

    questions = session.package.letters_questions.all().order_by('letter', 'question_type')
    questions_by_letter = {}
    for q in questions:
        questions_by_letter.setdefault(q.letter, {})[q.question_type] = q

    return render(request, 'games/letters/letters_session.html', {
        'session': session,
        'arabic_letters': arabic_letters,
        'questions_by_letter': questions_by_letter,
        'display_url': request.build_absolute_uri(reverse('games:letters_display', args=[session.display_link])),
        'contestants_url': request.build_absolute_uri(reverse('games:letters_contestants', args=[session.contestants_link])),
    })

# Views للألعاب الأخرى (مؤقتة)
def images_game_home(request):
    return render(request, 'games/coming_soon.html', {'game_name': 'تحدي الصور'})

def quiz_game_home(request):
    return render(request, 'games/coming_soon.html', {'game_name': 'سؤال وجواب'})

# =============================================================================
# API ENDPOINTS - الـ APIs الأساسية
# =============================================================================

@require_http_methods(["GET"])
def get_question(request):
    """جلب الأسئلة للحرف المحدد من السيرفر"""
    letter = request.GET.get('letter')
    session_id = request.GET.get('session_id')
    
    if not letter or not session_id:
        return JsonResponse({'success': False, 'error': 'المعاملات مطلوبة'}, status=400)
    
    try:
        # جلب الجلسة
        session = GameSession.objects.get(id=session_id)
        
        # جلب الأسئلة الثلاثة للحرف
        questions = {}
        
        # السؤال الرئيسي
        try:
            main_q = LettersGameQuestion.objects.get(
                package=session.package, 
                letter=letter, 
                question_type='main'
            )
            questions['main'] = {
                'question': main_q.question, 
                'answer': main_q.answer,
                'category': main_q.category
            }
        except LettersGameQuestion.DoesNotExist:
            questions['main'] = {
                'question': f'سؤال تجريبي: شيء يبدأ بحرف {letter}؟',
                'answer': 'إجابة تجريبية',
                'category': 'عام'
            }
        
        # السؤال البديل الأول
        try:
            alt1_q = LettersGameQuestion.objects.get(
                package=session.package, 
                letter=letter, 
                question_type='alt1'
            )
            questions['alt1'] = {
                'question': alt1_q.question, 
                'answer': alt1_q.answer,
                'category': alt1_q.category
            }
        except LettersGameQuestion.DoesNotExist:
            questions['alt1'] = {
                'question': f'سؤال بديل أول: مهنة تبدأ بحرف {letter}؟',
                'answer': 'مهنة تجريبية',
                'category': 'مهن'
            }
            
        # السؤال البديل الثاني
        try:
            alt2_q = LettersGameQuestion.objects.get(
                package=session.package, 
                letter=letter, 
                question_type='alt2'
            )
            questions['alt2'] = {
                'question': alt2_q.question, 
                'answer': alt2_q.answer,
                'category': alt2_q.category
            }
        except LettersGameQuestion.DoesNotExist:
            questions['alt2'] = {
                'question': f'سؤال بديل ثاني: حيوان يبدأ بحرف {letter}؟',
                'answer': 'حيوان تجريبي', 
                'category': 'حيوانات'
            }
        
        return JsonResponse({
            'success': True,
            'questions': questions,
            'letter': letter
        })
        
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def update_cell_state(request):
    """تحديث حالة الخلية (أبيض/أخضر/برتقالي)"""
    try:
        data = json.loads(request.body)
        session_id = data.get('session_id')
        letter = data.get('letter')
        state = data.get('state')  # 'normal', 'team1', 'team2'
        
        if not all([session_id, letter, state]):
            return JsonResponse({'success': False, 'error': 'جميع المعاملات مطلوبة'}, status=400)
        
        # جلب الجلسة
        session = GameSession.objects.get(id=session_id)
        
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
        
        return JsonResponse({
            'success': True,
            'message': 'تم تحديث حالة الخلية',
            'letter': letter,
            'state': state
        })
        
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة'}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'بيانات JSON غير صحيحة'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def update_scores(request):
    """تحديث نقاط الفرق"""
    try:
        data = json.loads(request.body)
        session_id = data.get('session_id')
        team1_score = data.get('team1_score', 0)
        team2_score = data.get('team2_score', 0)
        
        if not session_id:
            return JsonResponse({'success': False, 'error': 'معرف الجلسة مطلوب'}, status=400)
        
        # جلب وتحديث الجلسة
        session = GameSession.objects.get(id=session_id)
        session.team1_score = max(0, int(team1_score))
        session.team2_score = max(0, int(team2_score))
        
        # تحديد الفائز إذا وصل أحد الفرق لنقاط معينة
        winning_score = 10  # يمكن تعديلها حسب قواعد اللعبة
        
        if session.team1_score >= winning_score:
            session.winner_team = 'team1'
            session.is_completed = True
        elif session.team2_score >= winning_score:
            session.winner_team = 'team2'
            session.is_completed = True
        
        session.save()
        
        return JsonResponse({
            'success': True,
            'message': 'تم تحديث النقاط',
            'team1_score': session.team1_score,
            'team2_score': session.team2_score,
            'winner': session.winner_team,
            'is_completed': session.is_completed
        })
        
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة'}, status=404)
    except ValueError:
        return JsonResponse({'success': False, 'error': 'قيم النقاط يجب أن تكون أرقام'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)

@require_http_methods(["GET"])
def session_state(request):
    """جلب الحالة الحالية للجلسة"""
    session_id = request.GET.get('session_id')
    
    if not session_id:
        return JsonResponse({'success': False, 'error': 'معرف الجلسة مطلوب'}, status=400)
    
    try:
        # جلب الجلسة
        session = GameSession.objects.get(id=session_id)
        
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
                'is_active': contestant.is_active
            })
        
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
            'package_name': f"{session.package.get_game_type_display()} - حزمة {session.package.package_number}",
            'created_at': session.created_at.isoformat(),
        })
        
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'خطأ داخلي: {str(e)}'}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def add_contestant(request):
    """إضافة متسابق جديد"""
    try:
        data = json.loads(request.body)
        session_id = data.get('session_id')
        name = data.get('name', '').strip()
        team = data.get('team')  # 'team1' أو 'team2'
        
        if not all([session_id, name, team]):
            return JsonResponse({'success': False, 'error': 'جميع المعاملات مطلوبة'}, status=400)
        
        if team not in ['team1', 'team2']:
            return JsonResponse({'success': False, 'error': 'الفريق يجب أن يكون team1 أو team2'}, status=400)
        
        session = GameSession.objects.get(id=session_id)
        
        # إنشاء المتسابق
        contestant = Contestant.objects.create(
            session=session,
            name=name,
            team=team
        )
        
        return JsonResponse({
            'success': True,
            'message': 'تم إضافة المتسابق',
            'contestant': {
                'name': contestant.name,
                'team': contestant.team,
                'joined_at': contestant.joined_at.isoformat()
            }
        })
        
    except GameSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'الجلسة غير موجودة'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'خطأ: {str(e)}'}, status=500)