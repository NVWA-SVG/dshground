"""DSH's narrow JSON/stdin bridge. No shell or arbitrary Python execution."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "backend"))

from teacher_prep.service import Service
from teacher_prep.models import SCHEMAS, LessonDesign
from teacher_prep.analysis import analyze


def dispatch(service, message):
    op = message["op"]
    data = message.get("data", {})
    if op == 'teaching-delivery':
        from teacher_prep.teaching_delivery import prepare_delivery
        return prepare_delivery(service, data['version_id'], data.get('run_id'))
    if op.startswith("ppt-"):
        from teacher_prep.presentations import Presentations
        presentations = Presentations(service)
        if op in {'ppt-incremental-plan','ppt-save-incremental','ppt-reuse-all'}:
            from teacher_prep.incremental_presentations import IncrementalPresentations
            incremental=IncrementalPresentations(presentations)
            if op=='ppt-incremental-plan':return incremental.prepare(data['run_id'])
            if op=='ppt-save-incremental':return incremental.save(data['run_id'],data['payload'],data['image_config'])
            return incremental.save_all(data['run_id'],data['image_config'])
        if op == "ppt-prepare":
            return presentations.prepare(data["version_id"], data["run_id"], data.get("style", ""))
        if op == "ppt-save-prompt":
            return presentations.save_prompt(data["run_id"], data["payload"])
        if op == "ppt-read-prompt":
            return presentations.read_prompt(data["run_id"])
        if op in {"ppt-accept", "ppt-repair"}:
            return presentations.accept(data["run_id"], data["provider"], data["remote_id"], data.get("credits"), repair_text=op == "ppt-repair")
        if op == "ppt-compose-local":
            return presentations.compose_local(data["version_id"], data["run_id"])
        if op == "ppt-pages":
            return presentations.pages(data["version_id"])
        if op == "ppt-compose-manual":
            return presentations.compose_manual(data["version_id"], data["run_id"], data.get("mapping") or {})
        raise ValueError("未知课件操作")
    if op == "overview":
        result = service.list_lessons()
        with service.store.connect() as db:
            result["feedback"] = [dict(r) for r in db.execute(
                "SELECT id,lesson_id,version_id,created FROM feedback ORDER BY created DESC,id")]
            superseded = {json.loads(row['payload']).get('supersedes_feedback_id')
                          for row in db.execute('SELECT payload FROM feedback')}
            for row in result['feedback']:
                row['superseded'] = row['id'] in superseded
            result["decisions"] = [dict(r) for r in db.execute(
                "SELECT id,feedback_id,created FROM decisions ORDER BY created DESC,id")]
        return result
    if op == "schema":
        return SCHEMAS[data["kind"]].model_json_schema()
    if op == 'revision-outline':
        from teacher_prep.revision_patches import outline
        return outline(service.get('version',data['id'])['design'])
    if op == 'revision-read':
        return service.read_revision_targets(data['id'],data['targets'])
    if op == 'revision-preview':
        return service.preview_revision_patch(data['id'],data['payload'])
    if op == 'revise-patch':
        return service.revise_patch(data['id'],data['payload'],data['key'])
    if op == "density":
        version = service.get("version", data["id"])
        return analyze(LessonDesign.model_validate(version["design"]))
    if op == "source-text":
        row = service.get("source", data["id"])
        return {"id": row["id"], "name": row["name"],
                "text": Path(row["text_path"]).read_text(encoding="utf-8")}
    if op == 'source-context':
        from teacher_prep.retrieval import source_context
        return source_context(service,data.get('ids',[]),data.get('query',''),data.get('max_chars',18000))
    if op == "get":
        return service.get(data["kind"], data["id"])
    if op == "doctor":
        result = service.doctor()
        result["host"] = "Windows-native DeepSeek Harness teacher plugin"
        return result
    if op in {"create", "ingest"}:
        return getattr(service, op)(data["payload"], data["key"])
    if op == "update_lesson":
        return service.update_lesson(data["id"], data["payload"], data["key"])
    if op == "delete_lesson":
        return service.delete_lesson(data["id"])
    if op == "lesson_context":
        return service.lesson_context(data["id"])
    if op == "classes_list":
        return service.list_classes()
    if op == "class_profile":
        return service.class_profile(data["id"],query=data.get('query',''),limit=data.get('limit',8))
    if op == "class_update":
        return service.update_class(data["id"], data["payload"], data["key"])
    if op.startswith("knowledge_"):
        from teacher_prep.knowledge import KnowledgeBase
        knowledge = KnowledgeBase(service)
        if op == 'knowledge_vision_inventory':return knowledge.vision_inventory(data['id'],data.get('chapter_id'))
        if op == 'knowledge_vision_publish':return knowledge.publish_vision_description(data['id'],data['sha256'],data['description'],data['model'],data['cache_key'])
        if op == 'knowledge_import_limits':
            from teacher_prep.import_limits import import_limits
            return import_limits(service.root)
        if op == 'knowledge_pdf_structure':
            return knowledge.pdf_structure(data['id'])
        if op == 'knowledge_unit':
            return knowledge.create_unit(data['id'],data['start_page'],data['end_page'],data.get('title'),data['key'])
        if op == 'knowledge_index':return knowledge.book_index(data['id'])
        if op == 'knowledge_import_status':
            from teacher_prep.import_progress import status
            return status(service.root,data['key'])
        if op == 'knowledge_chunks':return knowledge.chunk_preview(data['id'],data.get('chapter'),data.get('offset',0),data.get('limit',30))
        if op == 'knowledge_reindex':return knowledge.rebuild_book_index(data['id'])
        if op == "knowledge_list":
            return knowledge.list()
        if op == "knowledge_create":
            return knowledge.create(data["path"], data.get("meta"), data["key"])
        if op == "knowledge_delete":
            return knowledge.delete(data["id"])
        if op == "knowledge_context":
            return knowledge.context(data.get("ids"), data.get("query", ""),max_chars=data.get('max_chars',16000))
        if op == "knowledge_assets":
            return {"assets": knowledge.assets(data["id"])}
        if op == "knowledge_asset_path":
            return {"path": str(knowledge.asset_file(data["id"], data["name"]))}
        if op == "knowledge_refresh":
            return knowledge.refresh_assets(data["id"])
        if op == "knowledge_text":
            return knowledge.text(data["id"])
        if op == "knowledge_asset_update":
            return knowledge.update_asset(data["id"], data["asset_id"], data.get("fields") or {})
        if op == "knowledge_asset_describe":
            return knowledge.describe_assets(data["id"], data.get("updates") or [])
    if op in {"save_design", "feedback", "decide", "revise"}:
        return getattr(service, op)(data["id"], data["payload"], data["key"])
    if op == "diff":
        return service.diff(data["id"], data["payload"])
    if op in {"export", "check"}:
        return getattr(service, op)(data["id"])
    raise ValueError("未知教师工作台操作")


if __name__ == "__main__":
    try:
        result = dispatch(Service(ROOT), json.load(sys.stdin))
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    except Exception as error:
        from pydantic import ValidationError
        details = error.errors(include_context=False,include_input=False)[:12] if isinstance(error, ValidationError) else str(error)
        print(json.dumps({"ok": False, "error": details, "error_code": getattr(error, "code", None)}, ensure_ascii=False, default=str))
        sys.exit(2)
