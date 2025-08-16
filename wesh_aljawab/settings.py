# wesh_aljawab/settings.py
from pathlib import Path
from decouple import config
import os
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

# ============== 🔐 الأمان ==============
SECRET_KEY = config('SECRET_KEY', default='unsafe-secret-key')
DEBUG = config('DEBUG', default=False, cast=bool)

def _split_csv(env_value, default=''):
    raw = env_value if isinstance(env_value, str) else default
    return [x.strip() for x in raw.split(',') if x.strip()]

ALLOWED_HOSTS = _split_csv(config('ALLOWED_HOSTS', default='127.0.0.1,localhost'))
CSRF_TRUSTED_ORIGINS = _split_csv(config('CSRF_TRUSTED_ORIGINS', default=''))

# ============== 🧩 التطبيقات ==============
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'accounts',
    'games',
    'payments.apps.PaymentsConfig',
]

# ============== ⚙️ الميدل وير ==============
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
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
def _db_from_url(url: str):
    """حوّل DATABASE_URL إلى dict إعدادات Django"""
    u = urlparse(url)
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': (u.path or '').lstrip('/'),
        'USER': u.username or '',
        'PASSWORD': u.password or '',
        'HOST': u.hostname or '',
        'PORT': str(u.port or '5432'),
        'OPTIONS': {'sslmode': 'require'},
    }

DATABASE_URL = os.environ.get('DATABASE_URL') or config('DATABASE_URL', default='')

if DATABASE_URL:
    # استخدم DATABASE_URL إن وُجد (موصى به في Render)
    DATABASES = {'default': _db_from_url(DATABASE_URL)}
else:
    if DEBUG:
        # تطوير محلي: SQLite
        DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}
    else:
        # إنتاج بدون DATABASE_URL: خذ المتغيرات المفصلة
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
        'FREE': {'DANGER': 5, 'WARNING': 10},
        'PAID': {'DANGER': 2, 'WARNING': 6},
    }
}

# ============== 🔄 Redis / Channels ==============
from urllib.parse import urlparse as _urlparse
def _is_rediss(url: str) -> bool:
    try:
        return _urlparse(url).scheme == 'rediss'
    except Exception:
        return False

REDIS_URL = config('REDIS_URL', default='')
REDIS_CACHE_URL = config('REDIS_CACHE_URL', default=REDIS_URL or '')
FORCE_REDIS = config('FORCE_REDIS', default=False, cast=bool)

try:
    if FORCE_REDIS and REDIS_URL:
        CHANNEL_LAYERS = {
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {
                    "hosts": [REDIS_URL],  # لا نمرر SSL؛ rediss:// يكفي
                    "capacity": 1000,
                    "expiry": 10,
                },
            }
        }
    else:
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
except Exception:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

try:
    if FORCE_REDIS and REDIS_CACHE_URL:
        CACHES = {
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": REDIS_CACHE_URL,
                "OPTIONS": {
                    "CLIENT_CLASS": "django_redis.client.DefaultClient",
                    "CONNECTION_POOL_KWARGS": {"max_connections": 50},
                    **({"SSL": True} if _is_rediss(REDIS_CACHE_URL) else {}),
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
except Exception:
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

# ============== 📁 Static/Media ==============
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

# ============== 🛡 الأمان ==============
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

# ============== ✉️ البريد ==============
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='localhost')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='وش الجواب <noreply@weshaljawab.com>')

# ============== 📊 التسجيل ==============
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
        'file': {'level': 'INFO','class': 'logging.FileHandler','filename': LOG_DIR / 'django.log','formatter': 'verbose'},
        'console': {'level': 'INFO', 'class': 'logging.StreamHandler', 'formatter': 'simple'},
        'admin_file': {'level': 'INFO','class': 'logging.FileHandler','filename': LOG_DIR / 'admin_activities.log','formatter': 'verbose'},
    },
    'root': {'handlers': ['console', 'file'], 'level': 'INFO'},
    'loggers': {
        'django': {'handlers': ['console', 'file'], 'level': 'INFO', 'propagate': False},
        'accounts': {'handlers': ['console', 'file'], 'level': 'DEBUG', 'propagate': False},
        'games': {'handlers': ['console', 'file'], 'level': 'DEBUG', 'propagate': False},
        'admin_analytics': {'handlers': ['admin_file', 'console'], 'level': 'INFO', 'propagate': True},
        'payments': {'handlers': ['file', 'console'], 'level': 'INFO', 'propagate': True},
    },
}

# ============== 🚀 إنتاج ==============
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
else:
    SECURE_SSL_REDIRECT = False

# ===============================
# 📊 إعدادات الإدارة المحسنة - إضافات جديدة
# ===============================

# إعدادات التحليلات والتقارير
ADMIN_ANALYTICS_SETTINGS = {
    'CACHE_TIMEOUT': 300,  # 5 دقائق تخزين مؤقت للتحليلات
    'REPORTS_PER_PAGE': 20,
    'MAX_CHART_ITEMS': 10,
    'DEFAULT_CURRENCY': 'SAR',
    'COST_CALCULATION_MONTHS': 6,  # عدد الأشهر لحساب التكاليف
}

# إعدادات الرسوم والتكاليف الافتراضية
DEFAULT_GATEWAY_FEES = {
    'FIXED_FEE': 1.00,  # ريال واحد لكل معاملة
    'VISA_PERCENTAGE': 0.027,  # 2.7% للفيزا
    'MADA_PERCENTAGE': 0.01,   # 1% لمدى
}

# إعدادات التكاليف الافتراضية (سيتم إدراجها تلقائياً)
DEFAULT_COSTS = [
    {
        'name': 'رسوم تأسيس بوابة الدفع',
        'description': 'رسوم إعداد بوابة الدفع',
        'cost_type': 'one_time',
        'amount': 460.00,
        'currency': 'SAR'
    },
    {
        'name': 'السيرفر الشهري',
        'description': 'تكلفة استضافة السيرفر',
        'cost_type': 'monthly', 
        'amount': 26.25,  # 7 دولار تقريباً
        'currency': 'SAR'
    },
    {
        'name': 'اشتراك GPT',
        'description': 'اشتراك واجهة برمجة GPT',
        'cost_type': 'monthly',
        'amount': 90.00,
        'currency': 'SAR'
    }
]

# إعدادات الصفحات المخصصة للإدارة
ADMIN_CUSTOM_SETTINGS = {
    'SHOW_ANALYTICS_LINK': True,
    'SHOW_FINANCIAL_REPORTS': True,
    'ENABLE_USER_MANAGEMENT': True,
    'ENABLE_COST_TRACKING': True,
    'AUTO_REFRESH_STATS': True,
    'REFRESH_INTERVAL': 30000,  # 30 ثانية
}

# إعدادات الأمان للإدارة
ADMIN_SECURITY_SETTINGS = {
    'REQUIRE_STAFF_LOGIN': True,
    'ENABLE_ACTIVITY_LOGGING': True,
    'MAX_LOGIN_ATTEMPTS': 3,
    'LOCKOUT_DURATION': 300,  # 5 دقائق
}

# إعدادات التصدير والتقارير
REPORT_SETTINGS = {
    'ENABLE_PDF_EXPORT': True,
    'ENABLE_EXCEL_EXPORT': True,
    'MAX_EXPORT_RECORDS': 10000,
    'REPORT_CACHE_TIMEOUT': 600,  # 10 دقائق
}

# متغيرات البيئة للتكاليف (يمكن تعديلها بسهولة)
from decimal import Decimal

GATEWAY_SETUP_FEE = Decimal(os.getenv('GATEWAY_SETUP_FEE', '460.00'))
MONTHLY_SERVER_COST = Decimal(os.getenv('MONTHLY_SERVER_COST', '26.25'))
MONTHLY_GPT_COST = Decimal(os.getenv('MONTHLY_GPT_COST', '90.00'))
VISA_FEE_PERCENTAGE = Decimal(os.getenv('VISA_FEE_PERCENTAGE', '0.027'))
MADA_FEE_PERCENTAGE = Decimal(os.getenv('MADA_FEE_PERCENTAGE', '0.01'))

# تنسيق التواريخ والأرقام في التقارير
DATE_FORMAT = 'Y-m-d'
DATETIME_FORMAT = 'Y-m-d H:i:s'
NUMBER_GROUPING = 3

# إعدادات الرسائل للإدارة
MESSAGE_TAGS = {
    50: 'danger',  # ERROR
    40: 'danger',  # ERROR
    30: 'warning', # WARNING  
    25: 'success', # SUCCESS
    20: 'info',    # INFO
    10: 'info',    # DEBUG
}

# ===============================
# 🔧 إعدادات قاعدة البيانات لإنشاء البيانات الافتراضية
# ===============================

def create_default_admin_data():
    """إنشاء البيانات الافتراضية للإدارة"""
    try:
        from wesh_aljawab.models import AdminCost, AdminSettings
        
        # إنشاء التكاليف الافتراضية
        for cost_data in DEFAULT_COSTS:
            AdminCost.objects.get_or_create(
                name=cost_data['name'],
                defaults=cost_data
            )
        
        # إنشاء الإعدادات الافتراضية
        default_settings = [
            ('analytics_refresh_interval', '30000', 'فترة تحديث التحليلات بالميلي ثانية'),
            ('max_chart_items', '10', 'أقصى عدد عناصر في الرسوم البيانية'),
            ('reports_cache_timeout', '300', 'مدة تخزين التقارير مؤقتاً بالثواني'),
            ('enable_email_reports', 'false', 'تفعيل إرسال التقارير بالبريد'),
            ('default_report_period', '30', 'فترة التقرير الافتراضية بالأيام'),
        ]
        
        for key, value, description in default_settings:
            AdminSettings.objects.get_or_create(
                key=key,
                defaults={'value': value, 'description': description}
            )
    except Exception:
        pass  # تجاهل الأخطاء أثناء التهيئة الأولى

# تشغيل إنشاء البيانات الافتراضية عند بدء الخادم
try:
    create_default_admin_data()
except Exception:
    pass  # تجاهل الأخطاء أثناء التهيئة الأولى