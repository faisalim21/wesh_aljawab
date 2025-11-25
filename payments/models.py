# payments/models.py
from django.db import models
from django.contrib.auth.models import User
from games.models import GamePackage
import uuid
from decimal import Decimal
from django.contrib.auth.models import User
from games.models import GamePackage

class PaymentMethod(models.Model):
    """طرق الدفع المتاحة"""
    name = models.CharField(max_length=50)  # مدى، فيزا، ماستركارد، تابي
    name_ar = models.CharField(max_length=50)  # الاسم بالعربي
    icon = models.ImageField(upload_to='payment_icons/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    processing_fee = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)  # رسوم المعالجة
    
    def __str__(self):
        return self.name_ar

class Transaction(models.Model):
    """معاملات الدفع"""
    STATUS_CHOICES = [
        ('pending', 'في الانتظار'),
        ('processing', 'جاري المعالجة'),
        ('completed', 'مكتملة'),
        ('failed', 'فشلت'),
        ('cancelled', 'ملغية'),
        ('refunded', 'مستردة'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='transactions')
    package = models.ForeignKey(GamePackage, on_delete=models.CASCADE)
    
    # معلومات المعاملة
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='SAR')
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.SET_NULL, null=True)
    
    # حالة المعاملة
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # معلومات البوابة (وهمية للتطوير)
    gateway_transaction_id = models.CharField(max_length=100, blank=True, null=True)
    gateway_response = models.JSONField(default=dict, blank=True)
    
    # التواريخ
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    
    # معلومات إضافية
    notes = models.TextField(blank=True, null=True)
    failure_reason = models.TextField(blank=True, null=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"معاملة {self.id} - {self.user.username} - {self.amount} {self.currency}"
    
    @property
    def is_successful(self):
        return self.status == 'completed'
    
    def calculate_total_with_fees(self):
        """حساب المبلغ الإجمالي مع الرسوم"""
        if self.payment_method and self.payment_method.processing_fee:
            return self.amount + self.payment_method.processing_fee
        return self.amount

class FakePaymentGateway(models.Model):
    """بوابة دفع وهمية للتطوير والاختبار"""
    name = models.CharField(max_length=50, default="بوابة وهمية")
    success_rate = models.IntegerField(default=90)  # نسبة نجاح المعاملات (%)
    processing_delay = models.IntegerField(default=3)  # تأخير المعالجة بالثواني
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return self.name
    
    def process_payment(self, transaction):
        """معالجة دفع وهمية"""
        import random
        import time
        from django.utils import timezone
        
        # محاكاة تأخير المعالجة
        time.sleep(self.processing_delay)
        
        # محاكاة نجاح أو فشل المعاملة
        success = random.randint(1, 100) <= self.success_rate
        
        if success:
            transaction.status = 'completed'
            transaction.completed_at = timezone.now()
            transaction.gateway_transaction_id = f"fake_{uuid.uuid4().hex[:8]}"
            transaction.gateway_response = {
                'status': 'success',
                'message': 'تمت المعاملة بنجاح',
                'timestamp': timezone.now().isoformat()
            }
        else:
            transaction.status = 'failed'
            transaction.failure_reason = 'فشل في معالجة الدفع - محاكاة'
            transaction.gateway_response = {
                'status': 'failed',
                'message': 'فشل في المعاملة',
                'error_code': 'FAKE_FAILURE',
                'timestamp': timezone.now().isoformat()
            }
        
        transaction.save()
        return success

class Discount(models.Model):
    """كوبونات الخصم"""
    code = models.CharField(max_length=20, unique=True)
    description = models.CharField(max_length=100)
    
    # نوع الخصم
    discount_type = models.CharField(max_length=20, choices=[
        ('percentage', 'نسبة مئوية'),
        ('fixed', 'مبلغ ثابت'),
    ])
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    
    # شروط الاستخدام
    min_purchase_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    max_uses = models.IntegerField(default=1)  # عدد مرات الاستخدام
    used_count = models.IntegerField(default=0)
    
    # صلاحية الكوبون
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    
    # تخصيص لألعاب معينة
    applicable_games = models.ManyToManyField(GamePackage, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"كوبون {self.code} - {self.description}"
    
    @property
    def is_valid(self):
        """تحقق من صلاحية الكوبون"""
        from django.utils import timezone
        now = timezone.now()
        return (
            self.is_active and
            self.valid_from <= now <= self.valid_until and
            self.used_count < self.max_uses
        )
    
    def calculate_discount(self, amount):
        """حساب قيمة الخصم"""
        if not self.is_valid or amount < self.min_purchase_amount:
            return Decimal('0.00')
        
        if self.discount_type == 'percentage':
            return amount * (self.discount_value / 100)
        else:  # fixed
            return min(self.discount_value, amount)

class Invoice(models.Model):
    """فواتير المشتريات"""
    transaction = models.OneToOneField(Transaction, on_delete=models.CASCADE, related_name='invoice')
    invoice_number = models.CharField(max_length=20, unique=True)
    
    # معلومات العميل
    customer_name = models.CharField(max_length=100)
    customer_email = models.EmailField()
    
    # معلومات الفاتورة
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)  # ضريبة القيمة المضافة
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    
    # كوبون الخصم المستخدم
    discount_code = models.ForeignKey(Discount, on_delete=models.SET_NULL, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def save(self, *args, **kwargs):
        if not self.invoice_number:
            # إنشاء رقم فاتورة تلقائي
            import datetime
            today = datetime.date.today()
            self.invoice_number = f"INV-{today.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"فاتورة {self.invoice_number}"
    

class TelrTransaction(models.Model):
    order_id = models.CharField(max_length=200, unique=True)
    purchase = models.ForeignKey("games.UserPurchase", on_delete=models.CASCADE, null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    package = models.ForeignKey(GamePackage, on_delete=models.CASCADE)

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="SAR")

    status = models.CharField(max_length=50)  # pending / success / failed
    raw_response = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"TelrTransaction {self.order_id} ({self.status})"