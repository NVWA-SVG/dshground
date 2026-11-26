import json
from pathlib import Path

from teacher_prep.analysis import analyze
from teacher_prep.models import Feedback, LessonDesign


def design():
    root = Path(__file__).resolve().parents[1]
    return LessonDesign.model_validate(json.loads((root / "examples/subject/design.json").read_text(encoding="utf-8")))


def test_density_evidence_missing_values_stay_unknown():
    report = analyze(design())
    assert all(row["actual_density"] is None and row["evidence_ids"] == [] for row in report["activity_evidence"])
    assert not any(s["id"].startswith("density_") for s in report["suggestions"])
    assert report["overall_score"] is None and report["causal_conclusion"] is None


def test_observed_overrun_links_density_to_correct_activity_evidence():
    lesson = design()
    activity = next(a for a in lesson.activities if a.introduces)
    question = next((q for q in lesson.questions if q.activity_id == activity.id), None)
    feedback = Feedback(raw_text="该环节实际超时，需要核对理解过程", observed_at="2026-11-05",
                        timings=[{"activity_id": activity.id, "actual_minutes": activity.duration_minutes + 2, "basis": "observed"}],
                        participation=[{"activity_id": activity.id, "participating": 0, "eligible": 30, "basis": "observed"}])
    report = analyze(lesson, feedback)
    suggestion = next(s for s in report["suggestions"] if s["id"] == "density_" + activity.id)
    assert suggestion["density_context"]["actual_density"] < suggestion["density_context"]["planned_density"]
    assert set(suggestion["evidence_ids"]) == {"timing:1", "participation:1"}
    assert report["participation"][0]["participation_fraction"] == 0
    assert report["assessment_results"] == []
    assert "不是效果提升" in suggestion["hypothesis"]


def test_practice_without_new_content_is_not_zero_learning():
    lesson = design()
    activity = next(a for a in lesson.activities if not a.introduces)
    feedback = Feedback(raw_text="练习超时", observed_at="2026-11-05", timings=[
        {"activity_id": activity.id, "actual_minutes": activity.duration_minutes + 1, "basis": "estimated"}])
    report = analyze(lesson, feedback)
    assert not any(s["id"] == "density_" + activity.id for s in report["suggestions"])
    assert next(r for r in report["density"] if r["activity_id"] == activity.id)["actual_points_per_activity_minute"] == 0
