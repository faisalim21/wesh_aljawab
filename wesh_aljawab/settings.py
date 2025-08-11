# wesh_aljawab/settings.py
from pathlib import Path
from decouple import config
import os
import sys
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

# ============== 🔐 إعدادات الأمان ==============
SECRET_KEY = config('SECRET_KEY', default='unsafe-secret-key')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = [h.strip() for h in config('ALLOWED_HOSTS', default='127.0.0.1,localhost').split(',') if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in config('CSRF_TRUSTED_ORIGINS', default='').split(',') if o.strip()]

# ============== 🧩 التطبيقات ==============
INSTALLED_APPS = [
    'daphne',  # خادم ASGI
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',  # WebSockets
    'accounts',
    'games',
    'payments',
]

# ============== ⚙️ الميدل وير ==============
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # Serve static files
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'wesh_aljawab.urls'

# ============== 🌐 القوالب ==============
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

WSGI_APPLICATION = 'wesh_aljawab.wsgi.application'
ASGI_APPLICATION = 'wesh_aljawab.asgi.application'

# ============== 🗄 قاعدة البيانات ==============
if DEBUG:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('DB_NAME'),
            'USER': config('DB_USER'),
            'PASSWORD': config('DB_PASSWORD'),
            'HOST': config('DB_HOST'),
            'PORT': config('DB_PORT', default='5432'),
            'OPTIONS': {'sslmode': 'require'},
        }
    }

# ============== ⚙️ إعدادات اللعبة ==============
GAME_SETTINGS = {
    'FREE_SESSION_DURATION_HOURS': 1,
    'PAID_SESSION_DURATION_DAYS': 3,
    'MAX_FREE_SESSIONS_PER_GAME_TYPE': 1,
    'SESSION_WARNING_THRESHOLDS': {
        'FREE': {'DANGER': 5, 'WARNING': 10},   # دقائق
        'PAID': {'DANGER': 2, 'WARNING': 6},    # ساعات
    }
}

# ============== 🔄 إعدادات Redis / Channels ==============
REDIS_URL = config('REDIS_URL', default='')
REDIS_CACHE_URL = config('REDIS_CACHE_URL', default=REDIS_URL or '')
FORCE_REDIS = config('FORCE_REDIS', default=False, cast=bool)

def _is_rediss(url: str) -> bool:
    try:
        return urlparse(url).scheme == 'rediss'
    except Exception:
        return False

def _channels_hosts(url: str):
    if not url:
        return []
    return [{'address': url, 'ssl': _is_rediss(url)}]

# Channels
try:
    if FORCE_REDIS and REDIS_URL:
        print("Using Redis for Channels...")
        CHANNEL_LAYERS = {
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {"hosts": _channels_hosts(REDIS_URL)},
            }
        }
    else:
        print("Using InMemory for Channels...")
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
except Exception as e:
    print(f"Redis connection failed for Channels, falling back to InMemory: {e}")
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Cache
try:
    if FORCE_REDIS and REDIS_CACHE_URL:
        print("Using Redis for Cache...")
        CACHES = {
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": REDIS_CACHE_URL,
                "OPTIONS": {
                    "CLIENT_CLASS": "django_redis.client.DefaultClient",
                    "CONNECTION_POOL_KWARGS": {"max_connections": 50},
                    # ضروري عند استخدام rediss://
                    **({"SSL": True} if _is_rediss(REDIS_CACHE_URL) else {}),
                },
                "KEY_PREFIX": "wesh",
                "TIMEOUT": 300,
            }
        }
    else:
        print("Using LocMem for Cache...")
        CACHES = {
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "wesh-local-cache",
                "TIMEOUT": 300,
                "OPTIONS": {"MAX_ENTRIES": 1000},
            }
        }
except Exception as e:
    print(f"Redis cache failed, falling back to LocMem: {e}")
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "wesh-local-cache",
            "TIMEOUT": 300,
            "OPTIONS": {"MAX_ENTRIES": 1000},
        }
    }

# ============== 🔐 المصادقة ==============
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# ============== 🌍 اللغة والتوقيت ==============
LANGUAGE_CODE = 'ar'
TIME_ZONE = 'Asia/Riyadh'
USE_I18N = True
USE_TZ = True

# ============== 📁 الملفات الثابتة والميديا ==============
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ============== 🛡 إعدادات الأمان ==============
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

# ============== ✉️ إعدادات البريد ==============
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='localhost')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='وش الجواب <noreply@weshaljawab.com>')

# ============== 📤 حدود رفع الملفات ==============
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ============== 📊 إعدادات التسجيل ==============
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

# ============== 🚀 إعدادات إضافية للإنتاج ==============
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
else:
    SECURE_SSL_REDIRECT = False
