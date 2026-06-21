"""
Hourly report daemon — sleeps 3600s between reports.
Run this once and it reports every hour forever.
"""
import subprocess, sys, time, os

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hourly_report.py")
PYTHON = r"C:\Users\Administrator\gate_bot\.venv\Scripts\python.exe"
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "hourly_daemon.log")

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

log("Hourly report daemon started")

while True:
    try:
        # Wait until next whole hour + 1 minute (e.g. 07:01, 08:01...)
        now = time.localtime()
        # Seconds until next hour:01
        secs_until_next = (60 - now.tm_sec) + 60 * (59 - now.tm_min)
        if secs_until_next < 0:
            secs_until_next += 3600
        log(f"Next report in {secs_until_next}s (~{secs_until_next//60}min)")
        time.sleep(secs_until_next)

        # Run report
        log("Generating hourly report...")
        r = subprocess.run([PYTHON, SCRIPT], capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            log(f"OK: {r.stdout.strip()}")
        else:
            log(f"FAIL (exit {r.returncode}): {r.stderr[:500]}")
    except KeyboardInterrupt:
        log("Daemon stopped by user")
        break
    except Exception as e:
        log(f"ERROR: {e}")
        time.sleep(60)  # retry in 1 min
