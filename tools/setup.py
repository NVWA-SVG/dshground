"""Validate existing dependencies; installing missing packages is an explicit option."""
import argparse
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--install", action="store_true", help="显式创建项目虚拟环境并安装锁定依赖")
args = parser.parse_args()
if args.install:
    virtual = root / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(virtual)], check=True)
    python = virtual / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "requirements.txt")], check=True)
    subprocess.run([str(python), str(__file__)], check=True)
    raise SystemExit(0)
expected = dict(line.strip().split("==", 1) for line in (root / "requirements.txt").read_text().splitlines() if "==" in line)
errors = []
for name, version in expected.items():
    try:
        actual = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        actual = "missing"
    if actual != version:
        errors.append(f"{name}: expected {version}, actual {actual}")
if errors:
    print(json.dumps({"ok": False, "dependencies": errors, "next": "python tools/setup.py --install"}))
    raise SystemExit(2)
for directory in ["config", "inputs", "data/requests"]:
    (root / directory).mkdir(parents=True, exist_ok=True)
configuration = {"python_executable": sys.executable, "dependencies": expected, "source": "validated_existing_interpreter", "format_version": 1}
(root / "config" / "runtime.json").write_text(json.dumps(configuration, indent=2), encoding="utf-8")
print(json.dumps({"ok": True, "python": sys.executable, "dependencies": expected}))
