"""Use a locally verified interpreter while preserving argv without shell interpolation."""
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
runtime = root / "config" / "runtime.json"
python = sys.executable
if runtime.is_file():
    python = json.loads(runtime.read_text(encoding="utf-8-sig"))["python_executable"]
    if not Path(python).is_file():
        raise SystemExit("配置的 Python 不存在，请重新执行 tools/setup.py")
environment = os.environ.copy()
environment["PYTHONUTF8"] = "1"
raise SystemExit(subprocess.call([python, str(root / "teacher_prep.py"), "--root", str(root), *sys.argv[1:]], env=environment))
