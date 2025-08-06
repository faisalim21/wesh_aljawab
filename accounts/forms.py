from django import forms
from django.contrib.auth.models import User
from .models import UserProfile

class SimpleRegisterForm(forms.Form):
    """نموذج تسجيل مبسط مع تحسينات الكيبورد"""
    
    first_name = forms.CharField(
        label="الاسم الكريم",
        max_length=150,
        widget=forms.TextInput(attrs={
            'placeholder': 'أدخل اسمك الكامل',
            'class': 'form-control',
            'inputmode': 'text',
            'autocomplete': 'given-name',
            'autofocus': True,
        }),
    )
    
    email = forms.EmailField(
        label="البريد الإلكتروني",
        widget=forms.EmailInput(attrs={
            'placeholder': 'أدخل بريدك الإلكتروني',
            'class': 'form-control',
            'inputmode': 'email',
            'autocomplete': 'email',
        }),
    )
    
    phone_number = forms.CharField(
        label="رقم الجوال",
        max_length=15,
        widget=forms.TextInput(attrs={
            'placeholder': 'مثال: 0512345678',
            'class': 'form-control',
            'inputmode': 'tel',
            'autocomplete': 'tel',
            'pattern': '[0-9]{10}',
        }),
    )
    
    password = forms.CharField(
        label="كلمة المرور",
        min_length=6,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'اختر كلمة مرور قوية',
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
    )

    def clean_email(self):
        """التحقق من البريد الإلكتروني"""
        email = self.cleaned_data['email'].lower().strip()
        
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("البريد الإلكتروني مستخدم مسبقاً")
        
        return email

    def clean_phone_number(self):
        """التحقق من رقم الجوال"""
        phone = self.cleaned_data['phone_number'].strip()
        
        # إزالة المسافات والرموز الإضافية
        phone = phone.replace(' ', '').replace('-', '').replace('+966', '0')
        
        if not phone.startswith('05') or len(phone) != 10:
            raise forms.ValidationError("رقم الجوال غير صحيح. يجب أن يبدأ بـ 05 ويتكون من 10 أرقام")
        
        if not phone.isdigit():
            raise forms.ValidationError("رقم الجوال يجب أن يحتوي على أرقام فقط")
        
        if UserProfile.objects.filter(phone_number=phone).exists():
            raise forms.ValidationError("رقم الجوال مستخدم مسبقاً")
        
        return phone

    def clean_first_name(self):
        """التحقق من الاسم"""
        name = self.cleaned_data['first_name'].strip()
        
        if len(name) < 2:
            raise forms.ValidationError("الاسم يجب أن يكون أكثر من حرف واحد")
            
        return name

    def clean_password(self):
        """التحقق من كلمة المرور"""
        password = self.cleaned_data['password']
        
        if len(password) < 6:
            raise forms.ValidationError("كلمة المرور يجب أن تكون 6 أحرف على الأقل")
            
        return password