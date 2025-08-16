# models.py - المسار: root/models.py (في مجلد المشروع الرئيسي)

from django.db import models
from decimal import Decimal

class AdminCost(models.Model):
    """تكاليف المشروع القابلة للإضافة من لوحة الإدارة"""
    
    COST_TYPES = [
        ('monthly', 'شهري'),
        ('one_time', 'مقطوع'),
        ('per_transaction', 'لكل معاملة'),
        ('percentage', 'نسبة مئوية'),
    ]
    
    CURRENCY_CHOICES = [
        ('SAR', 'ريال سعودي'),
        ('USD', 'دولار أمريكي'),
    ]
    
    name = models.CharField(
        max_length=100, 
        verbose_name="اسم التكلفة",
        help_text="مثال: اشتراك السيرفر، رسوم البوابة، اشتراك GPT"
    )
    
    description = models.TextField(
        blank=True, 
        verbose_name="الوصف",
        help_text="وصف تفصيلي للتكلفة"
    )
    
    cost_type = models.CharField(
        max_length=20, 
        choices=COST_TYPES,
        verbose_name="نوع التكلفة"
    )
    
    amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        verbose_name="المبلغ"
    )
    
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default='SAR',
        verbose_name="العملة"
    )
    
    is_active = models.BooleanField(
        default=True,
        verbose_name="فعال؟",
        help_text="هل يتم احتساب هذه التكلفة في التقارير؟"
    )
    
    notes = models.TextField(
        blank=True,
        verbose_name="ملاحظات",
        help_text="أي ملاحظات إضافية"
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ التحديث")
    
    class Meta:
        verbose_name = "تكلفة إدارية"
        verbose_name_plural = "التكاليف الإدارية"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.key}: {self.description}"
    
    @classmethod
    def get_setting(cls, key, default=None):
        """جلب قيمة إعداد"""
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default
    
    @classmethod
    def set_setting(cls, key, value, description=""):
        """تعيين قيمة إعداد"""
        setting, created = cls.objects.get_or_create(
            key=key,
            defaults={'value': value, 'description': description}
        )
        if not created:
            setting.value = value
            if description:
                setting.description = description
            setting.save()
        return setting
        return f"{self.name} - {self.amount} {self.currency} ({self.get_cost_type_display()})"
    
    def get_monthly_equivalent(self):
        """تحويل التكلفة إلى مكافئ شهري للمقارنة"""
        if self.cost_type == 'monthly':
            return self.amount
        elif self.cost_type == 'one_time':
            # افتراض توزيع على 12 شهر
            return self.amount / 12
        else:
            return Decimal('0.00')

class AdminSettings(models.Model):
    """إعدادات عامة قابلة للتعديل من لوحة الإدارة"""
    
    key = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="المفتاح",
        help_text="مفتاح الإعداد (بدون مسافات)"
    )
    
    value = models.TextField(
        verbose_name="القيمة",
        help_text="قيمة الإعداد"
    )
    
    description = models.CharField(
        max_length=200,
        verbose_name="الوصف",
        help_text="وصف لما يفعله هذا الإعداد"
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ التحديث")
    
    class Meta:
        verbose_name = "إعداد إداري"
        verbose_name_plural = "الإعدادات الإدارية"
        ordering = ['key']
    
    def __str__(self):