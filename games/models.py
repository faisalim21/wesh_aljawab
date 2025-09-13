# games/models.py

from django.db import models
from django.utils import timezone
from django.db.models import Q, F, Max
from django.conf import settings
from django.core.exceptions import ValidationError
from datetime import timedelta
from decimal import Decimal
import uuid

# =========================
#  فئات تحدّي الوقت
# =========================

class TimeCategory(models.Model):
    """
    تصنيف تحدّي الوقت:
      - slug يُولّد تلقائيًا من الاسم (يدعم العربي) ويُضمن أنه فريد.
      - order اختياري؛ لو تُرك فارغ/0 عند الإضافة يعيَّن تلقائيًا للرقم التالي.
    """
    name = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="الاسم"
    )
    slug = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,                     # <-- مهم: اختياري في الفورم
        verbose_name="المعرّف (Slug)",
        help_text="يُولَّد تلقائيًا من الاسم؛ اتركه فارغًا."
    )
    is_free_category = models.BooleanField(
        default=False,
        verbose_name="فئة مجانية؟",
        help_text="فئة تجربة (يجب أن تحتوي الحزمة #0 فقط)."
    )
    order = models.PositiveIntegerField(
        default=1,
        blank=True, null=True,          # <-- اختياري؛ نولّده عند الحفظ لو فاضي
        verbose_name="الترتيب",
        help_text="رقم أصغر يعني ظهورًا أعلى. اتركه فارغًا ليُعيَّن تلقائيًا."
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="فعّالة؟"
    )
    cover_image = models.URLField(
        blank=True,
        default="",
        verbose_name="صورة الغلاف",
        help_text="رابط صورة الغلاف التي تظهر في الواجهة."
    )

    class Meta:
        ordering = ("order", "name")
        verbose_name = "تصنيف تحدّي الوقت"
        verbose_name_plural = "تصنيفات تحدّي الوقت"

    def __str__(self):
        return self.name

    @property
    def free_only(self):
        return self.is_free_category

    def save(self, *args, **kwargs):
        # توليد slug تلقائيًا (مع ضمان الفريد) إن كان فارغًا
        if not self.slug and self.name:
            from django.utils.text import slugify
            base = slugify(self.name, allow_unicode=True) or "category"
            candidate = base
            i = 2
            while TimeCategory.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{i}"
                i += 1
            self.slug = candidate

        # تعيين ترتيب تلقائي عند الإضافة إذا كان None/0
        if not self.pk and (self.order is None or self.order == 0):
            from django.db.models import Max
            last = TimeCategory.objects.aggregate(m=Max('order'))['m'] or 0
            self.order = last + 1

        super().save(*args, **kwargs)

    """
    تصنيف تحدّي الوقت:
      - slug يُولّد تلقائيًا من الاسم (يدعم العربي) ويُضمن أنه فريد.
      - order اختياري؛ لو تُرك فارغ/0 عند الإضافة يعيَّن تلقائيًا للرقم التالي.
    """
    name = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="الاسم"
    )
    slug = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,                     # <-- مهم: اختياري في الفورم
        verbose_name="المعرّف (Slug)",
        help_text="يُولَّد تلقائيًا من الاسم؛ اتركه فارغًا."
    )
    is_free_category = models.BooleanField(
        default=False,
        verbose_name="فئة مجانية؟",
        help_text="فئة تجربة (يجب أن تحتوي الحزمة #0 فقط)."
    )
    order = models.PositiveIntegerField(
        default=1,
        blank=True, null=True,          # <-- اختياري؛ نولّده عند الحفظ لو فاضي
        verbose_name="الترتيب",
        help_text="رقم أصغر يعني ظهورًا أعلى. اتركه فارغًا ليُعيَّن تلقائيًا."
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="فعّالة؟"
    )
    cover_image = models.URLField(
        blank=True,
        default="",
        verbose_name="صورة الغلاف",
        help_text="رابط صورة الغلاف التي تظهر في الواجهة."
    )

    class Meta:
        ordering = ("order", "name")
        verbose_name = "تصنيف تحدّي الوقت"
        verbose_name_plural = "تصنيفات تحدّي الوقت"

    def __str__(self):
        return self.name

    @property
    def free_only(self):
        return self.is_free_category

    def save(self, *args, **kwargs):
        # توليد slug تلقائيًا (مع ضمان الفريد) إن كان فارغًا
        if not self.slug and self.name:
            from django.utils.text import slugify
            base = slugify(self.name, allow_unicode=True) or "category"
            candidate = base
            i = 2
            while TimeCategory.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{i}"
                i += 1
            self.slug = candidate

        # تعيين ترتيب تلقائي عند الإضافة إذا كان None/0
        if not self.pk and (self.order is None or self.order == 0):
            from django.db.models import Max
            last = TimeCategory.objects.aggregate(m=Max('order'))['m'] or 0
            self.order = last + 1

        super().save(*args, **kwargs)


# =========================
#  حِزم الألعاب
# =========================

class GamePackage(models.Model):
    """حزم الألعاب"""
    GAME_TYPES = [
        ('letters', 'خلية الحروف'),
        ('images',  'تحدي الصور'),
        ('time',    'تحدّي الوقت'),
        ('quiz',    'سؤال وجواب'),
    ]

    # أنواع الأسئلة (للحروف - قابلة للتوسّع)
    QUESTION_THEMES = [
        ('mixed',  'متنوعة'),
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

    # رقم الحزمة داخل نوع اللعبة/التصنيف
    package_number = models.IntegerField(
        verbose_name="رقم الحزمة",
        help_text="رقم الحزمة (0 للتجريبية في فئات تحدّي الوقت، وإلا 1، 2، 3...)"
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

    # نوع الأسئلة (للحروف)
    question_theme = models.CharField(
        max_length=20,
        choices=QUESTION_THEMES,
        default='mixed',
        verbose_name="نوع الأسئلة",
        help_text="مثال: متنوعة، رياضية"
    )

    # ربط فئات تحدّي الوقت (اختياري لباقي الألعاب — إجباري عند game_type='time')
    time_category = models.ForeignKey(
        TimeCategory,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='time_packages',
        verbose_name="تصنيف (تحدّي الوقت)"
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
            # لو النوع Time → الفريدة داخل (التصنيف + رقم الحزمة)
            models.UniqueConstraint(
                fields=['time_category', 'package_number'],
                condition=Q(game_type='time'),
                name='uniq_timepkg_per_category_number'
            ),
            # لباقي الأنواع → الفريدة (نوع اللعبة + رقم الحزمة)
            models.UniqueConstraint(
                fields=['game_type', 'package_number'],
                condition=~Q(game_type='time'),
                name='uniq_pkg_by_type_number_except_time'
            ),
            # المجانية سعرها 0
            models.CheckConstraint(
                check=Q(is_free=False) | Q(price=Decimal('0.00')),
                name='free_pkg_price_must_be_zero'
            ),
            # علاقة خصم صحيحة
            models.CheckConstraint(
                check=(
                    (Q(discounted_price__isnull=True) & Q(original_price__isnull=True)) |
                    (Q(discounted_price__isnull=False) & Q(original_price__isnull=False) &
                     Q(discounted_price__gt=Decimal('0.00')) & Q(original_price__gt=Decimal('0.00')) &
                     Q(discounted_price__lt=F('original_price')))
                ),
                name='valid_discount_relation'
            ),
        ]
        indexes = [
            models.Index(fields=['game_type', 'is_active']),
            models.Index(fields=['is_free']),
            models.Index(fields=['game_type', 'time_category', 'package_number']),
        ]

    def __str__(self):
        return f"{self.get_game_type_display()} - حزمة {self.package_number}"

    # قواعد التحقق
    def clean(self):
        # عام
        if self.is_free and self.price != Decimal('0.00'):
            raise ValidationError("الحزم المجانية يجب أن يكون سعرها 0.00.")
        if (self.discounted_price is None) ^ (self.original_price is None):
            raise ValidationError("إما تحديد السعر الأصلي وسعر الخصم معًا أو تركهما فارغين.")
        if self.discounted_price is not None and self.original_price is not None:
            if self.discounted_price <= Decimal('0.00') or self.original_price <= Decimal('0.00'):
                raise ValidationError("الأسعار يجب أن تكون أكبر من صفر.")
            if self.discounted_price >= self.original_price:
                raise ValidationError("سعر الخصم يجب أن يكون أقل من السعر الأصلي.")

        # خاص بتحدّي الوقت
        if self.game_type == 'time':
            if not self.time_category:
                raise ValidationError("يجب تحديد التصنيف (time_category) لحزم تحدّي الوقت.")
            if self.time_category.is_free_category:
                # الفئة مجانية ⇒ الحزمة يجب أن تكون التجريبية #0 ومجانية
                if self.package_number != 0:
                    raise ValidationError("الفئة المجانية لتحدّي الوقت يجب أن تحتوي حزمة رقم 0 فقط.")
                if not self.is_free:
                    raise ValidationError("حزمة #0 داخل الفئة المجانية يجب أن تكون مجانية.")
            else:
                # الفئة غير مجانية ⇒ لا نسمح برقم 0 ولا بحزمة مجانية
                if self.package_number == 0:
                    raise ValidationError("لا يمكن استخدام رقم 0 في فئة غير مجانية لتحدّي الوقت.")
                if self.is_free:
                    raise ValidationError("الحزم داخل فئة غير مجانية يجب أن تكون غير مجانية.")

    # خصائص مساعدة
    @property
    def has_discount(self) -> bool:
        try:
            return (
                self.original_price is not None and
                self.discounted_price is not None and
                self.discounted_price > Decimal('0.00') and
                self.original_price > self.discounted_price
            )
        except Exception:
            return False

    @property
    def effective_price(self) -> Decimal:
        return self.discounted_price if (self.discounted_price and self.discounted_price > Decimal('0.00')) else self.price

    @property
    def picture_limit(self) -> int:
        """حدّ ألغاز الصور: المجاني 10، المدفوع 22 (خاص بتحدّي الصور)."""
        return 10 if self.is_free else 22


# =========================
#  أسئلة خلية الحروف
# =========================

class LettersGameQuestion(models.Model):
    package = models.ForeignKey(
        GamePackage,
        on_delete=models.CASCADE,
        related_name='letters_questions',
        verbose_name="الحزمة",
        help_text="اختر حزمة خلية الحروف"
    )
    letter = models.CharField(max_length=3, verbose_name="الحرف")
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
        verbose_name="نوع السؤال"
    )
    question = models.TextField(verbose_name="السؤال")
    answer = models.CharField(max_length=100, verbose_name="الإجابة")
    category = models.CharField(max_length=50, verbose_name="التصنيف")

    class Meta:
        verbose_name = "سؤال خلية حروف"
        verbose_name_plural = "أسئلة خلية الحروف"
        ordering = ['letter', 'question_type']
        constraints = [
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


# =========================
#  مشتريات المستخدمين
# =========================

class UserPurchase(models.Model):
    """مشتريات المستخدمين — تنتهي صلاحية المدفوعة بعد 72 ساعة."""
    EXPIRY_HOURS = 72

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="المستخدم")
    package = models.ForeignKey(GamePackage, on_delete=models.CASCADE, verbose_name="الحزمة")
    purchase_date = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الشراء")
    expires_at = models.DateTimeField(blank=True, null=True, verbose_name="ينتهي في")
    is_completed = models.BooleanField(default=False, verbose_name="مكتملة؟")
    games_played = models.IntegerField(default=0, verbose_name="عدد الألعاب")

    class Meta:
        verbose_name = "مشترى حزمة"
        verbose_name_plural = "مشتريات الحزم"
        ordering = ['-purchase_date']
        constraints = [
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

    # أدوات مساعدة
    @property
    def expiry_duration(self) -> timedelta:
        return timedelta(hours=self.EXPIRY_HOURS)

    @property
    def computed_expires_at(self):
        base = self.purchase_date or timezone.now()
        return base + self.expiry_duration

    @property
    def is_expired(self) -> bool:
        now = timezone.now()
        end = self.expires_at or self.computed_expires_at
        return now >= end

    @property
    def time_left(self) -> timedelta:
        end = self.expires_at or self.computed_expires_at
        delta = end - timezone.now()
        return max(timedelta(0), delta)

    def mark_expired_if_needed(self, auto_save=True) -> bool:
        if not self.is_completed and self.is_expired:
            self.is_completed = True
            if auto_save:
                self.save(update_fields=['is_completed'])
            return True
        return False

    def save(self, *args, **kwargs):
        is_create = self._state.adding

        if not self.is_completed and self.expires_at and timezone.now() >= self.expires_at:
            self.is_completed = True

        super().save(*args, **kwargs)

        if is_create and self.expires_at is None:
            self.expires_at = self.purchase_date + self.expiry_duration
            super().save(update_fields=['expires_at'])

        if not self.is_completed and self.is_expired:
            self.is_completed = True
            super().save(update_fields=['is_completed'])


# =========================
#  جلسات اللعب
# =========================

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
        help_text="الشخص الذي ينظم اللعبة"
    )
    team1_score = models.PositiveIntegerField(default=0)
    team2_score = models.PositiveIntegerField(default=0)

    package = models.ForeignKey(GamePackage, on_delete=models.CASCADE, verbose_name="الحزمة")
    game_type = models.CharField(max_length=20, choices=GamePackage.GAME_TYPES, verbose_name="نوع اللعبة")

    purchase = models.OneToOneField(
        UserPurchase, on_delete=models.PROTECT,
        related_name="game_session", null=True, blank=True,
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

    # انتهاء الجلسات المجانية
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
        if self.purchase_id:
            return self.purchase.is_expired
        if self.letters_free_expires_at:
            return timezone.now() >= self.letters_free_expires_at
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


# =========================
#  تقدّم لعبة الحروف
# =========================

class LettersGameProgress(models.Model):
    session = models.OneToOneField(
        GameSession, on_delete=models.CASCADE,
        related_name='letters_progress', verbose_name="الجلسة"
    )
    cell_states = models.JSONField(default=dict, verbose_name="حالة الخلايا")
    used_letters = models.JSONField(default=list, verbose_name="الحروف المستخدمة")
    current_letter = models.CharField(max_length=3, null=True, blank=True, verbose_name="الحرف الحالي")
    current_question_type = models.CharField(max_length=10, default='main', verbose_name="نوع السؤال الحالي")

    class Meta:
        verbose_name = "تقدم لعبة الحروف"
        verbose_name_plural = "تقدم ألعاب الحروف"

    def __str__(self):
        return f"تقدم جلسة {self.session.id}"


# =========================
#  المتسابقون
# =========================

class Contestant(models.Model):
    session = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='contestants', verbose_name="الجلسة")
    name = models.CharField(max_length=50, verbose_name="اسم المتسابق")
    team = models.CharField(max_length=10, choices=[('team1', 'الفريق الأول'), ('team2', 'الفريق الثاني')], verbose_name="الفريق")
    joined_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الانضمام")
    is_active = models.BooleanField(default=True, verbose_name="نشط؟")

    class Meta:
        verbose_name = "متسابق"
        verbose_name_plural = "المتسابقون"
        ordering = ['team', 'name']
        constraints = [
            models.UniqueConstraint(fields=['session', 'name'], name='uniq_contestant_name_per_session')
        ]
        indexes = [
            models.Index(fields=['session', 'team']),
        ]

    def __str__(self):
        return f"{self.name} - {self.get_team_display()}"


# =========================
#  تجربة مجانية (letters/images)
# =========================

class FreeTrialUsage(models.Model):
    """سجل استخدام التجربة المجانية لكل مستخدم/لعبة (مرة واحدة لكل لعبة)."""
    GAME_TYPES = (('letters', 'Letters'), ('images', 'Images'))

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='free_trials')
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


# =========================
#  تحدّي الصور (ألغاز صور)
# =========================

class PictureRiddle(models.Model):
    package   = models.ForeignKey(GamePackage, on_delete=models.CASCADE, related_name='picture_riddles')
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
        # حد الحزمة (مجاني 10، مدفوع 22)
        limit = self.package.picture_limit if self.package else 22
        count = PictureRiddle.objects.filter(package=self.package).exclude(pk=self.pk).count()
        if self._state.adding and count >= limit:
            raise ValidationError(f"تجاوزت الحدّ الأقصى ({limit}) لهذه الحزمة.")

    def save(self, *args, **kwargs):
        if self._state.adding:
            if not self.order:
                self.order = 1
            exists_same = PictureRiddle.objects.filter(package=self.package, order=self.order).exists()
            if exists_same:
                last = PictureRiddle.objects.filter(package=self.package).aggregate(m=Max('order'))['m'] or 0
                self.order = last + 1
        self.full_clean()
        return super().save(*args, **kwargs)


class PictureGameProgress(models.Model):
    session = models.OneToOneField(GameSession, on_delete=models.CASCADE, related_name='picture_progress')
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


# =========================
#  تحدّي الوقت (العناصر + التقدّم)
# =========================

class TimeRiddle(models.Model):
    """
    عنصر واحد داخل باقة تحدّي الوقت: صورة + إجابة (واختياري تلميح).
    ملاحظة: الـ package هنا هو GamePackage بنوع game_type = 'time'
    """
    package     = models.ForeignKey(GamePackage, on_delete=models.CASCADE, related_name='time_riddles')
    order       = models.PositiveIntegerField(default=1, help_text="ترتيب العرض داخل الباقة")
    image_url   = models.URLField(max_length=1000, help_text="رابط الصورة")
    answer      = models.CharField(max_length=200, help_text="الإجابة الصحيحة (تظهر للمقدّم فقط)")
    hint        = models.CharField(max_length=300, blank=True, null=True, help_text="تلميح اختياري (يظهر للمقدّم)")
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']
        unique_together = [('package', 'order')]
        verbose_name = "عنصر تحدّي الوقت"
        verbose_name_plural = "عناصر تحدّي الوقت"

    def __str__(self):
        return f"[{self.package}] #{self.order} — {self.answer}"

    def clean(self):
        super().clean()
        if not self.package or self.package.game_type != 'time':
            raise ValidationError("هذه الحزمة ليست من نوع تحدّي الوقت.")
        if self.order < 1:
            raise ValidationError("الترتيب يبدأ من 1.")
        # حد عناصر الحزمة (قرابة 40 صورة)
        limit = 40
        count = TimeRiddle.objects.filter(package=self.package).exclude(pk=self.pk).count()
        if self._state.adding and count >= limit:
            raise ValidationError(f"تجاوزت الحدّ الأقصى ({limit}) لعناصر هذه الحزمة.")


class TimeGameProgress(models.Model):
    """
    حالة جلسة تحدّي الوقت (مثل ساعة الشطرنج):
    - current_index: رقم العنصر الحالي (1..N)
    - active_side: اللاعب النشط ('A' أو 'B')
    - a_time_left_seconds / b_time_left_seconds: الوقت المتبقي لكل لاعب
    - last_started_at + is_running: لتتبع الخصم الذي يجري وقته الآن
    """
    SIDE_CHOICES = (('A', 'اللاعب A'), ('B', 'اللاعب B'))

    session                 = models.OneToOneField(GameSession, on_delete=models.CASCADE, related_name='time_progress')
    current_index           = models.PositiveIntegerField(default=1)
    active_side             = models.CharField(max_length=1, choices=SIDE_CHOICES, default='A')
    a_time_left_seconds     = models.PositiveIntegerField(default=60)
    b_time_left_seconds     = models.PositiveIntegerField(default=60)
    last_started_at         = models.DateTimeField(blank=True, null=True)
    is_running              = models.BooleanField(default=False)

    created_at              = models.DateTimeField(auto_now_add=True)
    updated_at              = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "تقدّم تحدّي الوقت"
        verbose_name_plural = "تقدّم تحدّي الوقت"

    def __str__(self):
        return f"TimeProgress(session={self.session_id}, idx={self.current_index}, side={self.active_side})"

    # أدوات الوقت
    def _apply_elapsed(self):
        if not self.is_running or not self.last_started_at:
            return
        now = timezone.now()
        elapsed = max(0, int((now - self.last_started_at).total_seconds()))
        if elapsed <= 0:
            return
        if self.active_side == 'A':
            self.a_time_left_seconds = max(0, self.a_time_left_seconds - elapsed)
        else:
            self.b_time_left_seconds = max(0, self.b_time_left_seconds - elapsed)
        self.last_started_at = now

    def start(self, side: str):
        side = 'A' if side == 'A' else 'B'
        self._apply_elapsed()
        self.active_side = side
        self.is_running = True
        self.last_started_at = timezone.now()

    def stop(self):
        self._apply_elapsed()
        self.is_running = False
        self.last_started_at = None

    def switch_after_answer(self):
        self.stop()
        next_side = 'B' if self.active_side == 'A' else 'A'
        self.active_side = next_side
        if (next_side == 'A' and self.a_time_left_seconds > 0) or (next_side == 'B' and self.b_time_left_seconds > 0):
            self.is_running = True
            self.last_started_at = timezone.now()
        else:
            self.is_running = False
            self.last_started_at = None

    def reset_timers(self, seconds_each: int = 60, start_side: str = 'A'):
        self.a_time_left_seconds = max(0, int(seconds_each))
        self.b_time_left_seconds = max(0, int(seconds_each))
        self.active_side = 'A' if start_side == 'A' else 'B'
        self.is_running = False
        self.last_started_at = None
        self.current_index = 1


# =========================
#  سجل لعب تحدّي الوقت + ربط جلسة/فئة
# =========================

class TimePlayHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    category = models.ForeignKey(TimeCategory, on_delete=models.CASCADE)
    package = models.ForeignKey(GamePackage, on_delete=models.CASCADE)
    played_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'category', 'package')
        ordering = ('-played_at',)
        verbose_name = "سجل لعب (تحدّي الوقت)"
        verbose_name_plural = "سجل اللعب (تحدّي الوقت)"

    def __str__(self):
        return f"{self.user_id} | {self.category_id} | pkg#{self.package.package_number}"


class TimeSessionPackage(models.Model):
    """يربط الجلسة بكل فئة والحزمة التي اختيرت لها (بعد الدفع)."""
    session = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='time_session_packages')
    category = models.ForeignKey(TimeCategory, on_delete=models.PROTECT)
    package = models.ForeignKey(GamePackage, on_delete=models.PROTECT)

    class Meta:
        unique_together = ('session', 'category')
        verbose_name = "حزمة جلسة (تحدّي الوقت)"
        verbose_name_plural = "حزم الجلسة (تحدّي الوقت)"

    def __str__(self):
        return f"{self.session_id} → {self.category.name} → pkg#{self.package.package_number}"
