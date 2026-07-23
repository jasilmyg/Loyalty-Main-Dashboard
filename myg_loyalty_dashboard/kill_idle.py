import os
import sys
import time

sys.path.insert(0, r'c:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')

import django
django.setup()

from django.db import connection
from django.conf import settings
import psycopg2

db = settings.DATABASES['default']

print("Attempting to connect and clear idle connections...")

connected = False
for i in range(10):
    try:
        conn = psycopg2.connect(
            host=db['HOST'],
            database=db['NAME'],
            user=db['USER'],
            password=db['PASSWORD'],
            port=db['PORT']
        )
        conn.autocommit = True
        connected = True
        print("Connected!")
        break
    except Exception as e:
        print(f"Attempt {i+1} failed: {e}")
        time.sleep(2)

if connected:
    with conn.cursor() as cur:
        # Kill all idle connections
        cur.execute("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE state = 'idle'
              AND pid <> pg_backend_pid();
        """)
        results = cur.fetchall()
        print(f"Terminated {len(results)} idle connections.")
        
        # Kill all connections that have been running for a long time just in case
        cur.execute("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE pid <> pg_backend_pid()
            AND state != 'idle';
        """)
        results2 = cur.fetchall()
        print(f"Terminated {len(results2)} other active connections.")
    conn.close()
    print("Done.")
else:
    print("Could not get a connection.")
