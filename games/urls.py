# games/urls.py
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
    path('letters/display/<str:display_link>/', views.letters_display, name='letters_display'),
    path('letters/contestants/<str:contestants_link>/', views.letters_contestants, name='letters_contestants'),

    # الألعاب الأخرى
    path('images/', views.images_game_home, name='images_home'),
    path('quiz/', views.quiz_game_home, name='quiz_home'),

    # API endpoints
    path('api/get-question/', views.get_question, name='api_get_question'),
    path('api/get-session-letters/', views.get_session_letters, name='api_get_session_letters'),
    path('api/update-cell-state/', views.update_cell_state, name='api_update_cell_state'),
    path('api/update-scores/', views.update_scores, name='api_update_scores'),
    path('api/session-state/', views.session_state, name='api_session_state'),
    path('api/add-contestant/', views.add_contestant, name='api_add_contestant'),
    path('api/check-eligibility/', views.api_check_free_session_eligibility, name='api_check_free_session_eligibility'),
    path('api/session-expiry-info/', views.api_session_expiry_info, name='api_session_expiry_info'),
    path('api/user-session-stats/', views.api_user_session_stats, name='api_user_session_stats'),
    path('api/contestant-buzz/', views.api_contestant_buzz_http, name='api_contestant_buzz_http'),
    path('api/letters-new-round/', views.letters_new_round, name='api_letters_new_round'),

    # الألعاب الأخرى
    path('images/', views.images_game_home, name='images_home'),
    path('images/create/', views.create_images_session, name='create_images_session'),
    path('images/session/<uuid:session_id>/', views.images_session, name='images_session'),
    path('images/display/<str:display_link>/', views.images_display, name='images_display'),
    path('images/contestants/<str:contestants_link>/', views.images_contestants, name='images_contestants'),

    # APIs لتحدّي الصور
    path('api/images/get-current/', views.api_images_get_current, name='api_images_get_current'),
    path('api/images/set-index/', views.api_images_set_index, name='api_images_set_index'),
    path('api/images/next/', views.api_images_next, name='api_images_next'),
    path('api/images/prev/', views.api_images_prev, name='api_images_prev'),
]
