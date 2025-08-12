from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import render

def home_view(request):
    """الصفحة الرئيسية"""
    return render(request, 'home.html')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home_view, name='home'),
    path('games/', include('games.urls')),
    path('accounts/', include('accounts.urls')),

    # تأكيد الـ namespace لتطبيق المدفوعات
    path('payments/', include(('payments.urls', 'payments'), namespace='payments')),
]

# ملفات الوسائط وملفات الستاتيك (فقط في التطوير)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    if settings.STATICFILES_DIRS:
        urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
