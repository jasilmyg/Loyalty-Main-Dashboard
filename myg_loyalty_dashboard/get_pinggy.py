import subprocess
import time
import re

print("Starting pinggy...")
process = subprocess.Popen(
    ['ssh', '-o', 'StrictHostKeyChecking=no', '-p', '443', '-R0:localhost:8000', 'a.pinggy.io'],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

time.sleep(5)
# Pinggy usually prints the URL immediately upon connection.
# Read stdout and stderr non-blocking or just read a few lines if possible.
# Since it might block, we will try to read from stderr/stdout using a short timeout or just communicate if it exits.
import threading

out_data = []

def read_stream(stream):
    for line in stream:
        out_data.append(line)
        print("Read line:", line.strip())

t1 = threading.Thread(target=read_stream, args=(process.stdout,))
t2 = threading.Thread(target=read_stream, args=(process.stderr,))
t1.daemon = True
t2.daemon = True
t1.start()
t2.start()

time.sleep(5)

url = None
for line in out_data:
    if 'pinggy.link' in line:
        url = line.strip()
        break

if url:
    print("FOUND URL:", url)
else:
    print("NO URL FOUND")
    print("Output lines:")
    for l in out_data:
        print(l.strip())
