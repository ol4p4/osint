import subprocess, sys, os
from pathlib import Path

PY = r"E:\software\python3.13.8\python.exe"
OSINT = r"D:\osint"
OUT = r"D:\osint\data"

# Clean temp scripts
for p in Path(OUT).glob("*.py"):
    if p.name.startswith("test_") or p.name.startswith("check_") or p.name.startswith("add_") or p.name.startswith("verify_") or p.name.startswith("clean_") or p.name.startswith("list_") or p.name.startswith("translate_") or p.name.startswith("fix_hyps_"):
        p.unlink()
        print(f"  removed {p.name}")

scripts = [
    ("Link intel->hypotheses", "link_intel_hyp.py"),
    ("Verify hypotheses", "verify_hypotheses.py"),
    ("Daily briefing", "daily_briefing.py"),
    ("Sync data", "sync_data.py"),
    ("Gen dashboard", "gen_dashboard.py"),
    ("Fix dashboard", "fix_dashboard.py"),
]

for label, script in scripts:
    path = os.path.join(OSINT, script)
    if not os.path.exists(path):
        print(f"SKIP {label}: {script} not found")
        continue
    print(f"\n=== {label} ===")
    result = subprocess.run([PY, path], capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    # Print last 3 lines of output
    lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
    for l in lines[-3:]:
        print(f"  {l}")
    if result.returncode != 0 and result.stderr:
        print(f"  ERR: {result.stderr[:200]}")

print("\n=== ALL DONE ===")
