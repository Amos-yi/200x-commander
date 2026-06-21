"""Stop Plan C paper cluster using PID files (Windows)."""
import os, glob, subprocess, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
pid_dir = os.path.join(SCRIPT_DIR, "logs")
pid_files = sorted(glob.glob(os.path.join(pid_dir, "pid_*.txt")))

if not pid_files:
    print("No PID files found. Nothing to stop.")
    sys.exit(0)

killed = 0
for pf in pid_files:
    coin = os.path.basename(pf).replace("pid_", "").replace(".txt", "")
    try:
        with open(pf) as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        print(f"  {coin}: bad PID file, skipping")
        continue

    # Windows: use taskkill
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, timeout=5)
        print(f"  {coin}: killed PID {pid}")
        killed += 1
    except Exception as e:
        print(f"  {coin}: PID {pid} — {e}")

    # Clean up PID file
    try:
        os.remove(pf)
    except OSError:
        pass

print(f"Stopped {killed}/{len(pid_files)} processes.")
