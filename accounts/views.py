# accounts/views.py - النسخة المحدثة والمحسنة
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from datetime import timedelta
import json
import logging

from .models import UserProfile, UserActivity, UserPreferences
from games.models import UserPurchase, GameSession

# إعداد logger
logger = logging.getLogger('accounts')

def login_view(request):
    """تسجيل الدخول"""
    if request.user.is_authenticated:
        return redirect('/')  # توجيه للصفحة الرئيسية
        
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        remember_me = request.POST.get('remember_me')
        
        # التحقق من البيانات
        if not username or not password:
            messages.error(request, 'يرجى ملء جميع الحقول المطلوبة')
            return render(request, 'accounts/login.html')
        
        # محاولة المصادقة
        try:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                if user.is_active:
                    login(request, user)
                    
                    # إعداد مدة الجلسة
                    if not remember_me:
                        request.session.set_expiry(0)  # إنهاء عند إغلاق المتصفح
                    else:
                        request.session.set_expiry(604800)  # أسبوع واحد
                    
                    # تسجيل النشاط
                    try:
                        UserActivity.objects.create(
                            user=user,
                            activity_type='login',
                            description=f'تسجيل دخول من {request.META.get("REMOTE_ADDR", "غير معروف")}'
                        )
                        logger.info(f'User {username} logged in successfully')
                    except Exception as e:
                        logger.error(f'Error creating login activity: {e}')
                    
                    messages.success(request, f'أهلاً وسهلاً {user.profile.display_name}! 🎉')
                    
                    # إعادة توجيه للصفحة المطلوبة
                    next_page = request.GET.get('next', '/')
                    return redirect(next_page)
                else:
                    messages.error(request, 'هذا الحساب معطل، يرجى التواصل مع الإدارة')
            else:
                messages.error(request, 'اسم المستخدم أو كلمة المرور غير صحيحة')
                logger.warning(f'Failed login attempt for username: {username}')
                
        except Exception as e:
            logger.error(f'Login error: {e}')
            messages.error(request, 'حدث خطأ أثناء تسجيل الدخول، يرجى المحاولة لاحقاً')
    
    return render(request, 'accounts/login.html')

def register_view(request):
    """تسجيل جديد"""
    if request.user.is_authenticated:
        return redirect('/')
        
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        
        # جمع البيانات
        first_name = request.POST.get('first_name', '').strip()
        email = request.POST.get('email', '').strip()
        host_name = request.POST.get('host_name', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()
        terms_agreement = request.POST.get('terms_agreement')
        
        # التحقق من الموافقة على الشروط
        if not terms_agreement:
            messages.error(request, 'يجب الموافقة على شروط الاستخدام وسياسة الخصوصية')
            return render(request, 'accounts/register.html', {'form': form})
        
        # التحقق من البريد الإلكتروني
        if email and User.objects.filter(email=email).exists():
            messages.error(request, 'البريد الإلكتروني مستخدم مسبقاً')
            return render(request, 'accounts/register.html', {'form': form})
        
        # التحقق من رقم الهاتف
        if phone_number and not phone_number.startswith('05'):
            messages.error(request, 'رقم الهاتف يجب أن يبدأ بـ 05')
            return render(request, 'accounts/register.html', {'form': form})
        
        if form.is_valid():
            try:
                # إنشاء المستخدم
                user = form.save(commit=False)
                user.first_name = first_name
                user.email = email
                user.save()
                
                # تحديث الملف الشخصي
                profile = user.profile
                profile.host_name = host_name
                profile.phone_number = phone_number
                profile.save()
                
                # إنشاء تفضيلات افتراضية
                UserPreferences.objects.get_or_create(user=user)
                
                # تسجيل النشاط
                try:
                    UserActivity.objects.create(
                        user=user,
                        activity_type='profile_updated',
                        description='إنشاء حساب جديد على المنصة'
                    )
                    logger.info(f'New user registered: {user.username}')
                except Exception as e:
                    logger.error(f'Error creating registration activity: {e}')
                
                messages.success(request, 'مرحباً بك! تم إنشاء حسابك بنجاح 🎊')
                
                # تسجيل دخول تلقائي
                login(request, user)
                return redirect('/')
                
            except Exception as e:
                logger.error(f'Registration error: {e}')
                messages.error(request, 'حدث خطأ أثناء إنشاء الحساب، يرجى المحاولة لاحقاً')
        else:
            # عرض أخطاء النموذج
            for field, errors in form.errors.items():
                for error in errors:
                    if 'password' in field.lower():
                        if 'too similar' in error:
                            messages.error(request, 'كلمة المرور مشابهة جداً لمعلوماتك الشخصية')
                        elif 'too short' in error:
                            messages.error(request, 'كلمة المرور قصيرة جداً، يجب أن تحتوي على 8 أحرف على الأقل')
                        elif 'too common' in error:
                            messages.error(request, 'كلمة المرور شائعة جداً، اختر كلمة مرور أقوى')
                        elif 'entirely numeric' in error:
                            messages.error(request, 'كلمة المرور لا يمكن أن تكون أرقام فقط')
                        else:
                            messages.error(request, f'خطأ في كلمة المرور: {error}')
                    elif 'username' in field.lower():
                        if 'already exists' in error:
                            messages.error(request, 'اسم المستخدم مستخدم مسبقاً')
                        else:
                            messages.error(request, f'خطأ في اسم المستخدم: {error}')
                    else:
                        messages.error(request, f'{error}')
    else:
        form = UserCreationForm()
    
    return render(request, 'accounts/register.html', {'form': form})

@login_required
def logout_view(request):
    """تسجيل الخروج"""
    user_name = request.user.profile.display_name
    
    try:
        # تسجيل النشاط قبل الخروج
        UserActivity.objects.create(
            user=request.user,
            activity_type='login',
            description='تسجيل خروج من المنصة'
        )
        logger.info(f'User {request.user.username} logged out')
    except Exception as e:
        logger.error(f'Error creating logout activity: {e}')
    
    logout(request)
    messages.success(request, f'وداعاً {user_name}! نراك قريباً 👋')
    return redirect('/')

@login_required
def profile_view(request):
    """الملف الشخصي"""
    user = request.user
    profile = user.profile
    
    # إحصائيات شاملة
    try:
        stats = {
            'total_purchases': UserPurchase.objects.filter(user=user).count(),
            'completed_games': UserPurchase.objects.filter(user=user, is_completed=True).count(),
            'hosted_sessions': GameSession.objects.filter(host=user).count(),
            'active_sessions': GameSession.objects.filter(host=user, is_active=True).count(),
            'completion_rate': profile.get_completion_rate(),
        }
    except Exception as e:
        logger.error(f'Error calculating user stats: {e}')
        stats = {
            'total_purchases': 0,
            'completed_games': 0,
            'hosted_sessions': 0,
            'active_sessions': 0,
            'completion_rate': 0,
        }
    
    # الأنشطة الأخيرة مع التصفح
    activities_list = UserActivity.objects.filter(user=user).order_by('-created_at')
    paginator = Paginator(activities_list, 10)
    page_number = request.GET.get('page')
    activities = paginator.get_page(page_number)
    
    # الألعاب المفضلة
    try:
        favorite_games = UserPurchase.objects.filter(
            user=user
        ).values('package__game_type').annotate(
            count=Count('package__game_type')
        ).order_by('-count')
    except Exception as e:
        logger.error(f'Error getting favorite games: {e}')
        favorite_games = []
    
    if request.method == 'POST':
        try:
            # تحديث معلومات المستخدم
            user.first_name = request.POST.get('first_name', '').strip()
            email = request.POST.get('email', '').strip()
            
            # التحقق من تكرار البريد الإلكتروني
            if email and User.objects.filter(email=email).exclude(id=user.id).exists():
                messages.error(request, 'البريد الإلكتروني مستخدم من مستخدم آخر')
                return redirect('accounts:profile')
            
            user.email = email
            user.save()
            
            # تحديث الملف الشخصي
            profile.host_name = request.POST.get('host_name', '').strip()
            phone_number = request.POST.get('phone_number', '').strip()
            
            # التحقق من رقم الهاتف
            if phone_number and not phone_number.startswith('05'):
                messages.error(request, 'رقم الهاتف يجب أن يبدأ بـ 05')
                return redirect('accounts:profile')
            
            profile.phone_number = phone_number
            profile.notifications_enabled = 'notifications_enabled' in request.POST
            profile.email_notifications = 'email_notifications' in request.POST
            profile.save()
            
            # تسجيل النشاط
            UserActivity.objects.create(
                user=user,
                activity_type='profile_updated',
                description='تحديث معلومات الملف الشخصي'
            )
            
            messages.success(request, 'تم حفظ التغييرات بنجاح! ✅')
            logger.info(f'User {user.username} updated profile')
            
        except Exception as e:
            logger.error(f'Profile update error: {e}')
            messages.error(request, 'حدث خطأ أثناء الحفظ، يرجى المحاولة مرة أخرى')
        
        return redirect('accounts:profile')
    
    return render(request, 'accounts/profile.html', {
        'stats': stats,
        'activities': activities,
        'favorite_games': favorite_games,
    })

@login_required
def preferences_view(request):
    """إعدادات التفضيلات"""
    preferences, created = UserPreferences.objects.get_or_create(user=request.user)
    
    if request.method == 'POST':
        try:
            # تحديث تفضيلات الألعاب
            preferences.default_team1_name = request.POST.get('team1_name', 'الفريق الأخضر')
            preferences.default_team2_name = request.POST.get('team2_name', 'الفريق البرتقالي')
            preferences.auto_start_timer = 'auto_start_timer' in request.POST
            preferences.show_answers_immediately = 'show_answers_immediately' in request.POST
            
            # تفضيلات الصوت والعرض
            preferences.sound_enabled = 'sound_enabled' in request.POST
            volume_level = request.POST.get('volume_level', '50')
            try:
                preferences.volume_level = max(0, min(100, int(volume_level)))
            except ValueError:
                preferences.volume_level = 50
            
            preferences.theme_preference = request.POST.get('theme_preference', 'light')
            
            # تفضيلات التحكم
            preferences.quick_mode_enabled = 'quick_mode_enabled' in request.POST
            preferences.show_statistics = 'show_statistics' in request.POST
            
            preferences.save()
            
            # تسجيل النشاط
            UserActivity.objects.create(
                user=request.user,
                activity_type='profile_updated',
                description='تحديث تفضيلات المستخدم'
            )
            
            messages.success(request, 'تم حفظ تفضيلاتك بنجاح! ⚙️')
            logger.info(f'User {request.user.username} updated preferences')
            
        except ValueError:
            messages.error(request, 'قيم غير صحيحة، يرجى التحقق من البيانات')
        except Exception as e:
            logger.error(f'Preferences update error: {e}')
            messages.error(request, 'حدث خطأ أثناء الحفظ')
        
        return redirect('accounts:preferences')
    
    return render(request, 'accounts/preferences.html', {
        'preferences': preferences,
    })

@login_required
def dashboard_view(request):
    """لوحة تحكم المستخدم"""
    user = request.user
    
    try:
        # الجلسات النشطة
        active_sessions = GameSession.objects.filter(
            host=user,
            is_active=True
        ).order_by('-created_at')[:5]  # أحدث 5 جلسات
        
        # آخر الأنشطة
        recent_activities = UserActivity.objects.filter(
            user=user
        ).order_by('-created_at')[:10]
        
        # إحصائيات الأسبوع الماضي
        week_ago = timezone.now() - timedelta(days=7)
        weekly_stats = {
            'games_this_week': GameSession.objects.filter(
                host=user,
                created_at__gte=week_ago
            ).count(),
            'purchases_this_week': UserPurchase.objects.filter(
                user=user,
                purchase_date__gte=week_ago
            ).count(),
        }
        
        # إحصائيات الشهر الماضي
        month_ago = timezone.now() - timedelta(days=30)
        monthly_stats = {
            'games_this_month': GameSession.objects.filter(
                host=user,
                created_at__gte=month_ago
            ).count(),
            'purchases_this_month': UserPurchase.objects.filter(
                user=user,
                purchase_date__gte=month_ago
            ).count(),
        }
        
    except Exception as e:
        logger.error(f'Dashboard data error: {e}')
        active_sessions = []
        recent_activities = []
        weekly_stats = {'games_this_week': 0, 'purchases_this_week': 0}
        monthly_stats = {'games_this_month': 0, 'purchases_this_month': 0}
    
    return render(request, 'accounts/dashboard.html', {
        'active_sessions': active_sessions,
        'recent_activities': recent_activities,
        'weekly_stats': weekly_stats,
        'monthly_stats': monthly_stats,
    })

@login_required
def delete_account_view(request):
    """حذف الحساب"""
    if request.method == 'POST':
        password = request.POST.get('password')
        confirm = request.POST.get('confirm_delete')
        
        if confirm == 'DELETE' and request.user.check_password(password):
            user = request.user
            username = user.username
            
            try:
                # تسجيل النشاط قبل الحذف
                UserActivity.objects.create(
                    user=user,
                    activity_type='profile_updated',
                    description='حذف الحساب نهائياً'
                )
                
                logger.warning(f'User {username} deleted their account')
                
                # حذف الحساب
                user.delete()
                
                messages.success(request, f'تم حذف الحساب {username} نهائياً')
                return redirect('/')
                
            except Exception as e:
                logger.error(f'Account deletion error: {e}')
                messages.error(request, 'حدث خطأ أثناء حذف الحساب')
        else:
            messages.error(request, 'كلمة المرور غير صحيحة أو لم تؤكد الحذف')
    
    return render(request, 'accounts/delete_account.html')

# API Views
@login_required
@require_http_methods(["GET"])
def api_user_stats(request):
    """API لإحصائيات المستخدم"""
    try:
        user = request.user
        profile = user.profile
        
        data = {
            'success': True,
            'user_id': user.id,
            'username': user.username,
            'display_name': profile.display_name,
            'total_games': profile.total_games_hosted,
            'completion_rate': profile.get_completion_rate(),
            'total_purchases': profile.get_total_purchases(),
            'account_type': profile.account_type,
            'member_since': user.date_joined.strftime('%Y-%m-%d'),
            'last_login': user.last_login.strftime('%Y-%m-%d %H:%M:%S') if user.last_login else None,
            'is_host': profile.is_host,
            'favorite_game': profile.favorite_game,
        }
        
        logger.info(f'User stats API called by {user.username}')
        return JsonResponse(data)
        
    except Exception as e:
        logger.error(f'User stats API error: {e}')
        return JsonResponse({
            'success': False,
            'error': 'حدث خطأ في جلب البيانات'
        }, status=500)

@login_required
@require_http_methods(["POST"])
@csrf_exempt
def api_update_preferences(request):
    """API لتحديث التفضيلات"""
    try:
        data = json.loads(request.body)
        preferences, created = UserPreferences.objects.get_or_create(user=request.user)
        
        # تحديث التفضيلات
        if 'theme_preference' in data:
            preferences.theme_preference = data['theme_preference']
        if 'sound_enabled' in data:
            preferences.sound_enabled = data['sound_enabled']
        if 'volume_level' in data:
            preferences.volume_level = max(0, min(100, int(data['volume_level'])))
        if 'notifications_enabled' in data:
            request.user.profile.notifications_enabled = data['notifications_enabled']
            request.user.profile.save()
        
        preferences.save()
        
        logger.info(f'User {request.user.username} updated preferences via API')
        
        return JsonResponse({
            'success': True,
            'message': 'تم حفظ التفضيلات بنجاح'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'بيانات JSON غير صحيحة'
        }, status=400)
    except Exception as e:
        logger.error(f'Update preferences API error: {e}')
        return JsonResponse({
            'success': False,
            'error': 'حدث خطأ في تحديث التفضيلات'
        }, status=500)

@login_required
@require_http_methods(["GET"])
def api_user_activities(request):
    """API لجلب أنشطة المستخدم"""
    try:
        page = int(request.GET.get('page', 1))
        limit = min(int(request.GET.get('limit', 10)), 50)  # حد أقصى 50
        
        activities = UserActivity.objects.filter(
            user=request.user
        ).order_by('-created_at')
        
        paginator = Paginator(activities, limit)
        activities_page = paginator.get_page(page)
        
        activities_data = []
        for activity in activities_page:
            activities_data.append({
                'id': activity.id,
                'type': activity.activity_type,
                'type_display': activity.get_activity_type_display(),
                'description': activity.description,
                'game_type': activity.game_type,
                'created_at': activity.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            })
        
        return JsonResponse({
            'success': True,
            'activities': activities_data,
            'pagination': {
                'current_page': activities_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': activities_page.has_next(),
                'has_previous': activities_page.has_previous(),
            }
        })
        
    except Exception as e:
        logger.error(f'User activities API error: {e}')
        return JsonResponse({
            'success': False,
            'error': 'حدث خطأ في جلب الأنشطة'
        }, status=500)