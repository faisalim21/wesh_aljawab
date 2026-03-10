# wesh_aljawab/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import render
from django.views.generic import TemplateView
from django.http import JsonResponse


def home_view(request):
    return render(request, 'base.html')


def home_stats_view(request):
    try:
        from django.contrib.auth import get_user_model
        from games.models import GamePackage
        User = get_user_model()
        total_users = User.objects.count()
        active_packages = GamePackage.objects.filter(is_active=True).count()
        return JsonResponse({
            'total_users': total_users,
            'active_packages': active_packages,
        })
    except Exception:
        return JsonResponse({
            'total_users': 0,
            'active_packages': 0,
        })


urlpatterns = [
    path('control-9f7a2c4e8b/', admin.site.urls),

    # الصفحة الرئيسية
    path('', home_view, name='home'),

    # إحصائيات الصفحة الرئيسية
    path('api/stats/', home_stats_view, name='home_stats'),

    # الألعاب
    path('games/', include(('games.urls', 'games'), namespace='games')),

    # الحسابات
    path('accounts/', include(('accounts.urls', 'accounts'), namespace='accounts')),

    # صفحات ثابتة
    path("privacy/", TemplateView.as_view(template_name="privacy.html"), name="privacy"),
    path("returns/", TemplateView.as_view(template_name="returns.html"), name="returns"),

    # المدفوعات
    path('payments/', include(('payments.urls', 'payments'), namespace='payments')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    if getattr(settings, "STATICFILES_DIRS", None):
        urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])