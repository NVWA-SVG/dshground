"""Run both synthetic reference lessons through generation, feedback and revision."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from teacher_prep.service import Service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "demo")
    args = parser.parse_args()
    service = Service(args.root)
    records = []
    for kind in ["subject", "theme"]:
        def load(name):
            return json.loads((ROOT / "examples" / kind / f"{name}.json").read_text(encoding="utf-8"))
        first = service.create(load("request"), f"demo-{kind}-request-01")
        version = service.save_design(first["lesson_id"], load("design"), f"demo-{kind}-design-01")
        initial = service.export(version["version_id"])
        feedback = service.feedback(version["version_id"], load("feedback"), f"demo-{kind}-feedback-01")
        decision = service.decide(feedback["feedback_id"], load("decision"), f"demo-{kind}-decision-01")
        revised = service.revise(decision["decision_id"], load("revision"), f"demo-{kind}-revision-01")
        final = service.export(revised["version_id"])
        records.append({"course_type": kind, "initial": {"version_id": version["version_id"], "directory": initial["directory"], "validation": service.check(version["version_id"])},
                        "revision": {"version_id": revised["version_id"], "directory": final["directory"], "validation": service.check(revised["version_id"])},
                        "feedback_id": feedback["feedback_id"], "feedback_report": feedback.get("report_path"), "decision_id": decision["decision_id"], "change_count": len(revised["changes"])})
    report = {"data_kind": "synthetic_engineering_demo", "real_classroom_evidence": False, "model_called": False,
              "external_client_test": "pending", "office_visual_review": "pending", "records": records}
    destination = args.root.resolve() / "工程演示验收报告.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(destination), "courses": len(records), "versions": len(records) * 2, "real_classroom_evidence": False}, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
