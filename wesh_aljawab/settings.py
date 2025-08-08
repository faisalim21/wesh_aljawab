from pathlib import Path
from decouple import config
import dj_database_url
import os
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

# -------- أساسيات --------
SECRET_KEY = config('SECRET_KEY', default='unsafe-secret-key')
DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = [
    h.strip() for h in config('ALLOWED_HOSTS', default='127.0.0.1,localhost').split(',') if h.strip()
]

# -------- التطبيقات --------
INSTALLED_APPS = [
    # مهم لسيرفر ASGI في الإنتاج
    'daphne',

    # Django
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # طرف ثالث
    'channels',

    # تطبيقات محلية
    'accounts',
    'games',
    'payments',
]

# -------- Middleware --------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',

    # لخدمة الملفات الثابتة في الإنتاج بدون Nginx
    'whitenoise.middleware.WhiteNoiseMiddleware',

    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'wesh_aljawab.urls'

# -------- القوالب --------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# -------- ASGI / WSGI --------
WSGI_APPLICATION = 'wesh_aljawab.wsgi.application'
ASGI_APPLICATION = 'wesh_aljawab.asgi.application'

# -------- قاعدة البيانات --------
DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        ssl_require=(not DEBUG),  # <-- بدل False
    )
}
# -------- إعدادات اللعبة --------
GAME_SETTINGS = {
    'FREE_SESSION_DURATION_HOURS': 1,
    'PAID_SESSION_DURATION_DAYS': 3,
    'MAX_FREE_SESSIONS_PER_GAME_TYPE': 1,
    'SESSION_WARNING_THRESHOLDS': {
        'FREE': {'DANGER': 5, 'WARNING': 10},   # دقائق
        'PAID': {'DANGER': 2, 'WARNING': 6},    # ساعات
    }
}

# -------- Channels / Redis --------
REDIS_URL = config('REDIS_URL', default='')  # مثال: redis://localhost:6379/0 أو rediss://...
FORCE_REDIS = config('FORCE_REDIS', default=not DEBUG, cast=bool)

def _channels_redis_hosts(url: str):
    if not url:
        return []
    # دعم TLS إذا كانت rediss://
    parsed = urlparse(url)
    use_ssl = parsed.scheme == 'rediss'
    host_cfg = {'address': url}
    if use_ssl:
        host_cfg['ssl'] = True
    return [host_cfg]

if FORCE_REDIS and REDIS_URL:
    # settings.py
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [os.environ.get("REDIS_URL")],
            },
        },
    }

else:
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }

# -------- الكاش --------
REDIS_CACHE_URL = config('REDIS_CACHE_URL', default=REDIS_URL or '')
def _cache_location(url: str):
    if not url:
        return ''
    parsed = urlparse(url)
    if parsed.scheme == 'rediss':
        # django-redis يدعم rediss مباشرة
        return url
    return url

if FORCE_REDIS and REDIS_CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": _cache_location(REDIS_CACHE_URL),
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "CONNECTION_POOL_KWARGS": {"max_connections": 100},
                # لو rediss وتحتاج شهادة مخصصة أضف 'SSL' خيارات هنا
            },
            "KEY_PREFIX": "wesh",
            "TIMEOUT": 300,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "wesh-local-cache",
            "TIMEOUT": 300,
            "OPTIONS": {"MAX_ENTRIES": 1000},
        }
    }

# -------- Auth --------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# -------- لغة وتوقيت --------
LANGUAGE_CODE = 'ar'
TIME_ZONE = 'Asia/Riyadh'
USE_I18N = True
USE_TZ = True

# -------- ملفات ثابتة وميديا --------
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# WhiteNoise تخزين مضغوط مع Manifest
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# -------- أمان --------
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'

CSRF_COOKIE_AGE = 31449600
CSRF_COOKIE_SECURE = config('CSRF_COOKIE_SECURE', default=not DEBUG, cast=bool)
SESSION_COOKIE_SECURE = config('SESSION_COOKIE_SECURE', default=not DEBUG, cast=bool)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

SESSION_COOKIE_AGE = 86400
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# CSRF Trusted Origins من env (أضف دومين Render)
_csrf_trusted = config('CSRF_TRUSTED_ORIGINS', default='')
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_trusted.split(',') if o.strip()] if _csrf_trusted else []

# -------- بريد --------
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='localhost')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='وش الجواب <noreply@weshaljawab.com>')

# -------- حدود رفع --------
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# -------- Logging --------
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}', 'style': '{'},
        'simple': {'format': '{levelname} {message}', 'style': '{'},
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': LOG_DIR / 'django.log',
            'formatter': 'verbose',
        },
        'console': {'level': 'INFO', 'class': 'logging.StreamHandler', 'formatter': 'simple'},
    },
    'root': {'handlers': ['console', 'file'], 'level': 'INFO'},
    'loggers': {
        'django': {'handlers': ['console', 'file'], 'level': 'INFO', 'propagate': False},
        'accounts': {'handlers': ['console', 'file'], 'level': 'DEBUG', 'propagate': False},
        'games': {'handlers': ['console', 'file'], 'level': 'DEBUG', 'propagate': False},
    },
}

# -------- إعدادات إضافية للإنتاج --------
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
else:
    SECURE_SSL_REDIRECT = False
