# payments/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.urls import reverse
from games.models import GamePackage, UserPurchase
from .models import Transaction, PaymentMethod, FakePaymentGateway, Invoice
from accounts.models import UserActivity
from django.utils import timezone

def payments_home(request):
    return render(request, 'payments/home.html')

@login_required
def purchase_package(request, package_id):
    """صفحة شراء الحزمة"""
    package = get_object_or_404(GamePackage, id=package_id, is_active=True)
    
    # تحقق من أن المستخدم لم يشتري هذه الحزمة من قبل
    existing_purchase = UserPurchase.objects.filter(
        user=request.user,
        package=package
    ).first()
    
    if existing_purchase:
        messages.warning(request, 'لديك هذه الحزمة بالفعل!')
        return redirect('games:letters_home')
    
    # جلب طرق الدفع المتاحة
    payment_methods = PaymentMethod.objects.filter(is_active=True)
    
    if request.method == 'POST':
        payment_method_id = request.POST.get('payment_method')
        payment_method = get_object_or_404(PaymentMethod, id=payment_method_id)
        
        # إنشاء معاملة دفع
        transaction = Transaction.objects.create(
            user=request.user,
            package=package,
            amount=package.price,
            payment_method=payment_method,
            status='pending'
        )
        
        # معالجة الدفع الوهمي
        gateway = FakePaymentGateway.objects.filter(is_active=True).first()
        if gateway:
            success = gateway.process_payment(transaction)
            
            if success:
                # إنشاء مشترى ناجح
                UserPurchase.objects.create(
                    user=request.user,
                    package=package
                )
                
                # إنشاء فاتورة
                Invoice.objects.create(
                    transaction=transaction,
                    customer_name=request.user.get_full_name() or request.user.username,
                    customer_email=request.user.email,
                    subtotal=package.price,
                    total_amount=transaction.calculate_total_with_fees()
                )
                
                # تسجيل النشاط
                UserActivity.objects.create(
                    user=request.user,
                    activity_type='package_purchased',
                    description=f'شراء حزمة {package.get_game_type_display()} - حزمة {package.package_number}'
                )
                
                messages.success(request, 'تم الشراء بنجاح! يمكنك الآن اللعب')
                return redirect('payments:success')
            else:
                messages.error(request, 'فشل في عملية الدفع، يرجى المحاولة مرة أخرى')
                return redirect('payments:cancel')
    
    return render(request, 'payments/purchase.html', {
        'package': package,
        'payment_methods': payment_methods,
    })

@login_required
def payment_success(request):
    """صفحة نجاح الدفع"""
    # جلب آخر معاملة ناجحة للمستخدم
    last_transaction = Transaction.objects.filter(
        user=request.user,
        status='completed'
    ).order_by('-completed_at').first()
    
    return render(request, 'payments/success.html', {
        'transaction': last_transaction
    })

def payment_cancel(request):
    """صفحة إلغاء الدفع"""
    return render(request, 'payments/cancel.html')

@login_required
def transaction_history(request):
    """تاريخ المعاملات"""
    transactions = Transaction.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'payments/history.html', {
        'transactions': transactions
    })

@login_required
def invoice_view(request, transaction_id):
    """عرض الفاتورة"""
    transaction = get_object_or_404(Transaction, id=transaction_id, user=request.user)
    try:
        invoice = transaction.invoice
    except:
        messages.error(request, 'لم يتم العثور على الفاتورة')
        return redirect('payments:history')
    
    return render(request, 'payments/invoice.html', {
        'invoice': invoice,
        'transaction': transaction
    })