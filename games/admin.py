# games/admin.py - لوحة تحكم مُقسّمة لكل لعبة + إحصائيات + رفع أسئلة
from django.contrib import admin
from django.urls import path, reverse
from django.db.models import Count, Max
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.contrib import messages
from django.shortcuts import get_object_or_404
from django.middleware.csrf import get_token
from django.template.response import TemplateResponse
from django import forms
from django.db import IntegrityError
import csv
import io

# حاول استيراد openpyxl إن وُجد
try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

from .models import (
    GamePackage,
    LettersGameQuestion,
    UserPurchase,
    GameSession,
    LettersGameProgress,
    Contestant,
)

# ===========================
#  Forms
# ===========================

class LettersPackageForm(forms.ModelForm):
    class Meta:
        model = GamePackage
        fields = ('package_number', 'is_free', 'price', 'is_active', 'description', 'question_theme')

    def clean_package_number(self):
        num = self.cleaned_data['package_number']
        exists = GamePackage.objects.filter(game_type='letters', package_number=num)\
                                    .exclude(pk=self.instance.pk).exists()
        if exists:
            raise forms.ValidationError(f"الحزمة رقم {num} موجودة بالفعل في خلية الحروف.")
        return num

# ===========================
#  Actions / Utilities
# ===========================

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

# ===========================
#  Proxy Models لتقسيم الأدمن
# ===========================

# الحزم
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

# الجلسات
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

# ===========================
#  Inlines
# ===========================

class LettersGameQuestionInline(admin.TabularInline):
    model = LettersGameQuestion
    extra = 0
    fields = ('letter', 'question_type', 'question', 'answer', 'category')
    show_change_link = True

# ===========================
#  Admin: حِزم خلية الحروف
# ===========================

@admin.register(LettersPackage)
class LettersPackageAdmin(admin.ModelAdmin):
    """
    قسم مخصص لحزم خلية الحروف:
    - يعرض عدد الأسئلة
    - يدعم رفع الأسئلة (Excel/CSV) + تنزيل قالب
    - يدعم الحقول الجديدة (الوصف + نوع الأسئلة)
    """
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
    actions = (action_mark_active, action_mark_inactive, action_export_csv)
    ordering = ('package_number',)
    form = LettersPackageForm
    fieldsets = (
        ('المعلومات الأساسية', {
            'fields': ('package_number', 'is_free', 'price', 'is_active')
        }),
        ('المحتوى', {
            'fields': ('description', 'question_theme')
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(game_type='letters').annotate(_qcount=Count('letters_questions'))

    def package_info(self, obj):
        return f"حزمة {obj.package_number}"
    package_info.short_description = "الرقم"

    def theme_badge(self, obj):
        theme = getattr(obj, 'question_theme', 'mixed') or 'mixed'
        label = obj.get_question_theme_display() if hasattr(obj, 'get_question_theme_display') else theme
        if theme == 'sports':
            return format_html(
                '<span style="background:#dcfce7;color:#166534;border:1px solid #86efac;padding:2px 8px;border-radius:999px;font-weight:700;">{}</span>',
                label
            )
        return format_html(
            '<span style="background:#e0e7ff;color:#4338ca;border:1px solid #a5b4fc;padding:2px 8px;border-radius:999px;font-weight:700;">{}</span>',
            label
        )
    theme_badge.short_description = "نوع الأسئلة"

    def questions_count_badge(self, obj):
        count = getattr(obj, '_qcount', 0)
        if count == 75:
            color = 'green'; icon = '✅'
        elif count > 0:
            color = 'orange'; icon = '⚠️'
        else:
            color = 'red'; icon = '❌'
        return format_html('<span style="color:{};font-weight:700;">{} {}</span>', color, icon, count)
    questions_count_badge.short_description = "عدد الأسئلة"

    def price_info(self, obj):
        return "🆓 مجانية" if obj.is_free else f"💰 {obj.price} ريال"
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
        return mark_safe(
            f'<a class="button" href="{upload_url}" style="background:#28a745;color:#fff;padding:4px 8px;border-radius:6px;text-decoration:none;margin-left:6px;">📁 رفع أسئلة</a>'
            f'<a class="button" href="{template_url}" style="background:#0ea5e9;color:#fff;padding:4px 8px;border-radius:6px;text-decoration:none;margin-left:6px;">⬇️ قالب</a>'
            f'<a class="button" href="{export_url}" style="background:#6b7280;color:#fff;padding:4px 8px;border-radius:6px;text-decoration:none;">📤 تصدير</a>'
        )
    letters_actions.short_description = "إجراءات"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields['package_number'].help_text = "يجب أن يكون فريدًا داخل خلية الحروف."
        if not obj:
            next_num = (GamePackage.objects.filter(game_type='letters')
                        .aggregate(Max('package_number'))['package_number__max'] or 0) + 1
            form.base_fields['package_number'].initial = next_num
        return form

    def save_model(self, request, obj, form, change):
        obj.game_type = 'letters'
        try:
            super().save_model(request, obj, form, change)
        except IntegrityError:
            messages.error(request, f"لا يمكن الحفظ: الرقم {obj.package_number} مستخدم بالفعل في خلية الحروف.")
            raise

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("stats/", self.admin_site.admin_view(self.stats_view), name="games_letterspackage_stats"),
            path("<uuid:pk>/upload/", self.admin_site.admin_view(self.upload_letters_view), name="games_letterspackage_upload"),
            path("<uuid:pk>/export/", self.admin_site.admin_view(self.export_letters_view), name="games_letterspackage_export"),
            path("download-template/", self.admin_site.admin_view(self.download_letters_template_view), name="games_letterspackage_download_template"),
        ]
        return custom + urls

    def stats_view(self, request):
        """صفحة إحصائيات لحزم/أسئلة خلية الحروف"""
        qs = GamePackage.objects.filter(game_type='letters').annotate(qcount=Count('letters_questions'))
        total_packages = qs.count()
        total_questions = LettersGameQuestion.objects.count()
        free_count = qs.filter(is_free=True).count()
        paid_count = qs.filter(is_free=False).count()
        active_count = qs.filter(is_active=True).count()

        top_packages = qs.order_by('-qcount', 'package_number')[:10]
        rows = "".join([
            f"<tr><td>حزمة {p.package_number}</td>"
            f"<td>{getattr(p, 'get_question_theme_display', lambda: '')()}</td>"
            f"<td style='text-align:center;'>{p.qcount}</td>"
            f"<td>{'مجانية' if p.is_free else 'مدفوعة'}</td>"
            f"<td>{'فعالة' if p.is_active else 'غير فعالة'}</td></tr>"
            for p in top_packages
        ])
        html = f"""
        <div style="padding:20px;font-family:Tahoma,Arial;">
          <h2>📊 إحصائيات خلية الحروف</h2>
          <ul>
            <li>إجمالي الحزم: <b>{total_packages}</b> (مجانية: {free_count} / مدفوعة: {paid_count})</li>
            <li>إجمالي الأسئلة: <b>{total_questions}</b></li>
            <li>حزم فعّالة: <b>{active_count}</b></li>
          </ul>
          <h4>أكثر الحزم من حيث عدد الأسئلة</h4>
          <table style="width:100%;border-collapse:collapse;" border="1" cellpadding="6">
            <thead style="background:#f1f5f9;">
              <tr>
                <th>الحزمة</th>
                <th>نوع الأسئلة</th>
                <th>عدد الأسئلة</th>
                <th>السعر</th>
                <th>الحالة</th>
              </tr>
            </thead>
            <tbody>{rows or '<tr><td colspan="5">لا توجد بيانات</td></tr>'}</tbody>
          </table>
        </div>
        """
        return HttpResponse(html)

    def upload_letters_view(self, request, pk):
        """
        رفع أسئلة (CSV أو Excel) للحزمة المحددة
        الأعمدة: [الحرف, نوع السؤال (رئيسي/بديل1/بديل2), السؤال, الإجابة, التصنيف]
        """
        package = get_object_or_404(GamePackage, pk=pk, game_type='letters')

        if request.method == 'POST':
            file = request.FILES.get('file')
            replace_existing = bool(request.POST.get('replace'))

            if not file:
                messages.error(request, "يرجى اختيار ملف")
                return HttpResponseRedirect(request.path)

            if replace_existing:
                package.letters_questions.all().delete()

            type_map = {
                'رئيسي': 'main',
                'بديل1': 'alt1', 'بديل 1': 'alt1',
                'بديل2': 'alt2', 'بديل 2': 'alt2',
                'بديل3': 'alt3', 'بديل 3': 'alt3',   # ✅ جديد
                'بديل4': 'alt4', 'بديل 4': 'alt4',   # ✅ جديد
            }

            added = 0

            try:
                name = file.name.lower()
                if name.endswith('.csv'):
                    decoded = file.read().decode('utf-8-sig')
                    reader = csv.reader(io.StringIO(decoded))
                    next(reader, None)  # تخطي الهيدر
                    for row in reader:
                        if len(row) >= 5:
                            letter, qtype_ar, question, answer, category = [str(x).strip() for x in row[:5]]
                            qtype = type_map.get(qtype_ar)
                            if not qtype:
                                continue
                            LettersGameQuestion.objects.update_or_create(
                                package=package, letter=letter, question_type=qtype,
                                defaults={'question': question, 'answer': answer, 'category': category}
                            )
                            added += 1

                elif name.endswith(('.xlsx', '.xlsm', '.xltx', '.xltm')):
                    if not HAS_OPENPYXL:
                        messages.error(request, "openpyxl غير مثبت. ثبّت الحزمة لاستخدام ملفات Excel.")
                        return HttpResponseRedirect(request.path)

                    wb = openpyxl.load_workbook(file)
                    sh = wb.active
                    for row in sh.iter_rows(min_row=2, values_only=True):
                        if not row or len(row) < 5:
                            continue
                        letter, qtype_ar, question, answer, category = [
                            str(x).strip() if x is not None else '' for x in row[:5]
                        ]
                        qtype = type_map.get(qtype_ar)
                        if not qtype:
                            continue
                        LettersGameQuestion.objects.update_or_create(
                            package=package, letter=letter, question_type=qtype,
                            defaults={'question': question, 'answer': answer, 'category': category}
                        )
                        added += 1
                else:
                    messages.error(request, "نوع الملف غير مدعوم. ارفع CSV أو Excel.")
                    return HttpResponseRedirect(request.path)

                messages.success(request, f"تم إضافة/تحديث {added} سؤال.")
                return HttpResponseRedirect(reverse('admin:games_letterspackage_changelist'))

            except Exception as e:
                messages.error(request, f"خطأ أثناء الرفع: {e}")
                return HttpResponseRedirect(request.path)

        # استخدم قالب الأدمن الموحد
        context = {
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
                "أعمدة مرتبة كالتالي: الحرف | نوع السؤال | السؤال | الإجابة | التصنيف.",
                "قيم نوع السؤال المقبولة: رئيسي، بديل1، بديل2 (وللحزم المدفوعة أيضًا: بديل3، بديل4).",
            ],
            "extra_note": "عند تفعيل خيار الحذف، سيتم حذف جميع الأسئلة الحالية في هذه الحزمة قبل الاستيراد.",
            "submit_label": "رفع الملف",
            "replace_label": "حذف الأسئلة الحالية قبل الرفع",
        }
        return TemplateResponse(request, "admin/import_csv.html", context)

    def download_letters_template_view(self, request):
        if not HAS_OPENPYXL:
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = 'attachment; filename="letters_template.csv"'
            writer = csv.writer(response)
            writer.writerow(['الحرف', 'نوع السؤال', 'السؤال', 'الإجابة', 'التصنيف'])
            writer.writerow(['أ', 'رئيسي', 'بلد يبدأ بحرف الألف', 'الأردن', 'بلدان'])
            writer.writerow(['أ', 'بديل1', 'حيوان يبدأ بحرف الألف', 'أسد', 'حيوانات'])
            writer.writerow(['أ', 'بديل2', 'طعام يبدأ بحرف الألف', 'أرز', 'أطعمة'])
            return response

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="letters_template.xlsx"'
        wb = openpyxl.Workbook()
        sh = wb.active
        sh.title = "قالب الأسئلة"
        headers = ['الحرف', 'نوع السؤال', 'السؤال', 'الإجابة', 'التصنيف']
        sh.append(headers)
        examples = [
            ['أ', 'رئيسي', 'بلد يبدأ بحرف الألف', 'الأردن', 'بلدان'],
            ['أ', 'بديل1', 'حيوان يبدأ بحرف الألف', 'أسد', 'حيوانات'],
            ['أ', 'بديل2', 'طعام يبدأ بحرف الألف', 'أرز', 'أطعمة'],
        ]
        for row in examples:
            sh.append(row)
        wb.save(response)
        return response

    def export_letters_view(self, request, pk):
        package = get_object_or_404(GamePackage, pk=pk, game_type='letters')
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="letters_package_{package.package_number}.csv"'
        writer = csv.writer(response)
        writer.writerow(['الحرف', 'نوع السؤال', 'السؤال', 'الإجابة', 'التصنيف'])
        type_map_ar = {'main': 'رئيسي', 'alt1': 'بديل1', 'alt2': 'بديل2'}
        for q in package.letters_questions.all().order_by('letter', 'question_type'):
            writer.writerow([q.letter, type_map_ar.get(q.question_type, q.question_type), q.question, q.answer, q.category])
        return response

# ===========================
#  Admin: حِزم الصور
# ===========================

@admin.register(ImagesPackage)
class ImagesPackageAdmin(admin.ModelAdmin):
    list_display = ('package_info', 'price_info', 'is_free_icon', 'status_badge', 'created_at', 'generic_actions')
    list_filter = ('is_free', 'is_active', 'created_at')
    search_fields = ('package_number', 'description')
    actions = (action_mark_active, action_mark_inactive, action_export_csv)
    ordering = ('package_number',)
    fieldsets = (
        ('المعلومات الأساسية', {'fields': ('package_number', 'is_free', 'price', 'is_active')}),
        ('المحتوى', {'fields': ('description',)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(game_type='images')

    def package_info(self, obj):
        return f"حزمة {obj.package_number}"
    package_info.short_description = "الرقم"

    def price_info(self, obj):
        return "🆓 مجانية" if obj.is_free else f"💰 {obj.price} ريال"
    price_info.short_description = "السعر"

    def is_free_icon(self, obj):
        return "✅" if obj.is_free else "—"
    is_free_icon.short_description = "مجانية"

    def status_badge(self, obj):
        return format_html('<b style="color:{};">{}</b>', 'green' if obj.is_active else 'red', 'فعّالة' if obj.is_active else 'غير فعّالة')
    status_badge.short_description = "الحالة"

    def generic_actions(self, obj):
        return "—"
    generic_actions.short_description = "إجراءات"

    def save_model(self, request, obj, form, change):
        obj.game_type = 'images'
        super().save_model(request, obj, form, change)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if not obj:
            next_num = (GamePackage.objects.filter(game_type='images')
                        .aggregate(Max('package_number'))['package_number__max'] or 0) + 1
            form.base_fields['package_number'].initial = next_num
        return form

# ===========================
#  Admin: حِزم سؤال وجواب
# ===========================

@admin.register(QuizPackage)
class QuizPackageAdmin(admin.ModelAdmin):
    list_display = ('package_info', 'price_info', 'is_free_icon', 'status_badge', 'created_at')
    list_filter = ('is_free', 'is_active', 'created_at')
    search_fields = ('package_number', 'description')
    actions = (action_mark_active, action_mark_inactive, action_export_csv)
    ordering = ('package_number',)
    fieldsets = (
        ('المعلومات الأساسية', {'fields': ('package_number', 'is_free', 'price', 'is_active')}),
        ('المحتوى', {'fields': ('description',)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(game_type='quiz')

    def package_info(self, obj):
        return f"حزمة {obj.package_number}"
    package_info.short_description = "الرقم"

    def price_info(self, obj):
        return "🆓 مجانية" if obj.is_free else f"💰 {obj.price} ريال"
    price_info.short_description = "السعر"

    def is_free_icon(self, obj):
        return "✅" if obj.is_free else "—"
    is_free_icon.short_description = "مجانية"

    def status_badge(self, obj):
        return format_html('<b style="color:{};">{}</b>', 'green' if obj.is_active else 'red', 'فعّالة' if obj.is_active else 'غير فعّالة')
    status_badge.short_description = "الحالة"

    def save_model(self, request, obj, form, change):
        obj.game_type = 'quiz'
        super().save_model(request, obj, form, change)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if not obj:
            next_num = (GamePackage.objects.filter(game_type='quiz')
                        .aggregate(Max('package_number'))['package_number__max'] or 0) + 1
            form.base_fields['package_number'].initial = next_num
        return form

# ===========================
#  Admin: أسئلة خلية الحروف (مباشر)
# ===========================

@admin.register(LettersGameQuestion)
class LettersGameQuestionAdmin(admin.ModelAdmin):
    list_display = ('package_num', 'letter', 'question_type_ar', 'category', 'question_preview', 'answer')
    list_filter = ('package__package_number', 'letter', 'question_type', 'category')
    search_fields = ('question', 'answer', 'letter', 'category')
    list_per_page = 30

    def get_queryset(self, request):
        return super().get_queryset(request).filter(package__game_type='letters')

    def package_num(self, obj):
        return f"حزمة {obj.package.package_number}"
    package_num.short_description = "الحزمة"

    def question_type_ar(self, obj):
        types = {'main': 'رئيسي', 'alt1': 'بديل 1', 'alt2': 'بديل 2'}
        return types.get(obj.question_type, obj.question_type)
    question_type_ar.short_description = "النوع"

    def question_preview(self, obj):
        return (obj.question[:50] + '...') if len(obj.question) > 50 else obj.question
    question_preview.short_description = "السؤال"

# ===========================
#  Admin: الجلسات (مقسّمة)
# ===========================

class _BaseSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'host', 'package_info', 'scores', 'is_active', 'created_at')
    list_filter = ('is_active', 'is_completed', 'created_at')
    search_fields = ('id', 'host__username', 'package__package_number')
    date_hierarchy = 'created_at'
    readonly_fields = ()
    ordering = ('-created_at',)

    def package_info(self, obj):
        return f"حزمة {obj.package.package_number} / {'مجانية' if obj.package.is_free else 'مدفوعة'}"
    package_info.short_description = "الحزمة"

    def scores(self, obj):
        return f"{obj.team1_name}: {obj.team1_score} | {obj.team2_name}: {obj.team2_score}"
    scores.short_description = "النقاط"

@admin.register(LettersSession)
class LettersSessionAdmin(_BaseSessionAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(game_type='letters')

@admin.register(ImagesSession)
class ImagesSessionAdmin(_BaseSessionAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(game_type='images')

@admin.register(QuizSession)
class QuizSessionAdmin(_BaseSessionAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(game_type='quiz')

# ===========================
#  Admin: المشتريات والمتسابقين
# ===========================

@admin.register(UserPurchase)
class UserPurchaseAdmin(admin.ModelAdmin):
    list_display = ('user', 'package_ref', 'is_completed', 'purchase_date')
    list_filter = ('is_completed', 'purchase_date', 'package__game_type')
    search_fields = ('user__username', 'package__package_number')
    date_hierarchy = 'purchase_date'
    ordering = ('-purchase_date',)

    def package_ref(self, obj):
        return f"{obj.package.get_game_type_display()} / حزمة {obj.package.package_number}"
    package_ref.short_description = "الحزمة"

@admin.register(Contestant)
class ContestantAdmin(admin.ModelAdmin):
    list_display = ('name', 'team', 'session_ref', 'is_active', 'joined_at')
    list_filter = ('team', 'is_active', 'session__game_type')
    search_fields = ('name', 'session__id')
    date_hierarchy = 'joined_at'
    ordering = ('-joined_at',)

    def session_ref(self, obj):
        return f"{obj.session.game_type} / {obj.session.id}"
    session_ref.short_description = "الجلسة"

# ===========================
#  تحسينات عامة للأدمن
# ===========================

admin.site.site_header = '🎮 إدارة الألعاب'
admin.site.site_title = 'لوحة تحكم وش الجواب'
admin.site.index_title = 'مرحبًا بك في لوحة التحكم'