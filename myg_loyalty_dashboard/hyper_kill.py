import os
import sys
import threading

sys.path.insert(0, r'c:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')

import django
django.setup()

import psycopg2
from django.conf import settings

db = settings.DATABASES['default']
SUCCESS = False

def hammer_db(thread_id):
    global SUCCESS
    while not SUCCESS:
        try:
            conn = psycopg2.connect(
                host=db['HOST'],
                database=db['NAME'],
                user=db['USER'],
                password=db['PASSWORD'],
                port=db['PORT'],
                connect_timeout=2
            )
            conn.autocommit = True
            if not SUCCESS:
                SUCCESS = True
                print(f"!!! THREAD {thread_id} GOT A SLOT !!!")
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT pg_terminate_backend(pid) 
                        FROM pg_stat_activity 
                        WHERE pid <> pg_backend_pid();
                    """)
                    res = cur.fetchall()
                    print(f"TERMINATED {len(res)} CONNECTIONS!!")
            conn.close()
            break
        except Exception:
            pass

print("Starting HYPER-AGGRESSIVE connection killer with 50 threads...")
threads = []
for i in range(50):
    t = threading.Thread(target=hammer_db, args=(i,))
    t.daemon = True
    t.start()
    threads.append(t)

# Wait up to 30 seconds for any thread to succeed
import time
start_time = time.time()
while not SUCCESS and time.time() - start_time < 30:
    time.sleep(0.1)

if SUCCESS:
    print("Victory. Connections cleared.")
else:
    print("Defeat. Could not beat the Render death loop.")
