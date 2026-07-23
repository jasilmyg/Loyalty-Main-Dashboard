import os
import sys
import time

sys.path.insert(0, r'c:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')

import django
django.setup()

from django.db import connection
import psycopg2
from django.conf import settings

db = settings.DATABASES['default']

print("AGGRESSIVE KILL: Looping indefinitely until a connection slot opens...")

success = False
attempts = 0
while not success and attempts < 200:
    attempts += 1
    try:
        conn = psycopg2.connect(
            host=db['HOST'],
            database=db['NAME'],
            user=db['USER'],
            password=db['PASSWORD'],
            port=db['PORT']
        )
        conn.autocommit = True
        print(f"GOT A SLOT ON ATTEMPT {attempts}! Commencing slaughter...")
        with conn.cursor() as cur:
            # Kill everything but us
            cur.execute("""
                SELECT pg_terminate_backend(pid) 
                FROM pg_stat_activity 
                WHERE pid <> pg_backend_pid();
            """)
            res = cur.fetchall()
            print(f"TERMINATED {len(res)} CONNECTIONS!")
        conn.close()
        success = True
    except Exception as e:
        if attempts % 10 == 0:
            print(f"Attempt {attempts} failed... keep hammering")
        time.sleep(0.5)

if not success:
    print("Could not get a connection even after aggressive hammering.")
