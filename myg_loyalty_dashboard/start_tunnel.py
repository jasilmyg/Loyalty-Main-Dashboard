import subprocess
import time
import re

print("Starting localhost.run tunnel...")
with open("lhr_stdout.txt", "w") as out, open("lhr_stderr.txt", "w") as err:
    process = subprocess.Popen(
        ['ssh', '-o', 'StrictHostKeyChecking=no', '-R', '80:localhost:8000', 'nokey@localhost.run'],
        stdout=out,
        stderr=err
    )

# Leave it running in the background
