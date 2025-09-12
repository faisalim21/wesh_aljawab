# games/models.py - النسخة المُحسّنة والنهائية

from django.db import models
from django.utils import timezone
from django.db.models import Q, F
from django.conf import settings
from django.core.exceptions import ValidationError
import uuid
from datetime import timedelta
from django.db.models import Max
from decimal import Decimal

class GamePackage(models.Model):
    """حزم الألعاب"""
    GAME_TYPES = [
        ('letters', 'خلية الحروف'),
        ('images', 'تحدي الصور'),
        ('quiz', 'سؤال وجواب'),
    ]

    # أنواع الأسئلة (قابلة للتوسّع لاحقًا)
    QUESTION_THEMES = [
        ('mixed', 'متنوعة'),
        ('sports', 'رياضية'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # نوع اللعبة
    game_type = models.CharField(
        max_length=20,
        choices=GAME_TYPES,
        verbose_name="نوع اللعبة",
        help_text="اختر نوع اللعبة"
    )

    # رقم الحزمة داخل نوع اللعبة
    package_number = models.IntegerField(
        verbose_name="رقم الحزمة",
        help_text="رقم الحزمة (1، 2، 3...)"
    )

    # مجاني/غير مجاني
    is_free = models.BooleanField(
        default=False,
        verbose_name="مجانية؟",
        help_text="هل هذه الحزمة مجانية؟"
    )

    # التسعير
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="السعر",
        help_text="سعر الحزمة بالريال السعودي"
    )
    original_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="السعر الأصلي قبل الخصم"
    )
    discounted_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="السعر بعد الخصم (اختياري)"
    )

    # حالة التفعيل
    is_active = models.BooleanField(
        default=True,
        verbose_name="فعالة؟",
        help_text="هل الحزمة متاحة للشراء؟"
    )

    # وصف الحزمة
    description = models.TextField(
        blank=True,
        verbose_name="الوصف",
        help_text="وصف قصير للحزمة يظهر للمستخدمين"
    )

    # نوع الأسئلة
    question_theme = models.CharField(
        max_length=20,
        choices=QUESTION_THEMES,
        default='mixed',
        verbose_name="نوع الأسئلة",
        help_text="مثال: متنوعة، رياضية"
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="تاريخ الإنشاء"
    )

    class Meta:
        ordering = ['game_type', 'package_number']
        verbose_name = "حزمة لعبة"
        verbose_name_plural = "حزم الألعاب"
        constraints = [
            # فريدة رقم الحزمة داخل نوع اللعبة
            models.UniqueConstraint(
                fields=['game_type', 'package_number'],
                name='uniq_pkg_by_type_number'
            ),
            # إذا كانت مجانية يجب أن يكون السعر 0.00
            models.CheckConstraint(
                check=Q(is_free=False) | Q(price=Decimal('0.00')),
                name='free_pkg_price_must_be_zero'
            ),
            # علاقة خصم صحيحة: كلاهما None أو (خصم > 0 وأقل من الأصلي وكلاهما > 0)
            models.CheckConstraint(
                check=(
                    (Q(discounted_price__isnull=True) & Q(original_price__isnull=True))
                    | (Q(discounted_price__isnull=False) & Q(original_price__isnull=False)
                       & Q(discounted_price__gt=Decimal('0.00')) & Q(original_price__gt=Decimal('0.00'))
                       & Q(discounted_price__lt=F('original_price')))
                ),
                name='valid_discount_relation'
            ),
        ]
        indexes = [
            models.Index(fields=['game_type', 'is_active']),
            models.Index(fields=['is_free']),
        ]

    def __str__(self):
        return f"{self.get_game_type_display()} - حزمة {self.package_number}"

    def clean(self):
        # تحقق إضافي وودّي للأخطاء المفهومة
        if self.is_free and self.price != Decimal('0.00'):
            raise ValidationError("الحزم المجانية يجب أن يكون سعرها 0.00.")
        if (self.discounted_price is None) ^ (self.original_price is None):
            raise ValidationError("إما تحديد السعر الأصلي وسعر الخصم معًا أو تركهما فارغين.")
        if self.discounted_price is not None and self.original_price is not None:
            if self.discounted_price <= Decimal('0.00') or self.original_price <= Decimal('0.00'):
                raise ValidationError("الأسعار يجب أن تكون أكبر من صفر.")
            if self.discounted_price >= self.original_price:
                raise ValidationError("سعر الخصم يجب أن يكون أقل من السعر الأصلي.")

    @property
    def has_discount(self) -> bool:
        try:
            return (
                self.original_price is not None
                and self.discounted_price is not None
                and self.discounted_price > Decimal('0.00')
                and self.original_price > self.discounted_price
            )
        except Exception:
            return False

    @property
    def effective_price(self) -> Decimal:
        """السعر المعتمد للعرض/الشراء."""
        if self.discounted_price and self.discounted_price > Decimal('0.00'):
            return self.discounted_price
        return self.price
    
    @property
    def picture_limit(self) -> int:
        """حدّ ألغاز الصور: المجاني 9، المدفوع 21."""
        return 10 if self.is_free else 22


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
            ('alt3', 'بديل ثالث'),
            ('alt4', 'بديل رابع'),
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
        verbose_name = "سؤال خلية حروف"
        verbose_name_plural = "أسئلة خلية الحروف"
        ordering = ['letter', 'question_type']
        constraints = [
            # سؤال واحد فقط لكل (حزمة، حرف، نوع)
            models.UniqueConstraint(
                fields=['package', 'letter', 'question_type'],
                name='uniq_letter_qtype_per_pkg'
            ),
        ]
        indexes = [
            models.Index(fields=['package', 'letter']),
            models.Index(fields=['package', 'question_type']),
        ]

    def __str__(self):
        return f"{self.letter} - {self.get_question_type_display()} - {self.question[:30]}..."

    def clean(self):
        super().clean()
        if not (self.letter or "").strip():
            raise ValidationError("الحرف مطلوب.")
        if len(self.letter.strip()) > 3:
            raise ValidationError("الحرف يجب ألا يتجاوز 3 خانات.")
        if self.package and self.package.game_type != 'letters':
            raise ValidationError("هذه الحزمة ليست من نوع خلية الحروف.")


class UserPurchase(models.Model):
    """
    مشتريات المستخدمين
    السياسة: تنتهي صلاحية المشتريات المدفوعة بعد 72 ساعة من وقت الشراء.
    - يُسمح بشراء الحزمة مرة أخرى بعد انتهاء الصلاحية (أو إتمام الاستخدام).
    - قيد فريد (شرطي) يمنع وجود أكثر من "شراء نشط" واحد غير مكتمل لنفس الحزمة لكل مستخدم.
    - "نشط" = is_completed=False. عند مرور 72 ساعة يتم اعتباره منتهيًا.
    """
    EXPIRY_HOURS = 72  # صلاحية الشراء

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
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
    expires_at = models.DateTimeField(
        blank=True, null=True,
        verbose_name="ينتهي في",
        help_text="وقت انتهاء صلاحية الشراء (يُحدَّد تلقائيًا إلى 72 ساعة بعد الشراء)"
    )

    # حالة الشراء
    is_completed = models.BooleanField(
        default=False,
        verbose_name="مكتملة؟",
        help_text="هل انتهى المستخدم من الاستخدام/اللعب على هذه الحزمة؟"
    )
    games_played = models.IntegerField(
        default=0,
        verbose_name="عدد الألعاب",
        help_text="عدد المرات التي لعب فيها"
    )

    class Meta:
        verbose_name = "مشترى حزمة"
        verbose_name_plural = "مشتريات الحزم"
        ordering = ['-purchase_date']
        constraints = [
            # يمنع شراءين "نشطين" (غير مكتملين) لنفس الحزمة للمستخدم
            models.UniqueConstraint(
                fields=['user', 'package'],
                condition=Q(is_completed=False),
                name='unique_active_purchase_per_package'
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'package']),
            models.Index(fields=['is_completed']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        status = "نشط" if not self.is_completed else "مكتمل"
        return f"{self.user} - {self.package} ({status})"

    # ======== أدوات مساعدة ========
    @property
    def expiry_duration(self) -> timedelta:
        """مدة صلاحية الشراء (افتراضيًا 72 ساعة)."""
        return timedelta(hours=self.EXPIRY_HOURS)

    @property
    def computed_expires_at(self):
        """تاريخ الانتهاء المحسوب إن لم يكن مضبوطًا في الحقل."""
        base = self.purchase_date or timezone.now()
        return base + self.expiry_duration

    @property
    def is_expired(self) -> bool:
        """
        هل الشراء منتهي الصلاحية الآن؟
        - يعتمد على expires_at إن وُجد، وإلا يُحسب من purchase_date.
        """
        now = timezone.now()
        end = self.expires_at or self.computed_expires_at
        return now >= end

    @property
    def time_left(self) -> timedelta:
        """الوقت المتبقي قبل الانتهاء (للعرض في الواجهة/API)."""
        end = self.expires_at or self.computed_expires_at
        delta = end - timezone.now()
        return max(timedelta(0), delta)

    def mark_expired_if_needed(self, auto_save=True) -> bool:
        """
        يضع is_completed=True تلقائيًا إذا انتهت الصلاحية.
        يعيد True لو تغيّرت الحالة.
        """
        if not self.is_completed and self.is_expired:
            self.is_completed = True
            if auto_save:
                self.save(update_fields=['is_completed'])
            return True
        return False

    def save(self, *args, **kwargs):
        is_create = self._state.adding

        # قبل الحفظ: إن انتهت الصلاحية نُكمِل الشراء
        if not self.is_completed and self.expires_at and timezone.now() >= self.expires_at:
            self.is_completed = True

        super().save(*args, **kwargs)

        # بعد الحفظ الأول: purchase_date صار متوفر
        if is_create and self.expires_at is None:
            # تحديد الانتهاء مرة واحدة بناءً على purchase_date
            self.expires_at = self.purchase_date + self.expiry_duration
            super().save(update_fields=['expires_at'])

        # بعد الحفظ: لو الوقت تعدّى الانتهاء، نكمّلها (حماية مزدوجة)
        if not self.is_completed and self.is_expired:
            self.is_completed = True
            super().save(update_fields=['is_completed'])


class GameSession(models.Model):
    """جلسة لعب (جلسة واحدة لكل شراء بفضل OneToOneField)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    IMAGES_FREE_TTL_MINUTES = 60
    LETTERS_FREE_TTL_MINUTES = 60

    host = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name="المقدم",
        help_text="الشخص الذي ينظم اللعبة (قد تكون فارغة)"
    )
    team1_score = models.PositiveIntegerField(default=0)
    team2_score = models.PositiveIntegerField(default=0)

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

    purchase = models.OneToOneField(
        UserPurchase,
        on_delete=models.PROTECT,
        related_name="game_session",
        null=True, blank=True,
        help_text="الشراء المرتبط بهذه الجلسة (إن وُجد)"
    )

    team1_name = models.CharField(max_length=50, default="الفريق الأخضر")
    team2_name = models.CharField(max_length=50, default="الفريق البرتقالي")

    is_active = models.BooleanField(default=True)
    is_completed = models.BooleanField(default=False)
    winner_team = models.CharField(
        max_length=10,
        choices=[('team1', 'الفريق الأول'), ('team2', 'الفريق الثاني'), ('draw', 'تعادل')],
        null=True, blank=True
    )

    display_link = models.CharField(max_length=100, unique=True)
    contestants_link = models.CharField(max_length=100, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "جلسة لعب"
        verbose_name_plural = "جلسات اللعب"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['game_type', 'created_at']),
            models.Index(fields=['is_active']),
            models.Index(fields=['package', 'is_active']),
        ]

    def clean(self):
        if self.purchase:
            if self.package_id != self.purchase.package_id:
                raise ValidationError("الحزمة في الجلسة لا تطابق حزمة الشراء.")
            if self.host and self.purchase.user_id != self.host_id:
                raise ValidationError("المضيف يجب أن يكون نفس صاحب الشراء.")
            if self.purchase.is_completed or self.purchase.is_expired:
                raise ValidationError("لا يمكن إنشاء جلسة لشراء منتهي/مكتمل.")
        if self.package and not self.package.is_active:
            raise ValidationError("لا يمكن إنشاء جلسة لحزمة غير مفعّلة.")

    def save(self, *args, **kwargs):
        if not self.display_link:
            self.display_link = f"display-{str(self.id)[:8]}"
        if not self.contestants_link:
            self.contestants_link = f"contestants-{str(self.id)[:8]}"
        if self.package and self.game_type != self.package.game_type:
            self.game_type = self.package.game_type
        super().save(*args, **kwargs)

    def __str__(self):
        host_txt = self.host.username if self.host else "بدون مُضيف"
        return f"جلسة {self.get_game_type_display()} - {host_txt}"

    @property
    def letters_free_expires_at(self):
        if self.package and self.package.game_type == 'letters' and self.package.is_free and not self.purchase_id:
            base = self.created_at or timezone.now()
            return base + timedelta(minutes=self.LETTERS_FREE_TTL_MINUTES)
        return None

    @property
    def images_free_expires_at(self):
        if self.package and self.package.game_type == 'images' and self.package.is_free and not self.purchase_id:
            base = self.created_at or timezone.now()
            return base + timedelta(minutes=self.IMAGES_FREE_TTL_MINUTES)
        return None

    @property
    def is_time_expired(self) -> bool:
        """التحقق الموحّد لانتهاء الجلسة (مدفوعة/مجانية)"""
        # جلسة مدفوعة
        if self.purchase_id:
            return self.purchase.is_expired

        # جلسة مجانية الحروف
        if self.letters_free_expires_at:
            return timezone.now() >= self.letters_free_expires_at

        # جلسة مجانية الصور
        if self.images_free_expires_at:
            return timezone.now() >= self.images_free_expires_at

        return False

    def mark_session_expired_if_needed(self, auto_save=True) -> bool:
        if not self.is_completed and self.is_time_expired:
            self.is_active = False
            self.is_completed = True
            if auto_save:
                self.save(update_fields=['is_active', 'is_completed'])
            return True
        return False


class LettersGameProgress(models.Model):
    """تقدم لعبة الحروف"""
    session = models.OneToOneField(
        GameSession,
        on_delete=models.CASCADE,
        related_name='letters_progress',
        verbose_name="الجلسة"
    )

    # حالة الخلايا
    cell_states = models.JSONField(
        default=dict,
        verbose_name="حالة الخلايا",
        help_text="حالة كل خلية في الشبكة"
    )

    # الحروف المستخدمة
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
        verbose_name = "متسابق"
        verbose_name_plural = "المتسابقون"
        ordering = ['team', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'name'],
                name='uniq_contestant_name_per_session'
            )
        ]
        indexes = [
            models.Index(fields=['session', 'team']),
        ]

    def __str__(self):
        return f"{self.name} - {self.get_team_display()}"


class FreeTrialUsage(models.Model):
    """سجل استخدام التجربة المجانية لكل مستخدم/لعبة (مرة واحدة لكل لعبة)."""
    GAME_TYPES = (('letters', 'Letters'), ('images', 'Images'))

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='free_trials'
    )
    game_type = models.CharField(max_length=32, choices=GAME_TYPES, default='letters')
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'game_type'], name='unique_free_trial_per_user_game')
        ]
        indexes = [
            models.Index(fields=['user', 'game_type'])
        ]

    def __str__(self):
        return f"FreeTrial({self.user_id}, {self.game_type})"







from django.db.models import Max
from django.core.exceptions import ValidationError

class PictureRiddle(models.Model):
    package   = models.ForeignKey('GamePackage', on_delete=models.CASCADE, related_name='picture_riddles')
    order     = models.PositiveIntegerField(default=1, db_index=True, help_text="ترتيب اللغز داخل الحزمة")
    image_url = models.URLField(max_length=500, help_text="رابط الصورة (Cloudinary/سحابة)")
    hint      = models.CharField(max_length=255, blank=True, default='', help_text="تلميح يظهر للمقدم فقط")
    answer    = models.CharField(max_length=255, help_text="الإجابة الصحيحة")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('package', 'order')
        unique_together = (('package', 'order'),)

    def clean(self):
        super().clean()
        if self.package and self.package.game_type != 'images':
            raise ValidationError("هذه الحزمة ليست من نوع تحدّي الصور.")
        if self.order < 1:
            raise ValidationError("الترتيب يبدأ من 1.")

        # الحدّ باستخدام خاصية الحزمة (مجاني 10، مدفوع 22)
        limit = self.package.picture_limit if self.package else 22
        count = PictureRiddle.objects.filter(package=self.package).exclude(pk=self.pk).count()
        if self._state.adding and count >= limit:
            raise ValidationError(f"تجاوزت الحدّ الأقصى ({limit}) لهذه الحزمة.")

    def save(self, *args, **kwargs):
        """
        عند الإنشاء:
        - إن لم يُحدَّد order (أو كان مكررًا)، نضعه تلقائيًا = (أكبر order موجود لنفس الحزمة) + 1
        - نحترم الحدّ (clean() سيتحقق)
        """
        if self._state.adding:
            if not self.order:
                self.order = 1
            # لو مكرر أو 1 افتراضية من الفورم؛ نحرّكها تلقائيًا إلى آخر ترتيب + 1
            exists_same = PictureRiddle.objects.filter(package=self.package, order=self.order).exists()
            if exists_same:
                last = PictureRiddle.objects.filter(package=self.package).aggregate(m=Max('order'))['m'] or 0
                self.order = last + 1

        self.full_clean()  # يتأكد من الحد والترتيب
        return super().save(*args, **kwargs)


            

class PictureGameProgress(models.Model):
    session = models.OneToOneField('GameSession', on_delete=models.CASCADE, related_name='picture_progress')
    current_index = models.PositiveIntegerField(default=1)

    class Meta:
        indexes = [models.Index(fields=['session'])]

    def clean(self):
        super().clean()
        if self.session and self.session.game_type != 'images':
            raise ValidationError("هذه الجلسة ليست لتحدّي الصور.")
        total = 0
        if self.session and self.session.package_id:
            total = PictureRiddle.objects.filter(package=self.session.package).count()
        if total and not (1 <= self.current_index <= total):
            raise ValidationError(f"current_index يجب أن يكون بين 1 و {total}.")

    @property
    def total_riddles(self) -> int:
        return PictureRiddle.objects.filter(package=self.session.package).count() if self.session else 0




