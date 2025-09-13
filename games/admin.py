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
    fk_name = 'package'     # مهم مع الـ Proxy
    extra = 0
    fields = ('letter', 'question_type', 'question', 'answer', 'category')
    show_change_link = True

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

@admin.register(LettersPackage)
class LettersPackageAdmin(admin.ModelAdmin):
    list_display = (
        'package_info',
        'theme_badge',
        'questions_count_badge',
        'price_info',
        'is_free_icon',
        'status_badge',
        'created_at',
        'letters_actions',
    )
    list_filter = ('is_free', 'is_active', 'question_theme', 'created_at')
    search_fields = ('package_number', 'description')
    inlines = [LettersGameQuestionInline]
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
        ('المحتوى', {'fields': ('description', 'question_theme')}),
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

        # util: normalize
        import re, unicodedata
        try:
            import openpyxl
            HAS_OPENPYXL = True
        except ImportError:
            HAS_OPENPYXL = False

        ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

        def strip_diacritics(s: str) -> str:
            s = s.replace("\u0640", "")  # TATWEEL
            norm = unicodedata.normalize('NFD', s)
            return "".join(ch for ch in norm if unicodedata.category(ch) != 'Mn')

        def normalize_qtype(raw):
            if raw is None:
                return None
            s = str(raw).strip()
            if not s:
                return None
            s = s.replace("\u200f", "").replace("\u200e", "")
            s = strip_diacritics(s)
            s = s.translate(ARABIC_INDIC)
            s = re.sub(r"\s+", " ", s).lower().strip()
            s_wo_al = s[2:] if s.startswith("ال") else s
            candidates = {s, s_wo_al}
            direct_map = {
                "main":"main","رئيسي":"main","اساسي":"main","أساسي":"main","رئيس":"main",
                "alt1":"alt1","alt 1":"alt1","بديل1":"alt1","بديل 1":"alt1","بديل اول":"alt1","بديل أول":"alt1",
                "alt2":"alt2","alt 2":"alt2","بديل2":"alt2","بديل 2":"alt2","بديل ثاني":"alt2","بديل الثاني":"alt2",
                "alt3":"alt3","alt 3":"alt3","بديل3":"alt3","بديل 3":"alt3","بديل ثالث":"alt3","بديل الثالث":"alt3",
                "alt4":"alt4","alt 4":"alt4","بديل4":"alt4","بديل 4":"alt4","بديل رابع":"alt4","بديل الرابع":"alt4",
            }
            for c in candidates:
                if c in direct_map:
                    return direct_map[c]
            for c in candidates:
                m = re.match(r"^(?:ال)?بديل\s*([1-4])$", c)
                if m: return f"alt{m.group(1)}"
            ordinal_map = {"اول":"1","أول":"1","ثاني":"2","ثالث":"3","رابع":"4"}
            for c in candidates:
                for ord_, num in ordinal_map.items():
                    if re.match(rf"^(?:ال)?بديل\s*{ord_}$", c):
                        return f"alt{num}"
            m = re.match(r"^alt\s*([1-4])$", s)
            if m: return f"alt{m.group(1)}"
            return None

        added = 0
        failed_rows = 0
        failed_examples = []

        def upsert_row(letter, qtype_raw, question, answer, category):
            nonlocal added, failed_rows, failed_examples
            qtype = normalize_qtype(qtype_raw)
            if not qtype:
                failed_rows += 1
                if len(failed_examples) < 5:
                    failed_examples.append(f"[الحرف={letter!s}, النوع='{qtype_raw!s}']")
                return
            LettersGameQuestion.objects.update_or_create(
                package=package, letter=str(letter).strip(), question_type=qtype,
                defaults={'question': question or '', 'answer': answer or '', 'category': category or ''})
            added += 1

        try:
            name = file.name.lower()
            if name.endswith('.csv'):
                decoded = file.read().decode('utf-8-sig', errors='ignore')
                reader = csv.reader(io.StringIO(decoded))
                next(reader, None)
                for row in reader:
                    if not row or len(row) < 5:
                        failed_rows += 1
                        continue
                    letter, qtype_raw, question, answer, category = [(str(x).strip() if x is not None else '') for x in row[:5]]
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
                    letter, qtype_raw, question, answer, category = [(str(x).strip() if x is not None else '') for x in row[:5]]
                    upsert_row(letter, qtype_raw, question, answer, category)
            else:
                messages.error(request, "نوع الملف غير مدعوم. ارفع CSV أو Excel.")
                return HttpResponseRedirect(request.path)

            if failed_rows and not added:
                msg = "لم يتم التعرف على أي صف. تفقد عمود (نوع السؤال)."
                if failed_examples: msg += " أمثلة متجاهلة: " + ", ".join(failed_examples)
                messages.error(request, msg)
            elif failed_rows:
                msg = f"تمت إضافة/تحديث {added} سؤال. تم تجاهل {failed_rows} صف بسبب نوع سؤال غير مفهوم."
                if failed_examples: msg += " أمثلة: " + ", ".join(failed_examples)
                messages.warning(request, msg)
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
    list_display = ('package_num', 'letter', 'question_type_ar', 'category', 'question_preview', 'answer')
    list_filter = ('package__package_number', 'letter', 'question_type', 'category')
    search_fields = ('question', 'answer', 'letter', 'category')
    list_per_page = 30
    list_select_related = ('package',)
    def get_queryset(self, request):
        return super().get_queryset(request).filter(package__game_type='letters')
    def package_num(self, obj): return f"حزمة {obj.package.package_number}"
    package_num.short_description = "الحزمة"
    def question_type_ar(self, obj): return {'main': 'رئيسي', 'alt1': 'بديل 1', 'alt2': 'بديل 2', 'alt3': 'بديل 3', 'alt4': 'بديل 4'}.get(obj.question_type, obj.question_type)
    question_type_ar.short_description = "النوع"
    def question_preview(self, obj): return (obj.question[:50] + '...') if len(obj.question) > 50 else obj.question
    question_preview.short_description = "السؤال"

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
        return super().get_queryset(request).filter(game_type='images').annotate(_rcount=Count('picture_riddles'))
    def package_info(self, obj): return f"حزمة {obj.package_number}"
    package_info.short_description = "الرقم"
    def riddles_count_badge(self, obj):
        cnt = getattr(obj, '_rcount', 0)
        limit = getattr(obj, 'picture_limit', (10 if obj.is_free else 22))
        if cnt == 0: color, icon = '#94a3b8','—'
        elif cnt > limit: color, icon = '#ef4444','⚠️'
        elif cnt == limit: color, icon = '#10b981','✅'
        else: color, icon = '#f59e0b','🧩'
        return format_html('<span style="color:{};font-weight:700;">{} {}/{} </span>', color, icon, cnt, limit)
    riddles_count_badge.short_description = "ألغاز الحزمة"
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
    def generic_actions(self, obj):
        list_url = reverse('admin:games_pictureriddle_changelist') + f'?package__id__exact={obj.id}'
        add_url  = reverse('admin:games_pictureriddle_add') + f'?package={obj.id}'
        return mark_safe(
            f'<a class="button" href="{list_url}" style="background:#0ea5e9;color:#0b1220;padding:4px 8px;border-radius:6px;margin-left:6px;">🖼️ عرض الألغاز</a>'
            f'<a class="button" href="{add_url}"  style="background:#22c55e;color:#0b1220;padding:4px 8px;border-radius:6px;">➕ إضافة لغز</a>'
        )
    generic_actions.short_description = "إجراءات"
    def save_model(self, request, obj, form, change):
        obj.game_type = 'images'
        super().save_model(request, obj, form, change)

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
    list_display = ('user', 'package_ref', 'is_completed', 'is_expired_badge', 'purchase_date', 'expires_at')
    list_filter = ('is_completed', 'purchase_date', 'expires_at', 'package__game_type')
    search_fields = ('user__username', 'package__package_number')
    date_hierarchy = 'purchase_date'
    ordering = ('-purchase_date',)
    list_select_related = ('user', 'package')
    actions = ('open_analytics',)
    def package_ref(self, obj):
        return f"{obj.package.get_game_type_display()} / حزمة {obj.package.package_number}"
    package_ref.short_description = "الحزمة"
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
        ]
        return custom + urls

    def analytics_view(self, request):
        days = int(request.GET.get("days", 30) or 30)
        end = timezone.now()
        start = end - timezone.timedelta(days=days)

        price_expr = _price_case_expr()
        period_purchases = UserPurchase.objects.filter(purchase_date__gte=start, purchase_date__lte=end)
        period_total = period_purchases.count()
        period_revenue = period_purchases.aggregate(total=Coalesce(Sum(price_expr), 0))['total'] or Decimal("0.00")
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

def _img_thumb(url, h=56):
    if not url: return "—"
    return format_html('<img src="{}" style="height:{}px;border-radius:6px;border:1px solid #ddd;" alt="thumb"/>', escape(url), h)

@admin.register(TimeCategory)
class TimeCategoryAdmin(admin.ModelAdmin):
    list_display = ('name','is_free_category','is_active','order','packages_count','free_pkg_ok','cover_preview','actions')
    list_filter  = ('is_free_category','is_active')
    search_fields= ('name','slug')
    ordering     = ('order','name')

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
    cover_preview.short_description = "غلاف"

    def actions(self, obj):
        pkgs_url = reverse('admin:games_timepackage_changelist') + f'?time_category__id__exact={obj.id}'
        return mark_safe(f'<a class="button" href="{pkgs_url}">📦 إدارة الحزم</a>')
    actions.short_description = "إجراءات"

    # لوحة فئات تحدّي الوقت (مع مقياس "الحزم المتبقية للمستخدم")
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("dashboard/", self.admin_site.admin_view(self.dashboard_view), name="games_timecategory_dashboard"),
        ]
        return custom + urls

    def dashboard_view(self, request):
        """
        تعرض كل الفئات + لكل فئة:
          - الاسم والغلاف والحالة
          - عدد الحزم (الكلية) + تحقق حزمة #0 المجانية
          - إن تم تمرير مستخدم (via ?user_id= أو ?user=اسم/إيميل) يعرض "الحزم المتبقية لهذا المستخدم"
            المنطق: المتبقي = عدد الحزم المدفوعة في الفئة - عدد الحزم (غير #0) التي لعبها المستخدم في تلك الفئة (TimePlayHistory)
        """
        # تحديد المستخدم المطلوب اختياريًا
        user_id = request.GET.get("user_id", "").strip()
        user_q  = request.GET.get("user", "").strip()  # اسم/إيميل

        from django.contrib.auth import get_user_model
        U = get_user_model()
        user_obj = None
        if user_id:
            try:
                user_obj = U.objects.get(pk=user_id)
            except U.DoesNotExist:
                user_obj = None
        elif user_q:
            user_obj = U.objects.filter(Q(username__iexact=user_q) | Q(email__iexact=user_q) | Q(first_name__iexact=user_q)).first()

        rows = []
        cats = TimeCategory.objects.all().order_by('order','name').prefetch_related('time_packages')
        for cat in cats:
            total_pkgs = cat.time_packages.filter(game_type='time').count()
            paid_pkgs  = cat.time_packages.filter(game_type='time').exclude(package_number=0).count()
            free_ok = cat.time_packages.filter(game_type='time', package_number=0, is_active=True).exists()
            remaining_txt = "—"
            if user_obj:
                played = TimePlayHistory.objects.filter(user=user_obj, category=cat).exclude(package__package_number=0).values('package_id').distinct().count()
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
              <div><label>user_id</label><input type="text" name="user_id" value="{escape(user_id)}" placeholder="ID" style="width:100%"></div>
              <div><label>user (اسم/إيميل)</label><input type="text" name="user" value="{escape(user_q)}" placeholder="username أو email" style="width:100%"></div>
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
        ctx = {**self.admin_site.each_context(request), "title": "فئات تحدّي الوقت", "content": mark_safe(html)}
        return TemplateResponse(request, "admin/simple_box.html", ctx)

# Proxy لإدارة حزم تحدّي الوقت عبر GamePackage
class TimePackage(GamePackage):
    class Meta:
        proxy = True
        verbose_name = "حزمة - تحدّي الوقت"
        verbose_name_plural = "حزم - تحدّي الوقت"

class TimeRiddleInlineFormSet(forms.models.BaseInlineFormSet):
    """
    - حد أقصى 40 لغزًا لكل حزمة.
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
                if o in orders: dup_orders.add(o)
                orders.add(o)
        if len(alive) > 40:
            raise forms.ValidationError("الحدّ الأقصى لعدد الألغاز في حزمة تحدّي الوقت هو 40 صورة.")
        if dup_orders:
            dup_s = ", ".join(str(x) for x in sorted(dup_orders))
            raise forms.ValidationError(f"يوجد تكرار في (الترتيب): {dup_s}. اجعل كل ترتيب فريدًا داخل الحزمة.")

class TimeRiddleInline(admin.TabularInline):
    model = TimeRiddle
    extra = 0
    formset = TimeRiddleInlineFormSet
    fields = ('order','image_url','answer','hint','thumb_tag')
    readonly_fields = ('thumb_tag',)
    ordering = ('order',)
    def thumb_tag(self, obj): return _img_thumb(getattr(obj, 'image_url', ''))
    thumb_tag.short_description = "معاينة"
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'answer':
            field.help_text = "الإجابة تظهر للمقدّم فقط أثناء اللعب، ولا تظهر للمتسابقين."
        return field

@admin.register(TimePackage)
class TimePackageAdmin(admin.ModelAdmin):
    list_display = ('pkg_ref','category_ref','is_free_icon','status_badge','created_at','manage_riddles')
    list_filter = ('is_active','is_free','time_category','created_at')
    search_fields = ('package_number','description','time_category__name')
    ordering = ('time_category__order','time_category__name','package_number')
    inlines = [TimeRiddleInline]
    fieldsets = (
        ('المعلومات الأساسية', {'fields': ('time_category','package_number','is_free','is_active')}),
        ('التسعير/الوصف', {'fields': (('original_price','discounted_price','price'),'description')}),
    )
    def get_queryset(self, request):
        return super().get_queryset(request).filter(game_type='time').select_related('time_category')
    def save_model(self, request, obj, form, change):
        obj.game_type = 'time'
        super().save_model(request, obj, form, change)
    def pkg_ref(self, obj):
        tag = "مجانية" if obj.is_free or obj.package_number == 0 else f"#{obj.package_number}"
        return format_html("<b>حزمة {}</b>", tag)
    pkg_ref.short_description = "الحزمة"
    def category_ref(self, obj):
        return obj.time_category.name if obj.time_category else "—"
    category_ref.short_description = "التصنيف"
    def is_free_icon(self, obj): return "✅" if (obj.is_free or obj.package_number == 0) else "—"
    is_free_icon.short_description = "مجانية"
    def status_badge(self, obj):
        return mark_safe(f"<b style='color:{'green' if obj.is_active else 'red'};'>{'فعّالة' if obj.is_active else 'غير فعّالة'}</b>")
    status_badge.short_description = "الحالة"
    def manage_riddles(self, obj):
        url = reverse('admin:games_timeriddle_changelist') + f'?package__id__exact={obj.id}'
        return mark_safe(f'<a class="button" href="{url}">🖼️ ألغاز الحزمة</a>')
    manage_riddles.short_description = "ألغاز"

@admin.register(TimeRiddle)
class TimeRiddleAdmin(admin.ModelAdmin):
    list_display  = ('package_ref', 'order', 'answer', 'hint_short', 'thumb')
    list_editable = ('order',)
    list_filter   = ('package__time_category', 'package__is_active', 'created_at')
    search_fields = ('answer', 'hint', 'package__package_number', 'package__time_category__name')
    ordering      = ('package__time_category__order', 'package__package_number', 'order')
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('package', 'package__time_category').filter(package__game_type='time')
    def package_ref(self, obj):
        cat = obj.package.time_category.name if (obj.package and obj.package.time_category) else "—"
        num = obj.package.package_number if obj.package else "—"
        tag = "مجانية" if (obj.package and (obj.package.is_free or obj.package.package_number == 0)) else f"#{num}"
        return format_html("<b>{} / حزمة {}</b>", cat, tag)
    package_ref.short_description = "التصنيف/الحزمة"
    def hint_short(self, obj): return (obj.hint or '')[:40]
    hint_short.short_description = "تلميح"
    def thumb(self, obj):
        if not obj.image_url: return "—"
        return format_html('<img src="{}" style="height:48px;border-radius:6px;border:1px solid #ddd;" alt="thumb"/>', escape(obj.image_url))
    thumb.short_description = "معاينة"

# ========= تحسينات عامة لواجهة الأدمن =========
admin.site.site_header = '🎮 إدارة الألعاب'
admin.site.site_title = 'لوحة تحكم وش الجواب'
admin.site.index_title = 'مرحبًا بك في لوحة التحكم'
