# games/admin.py — لوحة تحكم شاملة ومنظّمة

from django.contrib import admin, messages
from django.urls import path, reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.html import format_html, escape
from django.utils.safestring import mark_safe
from django import forms
from django.db import models, IntegrityError
from django.db.models import (
    Count, Sum, Max, F, Q, Case, When, DecimalField, IntegerField
)
from django.db.models.functions import TruncDate, Coalesce
from django.utils import timezone
from decimal import Decimal
import csv, io

# ========= موديلاتك =========
from .models import (
    GamePackage,
    LettersGameQuestion,
    LettersCellCategory,
    LettersCategoryQuestion,
    UserPurchase,
    GameSession,
    LettersGameProgress,
    Contestant,
    FreeTrialUsage,

    # تحدّي الوقت
    TimeCategory,
    TimeRiddle,
    TimeGameProgress,
    TimePlayHistory,
)

# ========= أدوات مساعدة =========

def _sar(v):
    try:
        v = Decimal(str(v))
        return f"{v.quantize(Decimal('0.01'))} ﷼"
    except Exception:
        return f"{v} ﷼"

def _kpi_card(label, value, sub=None, tone="info"):
    colors = {
        "ok":   ("#10b981", "#064e3b"),
        "warn": ("#f59e0b", "#7c2d12"),
        "bad":  ("#ef4444", "#7f1d1d"),
        "info": ("#3b82f6", "#1e3a8a"),
    }
    bg, border = colors.get(tone, colors["info"])
    return f"""
    <div style="flex:1;min-width:220px;margin:8px;padding:16px;border-radius:12px;
                background:{bg}22;border:1px solid {bg};box-shadow:0 1px 2px #0002;">
      <div style="color:{border};font-weight:700;font-size:13px;margin-bottom:8px;">{label}</div>
      <div style="color:#0ea5e9;font-size:22px;font-weight:800;letter-spacing:0.3px;">{value}</div>
      <div style="color:#94a3b8;font-size:12px;margin-top:6px;">{sub or ''}</div>
    </div>
    """

def _listing_table(headers, rows_html):
    head = "".join(f"<th style='padding:10px 12px;text-align:right;border-bottom:1px solid #1f2937;'>{h}</th>" for h in headers)
    body = "".join(rows_html) or f"<tr><td colspan='{len(headers)}' style='padding:12px;color:#94a3b8;'>لا توجد بيانات</td></tr>"
    return f"""
    <div class="module" style="margin:12px 0;border-radius:12px;overflow:hidden;">
      <table class="listing" style="width:100%;border-collapse:collapse;background:#0b1220;">
        <thead style="background:#0f172a;color:#cbd5e1;">{head}</thead>
        <tbody style="color:#e2e8f0;">{body}</tbody>
      </table>
    </div>
    """

def _price_case_expr():
    """سعر تقديري للمشتريات حسب خصم الحزمة الحالي (بدون سعر محفوظ على UserPurchase)."""
    return Case(
        When(
            package__discounted_price__isnull=False,
            package__original_price__isnull=False,
            package__discounted_price__gt=0,
            package__original_price__gt=F('package__discounted_price'),
            then=F('package__discounted_price'),
        ),
        default=F('package__price'),
        output_field=DecimalField(max_digits=10, decimal_places=2),
    )

# ========= آكشنات عامة =========

def action_mark_active(modeladmin, request, queryset):
    updated = queryset.update(is_active=True)
    messages.success(request, f"تم تفعيل {updated} عنصر/حزمة")
action_mark_active.short_description = "تفعيل المحدد"

def action_mark_inactive(modeladmin, request, queryset):
    updated = queryset.update(is_active=False)
    messages.info(request, f"تم تعطيل {updated} عنصر/حزمة")
action_mark_inactive.short_description = "تعطيل المحدد"

def action_export_csv(modeladmin, request, queryset):
    """تصدير مختصر CSV للحزم المحددة"""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="packages_export.csv"'
    writer = csv.writer(response)
    writer.writerow(['id', 'game_type', 'package_number', 'is_free', 'price', 'is_active', 'question_theme', 'description', 'created_at'])
    for obj in queryset:
        writer.writerow([
            str(obj.id), obj.game_type, obj.package_number,
            '1' if obj.is_free else '0', str(obj.price),
            '1' if obj.is_active else '0', getattr(obj, 'question_theme', ''),
            (getattr(obj, 'description', '') or '').replace('\n', ' '), obj.created_at.isoformat()
        ])
    return response
action_export_csv.short_description = "تصدير CSV (حزم)"

# ========= Proxy Models لتقسيم الأدمن =========

class LettersPackage(GamePackage):
    class Meta:
        proxy = True
        verbose_name = "حزمة - خلية الحروف"
        verbose_name_plural = "حزم - خلية الحروف"

class ImagesPackage(GamePackage):
    class Meta:
        proxy = True
        verbose_name = "حزمة - تحدي الصور"
        verbose_name_plural = "حزم - تحدي الصور"

class QuizPackage(GamePackage):
    class Meta:
        proxy = True
        verbose_name = "حزمة - سؤال وجواب"
        verbose_name_plural = "حزم - سؤال وجواب"

class LettersSession(GameSession):
    class Meta:
        proxy = True
        verbose_name = "جلسة - خلية الحروف"
        verbose_name_plural = "جلسات - خلية الحروف"

class ImagesSession(GameSession):
    class Meta:
        proxy = True
        verbose_name = "جلسة - تحدي الصور"
        verbose_name_plural = "جلسات - تحدي الصور"

class QuizSession(GameSession):
    class Meta:
        proxy = True
        verbose_name = "جلسة - سؤال وجواب"
        verbose_name_plural = "جلسات - سؤال وجواب"

# ========= Forms =========

class LettersPackageForm(forms.ModelForm):
    class Meta:
        model = GamePackage
        fields = (
            'package_number', 'is_free',
            'original_price', 'discounted_price', 'price',
            'is_active', 'description', 'question_theme'
        )
    def clean_package_number(self):
        num = self.cleaned_data['package_number']
        exists = GamePackage.objects.filter(game_type='letters', package_number=num).exclude(pk=self.instance.pk).exists()
        if exists:
            raise forms.ValidationError(f"الحزمة رقم {num} موجودة بالفعل في خلية الحروف.")
        return num

# ========= Inlines =========

class LettersGameQuestionInline(admin.TabularInline):
    model = LettersGameQuestion
    fk_name = 'package'
    extra = 1
    fields = ('letter', 'question_type', 'question', 'answer', 'category', 'difficulty', 'answer_type', 'accepted_answers')
    show_change_link = True

    def question_type_display(self, obj):
        map_ = {'main': '١', 'alt1': '٢', 'alt2': '٣', 'alt3': '٤', 'alt4': '٥'}
        return map_.get(obj.question_type, obj.question_type)
    question_type_display.short_description = "الترتيب"

# صور (تحدّي الصور)
from .models import PictureRiddle, PictureGameProgress

class PictureRiddleInlineFormSet(forms.models.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        alive = 0
        for form in self.forms:
            if form.cleaned_data.get('DELETE'):
                continue
            if (not form.cleaned_data) and (not form.instance.pk):
                continue
            alive += 1
        pkg = self.instance
        limit = getattr(pkg, 'picture_limit', (10 if getattr(pkg, 'is_free', False) else 22))
        if alive > limit:
            raise forms.ValidationError(f"الحدّ الأقصى لهذه الحزمة هو {limit} لغز بالصور.")

class PictureRiddleInline(admin.TabularInline):
    model = PictureRiddle
    fk_name = 'package'
    extra = 0
    formset = PictureRiddleInlineFormSet
    fields = ('order', 'image_url', 'answer', 'hint', 'thumb_tag')
    readonly_fields = ('thumb_tag',)
    ordering = ('order',)
    def thumb_tag(self, obj):
        if not obj or not obj.image_url:
            return "—"
        return format_html('<img src="{}" style="height:56px;border-radius:6px;border:1px solid #ddd;" alt="thumb"/>', escape(obj.image_url))
    thumb_tag.short_description = "معاينة"

# ========= Admin: حِزم خلية الحروف =========


class LettersCategoryQuestionPackageInline(admin.TabularInline):
    model = LettersCategoryQuestion
    extra = 1
    fields = ['category', 'question', 'answer', 'accepted_answers', 'image', 'order']
    verbose_name = "سؤال فقرة مطورة"
    verbose_name_plural = "أسئلة الفقرات المطورة"



@admin.register(LettersPackage)
class LettersPackageAdmin(admin.ModelAdmin):
    list_display = (
        'package_info',
        'theme_badge',
        'difficulty_badge',
        'questions_count_badge',
        'price_info',
        'is_free_icon',
        'status_badge',
        'created_at',
        'letters_actions',
    )
    list_filter = ('is_free', 'is_active', 'question_theme', 'created_at')
    search_fields = ('package_number', 'description')
    inlines = [LettersGameQuestionInline, LettersCategoryQuestionPackageInline]
    actions = (action_mark_active, action_mark_inactive, action_export_csv, 'open_stats')
    ordering = ('package_number',)
    form = LettersPackageForm

    fieldsets = (
        ('المعلومات الأساسية', {
            'fields': (
                'package_number', 'is_free',
                ('original_price', 'discounted_price', 'price'),
                'is_active'
            )
        }),
        ('المحتوى', {'fields': ('description', 'question_theme', 'difficulty_level')}),
    )

    @admin.action(description="📊 فتح صفحة إحصائيات خلية الحروف")
    def open_stats(self, request, queryset):
        return redirect('admin:games_letterspackage_stats')

    def get_queryset(self, request):
        return super().get_queryset(request).filter(game_type='letters').annotate(_qcount=Count('letters_questions'))

    def package_info(self, obj):
        return f"حزمة {obj.package_number}"
    package_info.short_description = "الرقم"

    def theme_badge(self, obj):
        theme = getattr(obj, 'question_theme', 'mixed') or 'mixed'
        label = obj.get_question_theme_display() if hasattr(obj, 'get_question_theme_display') else theme
        if theme == 'sports':
            return format_html('<span style="background:#dcfce7;color:#166534;border:1px solid #86efac;padding:2px 8px;border-radius:999px;font-weight:700;">{}</span>', label)
        return format_html('<span style="background:#e0e7ff;color:#4338ca;border:1px solid #a5b4fc;padding:2px 8px;border-radius:999px;font-weight:700;">{}</span>', label)
    theme_badge.short_description = "نوع الأسئلة"

    def difficulty_badge(self, obj):
        level = obj.difficulty_level or 'mixed'
        styles = {
            'mixed':  ('🎲', '#e0e7ff', '#4338ca', '#a5b4fc'),
            'easy':   ('🟢', '#dcfce7', '#166534', '#86efac'),
            'medium': ('🟡', '#fef9c3', '#854d0e', '#fde047'),
            'hard':   ('🔴', '#fee2e2', '#991b1b', '#fca5a5'),
        }
        icon, bg, color, border = styles.get(level, styles['mixed'])
        label = obj.get_difficulty_level_display()
        return format_html(
            '<span style="background:{};color:{};border:1px solid {};padding:2px 8px;border-radius:999px;font-weight:700;">{} {}</span>',
            bg, color, border, icon, label
        )
    difficulty_badge.short_description = "الصعوبة"

    def questions_count_badge(self, obj):
        count = getattr(obj, '_qcount', 0)
        per_letter = 3 if (obj.is_free and obj.package_number == 0) else 5
        expected_letters = 25 if (obj.is_free and obj.package_number == 0) else 28
        expected = expected_letters * per_letter
        if count >= expected and expected > 0:
            color = 'green'; icon = '✅'
        elif count > 0:
            color = 'orange'; icon = '⚠️'
        else:
            color = 'red'; icon = '❌'
        return format_html('<span style="color:{};font-weight:700;">{} {} / {}</span>', color, icon, count, expected)
    questions_count_badge.short_description = "عدد الأسئلة"

    def price_info(self, obj):
        if obj.is_free:
            return "🆓 مجانية"
        if getattr(obj, 'has_discount', False):
            return format_html('<span style="text-decoration:line-through;color:#64748b;">{} ﷼</span> → <b style="color:#0ea5e9;">{} ﷼</b>', obj.original_price, obj.discounted_price)
        return f"💰 {obj.price} ريال"
    price_info.short_description = "السعر"

    def is_free_icon(self, obj):
        return "✅" if obj.is_free else "—"
    is_free_icon.short_description = "مجانية"

    def status_badge(self, obj):
        return format_html('<b style="color:{};">{}</b>', 'green' if obj.is_active else 'red', 'فعّالة' if obj.is_active else 'غير فعّالة')
    status_badge.short_description = "الحالة"

    def letters_actions(self, obj):
        upload_url = reverse('admin:games_letterspackage_upload', args=[obj.id])
        template_url = reverse('admin:games_letterspackage_download_template')
        export_url = reverse('admin:games_letterspackage_export', args=[obj.id])
        stats_url = reverse('admin:games_letterspackage_stats')
        return mark_safe(
            f'<a class="button" href="{upload_url}" style="background:#22c55e;color:#0b1220;padding:4px 8px;border-radius:6px;text-decoration:none;margin-left:6px;">📁 رفع</a>'
            f'<a class="button" href="{template_url}" style="background:#0ea5e9;color:#0b1220;padding:4px 8px;border-radius:6px;text-decoration:none;margin-left:6px;">⬇️ قالب</a>'
            f'<a class="button" href="{export_url}" style="background:#6b7280;color:#fff;padding:4px 8px;border-radius:6px;text-decoration:none;margin-left:6px;">📤 تصدير</a>'
            f'<a class="button" href="{stats_url}" style="background:#3b82f6;color:#0b1220;padding:4px 8px;border-radius:6px;text-decoration:none;">📊 إحصاءات</a>'
        )
    letters_actions.short_description = "إجراءات"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields['package_number'].help_text = "يجب أن يكون فريدًا داخل خلية الحروف."
        if not obj:
            next_num = (GamePackage.objects.filter(game_type='letters').aggregate(Max('package_number'))['package_number__max'] or 0) + 1
            form.base_fields['package_number'].initial = next_num
        return form

    def save_model(self, request, obj, form, change):
        obj.game_type = 'letters'
        try:
            super().save_model(request, obj, form, change)
        except IntegrityError:
            messages.error(request, f"لا يمكن الحفظ: الرقم {obj.package_number} مستخدم بالفعل في خلية الحروف.")
            raise

    # ===== روابط/صفحات مخصصة =====
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("stats/", self.admin_site.admin_view(self.stats_view), name="games_letterspackage_stats"),
            path("<uuid:pk>/upload/", self.admin_site.admin_view(self.upload_letters_view), name="games_letterspackage_upload"),
            path("<uuid:pk>/export/", self.admin_site.admin_view(self.export_letters_view), name="games_letterspackage_export"),
            path("download-template/", self.admin_site.admin_view(self.download_letters_template_view), name="games_letterspackage_download_template"),
        ]
        return custom + urls

    # ===== صفحة الإحصاءات =====
    def stats_view(self, request):
        qs = GamePackage.objects.filter(game_type='letters').annotate(qcount=Count('letters_questions'))
        total_packages = qs.count()
        total_questions = LettersGameQuestion.objects.count()
        free_count = qs.filter(is_free=True).count()
        paid_count = qs.filter(is_free=False).count()
        active_count = qs.filter(is_active=True).count()
        top_packages = qs.order_by('-qcount', 'package_number')[:10]

        def price_str(p):
            if p.is_free:
                return "🆓 مجانية"
            if getattr(p, 'discounted_price', None) and getattr(p, 'original_price', None) and p.discounted_price < p.original_price:
                return f"<span style='text-decoration:line-through;color:#64748b;'>{p.original_price} ﷼</span> → <b style='color:#0ea5e9;'>{p.discounted_price} ﷼</b>"
            return f"💰 {p.price} ﷼"

        rows = []
        for p in top_packages:
            rows.append(
                "<tr>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>حزمة {p.package_number}</td>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{getattr(p, 'get_question_theme_display', lambda: '')()}</td>"
                f"<td style='text-align:center;padding:10px 12px;border-bottom:1px solid #1f2937;'>{p.qcount}</td>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{price_str(p)}</td>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{'فعّالة' if p.is_active else 'غير فعّالة'}</td>"
                "</tr>"
            )

        html = f"""
        <div style="padding:16px 20px;">
          <h2 style="margin:0 0 10px;">📊 إحصائيات خلية الحروف</h2>
          <div style="display:flex;flex-wrap:wrap;gap:12px;margin-top:10px;">
            {_kpi_card("إجمالي الحزم", total_packages, f"مجانية: {free_count} / مدفوعة: {paid_count}", "info")}
            {_kpi_card("إجمالي الأسئلة", total_questions, "إجمالي في جميع الحزم", "ok")}
            {_kpi_card("حزم فعّالة", active_count, "قابلة للشراء الآن", "warn" if active_count==0 else "ok")}
          </div>
          <h4 style="margin:20px 0 8px;">أكثر الحزم من حيث عدد الأسئلة</h4>
          {_listing_table(["الحزمة","نوع الأسئلة","عدد الأسئلة","السعر","الحالة"], rows)}
        </div>
        """
        ctx = {**self.admin_site.each_context(request), "title": "إحصائيات خلية الحروف", "content": mark_safe(html)}
        return TemplateResponse(request, "admin/simple_box.html", ctx)

    # ===== رفع/تنزيل/تصدير أسئلة =====
    def upload_letters_view(self, request, pk):
        package = get_object_or_404(GamePackage, pk=pk, game_type='letters')

        # GET
        if request.method != 'POST':
            ctx = {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": f"رفع أسئلة - حزمة {package.package_number}",
                "package": package,
                "accept": ".csv,.xlsx,.xlsm,.xltx,.xltm",
                "download_template_url": reverse('admin:games_letterspackage_download_template'),
                "export_url": reverse('admin:games_letterspackage_export', args=[package.id]),
                "change_url": reverse('admin:games_letterspackage_change', args=[package.id]),
                "back_url": reverse('admin:games_letterspackage_changelist'),
                "help_rows": [
                    "الملف يجب أن يحتوي على صف عناوين (هيدر) ثم البيانات.",
                    "الأعمدة: الحرف | نوع السؤال | السؤال | الإجابة | التصنيف.",
                    "أنواع صالحة: رئيسي/أساسي/main، بديل1..بديل4، (البديل) 1..4، بديل أول/ثاني/ثالث/رابع، alt1..alt4.",
                ],
                "extra_note": "تفعيل خيار الحذف سيحذف أسئلة هذه الحزمة قبل الاستيراد.",
                "submit_label": "رفع الملف",
                "replace_label": "حذف الأسئلة الحالية قبل الرفع",
            }
            return TemplateResponse(request, "admin/import_csv.html", ctx)

        # POST
        file = request.FILES.get('file')
        replace_existing = bool(request.POST.get('replace'))

        if not file:
            messages.error(request, "يرجى اختيار ملف")
            return HttpResponseRedirect(request.path)

        if replace_existing:
            package.letters_questions.all().delete()

        # ===== أدوات التطبيع/التحويل =====
        import re, unicodedata
        try:
            import openpyxl
            HAS_OPENPYXL = True
        except ImportError:
            HAS_OPENPYXL = False

        ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
        _TATWEEL = "\u0640"
        _LRM_RLM = {"\u200f", "\u200e"}
        # لا نحذف U+0654 (Hamza Above) حتى لا نفسد "رئيسي/رئيسي"
        _STRIPPABLE_DIACRITICS = {
            "\u064b", "\u064c", "\u064d",  # تنوين
            "\u064e", "\u064f", "\u0650",  # فتحة/ضمة/كسرة
            "\u0651", "\u0652",            # شدة/سكون
            "\u0653",                      # مد
        }

        def strip_diacritics(s: str) -> str:
            """إزالة التشكيل الشائع + التطويل + محارف الاتجاه الخفية، مع الإبقاء على U+0654."""
            out = []
            for ch in s or "":
                if ch == _TATWEEL or ch in _LRM_RLM:
                    continue
                if ch in _STRIPPABLE_DIACRITICS:
                    continue
                out.append(ch)
            return "".join(out)

        def _clean_spaces(s: str) -> str:
            # توحيد الفواصل/الشرطات لمسافة واحدة
            s = re.sub(r"[\/\-]", " ", s)
            s = re.sub(r"\s+", " ", s)
            return s.strip()

        def normalize_qtype(raw):
            """توحيد (نوع السؤال) إلى one of: main | alt1..alt4"""
            if raw is None:
                return None
            s = str(raw).strip()
            if not s:
                return None

            s = s.replace("\u200f", "").replace("\u200e", "")
            s = strip_diacritics(s)
            s = s.translate(ARABIC_INDIC)
            s = _clean_spaces(s).lower()

            # إزالة "ال" من البداية لتوافق "البديل"
            s_wo_al = s[2:] if s.startswith("ال") else s
            candidates = {s, s_wo_al}

            # خرائط مباشرة موسّعة
            direct_map = {
                # main
                "main": "main",
                "رئيسي": "main", "رئيسي": "main", "اساسي": "main", "أساسي": "main", "رئيس": "main",
                # alt1..4 + صيغ شائعة
                "alt1": "alt1", "alt 1": "alt1", "بديل1": "alt1", "بديل 1": "alt1", "بديل اول": "alt1", "بديل أول": "alt1",
                "alt2": "alt2", "alt 2": "alt2", "بديل2": "alt2", "بديل 2": "alt2", "بديل ثاني": "alt2", "بديل الثاني": "alt2",
                "alt3": "alt3", "alt 3": "alt3", "بديل3": "alt3", "بديل 3": "alt3", "بديل ثالث": "alt3", "بديل الثالث": "alt3",
                "alt4": "alt4", "alt 4": "alt4", "بديل4": "alt4", "بديل 4": "alt4", "بديل رابع": "alt4", "بديل الرابع": "alt4",
            }

            # أضف متغيّرات بعد استبدال الشرطات/السلاش
            for c in list(candidates):
                candidates.add(_clean_spaces(c.replace("-", " ").replace("/", " ")))

            for c in candidates:
                if c in direct_map:
                    return direct_map[c]

            # "بديل 1..4"
            for c in candidates:
                m = re.match(r"^(?:ال)?بديل\s*([1-4])$", c)
                if m:
                    return f"alt{m.group(1)}"

            # "بديل أول/ثاني/ثالث/رابع"
            ordinal_map = {"اول": "1", "أول": "1", "ثاني": "2", "ثالث": "3", "رابع": "4"}
            for c in candidates:
                for ord_, num in ordinal_map.items():
                    if re.match(rf"^(?:ال)?بديل\s*{ord_}$", c):
                        return f"alt{num}"

            # "alt 1..4"
            for c in candidates:
                m = re.match(r"^alt\s*([1-4])$", c)
                if m:
                    return f"alt{m.group(1)}"

            return None

        added = 0
        failed_rows = 0
        failed_examples = []
        blank_rows = 0

        def upsert_row(letter, qtype_raw, question, answer, category):
            nonlocal added, failed_rows, failed_examples, blank_rows
            # تجاهل الصفوف الفارغة تمامًا
            if not any([letter, qtype_raw, question, answer, category]):
                blank_rows += 1
                return

            qtype = normalize_qtype(qtype_raw)
            if not qtype:
                failed_rows += 1
                if len(failed_examples) < 5:
                    failed_examples.append(f"[الحرف={letter!s}, النوع='{qtype_raw!s}']")
                return

            LettersGameQuestion.objects.update_or_create(
                package=package,
                letter=str(letter or "").strip(),
                question_type=qtype,
                defaults={
                    'question': (question or '').strip(),
                    'answer':   (answer   or '').strip(),
                    'category': (category or '').strip()
                }
            )
            added += 1

        try:
            name = file.name.lower()

            if name.endswith('.csv'):
                decoded = file.read().decode('utf-8-sig', errors='ignore')
                reader = csv.reader(io.StringIO(decoded))
                next(reader, None)  # تخطّي الهيدر
                for row in reader:
                    if not row or len(row) < 5:
                        failed_rows += 1
                        continue
                    letter, qtype_raw, question, answer, category = [
                        (str(x).strip() if x is not None else '') for x in row[:5]
                    ]
                    upsert_row(letter, qtype_raw, question, answer, category)

            elif name.endswith(('.xlsx', '.xlsm', '.xltx', '.xltm')):
                if not HAS_OPENPYXL:
                    messages.error(request, "openpyxl غير مثبت. ثبّت الحزمة لاستخدام ملفات Excel.")
                    return HttpResponseRedirect(request.path)

                wb = openpyxl.load_workbook(file, data_only=True)
                sh = wb.active
                for row in sh.iter_rows(min_row=2, values_only=True):
                    if not row or len(row) < 5:
                        failed_rows += 1
                        continue
                    letter, qtype_raw, question, answer, category = [
                        (str(x).strip() if x is not None else '') for x in row[:5]
                    ]
                    upsert_row(letter, qtype_raw, question, answer, category)
            else:
                messages.error(request, "نوع الملف غير مدعوم. ارفع CSV أو Excel.")
                return HttpResponseRedirect(request.path)

            # رسائل النتيجة
            if added == 0 and failed_rows > 0:
                msg = "لم يتم التعرف على أي صف. تفقد عمود (نوع السؤال)."
                if failed_examples:
                    msg += " أمثلة متجاهلة: " + ", ".join(failed_examples)
                messages.error(request, msg)
            elif failed_rows > 0 or blank_rows > 0:
                parts = [f"تمت إضافة/تحديث {added} سؤال."]
                if failed_rows:
                    p = f"تجاهل {failed_rows} صف بسبب (نوع سؤال) غير مفهوم"
                    if failed_examples:
                        p += " — أمثلة: " + ", ".join(failed_examples)
                    parts.append(p)
                if blank_rows:
                    parts.append(f"تم تخطي {blank_rows} صف فارغ.")
                messages.warning(request, " ".join(parts))
            else:
                messages.success(request, f"تم إضافة/تحديث {added} سؤال.")

            return HttpResponseRedirect(reverse('admin:games_letterspackage_changelist'))

        except Exception as e:
            messages.error(request, f"خطأ أثناء الرفع: {e}")
            return HttpResponseRedirect(request.path)


    def download_letters_template_view(self, request):
        # CSV بسيط (متوافق دائمًا)
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="letters_template.csv"'
        w = csv.writer(response)
        w.writerow(['الحرف', 'نوع السؤال', 'السؤال', 'الإجابة', 'التصنيف'])
        w.writerow(['أ', 'رئيسي', 'بلد يبدأ بحرف الألف', 'الأردن', 'بلدان'])
        w.writerow(['أ', 'بديل1', 'حيوان يبدأ بحرف الألف', 'أسد', 'حيوانات'])
        w.writerow(['أ', 'بديل2', 'طعام يبدأ بحرف الألف', 'أرز', 'أطعمة'])
        return response

    def export_letters_view(self, request, pk):
        package = get_object_or_404(GamePackage, pk=pk, game_type='letters')
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="letters_package_{package.package_number}.csv"'
        writer = csv.writer(response)
        writer.writerow(['الحرف', 'نوع السؤال', 'السؤال', 'الإجابة', 'التصنيف'])
        type_map_ar = {'main': 'رئيسي', 'alt1': 'بديل1', 'alt2': 'بديل2', 'alt3': 'بديل3', 'alt4': 'بديل4'}
        for q in package.letters_questions.all().order_by('letter', 'question_type'):
            writer.writerow([q.letter, type_map_ar.get(q.question_type, q.question_type), q.question, q.answer, q.category])
        return response

# ========= Admin: أسئلة خلية الحروف (مباشر) =========

@admin.register(LettersGameQuestion)
class LettersGameQuestionAdmin(admin.ModelAdmin):
    list_display = ('package_num', 'letter', 'question_order', 'category', 'difficulty_badge', 'answer_type', 'question_preview', 'answer')
    list_filter = ('package__package_number', 'letter', 'question_type', 'category', 'difficulty', 'answer_type')
    search_fields = ('question', 'answer', 'letter', 'category')
    list_per_page = 30
    list_select_related = ('package',)

    # الحقول في شاشة الإضافة/التعديل
    fields = ('package', 'letter', 'question_type', 'question', 'answer', 'category', 'difficulty', 'answer_type', 'accepted_answers')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'package':
            kwargs['queryset'] = GamePackage.objects.filter(game_type='letters').order_by('package_number')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


    change_form_template = "admin/letters_question_change.html"
    add_form_template = "admin/letters_question_change.html"

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['bulk_add_url'] = reverse('admin:letters_question_bulk_add')
        return super().changelist_view(request, extra_context)


    class Media:
        css = {'all': []}
        js = []

    def get_queryset(self, request):
        return super().get_queryset(request).filter(package__game_type='letters')

    def package_num(self, obj):
        return f"حزمة {obj.package.package_number}"
    package_num.short_description = "الحزمة"

    def question_order(self, obj):
        map_ = {'main': '١', 'alt1': '٢', 'alt2': '٣', 'alt3': '٤', 'alt4': '٥'}
        return map_.get(obj.question_type, obj.question_type)
    question_order.short_description = "الترتيب"

    def difficulty_badge(self, obj):
        styles = {
            'easy':        ('🟢', '#dcfce7', '#166534', '#86efac'),
            'medium':      ('🟡', '#fef9c3', '#854d0e', '#fde047'),
            'hard':        ('🔴', '#fee2e2', '#991b1b', '#fca5a5'),
            'unspecified': ('⚪', '#f1f5f9', '#475569', '#cbd5e1'),
        }
        icon, bg, color, border = styles.get(obj.difficulty, styles['unspecified'])
        label = obj.get_difficulty_display()
        return format_html(
            '<span style="background:{};color:{};border:1px solid {};padding:2px 8px;border-radius:999px;font-size:12px;font-weight:700;">{} {}</span>',
            bg, color, border, icon, label
        )
    difficulty_badge.short_description = "الصعوبة"

    def question_preview(self, obj):
        return (obj.question[:50] + '...') if len(obj.question) > 50 else obj.question
    question_preview.short_description = "السؤال"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('search-similar/', self.admin_site.admin_view(self.search_similar_view), name='letters_question_search_similar'),
            path('bulk-add/', self.admin_site.admin_view(self.bulk_add_view), name='letters_question_bulk_add'),
        ]
        return custom + urls

    def search_similar_view(self, request):
        """API بحث عن أسئلة مشابهة بالنص أو الإجابة أو بالحرف مباشرة"""
        q = request.GET.get('q', '').strip()
        results = []
        if q and len(q) >= 1:
            qs = LettersGameQuestion.objects.filter(
                package__game_type='letters'
            ).filter(
                Q(letter=q) | Q(question__icontains=q) | Q(answer__icontains=q)
            ).select_related('package').order_by('package__package_number', 'letter')[:50]

            

            for item in qs:
                order_map = {'main': '١', 'alt1': '٢', 'alt2': '٣', 'alt3': '٤', 'alt4': '٥'}
                results.append({
                    'id': str(item.id),
                    'package': f"حزمة {item.package.package_number}",
                    'letter': item.letter,
                    'order': order_map.get(item.question_type, item.question_type),
                    'question': item.question,
                    'answer': item.answer,
                    'category': item.category or '—',
                    'difficulty': item.get_difficulty_display(),
                })
        from django.http import JsonResponse
        return JsonResponse({'results': results, 'count': len(results)})
    
    def bulk_add_view(self, request):
        from django.http import JsonResponse

        ALL_LETTERS = ['أ','ب','ت','ث','ج','ح','خ','د','ذ','ر','ز','س','ش','ص','ض','ط','ظ','ع','غ','ف','ق','ك','ل','م','ن','هـ','و','ي']
        TYPE_MAP = {'1': 'main', '2': 'alt1', '3': 'alt2', '4': 'alt3', '5': 'alt4'}
        TYPE_MAP_AR = {'main': 'رئيسي', 'alt1': 'بديل ١', 'alt2': 'بديل ٢', 'alt3': 'بديل ٣', 'alt4': 'بديل ٤'}

        packages = GamePackage.objects.filter(game_type='letters').order_by('package_number')

        if request.method == 'POST' and request.POST.get('action') == 'save':
            package_id = request.POST.get('package_id')
            package = get_object_or_404(GamePackage, pk=package_id, game_type='letters')
            saved = 0
            duplicates = []
            errors = []

            for key, value in request.POST.items():
                if not key.startswith('q_'):
                    continue
                value = value.strip()
                if not value:
                    continue
                parts = key.split('_')
                if len(parts) < 4:
                    continue
                letter = parts[1]
                qtype = parts[2]
                field = parts[3]
                if field != 'question':
                    continue

                answer_key = f'q_{letter}_{qtype}_answer'
                category_key = f'q_{letter}_{qtype}_category'
                difficulty_key = f'q_{letter}_{qtype}_difficulty'

                answer = request.POST.get(answer_key, '').strip()
                category = request.POST.get(category_key, '').strip()
                difficulty = request.POST.get(difficulty_key, 'unspecified').strip()

                if not answer:
                    continue

                # تحقق تكرار
                exact = LettersGameQuestion.objects.filter(
                    package__game_type='letters',
                    question__iexact=value
                ).first()
                if exact:
                    duplicates.append(f"{letter}: {value[:40]}")
                    continue

                try:
                    LettersGameQuestion.objects.update_or_create(
                        package=package,
                        letter=letter,
                        question_type=qtype,
                        defaults={'question': value, 'answer': answer, 'category': category, 'difficulty': difficulty}
                    )
                    saved += 1
                except Exception as e:
                    errors.append(str(e))

            if duplicates:
                messages.warning(request, f'⚠️ تم تخطي {len(duplicates)} سؤال مكرر.')
            if errors:
                messages.error(request, f'❌ أخطاء: {len(errors)}')
            if saved:
                messages.success(request, f'✅ تم حفظ {saved} سؤال بنجاح.')

            return HttpResponseRedirect(reverse('admin:games_lettersgamequestion_changelist'))

        ctx = {
            **self.admin_site.each_context(request),
            'title': 'إضافة أسئلة بالجملة — خلية الحروف',
            'packages': packages,
            'all_letters': ALL_LETTERS,
            'type_map_ar': TYPE_MAP_AR,
            'category_choices': [
                ('', '— غير محدد —'),
                ('general', 'الثقافة العامة'),
                ('religious', 'ديني'),
                ('science', 'العلوم'),
                ('geography', 'الجغرافيا'),
                ('arabic', 'اللغة العربية'),
                ('history', 'التاريخ'),
                ('who_said', 'من القائل ⚠️'),
                ('who_am_i', 'من أنا؟'),
                ('sports', 'الرياضة'),
                ('politics', 'السياسة'),
            ],
            'difficulty_choices': [
                ('unspecified', 'غير محدد'),
                ('easy', 'سهل'),
                ('medium', 'متوسط'),
                ('hard', 'صعب'),
            ],
        }
        return TemplateResponse(request, 'admin/letters_bulk_add.html', ctx)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        if obj:
            # أسئلة نفس الحرف من كل الحزم
            same_letter_qs = LettersGameQuestion.objects.filter(
                package__game_type='letters',
                letter=obj.letter
            ).exclude(pk=obj.pk).select_related('package').order_by('package__package_number', 'question_type')

            order_map = {'main': '١', 'alt1': '٢', 'alt2': '٣', 'alt3': '٤', 'alt4': '٥'}
            same_letter = []
            for q in same_letter_qs:
                same_letter.append({
                    'package': f"حزمة {q.package.package_number}",
                    'theme': q.package.get_question_theme_display(),
                    'order': order_map.get(q.question_type, q.question_type),
                    'question': q.question,
                    'answer': q.answer,
                    'category': q.category or '—',
                    'difficulty': q.get_difficulty_display(),
                })
            extra_context['same_letter_questions'] = same_letter
            extra_context['current_letter'] = obj.letter
        return super().change_view(request, object_id, form_url, extra_context)

    def add_view(self, request, form_url='', extra_context=None):
        extra_context = extra_context or {}
        extra_context['letters_packages'] = GamePackage.objects.filter(game_type='letters').order_by('package_number')

        # معالجة الحفظ بالجملة
        if request.method == 'POST' and request.POST.get('bulk_action') == '1':
            package_id = request.POST.get('package', '').strip()
            if not package_id:
                messages.error(request, '⛔ يرجى اختيار الحزمة أولاً.')
                return HttpResponseRedirect(request.path)
            package = get_object_or_404(GamePackage, pk=package_id, game_type='letters')


            saved = skipped = 0
            for key, value in request.POST.items():
                if not key.startswith('q_') or not key.endswith('_question'):
                    continue
                value = value.strip()
                if not value:
                    continue
                parts = key.split('_')
                if len(parts) < 4:
                    continue
                letter = parts[1]
                qtype = parts[2]
                answer = request.POST.get(f'q_{letter}_{qtype}_answer', '').strip()
                category = request.POST.get(f'q_{letter}_{qtype}_category', '').strip()
                difficulty = request.POST.get(f'q_{letter}_{qtype}_difficulty', 'unspecified').strip()
                if not answer:
                    continue
                exact = LettersGameQuestion.objects.filter(package__game_type='letters', question__iexact=value).first()
                if exact:
                    skipped += 1
                    continue
                LettersGameQuestion.objects.update_or_create(
                    package=package, letter=letter, question_type=qtype,
                    defaults={'question': value, 'answer': answer, 'category': category, 'difficulty': difficulty}
                )
                saved += 1
            if skipped:
                messages.warning(request, f'⚠️ تم تخطي {skipped} سؤال مكرر.')
            if saved:
                messages.success(request, f'✅ تم حفظ {saved} سؤال بنجاح.')
            return HttpResponseRedirect(reverse('admin:games_lettersgamequestion_changelist'))

        letter = request.GET.get('letter', '') or request.POST.get('letter', '')
        if letter:
            same_letter_qs = LettersGameQuestion.objects.filter(
                package__game_type='letters',
                letter=letter
            ).select_related('package').order_by('package__package_number', 'question_type')

            order_map = {'main': '١', 'alt1': '٢', 'alt2': '٣', 'alt3': '٤', 'alt4': '٥'}
            same_letter = []
            for q in same_letter_qs:
                same_letter.append({
                    'package': f"حزمة {q.package.package_number}",
                    'theme': q.package.get_question_theme_display(),
                    'order': order_map.get(q.question_type, q.question_type),
                    'question': q.question,
                    'answer': q.answer,
                    'category': q.category or '—',
                    'difficulty': q.get_difficulty_display(),
                })
            extra_context['same_letter_questions'] = same_letter
            extra_context['current_letter'] = letter
        return super().add_view(request, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        """تنبيه التكرار عند الحفظ"""
        # تحقق تطابق 100%
        exact = LettersGameQuestion.objects.filter(
            package__game_type='letters',
            question__iexact=obj.question
        ).exclude(pk=obj.pk).select_related('package').first()

        if exact:
            messages.error(
                request,
                f'⛔ مكرر: نفس السؤال موجود في حزمة {exact.package.package_number} / حرف {exact.letter}. لم يتم الحفظ.'
            )
            return  # لا نحفظ

        # تحقق تشابه جزئي (كلمات مشتركة)
        words = [w for w in obj.question.split() if len(w) > 2]
        similar = None
        if words:
            q_filter = Q()
            for w in words[:5]:
                q_filter |= Q(question__icontains=w)
            similar = LettersGameQuestion.objects.filter(
                package__game_type='letters'
            ).filter(q_filter).exclude(
                pk=obj.pk
            ).exclude(
                question__iexact=obj.question
            ).select_related('package').first()

        if similar:
            messages.warning(
                request,
                f'⚠️ تحذير تشابه: السؤال مشابه لسؤال في حزمة {similar.package.package_number} / حرف {similar.letter}: "{similar.question[:60]}..." — تم الحفظ.'
            )

        super().save_model(request, obj, form, change)


class LettersCategoryQuestionInline(admin.TabularInline):
    model = LettersCategoryQuestion
    extra = 1
    fields = ['question', 'answer', 'accepted_answers', 'image', 'order']


@admin.register(LettersCellCategory)
class LettersCellCategoryAdmin(admin.ModelAdmin):
    list_display = ['emoji', 'name', 'input_type', 'is_active', 'order']
    list_editable = ['is_active', 'order']
    search_fields = ['name']
    inlines = [LettersCategoryQuestionInline]



# ========= Admin: حِزم الصور =========

@admin.register(PictureRiddle)
class PictureRiddleAdmin(admin.ModelAdmin):
    list_display  = ('package', 'order', 'answer', 'hint_short', 'thumb')
    list_editable = ('order',)
    list_filter   = ('package',)
    search_fields = ('answer', 'hint')
    ordering      = ('package', 'order')
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if 'package' in request.GET:
            initial['package'] = request.GET.get('package')
        return initial
    def hint_short(self, obj): return (obj.hint or '')[:40]
    hint_short.short_description = "تلميح"
    def thumb(self, obj):
        if not obj.image_url: return "—"
        return format_html('<img src="{}" style="height:48px;border-radius:6px;border:1px solid #ddd;" alt="thumb"/>', escape(obj.image_url))
    thumb.short_description = "معاينة"

@admin.register(ImagesPackage)
class ImagesPackageAdmin(admin.ModelAdmin):
    list_display = ('package_info','riddles_count_badge','price_info','is_free_icon','status_badge','created_at','generic_actions')
    list_filter = ('is_free','is_active','created_at')
    search_fields = ('package_number','description')
    actions = (action_mark_active, action_mark_inactive, action_export_csv)
    ordering = ('package_number',)
    inlines = [PictureRiddleInline]

    fieldsets = (
        ('المعلومات الأساسية', {
            'fields': (
                'package_number','is_free',
                ('original_price','discounted_price','price'),
                'is_active'
            )
        }),
        ('المحتوى', {'fields': ('description',)}),
    )

    def get_queryset(self, request):
        return (super()
                .get_queryset(request)
                .filter(game_type='images')
                .annotate(_rcount=Count('picture_riddles')))

    def package_info(self, obj):
        return f"حزمة {obj.package_number}"
    package_info.short_description = "الرقم"

    def riddles_count_badge(self, obj):
        cnt = getattr(obj, '_rcount', 0)
        limit = getattr(obj, 'picture_limit', (10 if obj.is_free else 22))
        if cnt == 0:
            color, icon = '#94a3b8','—'
        elif cnt > limit:
            color, icon = '#ef4444','⚠️'
        elif cnt == limit:
            color, icon = '#10b981','✅'
        else:
            color, icon = '#f59e0b','🧩'
        return format_html('<span style="color:{};font-weight:700;">{} {}/{} </span>', color, icon, cnt, limit)
    riddles_count_badge.short_description = "ألغاز الحزمة"

    def price_info(self, obj):
        if obj.is_free:
            return "🆓 مجانية"
        if getattr(obj, 'has_discount', False):
            return format_html('<span style="text-decoration:line-through;color:#64748b;">{} ﷼</span> → <b style="color:#0ea5e9;">{} ﷼</b>', obj.original_price, obj.discounted_price)
        return f"💰 {obj.price} ريال"
    price_info.short_description = "السعر"

    def is_free_icon(self, obj):
        return "✅" if obj.is_free else "—"
    is_free_icon.short_description = "مجانية"

    def status_badge(self, obj):
        return format_html('<b style="color:{};">{}</b>', 'green' if obj.is_active else 'red', 'فعّالة' if obj.is_active else 'غير فعّالة')
    status_badge.short_description = "الحالة"

    def generic_actions(self, obj):
        list_url   = reverse('admin:games_pictureriddle_changelist') + f'?package__id__exact={obj.id}'
        add_url    = reverse('admin:games_pictureriddle_add') + f'?package={obj.id}'
        upload_zip = reverse('admin:games_imagespackage_upload_zip', args=[obj.id])
        return mark_safe(
            f'<a class="button" href="{list_url}"   style="background:#0ea5e9;color:#0b1220;padding:4px 8px;border-radius:6px;margin-left:6px;">🖼️ عرض الألغاز</a>'
            f'<a class="button" href="{add_url}"    style="background:#22c55e;color:#0b1220;padding:4px 8px;border-radius:6px;margin-left:6px;">➕ إضافة لغز</a>'
            f'<a class="button" href="{upload_zip}" style="background:#a78bfa;color:#0b1220;padding:4px 8px;border-radius:6px;">📦 رفع ZIP</a>'
        )
    generic_actions.short_description = "إجراءات"

    def save_model(self, request, obj, form, change):
        obj.game_type = 'images'
        super().save_model(request, obj, form, change)

    # -------- روابط مخصصة (رفع ZIP) --------
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("<uuid:pk>/upload-zip/", self.admin_site.admin_view(self.upload_images_zip_view), name="games_imagespackage_upload_zip"),
        ]
        return custom + urls

    def upload_images_zip_view(self, request, pk):
        """
        يرفع ملف ZIP، يحمّل الصور إلى Cloudinary (إن توفر) أو التخزين المحلي،
        ثم ينشئ PictureRiddle مع استخراج الإجابة والتلميح من اسم الملف.
        صيغ اسم الملف المدعومة لاستخراج التلميح (اختياري):
          - الإجابة__التلميح.jpg        ← شرطان سفليّان
          - الإجابة -- التلميح.jpg      ← شرطتان
          - الإجابة - التلميح.jpg       ← شرطة واحدة تفصل بمسافات
          - الإجابة (التلميح).jpg       ← بين أقواس
          - الإجابة [التلميح].jpg       ← بين معقوفين
        إذا لم يوجد تلميح، نتركه فارغًا.
        """
        package = get_object_or_404(GamePackage, pk=pk, game_type='images')

        # GET: صفحة رفع
        if request.method != 'POST':
            ctx = {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": f"رفع ملف ZIP للصور — حزمة الصور {package.package_number}",
                "package": package,
                "accept": ".zip",
                "download_template_url": "",
                "export_url": "",
                "change_url": reverse('admin:games_imagespackage_change', args=[package.id]),
                "back_url": reverse('admin:games_imagespackage_changelist'),
                "help_rows": [
                    "ارفع ملف ZIP يحتوي على صور ألغاز هذه الحزمة.",
                    "الإجابة تُستخرج من اسم الملف بدون الامتداد.",
                    "لإضافة تلميح اختياري، استخدم إحدى الصيغ: الإجابة__التلميح | الإجابة - التلميح | الإجابة (التلميح) | الإجابة [التلميح].",
                    "سيتم احترام حد الحزمة (مجانية: 10 / مدفوعة: 22) أو picture_limit إن وُجد.",
                ],
                "extra_note": "يدعم: jpg, jpeg, png, webp, gif, bmp. وسنحاول التعرف حتى لو لم يوجد امتداد.",
                "submit_label": "رفع الملف",
                "replace_label": "حذف الألغاز الحالية قبل الاستيراد",
            }
            return TemplateResponse(request, "admin/import_csv.html", ctx)

        # POST: معالجة الملف
        file = request.FILES.get('file')
        replace_existing = bool(request.POST.get('replace'))

        if not file:
            messages.error(request, "يرجى اختيار ملف ZIP.")
            return HttpResponseRedirect(request.path)

        if replace_existing:
            package.picture_riddles.all().delete()

        import os, io, zipfile, imghdr, re
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        # Cloudinary إن توفّر
        use_cloudinary = False
        uploader = None
        try:
            import cloudinary.uploader as _uploader
            uploader = _uploader
            use_cloudinary = True
        except Exception:
            use_cloudinary = False

        ALLOWED_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}

        def _normalize_name(name: str) -> str:
            name = os.path.basename(name)
            try:
                name.encode('utf-8')
            except Exception:
                try:
                    name = name.encode('cp437').decode('utf-8', 'ignore')
                except Exception:
                    name = name.encode('latin1', 'ignore').decode('utf-8', 'ignore')
            return name

        def _split_answer_hint(stem: str):
            """
            يحوّل اسم الملف (بدون الامتداد) إلى (answer, hint)
            أمثلة:
              "ألمانيا__أوروبا" → ("ألمانيا","أوروبا")
              "تويوتا - يابانية" → ("تويوتا","يابانية")
              "برج_إيفل (باريس)" → ("برج إيفل","باريس")
              "بي إم دبليو" → ("بي إم دبليو","")
            """
            s = stem.strip()
            s = re.sub(r"[_]+", " ", s).strip()  # تحوير _ إلى مسافة (للإجابة بشكل أجمل)
            # 1) __
            if "__" in s:
                a, h = s.split("__", 1)
                return a.strip(), h.strip()
            # 2) -- (بمسافات أو بدون)
            if " -- " in s:
                a, h = s.split(" -- ", 1)
                return a.strip(), h.strip()
            # 3) " - " (شرطة ومسافات)
            if " - " in s:
                a, h = s.split(" - ", 1)
                return a.strip(), h.strip()
            # 4) بين أقواس/معقوفين
            m = re.match(r"^(.*?)[\s]*\((.+)\)$", s)
            if m:
                return m.group(1).strip(), m.group(2).strip()
            m = re.match(r"^(.*?)[\s]*\[(.+)\]$", s)
            if m:
                return m.group(1).strip(), m.group(2).strip()
            return s, ""  # لا يوجد تلميح

        def _is_image_bytes(data: bytes) -> bool:
            return bool(imghdr.what(None, h=data))

        # حد الحزمة
        current_count = package.picture_riddles.count()
        limit = getattr(package, 'picture_limit', (10 if package.is_free else 22))
        can_add = max(0, limit - current_count)
        if can_add <= 0:
            messages.error(request, f"هذه الحزمة وصلت الحد الأقصى ({limit}) من الألغاز.")
            return HttpResponseRedirect(reverse('admin:games_imagespackage_change', args=[package.id]))

        start_order = (package.picture_riddles.aggregate(Max('order'))['order__max'] or 0) + 1

        added = skipped = failed = 0
        notes = []

        try:
            with zipfile.ZipFile(file) as zf:
                for zinfo in zf.infolist():
                    if added >= can_add:
                        skipped += 1
                        notes.append(f"تخطّي الباقي: وصلنا للحد {limit}.")
                        break
                    if zinfo.is_dir():
                        continue

                    raw_name = _normalize_name(zinfo.filename)
                    if not raw_name:
                        continue

                    stem, ext = os.path.splitext(raw_name)
                    data = zf.read(zinfo)

                    # تأكيد صورة
                    is_image = (ext.lower() in ALLOWED_EXTS) or _is_image_bytes(data)
                    if not is_image:
                        skipped += 1
                        if len(notes) < 5:
                            notes.append(f"تخطي «{raw_name}»: ليس ملف صورة مدعوم.")
                        continue

                    # ارفع (Cloudinary أو media)
                    try:
                        if use_cloudinary and uploader:
                            up = uploader.upload(
                                io.BytesIO(data),
                                folder=f"wesh/images/{package.id}",
                                public_id=None,
                                resource_type="image",
                            )
                            image_url = up.get('secure_url') or up.get('url')
                        else:
                            safe_name = raw_name
                            base, ext0 = os.path.splitext(safe_name)
                            idx = 1
                            path = f"picture_riddles/{package.id}/{safe_name}"
                            while default_storage.exists(path):
                                safe_name = f"{base}_{idx}{ext0}"
                                path = f"picture_riddles/{package.id}/{safe_name}"
                                idx += 1
                            saved_path = default_storage.save(path, ContentFile(data))
                            from django.conf import settings
                            media_url = getattr(settings, 'MEDIA_URL', '/media/')
                            image_url = media_url.rstrip('/') + '/' + saved_path.lstrip('/')
                    except Exception as e:
                        failed += 1
                        if len(notes) < 5:
                            notes.append(f"فشل رفع «{raw_name}»: {e}")
                        continue

                    # استخرج الإجابة/التلميح من الاسم
                    answer, hint = _split_answer_hint(stem)

                    try:
                        PictureRiddle.objects.create(
                            package=package,
                            order=start_order + added,
                            image_url=image_url,
                            answer=answer,
                            hint=hint,
                        )
                        added += 1
                    except Exception as e:
                        failed += 1
                        if len(notes) < 5:
                            notes.append(f"فشل إنشاء سجل «{raw_name}»: {e}")

        except zipfile.BadZipFile:
            messages.error(request, "الملف ليس ZIP صالحًا.")
            return HttpResponseRedirect(request.path)
        except Exception as e:
            messages.error(request, f"حدث خطأ أثناء قراءة الملف: {e}")
            return HttpResponseRedirect(request.path)

        # الرسالة النهائية
        if added and not (failed or skipped):
            messages.success(request, f"تم رفع {added} صورة بنجاح وإضافتها كلغاز.")
        else:
            parts = [f"تمت إضافة {added} لغز."]
            if skipped: parts.append(f"تخطي {skipped} عنصر.")
            if failed:  parts.append(f"فشل {failed} عنصر.")
            if notes:   parts.append("ملاحظات: " + " | ".join(notes))
            level = messages.WARNING if (skipped or failed) else messages.SUCCESS
            messages.add_message(request, level, " ".join(parts))

        return HttpResponseRedirect(reverse('admin:games_imagespackage_change', args=[package.id]))

# ========= Admin: حِزم سؤال وجواب =========

@admin.register(QuizPackage)
class QuizPackageAdmin(admin.ModelAdmin):
    list_display = ('package_info', 'price_info', 'is_free_icon', 'status_badge', 'created_at')
    list_filter = ('is_free', 'is_active', 'created_at')
    search_fields = ('package_number', 'description')
    actions = (action_mark_active, action_mark_inactive, action_export_csv)
    ordering = ('package_number',)
    fieldsets = (
        ('المعلومات الأساسية', {
            'fields': (
                'package_number', 'is_free',
                ('original_price', 'discounted_price', 'price'),
                'is_active'
            )
        }),
        ('المحتوى', {'fields': ('description',)}),
    )
    def get_queryset(self, request): return super().get_queryset(request).filter(game_type='quiz')
    def package_info(self, obj): return f"حزمة {obj.package_number}"
    package_info.short_description = "الرقم"
    def price_info(self, obj):
        if obj.is_free: return "🆓 مجانية"
        if getattr(obj, 'has_discount', False):
            return format_html('<span style="text-decoration:line-through;color:#64748b;">{} ﷼</span> → <b style="color:#0ea5e9;">{} ﷼</b>', obj.original_price, obj.discounted_price)
        return f"💰 {obj.price} ريال"
    price_info.short_description = "السعر"
    def is_free_icon(self, obj): return "✅" if obj.is_free else "—"
    is_free_icon.short_description = "مجانية"
    def status_badge(self, obj):
        return format_html('<b style="color:{};">{}</b>', 'green' if obj.is_active else 'red', 'فعّالة' if obj.is_active else 'غير فعّالة')
    status_badge.short_description = "الحالة"
    def save_model(self, request, obj, form, change):
        obj.game_type = 'quiz'
        super().save_model(request, obj, form, change)

# ========= Admin: الجلسات (مقسّمة) =========

class _BaseSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'host', 'package_info', 'scores', 'is_active', 'is_completed', 'created_at')
    list_filter = ('is_active', 'is_completed', 'package__is_free', 'created_at')
    search_fields = ('id', 'host__username', 'package__package_number')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    list_select_related = ('package', 'host')
    def package_info(self, obj):
        return f"حزمة {obj.package.package_number} / {'مجانية' if obj.package.is_free else 'مدفوعة'}"
    package_info.short_description = "الحزمة"
    def scores(self, obj):
        return f"{obj.team1_name}: {obj.team1_score} | {obj.team2_name}: {obj.team2_score}"
    scores.short_description = "النقاط"

@admin.register(LettersSession)
class LettersSessionAdmin(_BaseSessionAdmin):
    def get_queryset(self, request): return super().get_queryset(request).filter(game_type='letters')

@admin.register(ImagesSession)
class ImagesSessionAdmin(_BaseSessionAdmin):
    def get_queryset(self, request): return super().get_queryset(request).filter(game_type='images')

@admin.register(QuizSession)
class QuizSessionAdmin(_BaseSessionAdmin):
    def get_queryset(self, request): return super().get_queryset(request).filter(game_type='quiz')

# ========= Admin: المشتريات + التحليلات =========

@admin.register(UserPurchase)
class UserPurchaseAdmin(admin.ModelAdmin):
    list_display = ('user', 'package_ref', 'is_completed', 'is_gift_badge', 'is_expired_badge', 'purchase_date', 'expires_at')
    list_filter = ('is_completed', 'is_gift', 'purchase_date', 'expires_at', 'package__game_type')
    search_fields = ('user__username', 'package__package_number')
    date_hierarchy = 'purchase_date'
    ordering = ('-purchase_date',)
    list_select_related = ('user', 'package')
    actions = ('open_analytics',)
    def package_ref(self, obj):
        return f"{obj.package.get_game_type_display()} / حزمة {obj.package.package_number}"
    package_ref.short_description = "الحزمة"
    def is_gift_badge(self, obj):
        if not obj.is_gift:
            return "—"
        return mark_safe('<span style="background:#fef9c3;color:#92400e;border:1px solid #fde68a;padding:2px 8px;border-radius:999px;font-weight:700;">🎁 هدية</span>')
    is_gift_badge.short_description = "نوع"
    def is_expired_badge(self, obj):
        ok = obj.is_expired
        color = '#ef4444' if ok else '#10b981'
        label = 'منتهي' if ok else 'نشط'
        return mark_safe(f'<b style="color:{color};">{label}</b>')
    is_expired_badge.short_description = "حالة الصلاحية"
    @admin.action(description="📈 فتح لوحة التحليلات")
    def open_analytics(self, request, queryset):
        return redirect('admin:games_purchases_analytics')
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("analytics/", self.admin_site.admin_view(self.analytics_view), name="games_purchases_analytics"),
            path("analytics.csv", self.admin_site.admin_view(self.analytics_csv_view), name="games_purchases_analytics_csv"),
            path("grant-gift/", self.admin_site.admin_view(self.grant_gift_view), name="games_purchases_grant_gift"),
        ]
        return custom + urls

    def grant_gift_view(self, request):
        from django.contrib.auth import get_user_model
        User = get_user_model()

        success_msg = None
        error_msg = None

        if request.method == 'POST':
            user_id = request.POST.get('user_id')
            package_id = request.POST.get('package_id')
            try:
                user = User.objects.get(pk=user_id)
                package = GamePackage.objects.get(pk=package_id, is_active=True)

                # تحقق: هل يملك المستخدم هذه الحزمة مسبقاً؟
                already = UserPurchase.objects.filter(
                    user=user, package=package, is_completed=True
                ).exists()
                if already:
                    error_msg = f"المستخدم {user.username} يملك هذه الحزمة مسبقاً."
                else:
                    UserPurchase.objects.create(
                        user=user,
                        package=package,
                        is_completed=True,
                        is_gift=True,
                        expires_at=None,
                    )
                    success_msg = f"✅ تم منح حزمة «{package}» للمستخدم {user.username} كهدية."
            except User.DoesNotExist:
                error_msg = "المستخدم غير موجود."
            except GamePackage.DoesNotExist:
                error_msg = "الحزمة غير موجودة أو غير فعّالة."
            except Exception as e:
                error_msg = f"خطأ: {e}"

        users = User.objects.all().order_by('username')
        packages = GamePackage.objects.filter(is_active=True, is_free=False).order_by('game_type', 'package_number')

        # آخر 10 هدايا
        recent_gifts = UserPurchase.objects.filter(is_gift=True).select_related('user', 'package').order_by('-purchase_date')[:10]

        gifts_rows = "".join(
            f"<tr>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{g.user.username}</td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{g.package.get_game_type_display()} / حزمة {g.package.package_number}</td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{g.purchase_date.strftime('%Y-%m-%d %H:%M')}</td>"
            f"</tr>"
            for g in recent_gifts
        ) or "<tr><td colspan='3' style='padding:12px;color:#94a3b8;'>لا توجد هدايا بعد</td></tr>"

        users_options = "".join(f'<option value="{u.id}">{u.username} ({u.email})</option>' for u in users)
        packages_options = "".join(
            f'<option value="{p.id}">[{p.get_game_type_display()}] حزمة {p.package_number} — {p.effective_price} ﷼</option>'
            for p in packages
        )

        alert_html = ""
        if success_msg:
            alert_html = f'<div style="background:#d1fae5;border:1px solid #34d399;color:#065f46;padding:12px;border-radius:10px;margin-bottom:12px;font-weight:700;">{success_msg}</div>'
        elif error_msg:
            alert_html = f'<div style="background:#fee2e2;border:1px solid #f87171;color:#7f1d1d;padding:12px;border-radius:10px;margin-bottom:12px;font-weight:700;">{error_msg}</div>'

        html = f"""
        <div style="padding:16px 20px;max-width:700px;">
          <h2 style="margin:0 0 16px;">🎁 منح هدية</h2>
          {alert_html}
          <div class="module" style="padding:16px;border-radius:12px;background:#0b1220;border:1px solid #1f2937;margin-bottom:20px;">
            <form method="post" action="">
              <input type="hidden" name="csrfmiddlewaretoken" value="{request.META.get('CSRF_COOKIE','')}" />
              <div style="display:grid;gap:12px;">
                <div>
                  <label style="color:#cbd5e1;font-weight:700;display:block;margin-bottom:6px;">المستخدم</label>
                  <select name="user_id" required style="width:100%;padding:10px;border-radius:8px;background:#111827;color:#e2e8f0;border:1px solid #374151;">
                    <option value="">— اختر مستخدم —</option>
                    {users_options}
                  </select>
                </div>
                <div>
                  <label style="color:#cbd5e1;font-weight:700;display:block;margin-bottom:6px;">الحزمة</label>
                  <select name="package_id" required style="width:100%;padding:10px;border-radius:8px;background:#111827;color:#e2e8f0;border:1px solid #374151;">
                    <option value="">— اختر حزمة —</option>
                    {packages_options}
                  </select>
                </div>
                <button type="submit" class="button" style="background:#8b5cf6;color:#fff;padding:10px 20px;border-radius:8px;font-weight:700;border:none;cursor:pointer;width:fit-content;">
                  🎁 منح الهدية
                </button>
              </div>
            </form>
          </div>
          <h3 style="margin:0 0 8px;">آخر الهدايا الممنوحة</h3>
          <div class="module" style="border-radius:12px;overflow:hidden;">
            <table class="listing" style="width:100%;border-collapse:collapse;background:#0b1220;">
              <thead style="background:#0f172a;color:#cbd5e1;">
                <tr>
                  <th style="padding:10px 12px;text-align:right;">المستخدم</th>
                  <th style="padding:10px 12px;text-align:right;">الحزمة</th>
                  <th style="padding:10px 12px;text-align:right;">التاريخ</th>
                </tr>
              </thead>
              <tbody style="color:#e2e8f0;">{gifts_rows}</tbody>
            </table>
          </div>
        </div>
        """

        ctx = {**self.admin_site.each_context(request), "title": "منح هدية", "content": mark_safe(html)}
        return TemplateResponse(request, "admin/simple_box.html", ctx)

    def analytics_view(self, request):
        days = int(request.GET.get("days", 30) or 30)
        end = timezone.now()
        start = end - timezone.timedelta(days=days)

        price_expr = _price_case_expr()
        period_purchases = UserPurchase.objects.filter(purchase_date__gte=start, purchase_date__lte=end)
        period_total = period_purchases.count()
        period_revenue = period_purchases.filter(is_gift=False).aggregate(total=Coalesce(Sum(price_expr), Decimal("0.00")))['total'] or Decimal("0.00")
        gifts_count = period_purchases.filter(is_gift=True).count()
        period_users = set(period_purchases.values_list('user_id', flat=True))
        period_unique = len(period_users)

        # عائد العملاء (مدى الحياة)
        all_buyers_agg = UserPurchase.objects.values('user').annotate(c=Count('id'))
        lifetime_buyers = all_buyers_agg.count()
        lifetime_returning = all_buyers_agg.filter(c__gt=1).count()
        lifetime_return_rate = (lifetime_returning / lifetime_buyers * 100) if lifetime_buyers else 0

        # عائد العملاء خلال الفترة
        prior_buyers_in_period = UserPurchase.objects.filter(
            user_id__in=period_users, purchase_date__lt=start
        ).values('user_id').distinct().count()
        period_return_rate = (prior_buyers_in_period / period_unique * 100) if period_unique else 0

        # جلسات
        period_sessions = GameSession.objects.filter(created_at__gte=start, created_at__lte=end)
        total_sessions = period_sessions.count()
        completed_sessions = period_sessions.filter(is_completed=True).count()
        active_sessions = period_sessions.filter(is_active=True).count()
        completion_rate = (completed_sessions / total_sessions * 100) if total_sessions else 0

        # توزيع حسب النوع
        top_types_qs = period_purchases.values('package__game_type').annotate(n=Count('id')).order_by('-n')
        type_map_ar = {'letters': 'خلية الحروف', 'images': 'تحدي الصور', 'quiz': 'سؤال وجواب', 'time': 'تحدي الوقت'}
        most_type_label = type_map_ar.get(top_types_qs[0]['package__game_type'], '—') if top_types_qs else '—'

        # اتجاه 14 يوم
        trend_days = 14
        t_start = end - timezone.timedelta(days=trend_days)
        by_day_purchases = UserPurchase.objects.filter(purchase_date__gte=t_start, purchase_date__lte=end)\
            .annotate(d=TruncDate('purchase_date')).values('d').annotate(n=Count('id')).order_by('d')
        by_day_sessions = GameSession.objects.filter(created_at__gte=t_start, created_at__lte=end)\
            .annotate(d=TruncDate('created_at')).values('d').annotate(n=Count('id')).order_by('d')

        days_map = {}
        for r in by_day_purchases:
            days_map.setdefault(r['d'], {'p': 0, 's': 0})
            days_map[r['d']]['p'] = r['n']
        for r in by_day_sessions:
            days_map.setdefault(r['d'], {'p': 0, 's': 0})
            days_map[r['d']]['s'] = r['n']
        peak = max([v['p'] for v in days_map.values()] or [1])
        trend_rows = []
        for d in sorted(days_map.keys()):
            p = days_map[d]['p']; s = days_map[d]['s']
            pct = (p * 100 / peak) if peak else 0
            trend_rows.append(
                f"<tr>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #1f2937;'>{d}</td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #1f2937;'>{p}</td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #1f2937;'>{s}</td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #1f2937;'>"
                f"<div style='display:flex;align-items:center;gap:8px;'>"
                f"<div style='flex:1;background:#111827;border-radius:999px;overflow:hidden;height:8px;'>"
                f"<div style='width:{int(pct)}%;height:8px;background:#3b82f6;'></div>"
                f"</div><span style='font-size:12px;color:#94a3b8;'>{int(pct)}%</span></div></td></tr>"
            )

        kpis = [
            _kpi_card(f"مشتريات (آخر {days} يوم)", f"{period_total:,}", f"مستخدمون مميزون: {period_unique:,}", "ok" if period_total else "warn"),
            _kpi_card("إيراد تقديري الفترة", _sar(period_revenue), "يعتمد على سعر الحزمة الحالي", "info"),
            _kpi_card("معدّل إكمال الجلسات", f"{completion_rate:.1f}%", f"نشطة: {active_sessions} / مكتملة: {completed_sessions}", "ok" if completion_rate >= 60 else "warn"),
            _kpi_card("أكثر نوع هذه الفترة", most_type_label, "حسب عدد المشتريات", "info"),
            _kpi_card("🎁 هدايا ممنوحة", str(gifts_count), "لا تُحتسب في الإيراد", "warn"),
            _kpi_card("عائد العملاء (مدى الحياة)", f"{lifetime_return_rate:.1f}%", "من اشتروا أكثر من مرة", "info"),
            _kpi_card("عودة عملاء الفترة", f"{period_return_rate:.1f}%", "من اشترى سابقًا ثم اشترى الآن", "info"),
        ]

        tb_types = "".join([
            f"<tr><td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{type_map_ar.get(t['package__game_type'],'—')}</td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{t['n']}</td></tr>"
            for t in top_types_qs
        ])

        html = f"""
        <div style="padding:16px 20px;">
          <h2 style="margin:0 0 10px;">📈 لوحة التحليلات</h2>
          <div style="margin-bottom:12px;">
            <a class="button" href="{reverse('admin:games_purchases_grant_gift')}" style="background:#8b5cf6;color:#fff;padding:8px 14px;border-radius:8px;text-decoration:none;font-weight:700;">🎁 منح هدية</a>
          </div>
          <div style="margin:6px 0 14px;color:#94a3b8;font-size:13px;">
            المدة الحالية: آخر {days} يوم —
            <a href="?days=14">14</a> · <a href="?days=30">30</a> · <a href="?days=60">60</a> · <a href="?days=90">90</a>
            &nbsp;|&nbsp;
            <a href="{reverse('admin:games_purchases_analytics_csv')}?days={days}">تنزيل CSV للتقرير</a>
          </div>
          <form method="get" style="margin:8px 0;">
            <div class="module" style="padding:12px;border-radius:12px;background:#0b1220;border:1px solid #1f2937;display:flex;gap:8px;align-items:flex-end;">
              <div><label>الأيام</label><input type="number" min="1" name="days" value="{days}" style="width:120px"></div>
              <div><button class="button">تحديث</button></div>
            </div>
          </form>
          <div style="display:flex;flex-wrap:wrap;gap:12px;">{''.join(kpis)}</div>
          <div style="margin-top:16px;">
            <h3 style="margin:6px 0;">🎮 توزيع حسب نوع اللعبة</h3>
            {_listing_table(["نوع اللعبة","عدد المشتريات"], [tb_types])}
          </div>
          <div style="margin-top:16px;">
            <h3 style="margin:6px 0;">📅 اتجاه يومي (آخر {trend_days} يوم)</h3>
            {_listing_table(["اليوم","مشتريات","جلسات","نسبة إلى الذروة"], trend_rows)}
          </div>
        </div>
        """
        ctx = {**self.admin_site.each_context(request), "title": "لوحة التحليلات", "content": mark_safe(html)}
        return TemplateResponse(request, "admin/simple_box.html", ctx)

    def analytics_csv_view(self, request):
        days = int(request.GET.get("days", 30))
        end = timezone.now()
        start = end - timezone.timedelta(days=days)
        p_expr = _price_case_expr()
        qs = UserPurchase.objects.filter(purchase_date__gte=start, purchase_date__lte=end).select_related("user","package").order_by("-purchase_date")
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="analytics_{days}d.csv"'
        w = csv.writer(response)
        w.writerow(["purchase_id","user","game_type","package_number","is_completed","purchase_date","expires_at","price_estimated"])
        for p in qs:
            price = (p.package.discounted_price
                     if (p.package.discounted_price and p.package.original_price and p.package.discounted_price > 0 and p.package.original_price > p.package.discounted_price)
                     else p.package.price)
            w.writerow([
                p.id, p.user.username,
                p.package.game_type, p.package.package_number,
                "1" if p.is_completed else "0",
                p.purchase_date.isoformat(),
                p.expires_at.isoformat() if p.expires_at else "",
                str(price),
            ])
        return response

@admin.register(Contestant)
class ContestantAdmin(admin.ModelAdmin):
    list_display = ('name', 'team', 'session_ref', 'is_active', 'joined_at')
    list_filter = ('team', 'is_active', 'session__game_type')
    search_fields = ('name', 'session__id')
    date_hierarchy = 'joined_at'
    ordering = ('-joined_at',)
    list_select_related = ('session',)
    def session_ref(self, obj):
        return f"{obj.session.game_type} / {obj.session.id}"
    session_ref.short_description = "الجلسة"

# ========= تحدّي الوقت =========

# ========= تحدّي الوقت =========

def _img_thumb(url, h=56):
    if not url:
        return "—"
    return format_html(
        '<img src="{}" style="height:{}px;border-radius:6px;border:1px solid #ddd;" alt="thumb"/>',
        escape(url), h
    )

@admin.register(TimeCategory)
class TimeCategoryAdmin(admin.ModelAdmin):
    # أعمدة القائمة
    list_display = (
        'name_col', 'is_free_col', 'is_active_col', 'order_col',
        'packages_count', 'free_pkg_ok', 'cover_preview', 'row_actions',
    )
    list_filter   = (('is_free_category', admin.BooleanFieldListFilter),
                     ('is_active', admin.BooleanFieldListFilter))
    search_fields = ('name', 'slug')
    ordering      = ('order', 'name')

    # أعمدة بعناوين عربية
    def name_col(self, obj): return obj.name
    name_col.short_description = "الاسم"

    def is_free_col(self, obj): return "نعم" if obj.is_free_category else "لا"
    is_free_col.short_description = "فئة مجانية؟"

    def is_active_col(self, obj): return "نعم" if obj.is_active else "لا"
    is_active_col.short_description = "فعّالة؟"

    def order_col(self, obj): return obj.order
    order_col.short_description = "الترتيب"

    def packages_count(self, obj):
        return obj.time_packages.filter(game_type='time').count()
    packages_count.short_description = "عدد الحزم"

    def free_pkg_ok(self, obj):
        if not obj.is_free_category:
            return "—"
        ok = obj.time_packages.filter(game_type='time', package_number=0, is_active=True).exists()
        return "✅" if ok else "⚠️ لا توجد حزمة 0 فعّالة"
    free_pkg_ok.short_description = "حزمة التجربة"

    def cover_preview(self, obj):
        if not getattr(obj, "cover_image", None):
            return "—"
        return _img_thumb(obj.cover_image, h=40)
    cover_preview.short_description = "الغلاف"

    # زر سريع للانتقال إلى قائمة الحزم ضمن هذا التصنيف
    def row_actions(self, obj):
        pkgs_url = reverse('admin:games_timepackage_changelist') + f'?time_category__id__exact={obj.id}'
        return mark_safe(f'<a class="button" href="{pkgs_url}">📦 إدارة الحزم</a>')
    row_actions.short_description = "إجراءات"

    # صفحة معلومات/لوحة مبسطة للفئات
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("dashboard/", self.admin_site.admin_view(self.dashboard_view),
                 name="games_timecategory_dashboard"),
        ]
        return custom + urls

    def dashboard_view(self, request):
        """
        تعرض كل الفئات + إن تم تمرير مستخدم (?user_id= أو ?user=اسم/إيميل)
        تُظهر أيضًا "الحزم المدفوعة المتبقية" لهذا المستخدم في كل فئة.
        """
        user_id = request.GET.get("user_id", "").strip()
        user_q  = request.GET.get("user", "").strip()

        from django.contrib.auth import get_user_model
        U = get_user_model()
        user_obj = None
        if user_id:
            try:
                user_obj = U.objects.get(pk=user_id)
            except U.DoesNotExist:
                user_obj = None
        elif user_q:
            user_obj = U.objects.filter(
                Q(username__iexact=user_q) | Q(email__iexact=user_q) | Q(first_name__iexact=user_q)
            ).first()

        rows = []
        cats = TimeCategory.objects.all().order_by('order', 'name').prefetch_related('time_packages')
        for cat in cats:
            total_pkgs = cat.time_packages.filter(game_type='time').count()
            paid_pkgs  = cat.time_packages.filter(game_type='time').exclude(package_number=0).count()
            free_ok    = cat.time_packages.filter(game_type='time', package_number=0, is_active=True).exists()

            remaining_txt = "—"
            if user_obj:
                played = (TimePlayHistory.objects
                          .filter(user=user_obj, category=cat)
                          .exclude(package__package_number=0)
                          .values('package_id').distinct().count())
                remaining = max(0, paid_pkgs - played)
                remaining_txt = f"{remaining} من {paid_pkgs}"

            rows.append(
                f"<tr>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{escape(cat.name)}</td>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{_img_thumb(cat.cover_image, h=36) if cat.cover_image else '—'}</td>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{'نعم' if cat.is_active else 'لا'}</td>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{total_pkgs}</td>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{'✅' if free_ok else '⚠️'}</td>"
                f"<td style='padding:10px 12px;border-bottom:1px solid #1f2937;'>{remaining_txt}</td>"
                f"</tr>"
            )

        user_badge = f"المستخدم: <b>{escape(getattr(user_obj,'username', '—'))}</b>" if user_obj else "لم يتم تحديد مستخدم"
        html = f"""
        <div style="padding:16px 20px;">
          <h2 style="margin:0 0 10px;">⏱️ لوحة فئات تحدّي الوقت</h2>
          <form method="get" style="margin:8px 0;">
            <div class="module" style="padding:12px;border-radius:12px;background:#0b1220;border:1px solid #1f2937;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;align-items:end;">
              <div><label>المستخدم (ID)</label><input type="text" name="user_id" value="{escape(user_id)}" placeholder="رقم المستخدم" style="width:100%"></div>
              <div><label>المستخدم (اسم/إيميل)</label><input type="text" name="user" value="{escape(user_q)}" placeholder="username أو email" style="width:100%"></div>
              <div style="align-self:end;"><button class="button" style="width:100%;">تحديث</button></div>
              <div style="align-self:end;color:#9ca3af;">{user_badge}</div>
            </div>
          </form>
          <div style="margin:10px 0 14px;color:#94a3b8;font-size:13px;">
            * الحزمة رقم <b>0</b> لكل فئة هي الحزمة <b>المجانية</b> (تظهر هنا على شكل ✅ إن كانت فعّالة).<br>
            * الحزم المتبقية = جميع الحزم <b>المدفوعة</b> في الفئة ناقص الحزم التي لعبها هذا المستخدم (باستثناء #0).
          </div>
          {_listing_table(["الفئة","الغلاف","فعّالة؟","عدد الحزم","حزمة #0 نشطة","الحزم المتبقية (للمستخدم)"], rows)}
        </div>
        """
        ctx = {**self.admin_site.each_context(request),
               "title": "فئات تحدّي الوقت", "content": mark_safe(html)}
        return TemplateResponse(request, "admin/simple_box.html", ctx)


# Proxy لإظهار حِزم تحدّي الوقت ككيان مستقل في الأدمن
class TimePackage(GamePackage):
    class Meta:
        proxy = True
        verbose_name = "حزمة - تحدّي الوقت"
        verbose_name_plural = "حزم - تحدّي الوقت"


# حدّ/تحقق لألغاز الحزمة
class TimeRiddleInlineFormSet(forms.models.BaseInlineFormSet):
    """
    - حد أقصى 80 لغزًا لكل حزمة.
    - يمنع تكرار order داخل نفس الحزمة.
    """
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        alive = []
        orders = set()
        dup_orders = set()
        for form in self.forms:
            if form.cleaned_data.get('DELETE'):
                continue
            if (not form.cleaned_data) and (not form.instance.pk):
                continue
            alive.append(form)
            o = form.cleaned_data.get('order') or getattr(form.instance, 'order', None)
            if o is not None:
                if o in orders:
                    dup_orders.add(o)
                orders.add(o)
        if len(alive) > 80:
            raise forms.ValidationError("الحدّ الأقصى لعدد الألغاز في حزمة تحدّي الوقت هو 80 صورة.")
        if dup_orders:
            dup_s = ", ".join(str(x) for x in sorted(dup_orders))
            raise forms.ValidationError(f"يوجد تكرار في (الترتيب): {dup_s}. اجعل كل ترتيب فريدًا داخل الحزمة.")


class TimeRiddleInline(admin.TabularInline):
    model = TimeRiddle
    extra = 0
    formset = TimeRiddleInlineFormSet
    fields = ('order', 'image_url', 'answer', 'hint', 'thumb_tag')
    readonly_fields = ('thumb_tag',)
    ordering = ('order',)

    def thumb_tag(self, obj):
        return _img_thumb(getattr(obj, 'image_url', ''))
    thumb_tag.short_description = "معاينة"

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'accepted_answers':
            field.widget.attrs['placeholder'] = '["إجابة بديلة 1", "إجابة بديلة 2"]'
            field.widget.attrs['style'] = 'font-family: monospace; direction: ltr;'
        return field


@admin.register(TimePackage)
class TimePackageAdmin(admin.ModelAdmin):
    list_display  = ('pkg_ref', 'category_ref', 'is_free_icon',
                     'status_badge', 'created_at', 'manage_riddles')
    list_filter   = ('is_active', 'is_free', 'time_category', 'created_at')
    search_fields = ('package_number', 'description', 'time_category__name')
    ordering      = ('time_category__order', 'time_category__name', 'package_number')
    inlines       = [TimeRiddleInline]
    fieldsets     = (
        ('المعلومات الأساسية', {'fields': ('time_category', 'package_number', 'is_free', 'is_active')}),
        ('التسعير/الوصف',     {'fields': (('original_price','discounted_price','price'), 'description')}),
    )

    def get_queryset(self, request):
        return (super()
                .get_queryset(request)
                .filter(game_type='time')
                .select_related('time_category'))

    def save_model(self, request, obj, form, change):
        obj.game_type = 'time'
        super().save_model(request, obj, form, change)

    # أعمدة عرض
    def pkg_ref(self, obj):
        tag = "مجانية" if (obj.is_free or obj.package_number == 0) else f"#{obj.package_number}"
        return format_html("<b>حزمة {}</b>", tag)
    pkg_ref.short_description = "الحزمة"

    def category_ref(self, obj):
        return obj.time_category.name if obj.time_category else "—"
    category_ref.short_description = "التصنيف"

    def is_free_icon(self, obj):
        return "✅" if (obj.is_free or obj.package_number == 0) else "—"
    is_free_icon.short_description = "مجانية"

    def status_badge(self, obj):
        return mark_safe(
            f"<b style='color:{'green' if obj.is_active else 'red'};'>"
            f"{'فعّالة' if obj.is_active else 'غير فعّالة'}</b>"
        )
    status_badge.short_description = "الحالة"

    # زرَّان: عرض الألغاز + رفع ZIP
    def manage_riddles(self, obj):
        list_url       = reverse('admin:games_timeriddle_changelist') + f'?package__id__exact={obj.id}'
        upload_zip_url = reverse('admin:games_timepackage_upload_zip', args=[obj.id])
        return mark_safe(
            f'<a class="button" href="{list_url}" style="background:#0ea5e9;color:#0b1220;padding:4px 8px;border-radius:6px;margin-left:6px;">🖼️ عرض الألغاز</a>'
            f'<a class="button" href="{upload_zip_url}" style="background:#22c55e;color:#0b1220;padding:4px 8px;border-radius:6px;">📦 رفع ZIP</a>'
        )
    manage_riddles.short_description = "إجراءات"

    # مسار صفحة الرفع
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("<uuid:pk>/upload-zip/",
                 self.admin_site.admin_view(self.upload_time_zip_view),
                 name="games_timepackage_upload_zip"),
        ]
        return custom + urls

    # فيو رفع ZIP (Cloudinary إن وُجد أو MEDIA)
    def upload_time_zip_view(self, request, pk):
        package = get_object_or_404(GamePackage, pk=pk, game_type='time')

        if request.method != 'POST':
            ctx = {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": f"رفع ملف ZIP للصور — {package.time_category.name if package.time_category else 'تصنيف'} / حزمة {package.package_number}",
                "package": package,
                "accept": ".zip",
                "download_template_url": "",
                "export_url": "",
                "change_url": reverse('admin:games_timepackage_change', args=[package.id]),
                "back_url": reverse('admin:games_timepackage_changelist'),
                "help_rows": [
                    "ارفع ملف ZIP يحتوي على صور اللغز لهذا التصنيف.",
                    "اسم كل ملف يُستخدم كإجابة (بدون الامتداد): مثال السعودية.jpg → الإجابة: السعودية.",
                    "الحد الأقصى لعدد الألغاز في الحزمة: 80.",
                    "لو أردت الاستبدال، فعّل خيار الحذف قبل الرفع.",
                ],
                "extra_note": "يدعم: jpg, jpeg, png, webp, gif, bmp. وإن لم يوجد امتداد نحاول التعرف تلقائيًا.",
                "submit_label": "رفع الملف",
                "replace_label": "حذف الألغاز الحالية قبل الاستيراد",
            }
            return TemplateResponse(request, "admin/import_csv.html", ctx)

        # POST
        file = request.FILES.get('file')
        replace_existing = bool(request.POST.get('replace'))

        if not file:
            messages.error(request, "يرجى اختيار ملف ZIP.")
            return HttpResponseRedirect(request.path)

        if replace_existing:
            package.time_riddles.all().delete()

        import os, io, zipfile, imghdr
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        # Cloudinary إن توافرت
        use_cloudinary = False
        uploader = None
        try:
            import cloudinary.uploader as _uploader
            uploader = _uploader
            use_cloudinary = True
        except Exception:
            use_cloudinary = False

        ALLOWED_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}

        def _normalize_name(name: str) -> str:
            name = os.path.basename(name)
            try:
                name.encode('utf-8')
            except Exception:
                try:
                    name = name.encode('cp437').decode('utf-8', 'ignore')
                except Exception:
                    name = name.encode('latin1', 'ignore').decode('utf-8', 'ignore')
            return name

        def _answer_from_filename(fname: str) -> str:
            base, _ext = os.path.splitext(fname)
            base = base.replace('_', ' ').replace('-', ' ').strip()
            return " ".join(base.split())

        def _is_image_bytes(data: bytes) -> bool:
            return bool(imghdr.what(None, h=data))

        # تحقق الحد الأقصى
        current_count = package.time_riddles.count()
        max_allowed   = 80
        can_add       = max(0, max_allowed - current_count)
        if can_add <= 0:
            messages.error(request, f"هذه الحزمة وصلت الحد الأقصى ({max_allowed}) من الألغاز.")
            return HttpResponseRedirect(reverse('admin:games_timepackage_change', args=[package.id]))

        start_order = (package.time_riddles.aggregate(Max('order'))['order__max'] or 0) + 1

        added = skipped = failed = 0
        notes = []

        try:
            with zipfile.ZipFile(file) as zf:
                for zinfo in zf.infolist():
                    if added >= can_add:
                        skipped += 1
                        notes.append("تخطي الباقي: وصلنا للحد الأقصى 80.")
                        break
                    if zinfo.is_dir():
                        continue

                    raw_name = _normalize_name(zinfo.filename)
                    if not raw_name:
                        continue

                    _, ext = os.path.splitext(raw_name)
                    ext_norm = (ext or "").lower()

                    data = zf.read(zinfo)
                    is_image = (ext_norm in ALLOWED_EXTS) or _is_image_bytes(data)
                    if not is_image:
                        skipped += 1
                        if len(notes) < 5:
                            notes.append(f"تخطي «{raw_name}»: ليس ملف صورة مدعوم.")
                        continue

                    try:
                        if use_cloudinary and uploader:
                            up = uploader.upload(
                                io.BytesIO(data),
                                folder=f"wesh/time/{package.id}",
                                public_id=None,
                                resource_type="image",
                            )
                            image_url = up.get('secure_url') or up.get('url')
                        else:
                            safe_name = raw_name
                            base, ext0 = os.path.splitext(safe_name)
                            idx = 1
                            path = f"time_riddles/{package.id}/{safe_name}"
                            while default_storage.exists(path):
                                safe_name = f"{base}_{idx}{ext0}"
                                path = f"time_riddles/{package.id}/{safe_name}"
                                idx += 1
                            saved_path = default_storage.save(path, ContentFile(data))
                            from django.conf import settings
                            media_url = getattr(settings, 'MEDIA_URL', '/media/')
                            image_url = media_url.rstrip('/') + '/' + saved_path.lstrip('/')

                    except Exception as e:
                        failed += 1
                        if len(notes) < 5:
                            notes.append(f"فشل رفع «{raw_name}»: {e}")
                        continue

                    answer = _answer_from_filename(raw_name)

                    try:
                        TimeRiddle.objects.create(
                            package=package,
                            order=start_order + added,
                            image_url=image_url,
                            answer=answer,
                            hint=""
                        )
                        added += 1
                    except Exception as e:
                        failed += 1
                        if len(notes) < 5:
                            notes.append(f"فشل إنشاء سجل «{raw_name}»: {e}")

        except zipfile.BadZipFile:
            messages.error(request, "الملف ليس ZIP صالحًا.")
            return HttpResponseRedirect(request.path)
        except Exception as e:
            messages.error(request, f"حدث خطأ أثناء قراءة الملف: {e}")
            return HttpResponseRedirect(request.path)

        if added and not (failed or skipped):
            messages.success(request, f"تم رفع {added} صورة بنجاح وإضافتها كلغاز.")
        else:
            parts = [f"تمت إضافة {added} لغز."]
            if skipped: parts.append(f"تخطي {skipped} عنصر.")
            if failed:  parts.append(f"فشل {failed} عنصر.")
            if notes:   parts.append("ملاحظات: " + " | ".join(notes))
            level = messages.WARNING if (skipped or failed) else messages.SUCCESS
            messages.add_message(request, level, " ".join(parts))

        return HttpResponseRedirect(reverse('admin:games_timepackage_change', args=[package.id]))


@admin.register(TimeRiddle)
class TimeRiddleAdmin(admin.ModelAdmin):
    list_display  = ('package_ref', 'order', 'answer', 'hint_short', 'thumb')
    list_editable = ('order',)
    list_filter   = ('package__time_category', 'package__is_active', 'created_at')
    search_fields = ('answer', 'hint', 'package__package_number', 'package__time_category__name')
    ordering      = ('package__time_category__order', 'package__package_number', 'order')

    def get_queryset(self, request):
        return (super()
                .get_queryset(request)
                .select_related('package', 'package__time_category')
                .filter(package__game_type='time'))

    def package_ref(self, obj):
        cat = obj.package.time_category.name if (obj.package and obj.package.time_category) else "—"
        num = obj.package.package_number if obj.package else "—"
        tag = "مجانية" if (obj.package and (obj.package.is_free or obj.package.package_number == 0)) else f"#{num}"
        return format_html("<b>{} / حزمة {}</b>", cat, tag)
    package_ref.short_description = "التصنيف/الحزمة"

    def hint_short(self, obj): return (obj.hint or '')[:40]
    hint_short.short_description = "تلميح"

    def thumb(self, obj):
        if not obj.image_url:
            return "—"
        return format_html('<img src="{}" style="height:48px;border-radius:6px;border:1px solid #ddd;" alt="thumb"/>',
                           escape(obj.image_url))
    thumb.short_description = "معاينة"







    # ========= امبوستر=========
from .models import ImposterWord


class ImposterPackage(GamePackage):
    class Meta:
        proxy = True
        verbose_name = "حزمة - إمبوستر"
        verbose_name_plural = "حزم - إمبوستر"

class ImposterWordInline(admin.TabularInline):
    model = ImposterWord
    fk_name = 'package'
    extra = 1
    fields = ('word',)


@admin.register(ImposterPackage)
class ImposterPackageAdmin(admin.ModelAdmin):
    list_display = (
        'package_info',
        'words_count',
        'price_info',
        'is_free_icon',
        'status_badge',
        'created_at',
    )
    list_filter = ('is_free', 'is_active', 'created_at')
    search_fields = ('package_number', 'description')
    ordering = ('package_number',)
    inlines = [ImposterWordInline]

    fieldsets = (
        ('المعلومات الأساسية', {
            'fields': (
                'package_number', 'is_free',
                ('original_price', 'discounted_price', 'price'),
                'is_active'
            )
        }),
        ('الوصف', {'fields': ('description',)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(game_type='imposter').annotate(
            _wcount=Count('imposter_words')
        )

    def package_info(self, obj):
        return f"حزمة {obj.package_number}"
    package_info.short_description = "الرقم"

    def words_count(self, obj):
        return obj._wcount
    words_count.short_description = "عدد الكلمات"

    def price_info(self, obj):
        if obj.is_free:
            return "🆓 مجانية"
        if obj.discounted_price and obj.original_price and obj.discounted_price < obj.original_price:
            return format_html('<s>{} ﷼</s> → <b>{} ﷼</b>', obj.original_price, obj.discounted_price)
        return f"{obj.price} ﷼"
    price_info.short_description = "السعر"

    def is_free_icon(self, obj):
        return "✅" if obj.is_free else "—"
    is_free_icon.short_description = "مجانية"

    def status_badge(self, obj):
        return format_html(
            '<b style="color:{};">{}</b>',
            'green' if obj.is_active else 'red',
            'فعّالة' if obj.is_active else 'غير فعّالة'
        )
    status_badge.short_description = "الحالة"

    def save_model(self, request, obj, form, change):
        obj.game_type = 'imposter'
        super().save_model(request, obj, form, change)



# =========================
#  فاميلي فيود
# =========================

from .models import FamilyFeudQuestion, FamilyFeudAnswer, FamilyFeudProgress


class FeudPackage(GamePackage):
    class Meta:
        proxy = True
        verbose_name = "حزمة - فاميلي فيود"
        verbose_name_plural = "حزم - فاميلي فيود"


class FamilyFeudAnswerInline(admin.TabularInline):
    model = FamilyFeudAnswer
    fk_name = 'question'
    extra = 4
    max_num = 10
    min_num = 4
    validate_min = True
    validate_max = True
    fields = ('rank', 'text', 'points')
    ordering = ('rank',)

    def get_extra(self, request, obj=None, **kwargs):
        if obj is None:
            return 4
        count = obj.answers.count()
        return max(0, 4 - count)


class FamilyFeudQuestionInline(admin.StackedInline):
    model = FamilyFeudQuestion
    fk_name = 'package'
    extra = 0
    fields = ('order', 'question_text', 'multiplier')
    ordering = ('order',)
    show_change_link = True  # ← زر "تعديل" يفتح صفحة السؤال مع الإجابات

    def get_queryset(self, request):
        return super().get_queryset(request).filter(package__game_type='feud')

    


@admin.register(FeudPackage)
class FeudPackageAdmin(admin.ModelAdmin):
    list_display = (
        'package_info',
        'questions_count',
        'price_info',
        'is_free_icon',
        'status_badge',
        'created_at',
        'feud_actions',
    )
    list_filter = ('is_free', 'is_active', 'created_at')
    search_fields = ('package_number', 'description')
    ordering = ('package_number',)
    inlines = [FamilyFeudQuestionInline]

    fieldsets = (
        ('المعلومات الأساسية', {
            'fields': (
                'package_number', 'is_free',
                ('original_price', 'discounted_price', 'price'),
                'is_active'
            )
        }),
        ('الوصف', {'fields': ('description',)}),
    )

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .filter(game_type='feud')
            .annotate(
                _qcount=Count('feud_questions', distinct=True),
                _acount=Count('feud_questions__answers', distinct=True),
                _pcount=Coalesce(
                    Sum('feud_questions__answers__points'),
                    0,
                    output_field=IntegerField()
                )
            )
        )

    def save_model(self, request, obj, form, change):
        obj.game_type = 'feud'
        super().save_model(request, obj, form, change)

    def package_info(self, obj):
        return f"حزمة {obj.package_number}"
    package_info.short_description = "الرقم"

    def questions_count(self, obj):
        count   = getattr(obj, '_qcount', 0)
        answers = getattr(obj, '_acount', 0)
        points  = getattr(obj, '_pcount', 0)

        # لون حسب عدد الأسئلة
        if count == 9:
            color, icon = '#10b981', '✅'
            note = 'مثالي'
        elif count == 0:
            color, icon = '#ef4444', '❌'
            note = 'لا أسئلة'
        else:
            color, icon = '#f59e0b', '⚠️'
            note = f'المطلوب 9'

        return format_html(
            '<span style="color:{};font-weight:700;">{} {} سؤال</span>'
            '<br><span style="color:#94a3b8;font-size:0.78rem;">{} — 📝 {} إجابة | 🏆 {} نقطة</span>',
            color, icon, count, note, answers, points
        )
    questions_count.short_description = "الأسئلة"

    def price_info(self, obj):
        if obj.is_free:
            return "🆓 مجانية"
        if getattr(obj, 'has_discount', False):
            return format_html(
                '<span style="text-decoration:line-through;color:#64748b;">{} ﷼</span> → <b style="color:#0ea5e9;">{} ﷼</b>',
                obj.original_price, obj.discounted_price
            )
        return f"💰 {obj.price} ريال"
    price_info.short_description = "السعر"

    def is_free_icon(self, obj):
        return "✅" if obj.is_free else "—"
    is_free_icon.short_description = "مجانية"

    def status_badge(self, obj):
        return format_html(
            '<b style="color:{};">{}</b>',
            'green' if obj.is_active else 'red',
            'فعّالة' if obj.is_active else 'غير فعّالة'
        )
    status_badge.short_description = "الحالة"

    def feud_actions(self, obj):
        add_q_url = reverse('admin:games_familyfeudquestion_add') + f'?package={obj.id}'
        list_q_url = reverse('admin:games_familyfeudquestion_changelist') + f'?package__package_number={obj.package_number}'
        upload_url = reverse('admin:games_feudpackage_upload', args=[obj.id])
        template_url = reverse('admin:games_feudpackage_template')
        export_url = reverse('admin:games_feudpackage_export', args=[obj.id])
        return mark_safe(
            f'<a class="button" href="{add_q_url}" style="background:#22c55e;color:#0b1220;padding:4px 8px;border-radius:6px;text-decoration:none;margin-left:6px;">➕ سؤال جديد</a>'
            f'<a class="button" href="{list_q_url}" style="background:#6d28d9;color:#fff;padding:4px 8px;border-radius:6px;text-decoration:none;margin-left:6px;">📋 الأسئلة</a>'
            f'<a class="button" href="{upload_url}" style="background:#0ea5e9;color:#0b1220;padding:4px 8px;border-radius:6px;text-decoration:none;margin-left:6px;">📁 رفع CSV</a>'
            f'<a class="button" href="{template_url}" style="background:#475569;color:#fff;padding:4px 8px;border-radius:6px;text-decoration:none;margin-left:6px;">⬇️ قالب</a>'
            f'<a class="button" href="{export_url}" style="background:#6b7280;color:#fff;padding:4px 8px;border-radius:6px;text-decoration:none;">📤 تصدير</a>'
        )
    feud_actions.short_description = "إجراءات"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<uuid:pk>/upload/",
                self.admin_site.admin_view(self.upload_feud_view),
                name="games_feudpackage_upload"
            ),
            path(
                "<uuid:pk>/export/",
                self.admin_site.admin_view(self.export_feud_view),
                name="games_feudpackage_export"
            ),
            path(
                "template/",
                self.admin_site.admin_view(self.download_feud_template),
                name="games_feudpackage_template"
            ),
        ]
        return custom + urls

    def download_feud_template(self, request):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="feud_template.csv"'
        w = csv.writer(response)
        w.writerow(['رقم_السؤال', 'السؤال', 'المضاعف', 'ترتيب_الاجابة', 'نص_الاجابة', 'النقاط'])
        w.writerow([1, 'أكثر شيء يحبه الناس في الصيف؟', 1, 1, 'البحر', 42])
        w.writerow([1, 'أكثر شيء يحبه الناس في الصيف؟', 1, 2, 'السفر', 28])
        w.writerow([1, 'أكثر شيء يحبه الناس في الصيف؟', 1, 3, 'النوم', 15])
        w.writerow([1, 'أكثر شيء يحبه الناس في الصيف؟', 1, 4, 'الأكل', 10])
        w.writerow([1, 'أكثر شيء يحبه الناس في الصيف؟', 1, 5, 'المكيف', 5])
        w.writerow([2, 'أشهر رياضة في السعودية؟', 1, 1, 'كرة القدم', 55])
        w.writerow([2, 'أشهر رياضة في السعودية؟', 1, 2, 'السباحة', 20])
        w.writerow([2, 'أشهر رياضة في السعودية؟', 1, 3, 'كرة السلة', 15])
        w.writerow([2, 'أشهر رياضة في السعودية؟', 1, 4, 'الجري', 10])
        return response

    def upload_feud_view(self, request, pk):
        package = get_object_or_404(GamePackage, pk=pk, game_type='feud')

        if request.method != 'POST':
            ctx = {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": f"رفع أسئلة فاميلي فيود — حزمة {package.package_number}",
                "package": package,
                "accept": ".csv",
                "download_template_url": reverse('admin:games_feudpackage_template'),
                "export_url": reverse('admin:games_feudpackage_export', args=[package.id]),
                "change_url": reverse('admin:games_feudpackage_change', args=[package.id]),
                "back_url": reverse('admin:games_feudpackage_changelist'),
                "help_rows": [
                    "الأعمدة: رقم_السؤال | السؤال | المضاعف | ترتيب_الاجابة | نص_الاجابة | النقاط",
                    "المضاعف: 1 (عادي) أو 2 أو 3",
                    "كل سؤال يجب أن يكون له من 4 إلى 8 إجابات.",
                    "ترتيب الإجابة: 1 = الأكثر شيوعاً.",
                ],
                "extra_note": "تفعيل خيار الحذف سيحذف جميع أسئلة الحزمة قبل الرفع.",
                "submit_label": "رفع الملف",
                "replace_label": "حذف الأسئلة الحالية قبل الرفع",
            }
            return TemplateResponse(request, "admin/import_csv.html", ctx)

        file = request.FILES.get('file')
        replace = bool(request.POST.get('replace'))

        if not file:
            messages.error(request, "يرجى اختيار ملف CSV.")
            return HttpResponseRedirect(request.path)

        if replace:
            FamilyFeudQuestion.objects.filter(package=package).delete()

        try:
            decoded = file.read().decode('utf-8-sig', errors='ignore')
            reader = csv.reader(io.StringIO(decoded))
            next(reader, None)  # تخطي الهيدر

            # نجمع الأسئلة والإجابات أولاً قبل الحفظ
            questions_map = {}  # رقم_السؤال → {text, multiplier, answers[]}

            for row in reader:
                if not row or len(row) < 6:
                    continue
                q_num_raw, q_text, multiplier_raw, rank_raw, ans_text, points_raw = [
                    (str(x).strip() if x is not None else '') for x in row[:6]
                ]
                if not q_num_raw or not q_text or not ans_text:
                    continue
                try:
                    q_num = int(q_num_raw)
                    rank = int(rank_raw)
                    points = int(points_raw)
                    multiplier = int(multiplier_raw) if multiplier_raw in ('1', '2', '3') else 1
                except (ValueError, TypeError):
                    continue

                if q_num not in questions_map:
                    questions_map[q_num] = {
                        'text': q_text,
                        'multiplier': multiplier,
                        'answers': []
                    }
                questions_map[q_num]['answers'].append({
                    'rank': rank,
                    'text': ans_text,
                    'points': points
                })

            if not questions_map:
                messages.error(request, "لم يتم التعرف على أي سؤال في الملف.")
                return HttpResponseRedirect(request.path)

            saved_q = 0
            saved_a = 0
            for q_num in sorted(questions_map.keys()):
                q_data = questions_map[q_num]
                q_obj, _ = FamilyFeudQuestion.objects.update_or_create(
                    package=package,
                    order=q_num,
                    defaults={
                        'question_text': q_data['text'],
                        'multiplier': q_data['multiplier'],
                    }
                )
                saved_q += 1
                for ans in q_data['answers']:
                    FamilyFeudAnswer.objects.update_or_create(
                        question=q_obj,
                        rank=ans['rank'],
                        defaults={
                            'text': ans['text'],
                            'points': ans['points'],
                        }
                    )
                    saved_a += 1

            messages.success(request, f"تم حفظ {saved_q} سؤال و {saved_a} إجابة بنجاح.")
            return HttpResponseRedirect(reverse('admin:games_feudpackage_changelist'))

        except Exception as e:
            messages.error(request, f"خطأ أثناء الرفع: {e}")
            return HttpResponseRedirect(request.path)

    def export_feud_view(self, request, pk):
        package = get_object_or_404(GamePackage, pk=pk, game_type='feud')
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="feud_package_{package.package_number}.csv"'
        w = csv.writer(response)
        w.writerow(['رقم_السؤال', 'السؤال', 'المضاعف', 'ترتيب_الاجابة', 'نص_الاجابة', 'النقاط'])
        for q in package.feud_questions.all().order_by('order').prefetch_related('answers'):
            for ans in q.answers.all().order_by('rank'):
                w.writerow([q.order, q.question_text, q.multiplier, ans.rank, ans.text, ans.points])
        return response


@admin.register(FamilyFeudQuestion)
class FamilyFeudQuestionAdmin(admin.ModelAdmin):
    list_display = ('package_ref', 'order', 'question_preview', 'answers_count', 'multiplier')
    list_filter  = ('package__package_number', 'multiplier')
    search_fields = ('question_text',)
    ordering = ('package__package_number', 'order')
    inlines  = [FamilyFeudAnswerInline]
    list_select_related = ('package',)

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .filter(package__game_type='feud')
            .annotate(_acount=Count('answers'))
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'package':
            kwargs['queryset'] = GamePackage.objects.filter(
                game_type='feud'
            ).order_by('package_number')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        pkg_id = request.GET.get('package')
        if pkg_id:
            last = FamilyFeudQuestion.objects.filter(
                package_id=pkg_id
            ).aggregate(Max('order'))['order__max'] or 0
            initial['order']   = last + 1
            initial['package'] = pkg_id
        return initial

    def package_ref(self, obj):
        return f"حزمة {obj.package.package_number}"
    package_ref.short_description = "الحزمة"

    def question_preview(self, obj):
        return (obj.question_text[:60] + '...') if len(obj.question_text) > 60 else obj.question_text
    question_preview.short_description = "السؤال"

    def answers_count(self, obj):
        count = getattr(obj, '_acount', 0)
        if count < 4:
            return format_html(
                '<span style="color:#ef4444;font-weight:700;">⚠️ {} إجابات — أقل من الحد (4)</span>',
                count
            )
        elif count == 10:
            return format_html('<span style="color:#10b981;font-weight:700;">✅ {} إجابات</span>', count)
        elif count > 10:
            return format_html('<span style="color:#ef4444;font-weight:700;">⚠️ {} إجابات — تجاوز الحد (10)</span>', count)

        elif count > 8:
            return format_html(
                '<span style="color:#ef4444;font-weight:700;">⚠️ {} إجابات — تجاوز الحد (8)</span>',
                count
            )
        else:
            return format_html(
                '<span style="color:#f59e0b;font-weight:700;">🟡 {} إجابات</span>',
                count
            )
    answers_count.short_description = "الإجابات"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # تحقق من عدد الأسئلة في الحزمة
        total = FamilyFeudQuestion.objects.filter(package=obj.package).count()
        if total > 9:
            messages.warning(
                request,
                f'⚠️ تجاوزت عدد الأسئلة المثالي: الحزمة {obj.package.package_number} تحتوي الآن {total} سؤالاً (المثالي 9).'
            )
        elif total == 9:
            messages.success(
                request,
                f'✅ ممتاز! الحزمة {obj.package.package_number} تحتوي الآن على 9 أسئلة.'
            )

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if formset.model == FamilyFeudAnswer:
            obj   = form.instance
            count = obj.answers.count()
            if count > 10:
                messages.error(request, f'⛔ السؤال "{obj.question_text[:40]}" يحتوي على {count} إجابات — الحد الأقصى 10.')

            elif count < 4:
                messages.warning( 
                    request,
                    f'⚠️ السؤال "{obj.question_text[:40]}" يحتوي على {count} إجابات فقط — الحد الأدنى المقترح 4.'
                )
                
                # ========= تحسينات عامة لواجهة الأدمن =========
admin.site.site_header = '🎮 إدارة الألعاب'
admin.site.site_title = 'لوحة تحكم وش الجواب'
admin.site.index_title = 'مرحبًا بك في لوحة التحكم'
