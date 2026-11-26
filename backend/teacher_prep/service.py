from __future__ import annotations

import importlib.metadata
import json
import shutil
import sys
import tempfile
from pathlib import Path

from .analysis import analyze, design_diff, validate_revision
from .documents import extract_text
from .exporters import generate_bundle, sha256, validate_bundle
from .models import Feedback, LessonDesign, LessonRequest, Revision, TeacherDecision
from .store import Store, dump, identifier, now


class Service:
    def __init__(self, root):
        self.store = Store(root)
        self.root = self.store.root

    def _sources_exist(self, identifiers):
        for source_id in identifiers:
            self.store.read("sources", source_id)

    def _knowledge_exist(self, identifiers):
        for knowledge_id in identifiers or []:
            self.store.read("knowledge", knowledge_id)

    def create(self, raw, key):
        request = LessonRequest.model_validate(raw)
        self._sources_exist(request.source_ids)
        self._knowledge_exist(request.knowledge_ids)
        payload = request.model_dump(mode="json")
        if request.class_id:
            context = json.loads(self.store.read("classes", request.class_id)["payload"])
            if (context["class_label"], context["grade"]) != (request.class_label, request.grade):
                raise ValueError("复用班级标识时，班级名称与年级必须一致")

        def action(db):
            class_id = request.class_id or identifier("class")
            if request.class_id is None:
                class_payload = {name: payload[name] for name in ["class_label", "grade", "class_size", "prior_knowledge", "difficulties"]}
                db.execute("INSERT INTO classes VALUES(?,?,?)", (class_id, dump(class_payload), now()))
            lesson_id = identifier("lesson")
            db.execute("INSERT INTO lessons VALUES(?,?,?)", (lesson_id, dump({**payload, "class_id": class_id}), now()))
            return {"lesson_id": lesson_id, "class_id": class_id, "status": "awaiting_design", "personalization_basis": "provided" if request.prior_knowledge or request.difficulties else "not_provided"}
        return self.store.idempotent("create", key, payload, action)

    def update_lesson(self, lesson_id, raw, key):
        if not isinstance(raw, dict):
            raise ValueError("课程信息格式不正确")
        row = self.store.read("lessons", lesson_id)
        current = LessonRequest.model_validate_json(row["payload"]).model_dump(mode="json")
        with self.store.connect() as db:
            version_count = db.execute("SELECT COUNT(*) FROM versions WHERE lesson_id=?", (lesson_id,)).fetchone()[0]
        allowed = {"title", "category", "class_label"}
        if not version_count:
            allowed |= {"course_type", "grade", "subject", "duration_minutes", "class_size",
                        "prior_knowledge", "difficulties", "constraints"}
        extra = set(raw) - allowed
        if extra:
            if version_count:
                raise ValueError("已有教学版本时只能修改课程名称、分类和班级名称；其他内容请通过学情修订生成新版本")
            raise ValueError("包含不可修改的字段：" + "、".join(sorted(extra)))
        payload = LessonRequest.model_validate({**current, **{k: raw[k] for k in allowed if k in raw}}).model_dump(mode="json")

        def action(db):
            db.execute("UPDATE lessons SET payload=? WHERE id=?", (dump(payload), lesson_id))
            class_id = payload.get("class_id")
            if class_id:
                shared = any(json.loads(item["payload"]).get("class_id") == class_id
                             for item in db.execute("SELECT payload FROM lessons WHERE id<>?", (lesson_id,)))
                if not shared:
                    class_payload = {name: payload[name] for name in
                                     ["class_label", "grade", "class_size", "prior_knowledge", "difficulties"]}
                    db.execute("UPDATE classes SET payload=? WHERE id=?", (dump(class_payload), class_id))
            return {"lesson_id": lesson_id, "request": payload, "versions": version_count}
        return self.store.idempotent("update_lesson", key, {"id": lesson_id, "payload": raw}, action)

    def delete_lesson(self, lesson_id):
        with self.store.transaction() as db:
            lesson = db.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
            if lesson is None:
                raise ValueError("课程不存在")
            payload = json.loads(lesson["payload"])
            versions = [row["id"] for row in db.execute(
                "SELECT id FROM versions WHERE lesson_id=? ORDER BY number DESC", (lesson_id,))]
            for version_id in versions:
                if db.execute("SELECT 1 FROM export_jobs WHERE version_id=? AND status='running'", (version_id,)).fetchone():
                    raise ValueError("该版本正在导出，请稍后再删除")
            feedback_ids = [row["id"] for row in db.execute(
                "SELECT id FROM feedback WHERE lesson_id=? OR version_id IN (SELECT id FROM versions WHERE lesson_id=?)",
                (lesson_id, lesson_id))]
            for feedback_id in feedback_ids:
                db.execute("DELETE FROM decisions WHERE feedback_id=?", (feedback_id,))
            if feedback_ids:
                db.executemany("DELETE FROM feedback WHERE id=?", [(feedback_id,) for feedback_id in feedback_ids])
            for version_id in versions:
                db.execute("DELETE FROM export_jobs WHERE version_id=?", (version_id,))
                db.execute("DELETE FROM versions WHERE id=?", (version_id,))
            db.execute("DELETE FROM lessons WHERE id=?", (lesson_id,))
            class_id = payload.get("class_id")
            if class_id and not any(json.loads(row["payload"]).get("class_id") == class_id
                                    for row in db.execute("SELECT payload FROM lessons")):
                db.execute("DELETE FROM classes WHERE id=?", (class_id,))
            requested_sources = set(payload.get("source_ids") or [])
            remaining_sources = set()
            for row in db.execute("SELECT payload FROM lessons"):
                remaining_sources.update(json.loads(row["payload"]).get("source_ids") or [])
            deleted_sources = sorted(requested_sources - remaining_sources)
            for source_id in deleted_sources:
                db.execute("DELETE FROM sources WHERE id=?", (source_id,))
            deleted_ids = {lesson_id, *versions, *deleted_sources}
            for row in list(db.execute("SELECT operation,key,result FROM ledger")):
                if any(deleted_id in row["result"] for deleted_id in deleted_ids):
                    db.execute("DELETE FROM ledger WHERE operation=? AND key=?", (row["operation"], row["key"]))
        shutil.rmtree(self.root / "deliverables" / lesson_id, ignore_errors=True)
        for source_id in deleted_sources:
            shutil.rmtree(self.store.data / "sources" / source_id, ignore_errors=True)
        for version_id in versions:
            (self.store.data / "locks" / f"{version_id}.lock").unlink(missing_ok=True)
        return {"deleted": True, "lesson_id": lesson_id, "versions": versions, "sources": deleted_sources}

    def save_design(self, lesson_id, raw, key):
        request = LessonRequest.model_validate_json(self.store.read("lessons", lesson_id)["payload"])
        design = LessonDesign.model_validate(raw)
        if (design.course_type, design.grade, design.subject, design.duration_minutes) != (request.course_type, request.grade, request.subject, request.duration_minutes):
            raise ValueError("设计的课程类型、年级、学科和总课时必须与教师输入一致")
        if set(request.source_ids) - set(design.source_ids):
            raise ValueError("教学设计遗漏了教师提供的资料依据")
        if set(request.knowledge_ids) - set(design.knowledge_ids):
            raise ValueError("教学设计遗漏了已选择的知识库依据")
        self._sources_exist(design.source_ids)
        self._knowledge_exist(design.knowledge_ids)
        payload = design.model_dump(mode="json")

        def action(db):
            if db.execute("SELECT 1 FROM versions WHERE lesson_id=?", (lesson_id,)).fetchone():
                raise ValueError("课程已有初版；后续修改请使用反馈、教师选择和修订流程")
            version_id = identifier("version")
            db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                version_id, lesson_id, 1, None, None, dump(payload), "[]", "首次生成", "draft", None, None, now(),
            ))
            return {"lesson_id": lesson_id, "version_id": version_id, "number": 1, "status": "draft"}
        return self.store.idempotent("save_design", key, {"lesson_id": lesson_id, "design": payload}, action)

    def feedback(self, version_id, raw, key):
        version = self.store.read("versions", version_id)
        if version["status"] != "ready":
            raise ValueError("请先完成该版本材料导出，再关联授课反馈")
        design = LessonDesign.model_validate_json(version["design"])
        feedback = Feedback.model_validate(raw)
        if feedback.supersedes_feedback_id:
            previous = self.store.read('feedback', feedback.supersedes_feedback_id)
            if previous['version_id'] != version_id:
                raise ValueError('只能更正同一材料版本下的课堂记录')
        activities = {a.id for a in design.activities}
        questions = {q.id for q in design.questions}
        for entry in [*feedback.participation, *feedback.timings, *feedback.observations]:
            if entry.activity_id not in activities:
                raise ValueError("反馈引用了不存在的教学环节")
        if any(result.question_id not in questions for result in feedback.assessments):
            raise ValueError("反馈引用了不存在的检测题")
        question_map = {q.id: q for q in design.questions}
        for result in feedback.assessments:
            if question_map[result.question_id].mode == "rubric" and result.correct is not None:
                raise ValueError("情境表达任务请填写 met_criteria 达到评价要点人数，不使用知识正确率")
            if question_map[result.question_id].mode == "objective" and result.met_criteria is not None:
                raise ValueError("客观题请填写 correct 正确人数")
        request = json.loads(self.store.read("lessons", version["lesson_id"])["payload"])
        size = request.get("class_size")
        if size and (any(p.eligible and p.eligible > size for p in feedback.participation) or any(a.total and a.total > size for a in feedback.assessments)):
            raise ValueError("班级人数口径不一致；应参与或作答人数大于本班人数")
        report = analyze(design, feedback)
        payload = feedback.model_dump(mode="json")
        # Keep the canonical payload of legacy requests stable for idempotency.
        if not feedback.requested_changes:payload.pop('requested_changes',None)
        if not feedback.next_lesson_focus:payload.pop('next_lesson_focus',None)
        if feedback.supersedes_feedback_id is None:payload.pop('supersedes_feedback_id',None)
        if feedback.record_mode == 'revision':payload.pop('record_mode',None)

        def action(db):
            if feedback.supersedes_feedback_id:
                for row in db.execute('SELECT payload FROM feedback WHERE version_id=?',(version_id,)):
                    if json.loads(row['payload']).get('supersedes_feedback_id') == feedback.supersedes_feedback_id:
                        raise ValueError('这条记录已有更新，请刷新后更正最新记录')
            feedback_id = identifier("feedback")
            db.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?)", (feedback_id, version["lesson_id"], version_id, dump(payload), dump(report), now()))
            directory = self.store.data / "feedback" / feedback_id
            directory.mkdir(parents=True)
            notes = ["# 课堂记录与后续教学", "", f"课程：{design.title}｜所用材料：第{version['number']}版", "", "## 教师原始反馈", feedback.raw_text,
                     '', '## 下次教学关注',feedback.next_lesson_focus or '未单独填写；后续备课结合本课记录与新课目标。',
                     '', '## 有效做法或保留要求',*feedback.teacher_keep,"", "## 记录边界", "信息密度、课堂参与和学习表现分别记录；缺失数据不补估，以下解释不构成因果结论。", ""]
            if feedback.requested_changes:
                notes.extend(['## 历史本课调整意愿（不是下次课的学习成果）', feedback.requested_changes, ''])
            notes.extend(["## 环节信息密度", "", "首次引入知识点数 ÷ 整个环节分钟；0 个新知识点不代表没有学习价值。", "",
                          "| 环节 | 新知识点 | 计划/实际分钟 | 计划/实际密度 | 计划思考练习分钟 |", "|---|---:|---|---|---:|"])
            for row in report["density"]:
                actual = row["actual_minutes"] if row["actual_minutes"] is not None else "未记录"
                ratio = row["actual_points_per_activity_minute"] if row["actual_points_per_activity_minute"] is not None else "未记录"
                title = row["title"].replace("|", "／").replace("\n", " ")
                notes.append(f"| {title} | {row['new_knowledge_count']} | {row['planned_minutes']}/{actual} | {row['planned_points_per_activity_minute']}/{ratio} | {row['planned_thinking_minutes']} |")
            notes.append("")
            for suggestion in report["suggestions"]:
                notes.extend([f"## {suggestion['title']}", "观察：" + suggestion["observation"], "待核对解释：" + suggestion["hypothesis"], "可选调整：" + "；".join(suggestion["suggested_actions"]), "下次检查：" + suggestion["verify_next_time"], ""])
            report_path = directory / "学情反馈分析.md"
            report_path.write_text("\n".join(notes), encoding="utf-8")
            (directory / "分项记录.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"feedback_id": feedback_id, "version_id": version_id, "analysis": report, "report_path": str(report_path)}
        return self.store.idempotent("feedback", key, {"version_id": version_id, "feedback": payload}, action)

    def decide(self, feedback_id, raw, key):
        record = self.store.read("feedback", feedback_id)
        if json.loads(record['payload']).get('record_mode') == 'future_teaching':
            raise ValueError('这条课后记录用于后续教学，请为本班新建备课，不生成本课修订版')
        decision = TeacherDecision.model_validate(raw)
        valid = {s["id"] for s in json.loads(record["analysis"])["suggestions"]}
        if not {c.suggestion_id for c in decision.choices} <= valid:
            raise ValueError("教师选择引用了不存在的建议")
        parent = LessonDesign.model_validate_json(self.store.read("versions", record["version_id"])["design"])
        known = {f"{kind}:{item.id}" for key_name, kind in {"goals": "goal", "knowledge_points": "knowledge", "activities": "activity", "slides": "slide", "questions": "question"}.items() for item in getattr(parent, key_name)}
        if not set(decision.protected_ids) <= known:
            raise ValueError("保护标识必须指向已有目标、知识点、环节、页面或题目")
        payload = decision.model_dump(mode="json")

        def action(db):
            decision_id = identifier("decision")
            db.execute("INSERT INTO decisions VALUES(?,?,?,?)", (decision_id, feedback_id, dump(payload), now()))
            return {"decision_id": decision_id, "feedback_id": feedback_id, "status": "decision_recorded"}
        return self.store.idempotent("decide", key, {"feedback_id": feedback_id, "decision": payload}, action)

    def diff(self, version_id, raw):
        before = LessonDesign.model_validate_json(self.store.read("versions", version_id)["design"])
        after = LessonDesign.model_validate(raw)
        return {"changes": design_diff(before, after)}

    def revise(self, decision_id, raw, key):
        decision_record = self.store.read("decisions", decision_id)
        decision = TeacherDecision.model_validate_json(decision_record["payload"])
        feedback = self.store.read("feedback", decision_record["feedback_id"])
        parent = self.store.read("versions", feedback["version_id"])
        before = LessonDesign.model_validate_json(parent["design"])
        revision = Revision.model_validate(raw)
        self._sources_exist(revision.design.source_ids)
        if set(before.knowledge_ids) - set(revision.design.knowledge_ids):
            raise ValueError("修订版不能移除原版使用的知识库依据")
        self._knowledge_exist(revision.design.knowledge_ids)
        changes = validate_revision(before, revision.design, decision, revision.change_links, json.loads(feedback["analysis"]))

        def action(db):
            number = db.execute("SELECT COALESCE(MAX(number),0)+1 FROM versions WHERE lesson_id=?", (parent["lesson_id"],)).fetchone()[0]
            version_id = identifier("version")
            db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                version_id, parent["lesson_id"], number, parent["id"], decision_id,
                dump(revision.design.model_dump(mode="json")), dump(changes), revision.summary, "draft", None, None, now(),
            ))
            return {"lesson_id": parent["lesson_id"], "version_id": version_id, "number": number, "parent_id": parent["id"], "status": "draft", "changes": changes}
        return self.store.idempotent("revise", key, {"decision_id": decision_id, "revision": revision.model_dump(mode="json")}, action)

    def revision_parent(self, decision_id):
        decision=self.get('decision',decision_id)
        feedback=self.get('feedback',decision['feedback_id'])
        return self.get('version',feedback['version_id'])

    def read_revision_targets(self, decision_id, targets):
        from .revision_patches import read_targets
        return {'entities':read_targets(self.revision_parent(decision_id)['design'],targets)}

    def preview_revision_patch(self, decision_id, raw):
        from .revision_patches import merge_edits,compact_diff
        parent=self.revision_parent(decision_id)
        return compact_diff(parent['design'],merge_edits(parent['design'],raw,parent['id']))

    def revise_patch(self, decision_id, raw, key):
        from .models import IncrementalRevision
        from .revision_patches import merge_edits
        patch=IncrementalRevision.model_validate(raw)
        parent=self.revision_parent(decision_id)
        design=merge_edits(parent['design'],{'parent_version_id':patch.parent_version_id,
                           'operations':[op.model_dump() for op in patch.operations]},parent['id'])
        return self.revise(decision_id,{'design':design.model_dump(mode='json'),'summary':patch.summary,
                           'change_links':[link.model_dump() for link in patch.change_links]},key)

    def export(self, version_id):
        version = self.store.read("versions", version_id)
        design = LessonDesign.model_validate_json(version["design"])
        destination = self.root / "deliverables" / version["lesson_id"] / f"v{version['number']}-{version_id}"
        with self.store.export_lock(version_id):
            job_id = identifier("export")
            with self.store.transaction() as db:
                db.execute("UPDATE export_jobs SET status='interrupted',finished=? WHERE version_id=? AND status='running'", (now(), version_id))
                db.execute("INSERT INTO export_jobs VALUES(?,?,?,?,?,?)", (job_id, version_id, "running", None, now(), None))
                db.execute("UPDATE versions SET status='exporting',error=NULL WHERE id=?", (version_id,))
            try:
                if destination.exists():
                    manifest = json.loads((destination / "交付清单.json").read_text(encoding="utf-8"))
                    if manifest["version_id"] != version_id or manifest["lesson_id"] != version["lesson_id"]:
                        raise ValueError("已有交付目录不属于当前版本，拒绝覆盖")
                    validate_bundle(destination, manifest, design)
                else:
                    workspace = self.store.data / "workspaces"
                    workspace.mkdir(exist_ok=True)
                    staging = Path(tempfile.mkdtemp(prefix=f"{version_id}-", dir=workspace))
                    manifest = generate_bundle(design, staging, version["lesson_id"], version_id, version["number"], json.loads(version["changes"]), version["summary"])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    staging.rename(destination)
                result = {"version_id": version_id, "status": "ready", "directory": str(destination), "manifest": manifest}
                with self.store.transaction() as db:
                    db.execute("UPDATE versions SET status='ready',manifest=?,error=NULL WHERE id=?", (dump(result), version_id))
                    db.execute("UPDATE export_jobs SET status='completed',finished=? WHERE id=?", (now(), job_id))
                return result
            except BaseException as exc:
                with self.store.transaction() as db:
                    db.execute("UPDATE versions SET status='failed',error=? WHERE id=?", (str(exc), version_id))
                    db.execute("UPDATE export_jobs SET status='failed',error=?,finished=? WHERE id=?", (str(exc), now(), job_id))
                raise

    def check(self, version_id):
        version = self.store.read("versions", version_id)
        if not version["manifest"]:
            raise ValueError("当前版本尚无完成的交付清单")
        result = json.loads(version["manifest"])
        validation = validate_bundle(Path(result["directory"]), result["manifest"], LessonDesign.model_validate_json(version["design"]))
        return {"version_id": version_id, "status": version["status"], **validation}

    def get(self, kind, entity_id):
        tables = {"class": "classes", "lesson": "lessons", "version": "versions", "feedback": "feedback", "decision": "decisions", "source": "sources"}
        if kind not in tables:
            raise ValueError("未知对象类型")
        row = self.store.read(tables[kind], entity_id)
        for key in ["payload", "design", "changes", "analysis", "manifest"]:
            if row.get(key) is not None:
                row[key] = json.loads(row[key])
        return row

    def list_lessons(self):
        with self.store.connect() as db:
            lessons = [dict(row) for row in db.execute("SELECT id,payload,created FROM lessons ORDER BY created DESC,id")]
            for lesson in lessons:
                lesson["request"] = json.loads(lesson.pop("payload"))
                lesson["versions"] = [dict(row) for row in db.execute("SELECT id,number,status,parent_id,error FROM versions WHERE lesson_id=? ORDER BY number", (lesson["id"],))]
        return {"lessons": lessons}

    def lesson_context(self, lesson_id):
        row = self.store.read("lessons", lesson_id)
        with self.store.connect() as db:
            versions = [dict(item) for item in db.execute(
                "SELECT id,number,status,parent_id,error FROM versions WHERE lesson_id=? ORDER BY number", (lesson_id,))]
        return {"lesson_id": lesson_id, "request": json.loads(row["payload"]), "versions": versions, "created": row["created"]}

    def list_classes(self):
        with self.store.connect() as db:
            classes = [dict(row) for row in db.execute("SELECT * FROM classes ORDER BY created DESC,id")]
            lessons = [dict(row) for row in db.execute("SELECT payload FROM lessons")]
        counts = {}
        for lesson in lessons:
            class_id = json.loads(lesson["payload"]).get("class_id")
            if class_id:
                counts[class_id] = counts.get(class_id, 0) + 1
        return {"classes": [{**json.loads(row["payload"]), "id": row["id"], "created": row["created"],
                             "lesson_count": counts.get(row["id"], 0)} for row in classes]}

    def update_class(self, class_id, raw, key):
        if not isinstance(raw, dict):
            raise ValueError("班级信息格式不正确")
        current = json.loads(self.store.read("classes", class_id)["payload"])
        allowed = {"class_label", "grade", "class_size", "prior_knowledge", "difficulties"}
        extra = set(raw) - allowed
        if extra:
            raise ValueError("包含不可修改的班级字段：" + "、".join(sorted(extra)))
        merged = {**current, **{name: raw[name] for name in allowed if name in raw}}
        class_label = str(merged.get("class_label") or "").strip()
        grade = str(merged.get("grade") or "").strip()
        if not class_label or not grade:
            raise ValueError("班级名称和年级不能为空")
        size = merged.get("class_size")
        if size in ("", None):
            size = None
        else:
            try:
                size = int(size)
            except (TypeError, ValueError):
                raise ValueError("班级人数应为1—1000的整数") from None
            if size <= 0 or size > 1000:
                raise ValueError("班级人数应为1—1000的整数")
        prior_knowledge = merged.get("prior_knowledge")
        difficulties = merged.get("difficulties")
        prior_knowledge = str(prior_knowledge).strip() if prior_knowledge not in (None, "") else None
        difficulties = str(difficulties).strip() if difficulties not in (None, "") else None
        if len(class_label) > 12000 or (prior_knowledge and len(prior_knowledge) > 12000) or (difficulties and len(difficulties) > 12000):
            raise ValueError("班级信息过长")
        payload = {"class_label": class_label, "grade": grade, "class_size": size,
                   "prior_knowledge": prior_knowledge, "difficulties": difficulties}

        def action(db):
            db.execute("UPDATE classes SET payload=? WHERE id=?", (dump(payload), class_id))
            return {"class_id": class_id, "payload": payload}
        return self.store.idempotent("update_class", key, {"id": class_id, "payload": raw}, action)

    def class_profile(self, class_id, query='', limit=8):
        from .retrieval import tokens, passages
        from .learning_records import summarize_record, class_characteristics
        limit = max(1, min(20, int(limit)))
        baseline = json.loads(self.store.read("classes", class_id)["payload"])
        with self.store.connect() as db:
            lessons = [dict(row) for row in db.execute("SELECT id,payload,created FROM lessons ORDER BY created DESC,id")]
            feedback = [dict(row) for row in db.execute("SELECT id,lesson_id,version_id,payload,analysis,created FROM feedback ORDER BY created DESC,id")]
        class_lessons = [lesson for lesson in lessons if json.loads(lesson["payload"]).get("class_id") == class_id]
        lesson_payload = {lesson["id"]: json.loads(lesson["payload"]) for lesson in class_lessons}
        class_feedback = [item for item in feedback if item['lesson_id'] in lesson_payload]
        superseded = {json.loads(item['payload']).get('supersedes_feedback_id') for item in class_feedback}
        evidence, versions = [], {}
        query_tokens = tokens(query)
        for item in feedback:
            if item["lesson_id"] not in lesson_payload:
                continue
            if item['id'] in superseded:
                continue
            payload, analysis = json.loads(item["payload"]), json.loads(item["analysis"])
            if item['version_id'] not in versions:
                versions[item['version_id']] = json.loads(self.store.read('versions', item['version_id'])['design'])
            design = versions[item['version_id']]
            questions = {row['id']: row for row in design.get('questions', [])}
            goals = {row['id']: row['description'] for row in design.get('goals', [])}
            points = {row['id']: row['title'] for row in design.get('knowledge_points', [])}
            activities = {row['id']: row['title'] for row in design.get('activities', [])}
            results = []
            for row in analysis.get('assessment_results', [])[:12]:
                question = questions.get(row.get('question_id'), {})
                results.append({**{key: row.get(key) for key in ('question_id','correct','met_criteria','total','timing','days_after','basis','independent','errors')},
                                'question_prompt': question.get('prompt', ''),
                                'goals': [goals[key] for key in question.get('goal_ids', []) if key in goals],
                                'knowledge_points': [points[key] for key in question.get('knowledge_ids', []) if key in points]})
            raw = str(payload.get('raw_text', ''))
            pieces = list(passages(raw, size=800))
            if query_tokens and pieces:
                excerpt = max(pieces, key=lambda p: len(query_tokens & tokens(p['text'])))
            else:
                excerpt = pieces[0] if pieces else {'text':'','offset':0}
            record = {
                'feedback_id':item['id'],'lesson_id':item['lesson_id'],'version_id':item['version_id'],
                'lesson_title':lesson_payload[item['lesson_id']].get('title',''),
                'observed_at':payload.get('observed_at',''),'created':item['created'],
                'raw_text':excerpt['text'],'raw_excerpt_offset':excerpt['offset'],'raw_truncated':len(raw)>len(excerpt['text']),
                'requested_changes':str(payload.get('requested_changes',''))[:800],
                'requested_changes_truncated':len(str(payload.get('requested_changes','')))>800,
                'next_lesson_focus':str(payload.get('next_lesson_focus',''))[:800],
                'next_lesson_focus_truncated':len(str(payload.get('next_lesson_focus','')))>800,
                'observations':[{**{key:row.get(key) for key in ('activity_id','text','basis')},'activity_title':activities.get(row.get('activity_id'),'')} for row in payload.get('observations',[])[:4]],
                'participation':[{**{key:row.get(key) for key in ('activity_id','participating','eligible','basis')},'activity_title':activities.get(row.get('activity_id'),'')} for row in analysis.get('participation',[])[:12]],
                'assessments':results,'missing':analysis.get('missing',[]),
                'activity_pacing':[{key:row.get(key) for key in
                    ('activity_id','title','new_knowledge_count','planned_minutes','actual_minutes','actual_time_basis',
                     'planned_points_per_activity_minute','actual_points_per_activity_minute','planned_thinking_minutes','reasoning_steps')}
                    for row in analysis.get('density',[]) if row.get('actual_minutes') is not None][:12],
                'suggestions':[str(row.get('title','')) for row in analysis.get('suggestions',[])][:5],
                '_learning_record':analysis.get('learning_record') or summarize_record(LessonDesign.model_validate(design),Feedback.model_validate(payload)),
            }
            relevant_text = raw+' '+str(payload.get('next_lesson_focus',''))+' '+str(payload.get('requested_changes',''))+' '+record['lesson_title']+' '+json.dumps(results,ensure_ascii=False)
            evidence.append((len(query_tokens & tokens(relevant_text)),len(evidence),record))
        if query_tokens:
            evidence.sort(key=lambda row:(-row[0],row[1]))
        selected = [row[2] for row in evidence[:limit]]
        characteristics = class_characteristics(selected)
        for record in selected:
            record.pop('_learning_record', None)
        return {'class_id':class_id,'baseline':baseline,
                'lessons':[{'id':row['id'],'title':lesson_payload[row['id']].get('title',''),'created':row['created']} for row in class_lessons],
                'feedback_count':len(evidence),'historical_record_count':len(class_feedback),
                'evidence':selected,'characteristics':characteristics,
                'use':'后续同班备课的有来源线索；按新课相关性选择，不将单次表现视为固定能力。',
                'selection':'lesson_relevance_then_recency' if query_tokens else 'recency'}

    def ingest(self, path, key):
        path = Path(path).resolve()
        inputs = (self.root / "inputs").resolve()
        if not path.is_relative_to(inputs) or not path.is_file():
            raise ValueError(f"请将要处理的教材资料放入 {inputs}，只接入该目录内明确提供的文件")
        if path.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("首版单个资料文件上限为25MB")
        if path.suffix.lower() not in {".txt", ".md", ".docx", ".pdf"}:
            raise ValueError("首版支持 TXT、MD、DOCX 和可提取文本的 PDF")
        fingerprint = sha256(path)

        def action(db):
            text = extract_text(path)
            source_id = identifier("source")
            directory = self.store.data / "sources" / source_id
            directory.mkdir(parents=True)
            original = directory / ("original" + path.suffix.lower())
            shutil.copy2(path, original)
            extracted = directory / "extracted.txt"
            extracted.write_text(text, encoding="utf-8")
            db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?)", (source_id, path.name, fingerprint, str(extracted), str(original), now()))
            return {"source_id": source_id, "name": path.name, "sha256": fingerprint, "text_path": str(extracted), "characters": len(text)}
        return self.store.idempotent("ingest", key, {"sha256": fingerprint, "name": path.name}, action)

    def doctor(self):
        versions = {name: importlib.metadata.version(name) for name in ["pydantic", "python-pptx", "python-docx", "pypdf", "PyMuPDF"]}
        return {"project_root": str(self.root), "python": sys.version.split()[0], "dependencies": versions,
                "database": str(self.store.path), "host": "DSH provides generation; this CLI performs deterministic business operations",
                "external_client_integration": "not_verified_by_local_doctor", "office_visual_review": "not_verified"}
