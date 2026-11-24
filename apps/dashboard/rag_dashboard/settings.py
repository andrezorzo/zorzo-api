"""Django settings. Fails fast when DATABASE_URL is missing."""

import os

import dj_database_url

APP_ENV = os.environ.get("APP_ENV", "production")
DEBUG = APP_ENV == "local"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-only-not-secret" if DEBUG else "")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY is required outside APP_ENV=local")

_database_url = os.environ.get("DATABASE_URL")
if not _database_url:
    raise RuntimeError("DATABASE_URL is required")

DATABASES = {"default": dj_database_url.parse(_database_url)}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
INSTALLED_APPS = ["django.contrib.postgres", "data"]
ALLOWED_HOSTS = ["*"] if DEBUG else [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h]
USE_TZ = True
TIME_ZONE = "UTC"
ROOT_URLCONF = "rag_dashboard.urls"
