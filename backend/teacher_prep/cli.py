from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from pydantic import ValidationError

from .models import SCHEMAS
from .service import Service


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="教师备课业务命令：JSON输入输出，不直接调用模型API")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("list")
    schema = sub.add_parser("schema")
    schema.add_argument("kind", choices=SCHEMAS)
    schema.add_argument("--output")
    for command in ["create", "save-design", "feedback", "decide", "revise"]:
        child = sub.add_parser(command)
        child.add_argument("--file", required=True)
        child.add_argument("--key", required=True)
        id_field = {"save-design": "lesson-id", "feedback": "version-id", "decide": "feedback-id", "revise": "decision-id"}.get(command)
        if id_field:
            child.add_argument("--" + id_field, required=True)
    for command in ["export", "check", "density"]:
        child = sub.add_parser(command)
        child.add_argument("--version-id", required=True)
    diff = sub.add_parser("diff")
    diff.add_argument("--version-id", required=True)
    diff.add_argument("--file", required=True)
    get = sub.add_parser("get")
    get.add_argument("kind", choices=["class", "lesson", "version", "feedback", "decision", "source"])
    get.add_argument("id")
    ingest = sub.add_parser("ingest")
    ingest.add_argument("--file", required=True)
    ingest.add_argument("--key", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            result = SCHEMAS[args.kind].model_json_schema()
            if args.output:
                output = Path(args.output).resolve()
                root = Path(args.root).resolve()
                if not output.is_relative_to(root):
                    raise ValueError("schema 输出必须在项目目录内")
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                result = {"kind": args.kind, "path": str(output)}
        else:
            service = Service(args.root)
            if args.command in {"doctor", "list"}:
                result = service.doctor() if args.command == "doctor" else service.list_lessons()
            elif args.command == "create":
                result = service.create(load(args.file), args.key)
            elif args.command == "save-design":
                result = service.save_design(args.lesson_id, load(args.file), args.key)
            elif args.command == "feedback":
                result = service.feedback(args.version_id, load(args.file), args.key)
            elif args.command == "decide":
                result = service.decide(args.feedback_id, load(args.file), args.key)
            elif args.command == "revise":
                result = service.revise(args.decision_id, load(args.file), args.key)
            elif args.command in {"export", "check"}:
                result = getattr(service, args.command)(args.version_id)
            elif args.command == "density":
                from .analysis import analyze
                from .models import LessonDesign
                result = analyze(LessonDesign.model_validate(service.get("version", args.version_id)["design"]))
            elif args.command == "diff":
                result = service.diff(args.version_id, load(args.file))
            elif args.command == "get":
                result = service.get(args.kind, args.id)
            else:
                result = service.ingest(args.file, args.key)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        if isinstance(exc, ValidationError):
            error = {"type": "validation", "details": exc.errors(include_context=False)}
        else:
            error = {"type": type(exc).__name__, "message": str(exc)}
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False, default=str))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
