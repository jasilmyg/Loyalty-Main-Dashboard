#!/usr/bin/env bash
# Render build script for myG Loyalty Dashboard
set -o errexit

cd myg_loyalty_dashboard

# Install dependencies
pip install -r requirements.txt

# Collect static files
python manage.py collectstatic --no-input

# Run migrations (requires PGPASSWORD env var set in Render dashboard)
python manage.py migrate --no-input
