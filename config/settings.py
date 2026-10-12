"""Django settings for JejeGPT (local-first)."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path = BASE_DIR / ".env") -> None:
    """Charge un .env minimal (KEY=VALUE) sans dependance externe."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


SECRET_KEY = env("DJANGO_SECRET_KEY", "django-insecure-jejegpt-local-dev-only")
DEBUG = env("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",") if h]

if not DEBUG and (
    not SECRET_KEY
    or SECRET_KEY.startswith("django-insecure-")
    or SECRET_KEY.startswith("change-me-")
    or len(SECRET_KEY) < 50
    or len(set(SECRET_KEY)) < 5
):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY manquante ou insecure avec DEBUG=0.")

if not DEBUG:
    SESSION_COOKIE_SECURE = env("DJANGO_SESSION_COOKIE_SECURE", "1") == "1"
    CSRF_COOKIE_SECURE = env("DJANGO_CSRF_COOKIE_SECURE", "1") == "1"
    SECURE_SSL_REDIRECT = env("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "chat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "chat.context_processors.text_scale",
            ],
            # Templates compilés mémorisés en prod. Exclu en DEBUG : le reloader
            # dev ne redémarre pas à l'édition d'un template, le cache masquerait
            # les modifications. Voir docs Django (cached.Loader).
            **({} if DEBUG else {"loaders": [
                ("django.template.loaders.cached.Loader", [
                    "django.template.loaders.filesystem.Loader",
                    "django.template.loaders.app_directories.Loader",
                ]),
            ]}),
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "jejegpt",
        # Court par défaut (réglages) ; le HTML Markdown utilise son propre TTL long.
        "TIMEOUT": 60,
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# JejeGPT defaults (surchargeables par conversation)
JEJEGPT_DEFAULT_TEMPERATURE = float(env("JEJEGPT_DEFAULT_TEMPERATURE", "0.7"))
JEJEGPT_DEFAULT_SYSTEM_PROMPT = env(
    "JEJEGPT_DEFAULT_SYSTEM_PROMPT",
    "Tu réponds toujours en Markdown français structuré : titres ## avec un emoji, "
    "paragraphes courts, listes à puces avec mots-clés en **gras**, tableaux Markdown "
    "dès qu'il y a comparaison, classement ou exemples (2 colonnes minimum), exemples "
    "concrets et chiffrés. Interdit : les longs pavés de texte brut et les tirets "
    "collés en ligne sans sauts de ligne. Ton direct et utile. Entités nommées "
    "cliquables : quand tu cites une entité nommée (chaîne YouTube, article, outil, "
    "lieu…), écris-la en lien Markdown [Nom exact](https://URL officielle) avec "
    "l'URL vue pendant la recherche ou déjà présente dans la conversation ; si tu "
    "n'as vu aucune URL pour elle, laisse le nom en texte seul — n'invente jamais "
    "d'URL. Si l'utilisateur demande "
    "un fichier (PDF, Word, Excel, PowerPoint) : ne dis jamais que tu ne peux pas joindre "
    "de fichier et ne propose aucun script d'installation ; produis directement le contenu "
    "complet en Markdown structuré, car un lien de téléchargement sera joint automatiquement.",
)
JEJEGPT_HTTP_TIMEOUT = float(env("JEJEGPT_HTTP_TIMEOUT", "30"))
MEDIA_ROOT = Path(env("JEJEGPT_MEDIA_ROOT", str(BASE_DIR / "media")))
MEDIA_URL = "/media/"
JEJEGPT_EXPORTS_MAX_INPUT_CHARS = int(env("JEJEGPT_EXPORTS_MAX_INPUT_CHARS", "100000"))
JEJEGPT_EXPORTS_MAX_BYTES = int(env("JEJEGPT_EXPORTS_MAX_BYTES", "20971520"))
JEJEGPT_EXPORTS_QUOTA_BYTES = int(env("JEJEGPT_EXPORTS_QUOTA_BYTES", "209715200"))
JEJEGPT_EXPORTS_RETENTION_DAYS = int(env("JEJEGPT_EXPORTS_RETENTION_DAYS", "30"))
JEJEGPT_USAGE_RETENTION_DAYS = int(env("JEJEGPT_USAGE_RETENTION_DAYS", "180"))
JEJEGPT_SCHEDULER_ENABLED = env("JEJEGPT_SCHEDULER_ENABLED", "1") == "1"
JEJEGPT_SCHEDULER_TICK_SECONDS = int(env("JEJEGPT_SCHEDULER_TICK_SECONDS", "30"))
JEJEGPT_SCHEDULER_MAX_TASKS = int(env("JEJEGPT_SCHEDULER_MAX_TASKS", "20"))
JEJEGPT_SCHEDULER_MAX_FAILURES = int(env("JEJEGPT_SCHEDULER_MAX_FAILURES", "3"))
JEJEGPT_MEMORY_MAX_FACTS = int(env("JEJEGPT_MEMORY_MAX_FACTS", "50"))
JEJEGPT_MEMORY_MAX_CHARS = int(env("JEJEGPT_MEMORY_MAX_CHARS", "2000"))
# Images web illustratives (style ChatGPT) : hotlink, jamais de binaire persisté.
JEJEGPT_IMAGE_SEARCH_PROVIDER = env("JEJEGPT_IMAGE_SEARCH_PROVIDER", "duckduckgo")
JEJEGPT_IMAGE_SEARCH_API_KEY = env("JEJEGPT_IMAGE_SEARCH_API_KEY", "")
JEJEGPT_IMAGE_SEARCH_MAX_IMAGES = int(env("JEJEGPT_IMAGE_SEARCH_MAX_IMAGES", "3"))
JEJEGPT_IMAGE_SEARCH_AUTO_ENABLED = env("JEJEGPT_IMAGE_SEARCH_AUTO_ENABLED", "1") == "1"
JEJEGPT_IMAGE_SEARCH_TIMEOUT = float(env("JEJEGPT_IMAGE_SEARCH_TIMEOUT", "5"))
