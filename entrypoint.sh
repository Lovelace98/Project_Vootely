#!/bin/sh

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Waiting for database connection..."
python << END
import sys
import time
import psycopg2
from urllib.parse import urlparse
import os

db_url = os.environ.get('DATABASE_URL')
if not db_url:
    print("DATABASE_URL is not set. Skipping DB connection check.")
    sys.exit(0)

parsed = urlparse(db_url)
# Clean up path to get DB name
dbname = parsed.path.split('?')[0].lstrip('/')
user = parsed.username
password = parsed.password
host = parsed.hostname
port = parsed.port or 5432

attempts = 0
max_attempts = 30
while attempts < max_attempts:
    try:
        conn = psycopg2.connect(
            dbname=dbname,
            user=user,
            password=password,
            host=host,
            port=port,
            connect_timeout=3
        )
        conn.close()
        print("Database is ready!")
        sys.exit(0)
    except psycopg2.OperationalError as e:
        attempts += 1
        print(f"Database connection attempt {attempts}/{max_attempts} failed: {e}")
        time.sleep(2)

print("Could not connect to database. Exiting.")
sys.exit(1)
END

# Run migrations
echo "Running database migrations..."
python manage.py migrate --noinput

# Collect static files (needed because of persistent static volume mounting)
echo "Collecting static files..."
python manage.py collectstatic --noinput --clear

# Start application
echo "Starting gunicorn server..."
exec gunicorn votecentral.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120 --access-logfile - --error-logfile -
