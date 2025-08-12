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
from django.db import IntegrityError, transaction as db_txn


def payments_home(request):
    return render(request, 'payments/home.html')

@login_required
def purchase_package(request, package_id):
    """صفحة شراء الحزمة"""
    package = get_object_or_404(GamePackage, id=package_id, is_active=True)

    # منع شراء الحزم المجانية احترازياً
    if package.is_free:
        messages.info(request, 'هذه حزمة مجانية — لا حاجة للشراء.')
        return redirect('games:letters_home')

    # تحقق من عدم وجود شراء سابق
    existing_purchase = UserPurchase.objects.filter(user=request.user, package=package).first()
    if existing_purchase:
        messages.warning(request, 'لديك هذه الحزمة بالفعل!')
        return redirect('games:letters_home')

    payment_methods = PaymentMethod.objects.filter(is_active=True)

    if request.method == 'POST':
        payment_method_id = request.POST.get('payment_method')
        payment_method = get_object_or_404(PaymentMethod, id=payment_method_id, is_active=True)

        # ننشئ معاملة Pending
        txn = Transaction.objects.create(
            user=request.user,
            package=package,
            amount=package.price,
            payment_method=payment_method,
            status='pending'
        )

        # معالجة وهمية (متزامنة)
        gateway = FakePaymentGateway.objects.filter(is_active=True).first()
        if not gateway:
            messages.error(request, 'بوابة الدفع غير متوفرة حالياً.')
            return redirect('payments:cancel')

        success = gateway.process_payment(txn)

        if success:
            # ضمان عدم تكرار الشراء لو ضغط بسرعة
            try:
                with db_txn.atomic():
                    UserPurchase.objects.get_or_create(user=request.user, package=package)
            except IntegrityError:
                pass  # موجودة بالفعل

            # لا ننشئ فاتورة (حسب رغبتك)
            try:
                UserActivity.objects.create(
                    user=request.user,
                    activity_type='package_purchased',
                    description=f'شراء حزمة {package.get_game_type_display()} - حزمة {package.package_number}'
                )
            except Exception:
                pass

            messages.success(request, 'تم الشراء بنجاح! يمكنك الآن اللعب 🎉')
            return redirect('games:letters_home')
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