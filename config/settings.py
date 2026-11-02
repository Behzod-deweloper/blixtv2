from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import environ
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)
env.read_env(BASE_DIR / ".env")

DEBUG = env("DJANGO_DEBUG")
SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY or (not DEBUG and SECRET_KEY.startswith("replace-")):
    raise ImproperlyConfigured("Set a strong DJANGO_SECRET_KEY in the .env file.")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])
FRONTEND_URL = env(
    "FRONTEND_URL",
    default="http://127.0.0.1:5173" if DEBUG else "https://blixtv.uz",
).strip().rstrip("/")
frontend_url = urlsplit(FRONTEND_URL)
if (
    frontend_url.scheme not in {"http", "https"}
    or not frontend_url.netloc
    or frontend_url.path not in ("", "/")
    or frontend_url.query
    or frontend_url.fragment
):
    raise ImproperlyConfigured("FRONTEND_URL must be an HTTP(S) origin without a path.")
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", default="").strip()
GOOGLE_OAUTH_CLIENT_SECRET = env("GOOGLE_OAUTH_CLIENT_SECRET", default="").strip()
R2_BUCKET_NAME = env("R2_BUCKET_NAME", default="").strip()
R2_PUBLIC_URL = env("R2_PUBLIC_URL", default="").strip().rstrip("/")
R2_S3_ENDPOINT_URL = (
    env("R2_S3_ENDPOINT_URL", default="").strip()
    or env("R2_ENDPOINT_URL", default="").strip()
)
R2_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID", default="").strip()
R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", default="").strip()
R2_CDN_BASE_URL = env(
    "R2_CDN_BASE_URL",
    default="https://cdn.blixtv.uz",
).strip().rstrip("/")
cdn_url = urlsplit(R2_CDN_BASE_URL)
if (
    cdn_url.scheme != "https"
    or cdn_url.netloc.lower() != "cdn.blixtv.uz"
    or cdn_url.path not in ("", "/")
    or cdn_url.query
    or cdn_url.fragment
):
    raise ImproperlyConfigured("R2_CDN_BASE_URL must be https://cdn.blixtv.uz.")
R2_MULTIPART_PART_SIZE = 8 * 1024 * 1024
R2_MULTIPART_PRESIGNED_URL_TTL = 900
R2_MULTIPART_TOKEN_TTL = 24 * 60 * 60
R2_PUBLIC_MEDIA_URL = ""
if R2_PUBLIC_URL:
    public_url = urlsplit(R2_PUBLIC_URL)
    if (
        public_url.scheme != "https"
        or not public_url.netloc
        or public_url.path not in ("", "/")
        or public_url.query
        or public_url.fragment
    ):
        raise ImproperlyConfigured("R2_PUBLIC_URL must be an HTTPS public bucket URL.")
    R2_PUBLIC_MEDIA_URL = f"{R2_CDN_BASE_URL}/"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "corsheaders",
    "rest_framework",
    "django_filters",
    "accounts.apps.AccountsConfig",
    "catalog.apps.CatalogConfig",
]
AUTH_USER_MODEL = "accounts.User"
SITE_ID = 1
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
LOGIN_REDIRECT_URL = f"{FRONTEND_URL}/auth/google/callback"
LOGOUT_REDIRECT_URL = FRONTEND_URL
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APPS": [
            {
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "key": "",
            },
        ],
        "SCOPE": ["profile", "email"],
    },
}
# Skip allauth's confirmation page and redirect directly to Google's consent screen.
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_ADAPTER = "accounts.adapters.LoggingSocialAccountAdapter"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "oauth_console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "accounts.adapters": {
            "handlers": ["oauth_console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {}
postgres_source_url = env("POSTGRES_SOURCE_URL", default="").strip()
legacy_database_url = env("DATABASE_URL", default="").strip()
if not postgres_source_url and legacy_database_url.startswith(
    ("postgres://", "postgresql://")
):
    postgres_source_url = legacy_database_url
if postgres_source_url:
    postgres_source = env.db_url_config(postgres_source_url)
    if postgres_source["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("POSTGRES_SOURCE_URL must use PostgreSQL.")
    postgres_source.update(
        {
            "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False,
        }
    )
    DATABASES["postgres_source"] = postgres_source

DATABASES["default"] = {
    "ENGINE": "django.db.backends.mysql",
    "NAME": env("MYSQL_DATABASE", default="mangalab"),
    "USER": env("MYSQL_USER", default="mangalab"),
    "PASSWORD": env("MYSQL_PASSWORD", default=""),
    "HOST": env("MYSQL_HOST", default="127.0.0.1"),
    "PORT": env("MYSQL_PORT", default="3306"),
    "OPTIONS": {
        "charset": "utf8mb4",
        "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
    },
    "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=0),
    "CONN_HEALTH_CHECKS": True,
}

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://127.0.0.1:6379/1"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {"protocol": 2},
        },
        "TIMEOUT": 300,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uz"
LANGUAGES = [
    ("uz", "Uzbek"),
    ("en", "English"),
]
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
static_root = Path(env("STATIC_ROOT", default="staticfiles"))
STATIC_ROOT = static_root if static_root.is_absolute() else BASE_DIR / static_root
R2_STORAGE_ENABLED = env.bool("R2_STORAGE_ENABLED", default=False)
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
if R2_STORAGE_ENABLED:
    r2_settings = {
        "R2_S3_ENDPOINT_URL": R2_S3_ENDPOINT_URL,
        "R2_BUCKET_NAME": R2_BUCKET_NAME,
        "R2_ACCESS_KEY_ID": R2_ACCESS_KEY_ID,
        "R2_SECRET_ACCESS_KEY": R2_SECRET_ACCESS_KEY,
        "R2_PUBLIC_URL": R2_PUBLIC_URL,
    }
    missing_r2_settings = [name for name, value in r2_settings.items() if not value]
    if missing_r2_settings:
        raise ImproperlyConfigured(
            "R2_STORAGE_ENABLED requires these .env settings: " + ", ".join(missing_r2_settings)
        )
    endpoint_url = urlsplit(r2_settings["R2_S3_ENDPOINT_URL"])
    if endpoint_url.scheme != "https" or not endpoint_url.netloc:
        raise ImproperlyConfigured("R2_S3_ENDPOINT_URL must be a valid HTTPS S3 endpoint.")
    public_domain = urlsplit(R2_CDN_BASE_URL).netloc
    MEDIA_URL = R2_PUBLIC_MEDIA_URL
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "access_key": r2_settings["R2_ACCESS_KEY_ID"],
                "secret_key": r2_settings["R2_SECRET_ACCESS_KEY"],
                "bucket_name": r2_settings["R2_BUCKET_NAME"],
                "endpoint_url": r2_settings["R2_S3_ENDPOINT_URL"],
                "region_name": "auto",
                "signature_version": "s3v4",
                "addressing_style": "path",
                "custom_domain": public_domain,
                "url_protocol": "https:",
                "querystring_auth": False,
                "file_overwrite": True,
                "object_parameters": {
                    "CacheControl": "public, max-age=86400",
                },
            },
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
VIDEO_MAX_UPLOAD_SIZE = env.int("VIDEO_MAX_UPLOAD_SIZE", default=500 * 1024 * 1024)
FFMPEG_BINARY = env("FFMPEG_BINARY", default="ffmpeg")
FFPROBE_BINARY = env("FFPROBE_BINARY", default="ffprobe")
VIDEO_TRANSCODE_TIMEOUT_SECONDS = env.int("VIDEO_TRANSCODE_TIMEOUT_SECONDS", default=6 * 60 * 60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://127.0.0.1:6379/2")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = VIDEO_TRANSCODE_TIMEOUT_SECONDS + 300
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

#DEFAULT_THROTTLE_RATES

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "https://blixtv.uz",
        "https://www.blixtv.uz",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "https://doorway-sermon-acetone.ngrok-free.dev",
    ],
)
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=[
        "https://blixtv.uz",
        "https://www.blixtv.uz",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://10.38.21.60:5173",
    ],
)

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_RATES": {
        "comic_view": "60/hour",
        "auth_register": "10/hour",
        "auth_login": "10/minute",
        "google_session": "10/minute",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SITE_ID = 1
# SOCIALACCOUNT_LOGIN_ON_GET = True
