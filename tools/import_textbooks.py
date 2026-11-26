"""Bulk-import teacher-provided textbook files into the local knowledge base.

Usage:
    python tools/import_textbooks.py --dir "C:\\path\\to\\人教版\\三年级"

The tool only reads files the teacher already has. It never downloads content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from teacher_prep.knowledge import KnowledgeBase  # noqa: E402
from teacher_prep.service import Service  # noqa: E402
from teacher_prep.exporters import sha256  # noqa: E402

SUBJECTS = ("语文", "数学", "英语")
SUFFIXES = {".txt", ".md", ".docx", ".pdf"}


def digest(path):
    return sha256(path)


def infer(path, source):
    # Use the full path so a batch root such as .../人教版/三年级 keeps its grade;
    # an explicit --subject/--grade still overrides inference.
    text = " ".join(path.parts) + " " + path.name
    subject = next((name for name in SUBJECTS if name in text), "")
    grade = ""
    match = re.search(r"([一二三四五六七八九]年级)", text)
    if match:
        grade = match.group(1)
    elif re.search(r"[一二三四五六七八九][上下]", text):
        grade = re.search(r"([一二三四五六七八九])[上下]", text).group(1) + "年级"
    return path.stem, subject, grade


def main():
    parser = argparse.ArgumentParser(description="批量导入本地教材文件到教师知识库")
    parser.add_argument("--dir", default=str(ROOT / "inputs" / "人教版" / "三年级"),
                        help="教材所在目录；支持子目录，默认 inputs/人教版/三年级")
    parser.add_argument("--subject", default="", help="强制覆盖学科；留空按路径/文件名识别")
    parser.add_argument("--grade", default="", help="强制覆盖年级；留空按路径/文件名识别")
    args = parser.parse_args()

    source = Path(args.dir).expanduser().resolve()
    if not source.is_dir():
        print(json.dumps({"ok": False, "error": f"目录不存在：{source}"}, ensure_ascii=False))
        return 2

    service = Service(ROOT)
    knowledge = KnowledgeBase(service)
    existing = {entry.get("sha256"): entry for entry in knowledge.list()["entries"]}
    imported, skipped, failed = [], [], []

    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        fingerprint = digest(path)
        if fingerprint in existing:
            current = existing[fingerprint]
            if not current.get("asset_count"):
                try:
                    knowledge.refresh_assets(current["id"])
                except Exception as error:
                    failed.append({"file": str(path), "error": f"素材刷新失败：{error}"})
            skipped.append({"file": str(path), "reason": "已导入", "knowledge_id": current["id"]})
            continue
        title, subject, grade = infer(path, source)
        subject = args.subject or subject
        grade = args.grade or grade
        parent = ROOT / "inputs" / ("import_" + uuid.uuid4().hex)
        try:
            parent.mkdir(parents=True, exist_ok=True)
            target = parent / path.name
            shutil.copy2(path, target)
            result = knowledge.create(target, {"title": title, "subject": subject, "grade": grade},
                                      "textbook_" + fingerprint[:24])
            imported.append({"file": str(path), **result})
            existing[fingerprint] = result
        except Exception as error:  # Keep importing the rest; report each failure.
            failed.append({"file": str(path), "error": str(error)})
        finally:
            # KnowledgeBase keeps its own original/extracted/chunks copy.
            if parent.resolve().is_relative_to((ROOT/'inputs').resolve()) and parent.name.startswith('import_'):
                shutil.rmtree(parent, ignore_errors=True)

    print(json.dumps({"ok": not failed, "source": str(source), "imported": imported,
                      "skipped": skipped, "failed": failed}, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
