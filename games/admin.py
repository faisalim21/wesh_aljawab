# games/admin.py - نسخة مبسطة وعملية
from django.contrib import admin
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.urls import path, reverse
from django.http import HttpResponse
from django.utils.html import format_html
from django.db import transaction
import csv
import io
import openpyxl
from .models import GamePackage, LettersGameQuestion, UserPurchase, GameSession

class LettersGameQuestionInline(admin.TabularInline):
    model = LettersGameQuestion
    extra = 0
    fields = ('letter', 'question_type', 'question', 'answer', 'category')

@admin.register(GamePackage)
class GamePackageAdmin(admin.ModelAdmin):
    list_display = ['package_info', 'questions_count', 'price_info', 'status', 'upload_action']
    list_filter = ['is_free', 'is_active', 'created_at']
    search_fields = ['package_number']
    inlines = [LettersGameQuestionInline]
    
    def package_info(self, obj):
        return f"حزمة {obj.package_number}"
    package_info.short_description = 'رقم الحزمة'
    
    def questions_count(self, obj):
        count = obj.letters_questions.count()
        if count == 75:
            return format_html('<span style="color: green;">✅ {}</span>', count)
        elif count > 0:
            return format_html('<span style="color: orange;">⚠️ {}</span>', count)
        else:
            return format_html('<span style="color: red;">❌ 0</span>')
    questions_count.short_description = 'عدد الأسئلة'
    
    def price_info(self, obj):
        if obj.is_free:
            return "🆓 مجانية"
        return f"💰 {obj.price} ريال"
    price_info.short_description = 'السعر'
    
    def status(self, obj):
        if obj.is_active:
            return format_html('<span style="color: green;">🟢 فعالة</span>')
        return format_html('<span style="color: red;">🔴 غير فعالة</span>')
    status.short_description = 'الحالة'
    
    def upload_action(self, obj):
        upload_url = reverse('admin:upload_questions', args=[obj.id])
        return format_html(
            '<a href="{}" style="background:#28a745; color:white; padding:4px 8px; '
            'text-decoration:none; border-radius:3px; font-size:12px;">📁 رفع ملف</a>',
            upload_url
        )
    upload_action.short_description = 'إجراءات'
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<path:package_id>/upload/', self.upload_questions, name='upload_questions'),
            path('download-template/', self.download_template, name='download_template'),
        ]
        return custom_urls + urls
    
    def upload_questions(self, request, package_id):
        try:
            package = GamePackage.objects.get(id=package_id, game_type='letters')
        except GamePackage.DoesNotExist:
            messages.error(request, 'الحزمة غير موجودة')
            return redirect('admin:games_gamepackage_changelist')
        
        if request.method == 'POST':
            uploaded_file = request.FILES.get('file')
            replace_existing = request.POST.get('replace') == 'on'
            
            if not uploaded_file:
                messages.error(request, 'يرجى اختيار ملف')
                return redirect(request.path_info)
            
            try:
                if replace_existing:
                    package.letters_questions.all().delete()
                
                added_count = 0
                
                if uploaded_file.name.endswith('.xlsx'):
                    workbook = openpyxl.load_workbook(uploaded_file)
                    sheet = workbook.active
                    
                    for row in sheet.iter_rows(min_row=2, values_only=True):
                        if len(row) >= 5 and all(row[:5]):
                            letter, q_type, question, answer, category = row[:5]
                            
                            # تحويل نوع السؤال
                            type_map = {'رئيسي': 'main', 'بديل1': 'alt1', 'بديل2': 'alt2'}
                            q_type_en = type_map.get(str(q_type).strip())
                            
                            if q_type_en:
                                LettersGameQuestion.objects.update_or_create(
                                    package=package,
                                    letter=str(letter).strip(),
                                    question_type=q_type_en,
                                    defaults={
                                        'question': str(question).strip(),
                                        'answer': str(answer).strip(),
                                        'category': str(category).strip()
                                    }
                                )
                                added_count += 1
                
                elif uploaded_file.name.endswith('.csv'):
                    decoded_file = uploaded_file.read().decode('utf-8-sig')
                    reader = csv.reader(io.StringIO(decoded_file))
                    next(reader)  # تخطي الهيدر
                    
                    for row in reader:
                        if len(row) >= 5:
                            letter, q_type, question, answer, category = row[:5]
                            
                            type_map = {'رئيسي': 'main', 'بديل1': 'alt1', 'بديل2': 'alt2'}
                            q_type_en = type_map.get(q_type.strip())
                            
                            if q_type_en:
                                LettersGameQuestion.objects.update_or_create(
                                    package=package,
                                    letter=letter.strip(),
                                    question_type=q_type_en,
                                    defaults={
                                        'question': question.strip(),
                                        'answer': answer.strip(),
                                        'category': category.strip()
                                    }
                                )
                                added_count += 1
                else:
                    messages.error(request, 'نوع الملف غير مدعوم')
                    return redirect(request.path_info)
                
                messages.success(request, f'تم إضافة {added_count} سؤال بنجاح!')
                return redirect('admin:games_gamepackage_changelist')
                
            except Exception as e:
                messages.error(request, f'خطأ: {str(e)}')
        
        return render(request, 'admin/upload_questions.html', {
            'package': package,
            'title': f'رفع أسئلة للحزمة {package.package_number}',
        })
    
    def download_template(self, request):
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="template.xlsx"'
        
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "قالب الأسئلة"
        
        # الهيدر
        headers = ['الحرف', 'نوع السؤال', 'السؤال', 'الإجابة', 'التصنيف']
        sheet.append(headers)
        
        # أمثلة
        examples = [
            ['أ', 'رئيسي', 'بلد يبدأ بحرف الألف', 'الأردن', 'بلدان'],
            ['أ', 'بديل1', 'حيوان يبدأ بحرف الألف', 'أسد', 'حيوانات'],
            ['أ', 'بديل2', 'طعام يبدأ بحرف الألف', 'أرز', 'أطعمة'],
        ]
        
        for example in examples:
            sheet.append(example)
        
        workbook.save(response)
        return response
    
    def get_queryset(self, request):
        return super().get_queryset(request).filter(game_type='letters')
    
    def save_model(self, request, obj, form, change):
        obj.game_type = 'letters'
        super().save_model(request, obj, form, change)

@admin.register(LettersGameQuestion)
class LettersGameQuestionAdmin(admin.ModelAdmin):
    list_display = ['package_num', 'letter', 'question_type_ar', 'question_preview', 'answer']
    list_filter = ['package__package_number', 'letter', 'question_type']
    search_fields = ['question', 'answer', 'letter']
    list_per_page = 25
    
    def package_num(self, obj):
        return f"حزمة {obj.package.package_number}"
    package_num.short_description = 'الحزمة'
    
    def question_type_ar(self, obj):
        types = {'main': 'رئيسي', 'alt1': 'بديل 1', 'alt2': 'بديل 2'}
        return types.get(obj.question_type, obj.question_type)
    question_type_ar.short_description = 'النوع'
    
    def question_preview(self, obj):
        return obj.question[:50] + '...' if len(obj.question) > 50 else obj.question
    question_preview.short_description = 'السؤال'
    
    def get_queryset(self, request):
        return super().get_queryset(request).filter(package__game_type='letters')

# إدارة المشتريات (للمدير فقط)
@admin.register(UserPurchase)
class UserPurchaseAdmin(admin.ModelAdmin):
    list_display = ['user', 'package', 'purchase_date', 'is_completed']
    list_filter = ['is_completed', 'purchase_date']
    
    def has_module_permission(self, request):
        return request.user.is_superuser

@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):
    list_display = ['host', 'package', 'created_at', 'is_active']
    list_filter = ['is_active', 'created_at']
    
    def has_module_permission(self, request):
        return request.user.is_superuser

admin.site.site_header = '🔤 خلية الحروف'
admin.site.site_title = 'إدارة خلية الحروف'
admin.site.index_title = 'لوحة التحكم'