# wesh_aljawab/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.utils.decorators import method_decorator
from django.db.models import Count, Sum, Avg, Q, F
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
import json

def home_view(request):
    """الصفحة الرئيسية"""
    return render(request, 'home.html')

# ===============================
# Views مخصصة للتحليلات الإدارية
# ===============================

@staff_member_required
def analytics_view(request):
    """صفحة التحليلات الرئيسية"""
    from django.contrib.auth.models import User
    from games.models import GameSession, UserPurchase, FreeTrialUsage, GamePackage
    from payments.models import Transaction
    from accounts.models import UserActivity
    
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
    total_costs = calculate_total_costs()
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

@staff_member_required
def financial_report_view(request):
    """تقرير مالي مفصل"""
    from payments.models import Transaction, PaymentMethod
    from games.models import GamePackage
    
    # إجمالي الإيرادات
    total_revenue = Transaction.objects.filter(status='completed').aggregate(
        total=Sum('amount')
    )['total'] or 0
    
    # رسوم البوابات
    gateway_fees = calculate_gateway_fees()
    
    # التكاليف
    costs_breakdown = calculate_total_costs()
    
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

def calculate_gateway_fees():
    """حساب رسوم البوابات"""
    from payments.models import Transaction
    
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

def calculate_total_costs():
    """حساب إجمالي التكاليف"""
    try:
        from wesh_aljawab.models import AdminCost
        
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
    except:
        # إعدادات افتراضية إذا لم يتم إنشاء النماذج بعد
        return {
            'monthly': Decimal('116.25'),  # 90 + 26.25
            'one_time': Decimal('460.00'),
            'total': Decimal('1157.50'),  # (116.25 * 6) + 460
            'breakdown': [
                {'name': 'اشتراك GPT', 'amount': 90, 'cost_type': 'monthly'},
                {'name': 'السيرفر', 'amount': 26.25, 'cost_type': 'monthly'},
                {'name': 'رسوم البوابة', 'amount': 460, 'cost_type': 'one_time'}
            ]
        }

# ===============================
# تخصيص عناوين الإدارة
# ===============================
admin.site.site_header = "لوحة إدارة منصة الألعاب التفاعلية"
admin.site.site_title = "إدارة المنصة"
admin.site.index_title = "مرحباً في لوحة الإدارة"

urlpatterns = [
    # الإدارة العادية
    path('admin/', admin.site.urls),
    
    # صفحات التحليلات المخصصة
    path('admin/analytics/', analytics_view, name='admin_analytics'),
    path('admin/financial-report/', financial_report_view, name='admin_financial_report'),
    
    # باقي الصفحات
    path('', home_view, name='home'),
    path('games/', include('games.urls')),
    path('accounts/', include('accounts.urls')),
    path('payments/', include(('payments.urls', 'payments'), namespace='payments')),
]

# ملفات الوسائط وملفات الستاتيك (فقط في التطوير)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    if settings.STATICFILES_DIRS:
        urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])