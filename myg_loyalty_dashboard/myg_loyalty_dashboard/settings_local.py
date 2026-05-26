"""
Local development settings — uses SQLite so the server starts without
needing remote PostgreSQL credentials. Import all base settings and
override only the database.

Usage:
    python manage.py runserver --settings=myg_loyalty_dashboard.settings_local
"""
from myg_loyalty_dashboard.settings import *  # noqa: F401, F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'local_dev_db.sqlite3',
    }
}
