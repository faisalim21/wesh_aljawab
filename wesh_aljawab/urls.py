# wesh_aljawab/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import render
from django.views.generic import TemplateView

def home_view(request):
    return render(request, 'base.html')

urlpatterns = [
    path('admin/', admin.site.urls),

    # الصفحة الرئيسية
    path('', home_view, name='home'),

    # الألعاب
    path('games/', include(('games.urls', 'games'), namespace='games')),

    # الحسابات
    path('accounts/', include(('accounts.urls', 'accounts'), namespace='accounts')),

    # صفحات ثابتة
    path("privacy/", TemplateView.as_view(template_name="privacy.html"), name="privacy"),
    path("returns/", TemplateView.as_view(template_name="returns.html"), name="returns"),

    # المدفوعات (مهم يكون namespace = 'payments')
    path('payments/', include(('payments.urls', 'payments'), namespace='payments')),
]

# ملفات الوسائط والستاتيك في وضع التطوير فقط
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # لو مفعّل STATICFILES_DIRS (قائمة)، خذ أول مسار (يكفي للتطوير)
    if getattr(settings, "STATICFILES_DIRS", None):
        urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
