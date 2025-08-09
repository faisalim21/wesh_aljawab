# games/models.py - النسخة العربية الكاملة
from django.db import models
from django.contrib.auth.models import User
import uuid

class GamePackage(models.Model):
    """حزم الألعاب"""
    GAME_TYPES = [
        ('letters', 'خلية الحروف'),
        ('images', 'تحدي الصور'),
        ('quiz', 'سؤال وجواب'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    game_type = models.CharField(
        max_length=20, 
        choices=GAME_TYPES,
        verbose_name="نوع اللعبة",
        help_text="اختر نوع اللعبة"
    )
    package_number = models.IntegerField(
        verbose_name="رقم الحزمة",
        help_text="رقم الحزمة (1، 2، 3...)"
    )
    is_free = models.BooleanField(
        default=False,
        verbose_name="مجانية؟",
        help_text="هل هذه الحزمة مجانية؟"
    )
    price = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0.00,
        verbose_name="السعر",
        help_text="سعر الحزمة بالريال السعودي"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="فعالة؟",
        help_text="هل الحزمة متاحة للشراء؟"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="تاريخ الإنشاء"
    )
    
    class Meta:
        unique_together = ('game_type', 'package_number')
        ordering = ['game_type', 'package_number']
        verbose_name = "حزمة لعبة"
        verbose_name_plural = "حزم الألعاب"
    
    def __str__(self):
        return f"{self.get_game_type_display()} - حزمة {self.package_number}"

class LettersGameQuestion(models.Model):
    """أسئلة لعبة خلية الحروف"""
    package = models.ForeignKey(
        GamePackage, 
        on_delete=models.CASCADE, 
        related_name='letters_questions',
        verbose_name="الحزمة",
        help_text="اختر حزمة خلية الحروف"
    )
    letter = models.CharField(
        max_length=3,
        verbose_name="الحرف",
        help_text="الحرف العربي (أ، ب، ت...)"
    )
    question_type = models.CharField(
        max_length=10, 
        choices=[
            ('main', 'رئيسي'),
            ('alt1', 'بديل أول'),
            ('alt2', 'بديل ثاني'),
        ], 
        default='main',
        verbose_name="نوع السؤال",
        help_text="نوع السؤال (رئيسي أم بديل)"
    )
    question = models.TextField(
        verbose_name="السؤال",
        help_text="نص السؤال كاملاً"
    )
    answer = models.CharField(
        max_length=100,
        verbose_name="الإجابة",
        help_text="الإجابة الصحيحة"
    )
    category = models.CharField(
        max_length=50,
        verbose_name="التصنيف",
        help_text="تصنيف السؤال (بلدان، وظائف، حيوانات...)"
    )
    
    class Meta:
        unique_together = ('package', 'letter', 'question_type')
        verbose_name = "سؤال خلية حروف"
        verbose_name_plural = "أسئلة خلية الحروف"
        ordering = ['letter', 'question_type']
    
    def __str__(self):
        return f"{self.letter} - {self.get_question_type_display()} - {self.question[:30]}..."

class UserPurchase(models.Model):
    """مشتريات المستخدمين"""
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE,
        verbose_name="المستخدم"
    )
    package = models.ForeignKey(
        GamePackage, 
        on_delete=models.CASCADE,
        verbose_name="الحزمة"
    )
    purchase_date = models.DateTimeField(
        auto_now_add=True,
        verbose_name="تاريخ الشراء"
    )
    is_completed = models.BooleanField(
        default=False,
        verbose_name="مكتملة؟",
        help_text="هل انتهى المستخدم من اللعب؟"
    )
    games_played = models.IntegerField(
        default=0,
        verbose_name="عدد الألعاب",
        help_text="عدد المرات التي لعب فيها"
    )
    
    class Meta:
        unique_together = ('user', 'package')
        verbose_name = "مشترى حزمة"
        verbose_name_plural = "مشتريات الحزم"
        ordering = ['-purchase_date']
    
    def __str__(self):
        return f"{self.user.username} - {self.package}"

class GameSession(models.Model):
    """جلسة لعب"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    host = models.ForeignKey(
        User, 
        on_delete=models.CASCADE,
        verbose_name="المقدم",
        help_text="الشخص الذي ينظم اللعبة"
    )
    package = models.ForeignKey(
        GamePackage, 
        on_delete=models.CASCADE,
        verbose_name="الحزمة"
    )
    game_type = models.CharField(
        max_length=20, 
        choices=GamePackage.GAME_TYPES,
        verbose_name="نوع اللعبة"
    )
    
    # إعدادات اللعبة
    team1_name = models.CharField(
        max_length=50, 
        default="الفريق الأخضر",
        verbose_name="اسم الفريق الأول"
    )
    team2_name = models.CharField(
        max_length=50, 
        default="الفريق البرتقالي",
        verbose_name="اسم الفريق الثاني"
    )
    team1_score = models.IntegerField(
        default=0,
        verbose_name="نقاط الفريق الأول"
    )
    team2_score = models.IntegerField(
        default=0,
        verbose_name="نقاط الفريق الثاني"
    )
    
    # حالة اللعبة
    is_active = models.BooleanField(
        default=True,
        verbose_name="نشطة؟",
        help_text="هل الجلسة نشطة حالياً؟"
    )
    is_completed = models.BooleanField(
        default=False,
        verbose_name="مكتملة؟",
        help_text="هل انتهت الجلسة؟"
    )
    winner_team = models.CharField(
        max_length=10, 
        choices=[
            ('team1', 'الفريق الأول'),
            ('team2', 'الفريق الثاني'),
            ('draw', 'تعادل'),
        ], 
        null=True, 
        blank=True,
        verbose_name="الفريق الفائز"
    )
    
    # روابط اللعبة
    display_link = models.CharField(
        max_length=100, 
        unique=True,
        verbose_name="رابط العرض",
        help_text="رابط شاشة العرض للجمهور"
    )
    contestants_link = models.CharField(
        max_length=100, 
        unique=True,
        verbose_name="رابط المتسابقين",
        help_text="رابط صفحة المتسابقين"
    )
    
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="تاريخ الإنشاء"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="تاريخ التحديث"
    )
    
    class Meta:
        verbose_name = "جلسة لعب"
        verbose_name_plural = "جلسات اللعب"
        ordering = ['-created_at']
    
    def save(self, *args, **kwargs):
        if not self.display_link:
            self.display_link = f"display-{str(self.id)[:8]}"
        if not self.contestants_link:
            self.contestants_link = f"contestants-{str(self.id)[:8]}"
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"جلسة {self.get_game_type_display()} - {self.host.username}"

class LettersGameProgress(models.Model):
    """تقدم لعبة الحروف"""
    session = models.OneToOneField(
        GameSession, 
        on_delete=models.CASCADE, 
        related_name='letters_progress',
        verbose_name="الجلسة"
    )
    
    # حالة الخلايا (25 خلية)
    cell_states = models.JSONField(
        default=dict,
        verbose_name="حالة الخلايا",
        help_text="حالة كل خلية في الشبكة"
    )
    
    # الخلايا المستخدمة
    used_letters = models.JSONField(
        default=list,
        verbose_name="الحروف المستخدمة",
        help_text="قائمة الحروف التي تم استخدامها"
    )
    
    # السؤال الحالي
    current_letter = models.CharField(
        max_length=3, 
        null=True, 
        blank=True,
        verbose_name="الحرف الحالي"
    )
    current_question_type = models.CharField(
        max_length=10, 
        default='main',
        verbose_name="نوع السؤال الحالي"
    )
    
    class Meta:
        verbose_name = "تقدم لعبة الحروف"
        verbose_name_plural = "تقدم ألعاب الحروف"
    
    def __str__(self):
        return f"تقدم جلسة {self.session.id}"

class Contestant(models.Model):
    """المتسابقون"""
    session = models.ForeignKey(
        GameSession, 
        on_delete=models.CASCADE, 
        related_name='contestants',
        verbose_name="الجلسة"
    )
    name = models.CharField(
        max_length=50,
        verbose_name="اسم المتسابق"
    )
    team = models.CharField(
        max_length=10, 
        choices=[
            ('team1', 'الفريق الأول'),
            ('team2', 'الفريق الثاني'),
        ],
        verbose_name="الفريق"
    )
    joined_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="تاريخ الانضمام"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="نشط؟"
    )
    
    class Meta:
        unique_together = ('session', 'name')
        verbose_name = "متسابق"
        verbose_name_plural = "المتسابقون"
        ordering = ['team', 'name']
    
    def __str__(self):
        return f"{self.name} - {self.get_team_display()}"