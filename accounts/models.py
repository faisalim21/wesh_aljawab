# accounts/models.py
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class UserProfile(models.Model):
    """ملف المستخدم الشخصي"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    
    # معلومات شخصية
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    birth_date = models.DateField(blank=True, null=True)
    
    # معلومات المقدم
    is_host = models.BooleanField(default=True)  # كل مستخدم يمكن أن يكون مقدم
    host_name = models.CharField(max_length=50, blank=True, null=True)  # اسم المقدم المعروض
    
    # إحصائيات
    total_games_hosted = models.IntegerField(default=0)  # عدد الألعاب التي قدمها
    total_games_played = models.IntegerField(default=0)  # عدد الألعاب التي شارك فيها
    favorite_game = models.CharField(max_length=20, choices=[
        ('letters', 'خلية الحروف'),
        ('images', 'تحدي الصور'),
        ('quiz', 'سؤال وجواب'),
    ], blank=True, null=True)
    
    # إعدادات المستخدم
    notifications_enabled = models.BooleanField(default=True)
    email_notifications = models.BooleanField(default=False)
    
    # معلومات الحساب
    account_type = models.CharField(max_length=20, choices=[
        ('free', 'مجاني'),
        ('premium', 'مميز'),
    ], default='free')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"ملف {self.user.username}"
    
    @property
    def display_name(self):
        """الاسم المعروض للمستخدم"""
        if self.host_name:
            return self.host_name
        return self.user.first_name if self.user.first_name else self.user.username
    
    def get_total_purchases(self):
        """حساب إجمالي المشتريات"""
        from games.models import UserPurchase
        return UserPurchase.objects.filter(user=self.user).count()
    
    def get_completion_rate(self):
        """حساب معدل إتمام الألعاب"""
        from games.models import UserPurchase
        total = UserPurchase.objects.filter(user=self.user).count()
        completed = UserPurchase.objects.filter(user=self.user, is_completed=True).count()
        return (completed / total * 100) if total > 0 else 0

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """إنشاء ملف شخصي تلقائياً عند إنشاء مستخدم جديد"""
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """حفظ الملف الشخصي عند حفظ المستخدم"""
    try:
        instance.profile.save()
    except UserProfile.DoesNotExist:
        UserProfile.objects.create(user=instance)

class UserActivity(models.Model):
    """سجل نشاط المستخدم"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activities')
    
    activity_type = models.CharField(max_length=30, choices=[
        ('login', 'تسجيل دخول'),
        ('game_created', 'إنشاء لعبة'),
        ('game_joined', 'انضمام للعبة'),
        ('game_completed', 'إتمام لعبة'),
        ('package_purchased', 'شراء حزمة'),
        ('profile_updated', 'تحديث الملف الشخصي'),
    ])
    
    description = models.TextField(blank=True, null=True)
    game_type = models.CharField(max_length=20, blank=True, null=True)
    session_id = models.CharField(max_length=100, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.username} - {self.get_activity_type_display()}"

class UserPreferences(models.Model):
    """تفضيلات المستخدم"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    
    # تفضيلات الألعاب
    default_team1_name = models.CharField(max_length=50, default="الفريق الأخضر")
    default_team2_name = models.CharField(max_length=50, default="الفريق البرتقالي")
    auto_start_timer = models.BooleanField(default=True)
    show_answers_immediately = models.BooleanField(default=False)
    
    # تفضيلات الصوت والعرض
    sound_enabled = models.BooleanField(default=True)
    volume_level = models.IntegerField(default=50)  # 0-100
    theme_preference = models.CharField(max_length=20, choices=[
        ('light', 'فاتح'),
        ('dark', 'غامق'),
        ('auto', 'تلقائي'),
    ], default='light')
    
    # تفضيلات التحكم
    quick_mode_enabled = models.BooleanField(default=False)  # وضع سريع للمقدمين المحترفين
    show_statistics = models.BooleanField(default=True)
    
    def __str__(self):
        return f"تفضيلات {self.user.username}"