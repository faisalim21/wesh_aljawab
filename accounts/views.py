# accounts/views.py - النسخة المحسنة
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
from datetime import timedelta
from .models import UserProfile, UserActivity, UserPreferences
from games.models import UserPurchase, GameSession

def login_view(request):
    """تسجيل الدخول"""
    if request.user.is_authenticated:
        return redirect('games:home')
        
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        remember_me = request.POST.get('remember_me')
        
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            
            # إعداد مدة الجلسة
            if not remember_me:
                request.session.set_expiry(0)  # إنهاء عند إغلاق المتصفح
            
            # تسجيل النشاط
            UserActivity.objects.create(
                user=user,
                activity_type='login',
                description=f'تسجيل دخول من {request.META.get("REMOTE_ADDR", "غير معروف")}'
            )
            
            messages.success(request, f'أهلاً وسهلاً {user.profile.display_name}! 🎉')
            
            # إعادة توجيه للصفحة المطلوبة
            next_page = request.GET.get('next', 'games:home')
            return redirect(next_page)
        else:
            messages.error(request, 'اسم المستخدم أو كلمة المرور غير صحيحة')
    
    return render(request, 'accounts/login.html')

def register_view(request):
    """تسجيل جديد"""
    if request.user.is_authenticated:
        return redirect('games:home')
        
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            # التحقق من عدم تكرار البريد الإلكتروني
            email = request.POST.get('email', '').strip()
            if email and User.objects.filter(email=email).exists():
                messages.error(request, 'البريد الإلكتروني مستخدم مسبقاً')
                return render(request, 'accounts/register.html', {'form': form})
            
            # إنشاء المستخدم
            user = form.save()
            user.first_name = request.POST.get('first_name', '').strip()
            user.email = email
            user.save()
            
            # تحديث الملف الشخصي
            profile = user.profile
            profile.host_name = request.POST.get('host_name', '').strip()
            profile.phone_number = request.POST.get('phone_number', '').strip()
            profile.save()
            
            # إنشاء تفضيلات افتراضية
            UserPreferences.objects.create(user=user)
            
            # تسجيل النشاط
            UserActivity.objects.create(
                user=user,
                activity_type='profile_updated',
                description='إنشاء حساب جديد على المنصة'
            )
            
            messages.success(request, 'مرحباً بك! تم إنشاء حسابك بنجاح 🎊')
            login(request, user)
            return redirect('games:home')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{error}')
    else:
        form = UserCreationForm()
    
    return render(request, 'accounts/register.html', {'form': form})

@login_required
def logout_view(request):
    """تسجيل الخروج"""
    user_name = request.user.profile.display_name
    logout(request)
    messages.success(request, f'وداعاً {user_name}! نراك قريباً 👋')
    return redirect('home')

@login_required
def profile_view(request):
    """الملف الشخصي"""
    user = request.user
    profile = user.profile
    
    # إحصائيات شاملة
    stats = {
        'total_purchases': UserPurchase.objects.filter(user=user).count(),
        'completed_games': UserPurchase.objects.filter(user=user, is_completed=True).count(),
        'hosted_sessions': GameSession.objects.filter(host=user).count(),
        'active_sessions': GameSession.objects.filter(host=user, is_active=True).count(),
        'completion_rate': profile.get_completion_rate(),
    }
    
    # الأنشطة الأخيرة مع التصفح
    activities_list = UserActivity.objects.filter(user=user).order_by('-created_at')
    paginator = Paginator(activities_list, 10)
    page_number = request.GET.get('page')
    activities = paginator.get_page(page_number)
    
    # الألعاب المفضلة
    favorite_games = UserPurchase.objects.filter(
        user=user
    ).values('package__game_type').annotate(
        count=Count('package__game_type')
    ).order_by('-count')
    
    if request.method == 'POST':
        try:
            # تحديث معلومات المستخدم
            user.first_name = request.POST.get('first_name', '').strip()
            user.email = request.POST.get('email', '').strip()
            
            # التحقق من تكرار البريد الإلكتروني
            if user.email and User.objects.filter(email=user.email).exclude(id=user.id).exists():
                messages.error(request, 'البريد الإلكتروني مستخدم من مستخدم آخر')
                return redirect('accounts:profile')
            
            user.save()
            
            # تحديث الملف الشخصي
            profile.host_name = request.POST.get('host_name', '').strip()
            profile.phone_number = request.POST.get('phone_number', '').strip()
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
            
        except Exception as e:
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
            preferences.volume_level = int(request.POST.get('volume_level', 50))
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
            
        except ValueError:
            messages.error(request, 'قيم غير صحيحة، يرجى التحقق من البيانات')
        except Exception as e:
            messages.error(request, 'حدث خطأ أثناء الحفظ')
        
        return redirect('accounts:preferences')
    
    return render(request, 'accounts/preferences.html', {
        'preferences': preferences,
    })

@login_required
def dashboard_view(request):
    """لوحة تحكم المستخدم"""
    user = request.user
    
    # الجلسات النشطة
    active_sessions = GameSession.objects.filter(
        host=user,
        is_active=True
    ).order_by('-created_at')
    
    # آخر الأنشطة
    recent_activities = UserActivity.objects.filter(
        user=user
    ).order_by('-created_at')[:5]
    
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
    
    return render(request, 'accounts/dashboard.html', {
        'active_sessions': active_sessions,
        'recent_activities': recent_activities,
        'weekly_stats': weekly_stats,
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
            
            # تسجيل النشاط قبل الحذف
            UserActivity.objects.create(
                user=user,
                activity_type='profile_updated',
                description='حذف الحساب نهائياً'
            )
            
            # حذف الحساب
            user.delete()
            
            messages.success(request, f'تم حذف الحساب {username} نهائياً')
            return redirect('home')
        else:
            messages.error(request, 'كلمة المرور غير صحيحة أو لم تؤكد الحذف')
    
    return render(request, 'accounts/delete_account.html')

# API Views
@login_required
def api_user_stats(request):
    """API لإحصائيات المستخدم"""
    user = request.user
    profile = user.profile
    
    data = {
        'total_games': profile.total_games_hosted,
        'completion_rate': profile.get_completion_rate(),
        'total_purchases': profile.get_total_purchases(),
        'account_type': profile.account_type,
        'member_since': user.date_joined.strftime('%Y-%m-%d'),
    }
    
    return JsonResponse(data)