# admin.py - المسار: root/admin.py (في مجلد المشروع الرئيسي)

from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from django.urls import reverse, path
from django.shortcuts import render, redirect
from django.contrib.admin.views.decorators import staff_member_required
from django.utils.decorators import method_decorator
from django.db.models import Count, Sum, Avg, Q, F
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
import json

# استيراد النماذج
from accounts.models import UserProfile, UserActivity, UserPreferences
from games.models import GamePackage, GameSession, UserPurchase, LettersGameQuestion, Contestant, FreeTrialUsage
from payments.models import Transaction, PaymentMethod, Discount, Invoice
from .models import AdminCost, AdminSettings

# ===============================
# إعدادات الإدارة العامة
# ===============================

admin.site.site_header = "لوحة إدارة منصة الألعاب التفاعلية"
admin.site.site_title = "إدارة المنصة"
admin.site.index_title = "مرحباً في لوحة الإدارة"

# ===============================
# نماذج التكاليف والإعدادات
# ===============================

@admin.register(AdminCost)
class AdminCostAdmin(admin.ModelAdmin):
    list_display = ['name', 'cost_type', 'amount', 'currency', 'is_active', 'created_at']
    list_filter = ['cost_type', 'is_active', 'currency']
    search_fields = ['name', 'description']
    list_editable = ['amount', 'is_active']
    ordering = ['-created_at']
    
    fieldsets = (
        ('معلومات التكلفة', {
            'fields': ('name', 'description', 'cost_type', 'amount', 'currency')
        }),
        ('إعدادات', {
            'fields': ('is_active', 'notes')
        }),
    )

@admin.register(AdminSettings)
class AdminSettingsAdmin(admin.ModelAdmin):
    list_display = ['key', 'value', 'description', 'updated_at']
    search_fields = ['key', 'description']
    list_editable = ['value']
    
    def has_add_permission(self, request):
        return True
    
    def has_delete_permission(self, request, obj=None):
        return True

# ===============================
# إدارة المستخدمين المحسنة
# ===============================

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'الملف الشخصي'
    
    readonly_fields = ['total_games_hosted', 'total_games_played', 'created_at', 'updated_at']
    
    fieldsets = (
        ('المعلومات الشخصية', {
            'fields': ('phone_number', 'birth_date', 'host_name')
        }),
        ('إعدادات الحساب', {
            'fields': ('is_host', 'account_type', 'notifications_enabled', 'email_notifications')
        }),
        ('الإحصائيات', {
            'fields': ('total_games_hosted', 'total_games_played', 'favorite_game'),
            'classes': ['collapse']
        }),
    )

class EnhancedUserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]
    list_display = ['username', 'email', 'first_name', 'get_phone', 'get_account_type', 
                   'get_total_purchases', 'get_last_activity', 'is_active', 'date_joined']
    list_filter = ['is_active', 'is_staff', 'profile__account_type', 'profile__is_host']
    search_fields = ['username', 'email', 'first_name', 'profile__phone_number']
    ordering = ['-date_joined']
    
    actions = ['deactivate_users', 'activate_users', 'delete_user_data']
    
    def get_phone(self, obj):
        return obj.profile.phone_number if hasattr(obj, 'profile') else 'غير محدد'
    get_phone.short_description = 'رقم الهاتف'
    
    def get_account_type(self, obj):
        if hasattr(obj, 'profile'):
            return obj.profile.get_account_type_display()
        return 'غير محدد'
    get_account_type.short_description = 'نوع الحساب'
    
    def get_total_purchases(self, obj):
        count = UserPurchase.objects.filter(user=obj).count()
        return f"{count} حزمة"
    get_total_purchases.short_description = 'إجمالي المشتريات'
    
    def get_last_activity(self, obj):
        last_activity = UserActivity.objects.filter(user=obj).first()
        if last_activity:
            return last_activity.created_at.strftime('%Y-%m-%d')
        return 'لا يوجد نشاط'
    get_last_activity.short_description = 'آخر نشاط'
    
    def deactivate_users(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f"تم إلغاء تفعيل {queryset.count()} مستخدم")
    deactivate_users.short_description = "إلغاء تفعيل المستخدمين المحددين"
    
    def activate_users(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, f"تم تفعيل {queryset.count()} مستخدم")
    activate_users.short_description = "تفعيل المستخدمين المحددين"
    
    def delete_user_data(self, request, queryset):
        count = queryset.count()
        for user in queryset:
            # حذف البيانات المرتبطة
            UserActivity.objects.filter(user=user).delete()
            GameSession.objects.filter(host=user).delete()
            user.delete()
        self.message_user(request, f"تم حذف {count} مستخدم مع جميع بياناتهم")
    delete_user_data.short_description = "حذف المستخدمين وجميع بياناتهم"

# إعادة تسجيل User مع الإعدادات الجديدة
admin.site.unregister(User)
admin.site.register(User, EnhancedUserAdmin)

# ===============================
# إدارة الألعاب والحزم
# ===============================

class LettersGameQuestionInline(admin.TabularInline):
    model = LettersGameQuestion
    extra = 0
    fields = ['letter', 'question_type', 'question', 'answer', 'category']

@admin.register(GamePackage)
class GamePackageAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'game_type', 'package_number', 'price', 'is_free', 
                   'is_active', 'get_questions_count', 'get_purchases_count', 'created_at']
    list_filter = ['game_type', 'is_free', 'is_active', 'question_theme']
    search_fields = ['description']
    list_editable = ['price', 'is_active']
    ordering = ['game_type', 'package_number']
    
    inlines = [LettersGameQuestionInline]
    
    fieldsets = (
        ('معلومات الحزمة', {
            'fields': ('game_type', 'package_number', 'description', 'question_theme')
        }),
        ('التسعير', {
            'fields': ('is_free', 'price', 'original_price', 'discounted_price')
        }),
        ('الإعدادات', {
            'fields': ('is_active',)
        }),
    )
    
    def get_questions_count(self, obj):
        if obj.game_type == 'letters':
            count = obj.letters_questions.count()
            return f"{count} سؤال"
        return "غير متاح"
    get_questions_count.short_description = 'عدد الأسئلة'
    
    def get_purchases_count(self, obj):
        count = UserPurchase.objects.filter(package=obj).count()
        revenue = UserPurchase.objects.filter(package=obj).aggregate(
            total=Sum('package__price')
        )['total'] or 0
        return f"{count} شراء ({revenue} ر.س)"
    get_purchases_count.short_description = 'المشتريات والإيرادات'

@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'get_host_name', 'get_package_info', 'team1_name', 'team2_name', 
                   'team1_score', 'team2_score', 'is_active', 'is_completed', 'created_at']
    list_filter = ['game_type', 'is_active', 'is_completed', 'package__is_free']
    search_fields = ['host__username', 'team1_name', 'team2_name']
    readonly_fields = ['id', 'display_link', 'contestants_link', 'created_at', 'updated_at']
    ordering = ['-created_at']
    
    def get_host_name(self, obj):
        return obj.host.username if obj.host else 'مجهول'
    get_host_name.short_description = 'المضيف'
    
    def get_package_info(self, obj):
        package_type = "مجانية" if obj.package.is_free else "مدفوعة"
        return f"{obj.package} ({package_type})"
    get_package_info.short_description = 'الحزمة'
    
    actions = ['end_sessions', 'reactivate_sessions']
    
    def end_sessions(self, request, queryset):
        queryset.update(is_active=False, is_completed=True)
        self.message_user(request, f"تم إنهاء {queryset.count()} جلسة")
    end_sessions.short_description = "إنهاء الجلسات المحددة"
    
    def reactivate_sessions(self, request, queryset):
        queryset.update(is_active=True, is_completed=False)
        self.message_user(request, f"تم إعادة تفعيل {queryset.count()} جلسة")
    reactivate_sessions.short_description = "إعادة تفعيل الجلسات المحددة"

@admin.register(UserPurchase)
class UserPurchaseAdmin(admin.ModelAdmin):
    list_display = ['user', 'get_package_info', 'purchase_date', 'expires_at', 
                   'is_completed', 'games_played', 'get_revenue']
    list_filter = ['is_completed', 'package__game_type', 'package__is_free']
    search_fields = ['user__username', 'package__game_type']
    readonly_fields = ['purchase_date']
    ordering = ['-purchase_date']
    
    def get_package_info(self, obj):
        return f"{obj.package} ({obj.package.price} ر.س)"
    get_package_info.short_description = 'الحزمة والسعر'
    
    def get_revenue(self, obj):
        return f"{obj.package.price} ر.س"
    get_revenue.short_description = 'الإيرادات'

# ===============================
# إدارة المدفوعات
# ===============================

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'get_package_info', 'amount', 'currency', 
                   'get_payment_method', 'status', 'created_at', 'completed_at']
    list_filter = ['status', 'currency', 'payment_method', 'package__game_type']
    search_fields = ['user__username', 'gateway_transaction_id']
    readonly_fields = ['id', 'created_at', 'updated_at', 'gateway_transaction_id', 'gateway_response']
    ordering = ['-created_at']
    
    def get_package_info(self, obj):
        return f"{obj.package}"
    get_package_info.short_description = 'الحزمة'
    
    def get_payment_method(self, obj):
        return obj.payment_method.name_ar if obj.payment_method else 'غير محدد'
    get_payment_method.short_description = 'طريقة الدفع'

@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ['name_ar', 'name', 'processing_fee', 'is_active', 'get_usage_count']
    list_filter = ['is_active']
    search_fields = ['name', 'name_ar']
    list_editable = ['processing_fee', 'is_active']
    
    def get_usage_count(self, obj):
        count = Transaction.objects.filter(payment_method=obj).count()
        return f"{count} معاملة"
    get_usage_count.short_description = 'عدد الاستخدامات'

@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = ['code', 'description', 'discount_type', 'discount_value', 
                   'used_count', 'max_uses', 'is_active', 'valid_until']
    list_filter = ['discount_type', 'is_active']
    search_fields = ['code', 'description']
    list_editable = ['is_active']
    ordering = ['-created_at']

# ===============================
# إدارة الأنشطة
# ===============================

@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = ['user', 'activity_type', 'get_description_short', 'created_at']
    list_filter = ['activity_type', 'created_at']
    search_fields = ['user__username', 'description']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    
    def get_description_short(self, obj):
        return obj.description[:50] + "..." if len(obj.description) > 50 else obj.description
    get_description_short.short_description = 'الوصف'

# ===============================
# Views مخصصة للتحليلات
# ===============================

class AnalyticsAdminView:
    
    def get_urls(self):
        urls = [
            path('analytics/', self.analytics_view, name='analytics'),
            path('financial-report/', self.financial_report_view, name='financial_report'),
        ]
        return urls
    
    @method_decorator(staff_member_required)
    def analytics_view(self, request):
        """صفحة التحليلات الرئيسية"""
        
        # إحصائيات المستخدمين
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()
        new_users_this_month = User.objects.filter(
            date_joined__gte=timezone.now().replace(day=1)
        ).count()
        
        # إحصائيات الألعاب
        total_sessions = GameSession.objects.count()
        active_sessions = GameSession.objects.filter(is_active=True).count()
        free_sessions = GameSession.objects.filter(package__is_free=True).count()
        paid_sessions = GameSession.objects.filter(package__is_free=False).count()
        
        # إحصائيات المشتريات
        total_purchases = UserPurchase.objects.count()
        total_revenue = Transaction.objects.filter(status='completed').aggregate(
            total=Sum('amount')
        )['total'] or 0
        
        # معدل التحويل (من مجاني لمدفوع)
        free_trial_users = FreeTrialUsage.objects.values('user').distinct().count()
        paid_users = UserPurchase.objects.values('user').distinct().count()
        conversion_rate = (paid_users / free_trial_users * 100) if free_trial_users > 0 else 0
        
        # أهم العملاء
        top_customers = User.objects.annotate(
            purchase_count=Count('userpurchase'),
            total_spent=Sum('transactions__amount', filter=Q(transactions__status='completed'))
        ).filter(purchase_count__gt=0).order_by('-total_spent')[:10]
        
        # الحزم الأكثر مبيعاً
        top_packages = GamePackage.objects.annotate(
            purchase_count=Count('userpurchase'),
            revenue=Sum('userpurchase__package__price')
        ).filter(purchase_count__gt=0).order_by('-purchase_count')[:5]
        
        # الإحصائيات الشهرية (آخر 6 أشهر)
        monthly_stats = []
        for i in range(6):
            month_start = (timezone.now().replace(day=1) - timedelta(days=30*i)).replace(day=1)
            month_end = (month_start.replace(month=month_start.month+1) if month_start.month < 12 
                        else month_start.replace(year=month_start.year+1, month=1))
            
            month_data = {
                'month': month_start.strftime('%Y-%m'),
                'month_name': month_start.strftime('%B %Y'),
                'users': User.objects.filter(
                    date_joined__gte=month_start, 
                    date_joined__lt=month_end
                ).count(),
                'sessions': GameSession.objects.filter(
                    created_at__gte=month_start, 
                    created_at__lt=month_end
                ).count(),
                'revenue': Transaction.objects.filter(
                    status='completed',
                    created_at__gte=month_start, 
                    created_at__lt=month_end
                ).aggregate(total=Sum('amount'))['total'] or 0
            }
            monthly_stats.append(month_data)
        
        monthly_stats.reverse()
        
        # حساب التكاليف
        total_costs = self.calculate_total_costs()
        net_profit = total_revenue - total_costs['total']
        
        context = {
            'title': 'تحليلات المنصة',
            'total_users': total_users,
            'active_users': active_users,
            'new_users_this_month': new_users_this_month,
            'total_sessions': total_sessions,
            'active_sessions': active_sessions,
            'free_sessions': free_sessions,
            'paid_sessions': paid_sessions,
            'total_purchases': total_purchases,
            'total_revenue': total_revenue,
            'conversion_rate': round(conversion_rate, 2),
            'top_customers': top_customers,
            'top_packages': top_packages,
            'monthly_stats': monthly_stats,
            'total_costs': total_costs,
            'net_profit': net_profit,
        }
        
        return render(request, 'admin/analytics.html', context)
    
    @method_decorator(staff_member_required)
    def financial_report_view(self, request):
        """تقرير مالي مفصل"""
        
        # إجمالي الإيرادات
        total_revenue = Transaction.objects.filter(status='completed').aggregate(
            total=Sum('amount')
        )['total'] or 0
        
        # رسوم البوابات
        gateway_fees = self.calculate_gateway_fees()
        
        # التكاليف
        costs_breakdown = self.calculate_total_costs()
        
        # صافي الربح
        net_profit = total_revenue - gateway_fees - costs_breakdown['total']
        
        # الإيرادات حسب طريقة الدفع
        revenue_by_method = Transaction.objects.filter(status='completed').values(
            'payment_method__name_ar'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')
        
        # الإيرادات حسب نوع الحزمة
        revenue_by_package = GamePackage.objects.annotate(
            revenue=Sum('userpurchase__package__price'),
            purchases=Count('userpurchase')
        ).filter(revenue__gt=0).order_by('-revenue')
        
        context = {
            'title': 'التقرير المالي',
            'total_revenue': total_revenue,
            'gateway_fees': gateway_fees,
            'costs_breakdown': costs_breakdown,
            'net_profit': net_profit,
            'revenue_by_method': revenue_by_method,
            'revenue_by_package': revenue_by_package,
        }
        
        return render(request, 'admin/financial_report.html', context)
    
    def calculate_gateway_fees(self):
        """حساب رسوم البوابات"""
        total_fees = 0
        
        # رسوم المعاملات
        transactions = Transaction.objects.filter(status='completed').select_related('payment_method')
        
        for transaction in transactions:
            # رسوم ثابتة
            total_fees += 1  # ريال واحد لكل معاملة
            
            # رسوم متغيرة حسب نوع الدفع
            if transaction.payment_method:
                if 'فيزا' in transaction.payment_method.name_ar or 'ماستر' in transaction.payment_method.name_ar:
                    total_fees += transaction.amount * Decimal('0.027')  # 2.7%
                elif 'مدى' in transaction.payment_method.name_ar:
                    total_fees += transaction.amount * Decimal('0.01')   # 1%
        
        return total_fees
    
    def calculate_total_costs(self):
        """حساب إجمالي التكاليف"""
        
        # التكاليف من قاعدة البيانات
        db_costs = AdminCost.objects.filter(is_active=True)
        
        monthly_costs = 0
        one_time_costs = 0
        
        for cost in db_costs:
            if cost.cost_type == 'monthly':
                monthly_costs += cost.amount
            elif cost.cost_type == 'one_time':
                one_time_costs += cost.amount
        
        # حساب التكاليف الشهرية لفترة (افتراض 6 أشهر)
        total_monthly = monthly_costs * 6
        
        return {
            'monthly': monthly_costs,
            'one_time': one_time_costs,
            'total': total_monthly + one_time_costs,
            'breakdown': list(db_costs.values('name', 'amount', 'cost_type'))
        }

# تسجيل الـ views المخصصة
analytics_view = AnalyticsAdminView()

# إضافة الروابط للـ admin
def get_admin_urls():
    from django.urls import path
    return [
        path('analytics/', analytics_view.analytics_view, name='admin_analytics'),
        path('financial-report/', analytics_view.financial_report_view, name='admin_financial_report'),
    ]

# تخصيص الصفحة الرئيسية للإدارة
def admin_index_view(request):
    """عرض مخصص للصفحة الرئيسية"""
    
    # إحصائيات سريعة
    stats = {
        'total_users': User.objects.count(),
        'active_users': User.objects.filter(is_active=True).count(),
        'total_sessions_today': GameSession.objects.filter(
            created_at__date=timezone.now().date()
        ).count(),
        'revenue_this_month': Transaction.objects.filter(
            status='completed',
            created_at__gte=timezone.now().replace(day=1)
        ).aggregate(total=Sum('amount'))['total'] or 0,
    }
    
    return render(request, 'admin/index.html', {'stats': stats})

# تعديل عرض الصفحة الرئيسية
admin.site.index_template = 'admin/custom_index.html'