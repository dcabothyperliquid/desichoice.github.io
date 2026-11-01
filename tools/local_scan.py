"""
local_scan.py - ONE command from your own PC / VPS (Indian home IP = no captcha):

    pip install playwright && python -m playwright install chromium
    python tools/local_scan.py            # feed + offers, then git commit + push guides/

Use this if the GitHub Actions run keeps hitting Amazon captcha (datacenter IP).
Optional env: AMAZON_IN_TAG=yourid-21   SCAN_MODE=feed|offers|auto
"""
import os, subprocess, sys
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(root)
env = dict(os.environ)
modes = [env["SCAN_MODE"]] if env.get("SCAN_MODE") else ["feed", "offers"]
for m in modes:
    print("== scan", m)
    subprocess.run([sys.executable, "tools/scan_in.py", m], env=env, check=False)
run = lambda *a: subprocess.run(a, check=False)
run("git", "pull", "--rebase", "--autostash")
run("git", "add", "-A", "guides")
if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
    print("no change - nothing to push")
else:
    run("git", "commit", "-m", "feed: India deals updated (local scan)")
    run("git", "push")
    print("pushed")
