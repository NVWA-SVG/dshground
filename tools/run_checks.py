"""Keep pytest temporary files within the project workspace."""
import os
import json
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parent = root / ".test-runs"
parent.mkdir(exist_ok=True)
destination = (parent / ("run-" + uuid.uuid4().hex)).resolve()
if not destination.is_relative_to(root.resolve()) or destination.exists():
    raise SystemExit("拒绝使用非项目内的新测试目录")
environment = os.environ.copy()
environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
environment["PYTHONUTF8"] = "1"
reports = root / "docs"
reports.mkdir(exist_ok=True)
xml_path = reports / "test-results.xml"
command = [sys.executable, "-m", "pytest", "--basetemp", str(destination), "--tb=short", f"--junitxml={xml_path}"]
result = subprocess.run(command, cwd=root, env=environment, capture_output=True, encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
print(result.stdout, end="")
print(result.stderr, end="", file=sys.stderr)
report = {"exit_code": result.returncode, "command": command, "stdout": result.stdout, "stderr": result.stderr, "external_client_verified": False, "real_classroom_verified": False}
if xml_path.is_file():
    suites = ET.parse(xml_path).getroot().findall("testsuite")
    report["counts"] = {key: sum(int(s.get(key, 0)) for s in suites) for key in ["tests", "failures", "errors", "skipped"]}
(reports / "本地测试结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
raise SystemExit(result.returncode)
