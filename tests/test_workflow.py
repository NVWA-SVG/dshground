from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from docx import Document
from pydantic import ValidationError
from pptx import Presentation

from teacher_prep.analysis import analyze, design_diff
from teacher_prep.models import Feedback, LessonDesign, TeacherDecision
from teacher_prep.service import Service

ROOT = Path(__file__).resolve().parents[1]


def example(kind, name):
    return json.loads((ROOT / "examples" / kind / f"{name}.json").read_text(encoding="utf-8"))


def initial(root, kind="subject"):
    service = Service(root)
    lesson = service.create(example(kind, "request"), f"{kind}-request")
    version = service.save_design(lesson["lesson_id"], example(kind, "design"), f"{kind}-design")
    return service, lesson, version


@pytest.mark.parametrize("kind", ["subject", "theme"])
def test_complete_generation_and_teacher_revision(tmp_path, kind):
    service, lesson, first = initial(tmp_path, kind)
    bundle = service.export(first["version_id"])
    original_ppt = (Path(bundle["directory"]) / "课件.pptx").read_bytes()
    feedback = service.feedback(first["version_id"], example(kind, "feedback"), "feedback")
    assert Path(feedback["report_path"]).is_file()
    result = feedback["analysis"]["assessment_results"][0]
    assert (result["correct_fraction"] if kind == "subject" else result["met_criteria_fraction"]) == 0.4
    assert feedback["analysis"]["overall_score"] is None
    choice = service.decide(feedback["feedback_id"], example(kind, "decision"), "choice")
    second = service.revise(choice["decision_id"], example(kind, "revision"), "revision")
    updated = service.export(second["version_id"])
    assert second["parent_id"] == first["version_id"] and second["number"] == 2
    assert Path(bundle["directory"]) != Path(updated["directory"])
    assert (Path(bundle["directory"]) / "课件.pptx").read_bytes() == original_ppt
    assert service.check(first["version_id"])["valid"] and service.check(second["version_id"])["valid"]
    deck = Presentation(Path(updated["directory"]) / "课件.pptx")
    assert len(deck.slides) == updated["manifest"]["slide_count"]
    assert any(shape.has_text_frame and shape.text for page in deck.slides for shape in page.shapes)
    assert all(page.notes_slide.notes_text_frame.text.strip() for page in deck.slides)
    with zipfile.ZipFile(Path(updated["directory"]) / "交付包.zip") as package:
        assert "交付清单.json" in package.namelist()
        assert json.loads(package.read("交付清单.json")) == updated["manifest"]
    assert all(change["evidence_ids"] == ["assessment:1"] for change in second["changes"])


def test_idempotency_restart_and_collision(tmp_path):
    service, lesson, version = initial(tmp_path)
    reopened = Service(tmp_path)
    assert reopened.create(example("subject", "request"), "subject-request")["lesson_id"] == lesson["lesson_id"]
    assert reopened.save_design(lesson["lesson_id"], example("subject", "design"), "subject-design") == version
    changed = example("subject", "request")
    changed["title"] = "不同输入"
    with pytest.raises(ValueError, match="幂等"):
        reopened.create(changed, "subject-request")
    assert len(reopened.list_lessons()["lessons"]) == 1


def test_concurrent_same_request_is_single_lesson(tmp_path):
    service = Service(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: service.create(example("subject", "request"), "same-request")["lesson_id"], range(4)))
    assert len(set(ids)) == 1 and len(service.list_lessons()["lessons"]) == 1


def test_class_reuse_is_explicit_and_same_names_are_not_silently_merged(tmp_path):
    service = Service(tmp_path)
    first = service.create(example("subject", "request"), "first")
    second = service.create(example("subject", "request"), "second")
    assert first["class_id"] != second["class_id"]
    request = example("subject", "request")
    request["class_id"] = first["class_id"]
    reused = service.create(request, "reused")
    assert reused["class_id"] == first["class_id"]
    assert service.get("class", first["class_id"])["payload"]["grade"] == request["grade"]
    request["grade"] = "不同年级"
    with pytest.raises(ValueError, match="班级"):
        service.create(request, "invalid-class")


@pytest.mark.parametrize("mutation", ["time", "reference", "introduced", "duplicate", "goal_coverage"])
def test_design_rejects_broken_invariants(mutation):
    raw = example("subject", "design")
    if mutation == "time":
        raw["activities"][0]["duration_minutes"] += 1
    elif mutation == "reference":
        raw["slides"][0]["activity_id"] = "MISSING"
    elif mutation == "introduced":
        raw["activities"][4]["introduces"] = ["K3"]
    elif mutation == "duplicate":
        raw["slides"][1]["id"] = raw["slides"][0]["id"]
    else:
        for question in raw["questions"]:
            question["goal_ids"] = ["G1"]
    with pytest.raises(ValidationError):
        LessonDesign.model_validate(raw)


def test_missing_values_are_not_estimated_and_time_basis_is_preserved():
    raw = example("subject", "feedback")
    raw["assessments"][0]["total"] = None
    raw["participation"][0]["eligible"] = None
    raw["timings"] = []
    report = analyze(LessonDesign.model_validate(example("subject", "design")), Feedback.model_validate(raw))
    assert report["assessment_results"][0]["correct_fraction"] is None
    assert report["participation"][0]["participation_fraction"] is None
    assert all(row["actual_points_per_activity_minute"] is None for row in report["density"])
    assert report["causal_conclusion"] is None


@pytest.mark.parametrize("case", ["impossible_count", "zero_denominator", "missing_delay", "duplicate_result"])
def test_feedback_rejects_invalid_measurement(case):
    raw = example("subject", "feedback")
    if case == "impossible_count":
        raw["assessments"][0]["correct"] = 31
    elif case == "zero_denominator":
        raw["assessments"][0]["total"] = 0
    elif case == "missing_delay":
        raw["assessments"][0]["timing"] = "delayed"
        raw["assessments"][0]["days_after"] = None
    else:
        raw["assessments"].append(copy.deepcopy(raw["assessments"][0]))
    with pytest.raises(ValidationError):
        Feedback.model_validate(raw)


def test_theme_uses_criteria_not_knowledge_correctness(tmp_path):
    service, _, version = initial(tmp_path, "theme")
    service.export(version["version_id"])
    raw = example("theme", "feedback")
    raw["assessments"][0]["correct"] = raw["assessments"][0].pop("met_criteria")
    with pytest.raises(ValueError, match="评价要点"):
        service.feedback(version["version_id"], raw, "bad-rubric")


@pytest.mark.parametrize("case", ["unconfirmed", "rejected", "goal_changed", "missing_link", "protected"])
def test_revision_requires_teachers_decision_and_exact_change_coverage(tmp_path, case):
    service, _, version = initial(tmp_path)
    service.export(version["version_id"])
    feedback = service.feedback(version["version_id"], example("subject", "feedback"), "feedback")
    choice = example("subject", "decision")
    revision = example("subject", "revision")
    if case == "unconfirmed":
        choice["teacher_confirmed"] = False
        with pytest.raises(ValidationError):
            service.decide(feedback["feedback_id"], choice, "bad")
        return
    if case == "rejected":
        choice["choices"][0]["action"] = "reject"
    if case == "protected":
        choice["protected_ids"].append("slide:S06")
    decision = service.decide(feedback["feedback_id"], choice, "choice")
    if case == "goal_changed":
        revision["design"]["goals"][0]["description"] = "更换了核心目标"
    if case == "missing_link":
        revision["change_links"].pop()
    with pytest.raises(ValueError):
        service.revise(decision["decision_id"], revision, "invalid-revision")
    assert len(service.list_lessons()["lessons"][0]["versions"]) == 1


def test_interrupted_export_can_retry_without_duplicate_version(tmp_path, monkeypatch):
    import teacher_prep.service as module
    service, _, version = initial(tmp_path)
    original = module.generate_bundle

    def fail(*args, **kwargs):
        (args[1] / "partial.txt").write_text("incomplete", encoding="utf-8")
        raise OSError("simulated export interruption")
    monkeypatch.setattr(module, "generate_bundle", fail)
    with pytest.raises(OSError):
        service.export(version["version_id"])
    assert service.get("version", version["version_id"])["status"] == "failed"
    monkeypatch.setattr(module, "generate_bundle", original)
    assert service.export(version["version_id"])["status"] == "ready"
    assert len(service.list_lessons()["lessons"][0]["versions"]) == 1
    assert service.export(version["version_id"])["status"] == "ready"


def test_tampered_bundle_is_detected_and_not_overwritten(tmp_path):
    service, _, version = initial(tmp_path)
    result = service.export(version["version_id"])
    path = Path(result["directory"]) / "逐字稿.docx"
    path.write_bytes(b"damaged")
    with pytest.raises(ValueError):
        service.check(version["version_id"])
    with pytest.raises(ValueError):
        service.export(version["version_id"])
    assert path.read_bytes() == b"damaged"


def test_export_lock_queries_current_process_without_signalling_it(tmp_path):
    service = Service(tmp_path)
    with service.store.export_lock("version_lock_test"):
        with pytest.raises(ValueError, match="正在导出"):
            with service.store.export_lock("version_lock_test"):
                pytest.fail("second writer acquired the lock")
        assert os.getpid() > 0
    assert not (service.store.data / "locks" / "version_lock_test.lock").exists()


def test_long_content_paginates_and_script_uses_actual_page_range(tmp_path):
    service = Service(tmp_path)
    request = service.create(example("subject", "request"), "request")
    raw = example("subject", "design")
    raw["slides"][0]["points"] = ["这是一条用于检验长内容自动分页和页码一致性的示例内容。" * 3] * 5
    version = service.save_design(request["lesson_id"], raw, "long-design")
    result = service.export(version["version_id"])
    mapping = result["manifest"]["page_mapping"]["S01"]
    assert len(mapping) > 1
    text = "\n".join(p.text for p in Document(Path(result["directory"]) / "逐字稿.docx").paragraphs)
    assert f"PPT第{mapping[0]}—{mapping[-1]}页" in text
    assert service.check(version["version_id"])["valid"]


def test_ingest_sources_are_explicit_and_preserved(tmp_path):
    service = Service(tmp_path)
    directory = tmp_path / "inputs"
    directory.mkdir()
    path = directory / "教材节选.txt"
    path.write_text("这是教师明确提供的教材节选。", encoding="utf-8")
    source = service.ingest(path, "source")
    assert Path(source["text_path"]).read_text(encoding="utf-8") == path.read_text(encoding="utf-8")
    assert service.ingest(path, "source")["source_id"] == source["source_id"]
    outside = tmp_path / "other.txt"
    outside.write_text("not selected", encoding="utf-8")
    with pytest.raises(ValueError, match="inputs"):
        service.ingest(outside, "outside")


def test_cli_returns_utf8_json_and_nonzero_errors(tmp_path):
    command = [sys.executable, str(ROOT / "teacher_prep.py"), "--root", str(tmp_path)]
    success = subprocess.run(command + ["doctor"], capture_output=True, encoding="utf-8")
    assert success.returncode == 0 and json.loads(success.stdout)["ok"] is True
    failure = subprocess.run(command + ["create", "--file", str(tmp_path / "missing.json"), "--key", "missing"], capture_output=True, encoding="utf-8")
    assert failure.returncode == 2 and json.loads(failure.stdout)["ok"] is False


def test_cli_corrupt_source_file_returns_structured_error(tmp_path):
    directory = tmp_path / "inputs"
    directory.mkdir()
    source = directory / "corrupt.docx"
    source.write_bytes(b"not-an-office-file")
    command = [sys.executable, str(ROOT / "teacher_prep.py"), "--root", str(tmp_path), "ingest", "--file", str(source), "--key", "invalid-source"]
    result = subprocess.run(command, capture_output=True, encoding="utf-8")
    assert result.returncode == 2 and json.loads(result.stdout)["ok"] is False
