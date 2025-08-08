# games/urls.py - الملف المُصحح
from django.urls import path
from . import views

app_name = 'games'

urlpatterns = [
    # الصفحات الرئيسية
    path('', views.games_home, name='home'),
    
    # لعبة خلية الحروف
    path('letters/', views.letters_game_home, name='letters_home'),
    path('letters/create/', views.create_letters_session, name='create_letters_session'),
    path('letters/session/<uuid:session_id>/', views.letters_session, name='letters_session'),
    # تم حذف letters_host لأنه غير موجود في views.py الجديد
    path('letters/display/<str:display_link>/', views.letters_display, name='letters_display'),
    path('letters/contestants/<str:contestants_link>/', views.letters_contestants, name='letters_contestants'),
    
    # الألعاب الأخرى (مؤقتة)
    path('images/', views.images_game_home, name='images_home'),
    path('quiz/', views.quiz_game_home, name='quiz_home'),
    
    # API endpoints
    path('api/get-question/', views.get_question, name='api_get_question'),
    path('api/get-session-letters/', views.get_session_letters, name='api_get_session_letters'),
    path('api/update-cell-state/', views.update_cell_state, name='api_update_cell_state'),
    path('api/update-scores/', views.update_scores, name='api_update_scores'),
    path('api/session-state/', views.session_state, name='api_session_state'),
    path('api/add-contestant/', views.add_contestant, name='api_add_contestant'),
    path('api/check-eligibility/', views.api_check_free_session_eligibility, name='api_check_eligibility'),
    path('api/session-expiry-info/', views.api_session_expiry_info, name='api_session_expiry_info'),
    path('api/user-session-stats/', views.api_user_session_stats, name='api_user_session_stats'),
    path('api/contestant-buzz/', views.api_contestant_buzz_http, name='api_contestant_buzz_http'),
    
]