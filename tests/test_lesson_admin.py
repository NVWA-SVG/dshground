import json
from pathlib import Path

import pytest

from teacher_prep.service import Service

ROOT = Path(__file__).resolve().parents[1]


def fixture(kind, name):
    return json.loads((ROOT / "examples" / kind / (name + ".json")).read_text(encoding="utf-8"))


def setup(root, kind="subject"):
    service = Service(root)
    created = service.create(fixture(kind, "request"), "create")
    return service, created["lesson_id"]


def test_update_lesson_metadata_and_category(tmp_path):
    service, lesson_id = setup(tmp_path)
    updated = service.update_lesson(
        lesson_id,
        {"title": "新课题", "category": "三年级数学", "grade": "小学四年级", "duration_minutes": 45},
        "update_1",
    )
    assert updated["request"]["title"] == "新课题"
    assert updated["request"]["category"] == "三年级数学"
    assert updated["request"]["duration_minutes"] == 45
    lesson = next(item for item in service.list_lessons()["lessons"] if item["id"] == lesson_id)
    assert lesson["request"]["category"] == "三年级数学"
    class_payload = json.loads(service.store.read("classes", lesson["request"]["class_id"])["payload"])
    assert class_payload["grade"] == "小学四年级"


def test_update_after_version_is_limited(tmp_path):
    service, lesson_id = setup(tmp_path)
    service.save_design(lesson_id, fixture("subject", "design"), "design")
    updated = service.update_lesson(
        lesson_id, {"title": "改名", "category": "分类A", "class_label": "新班级"}, "update_2"
    )
    assert updated["request"]["title"] == "改名"
    with pytest.raises(ValueError, match="只能修改课程名称、分类和班级名称"):
        service.update_lesson(lesson_id, {"grade": "小学四年级"}, "update_3")
    with pytest.raises(ValueError, match="只能修改"):
        service.update_lesson(lesson_id, {"source_ids": []}, "update_4")


def test_delete_lesson_removes_versions_materials_and_class(tmp_path):
    service, lesson_id = setup(tmp_path)
    service.save_design(lesson_id, fixture("subject", "design"), "design")
    version = service.list_lessons()["lessons"][0]["versions"][0]
    exported = service.export(version["id"])
    directory = Path(exported["directory"])
    assert directory.is_dir()
    class_id = json.loads(service.store.read("lessons", lesson_id)["payload"])["class_id"]

    result = service.delete_lesson(lesson_id)

    assert result["deleted"] is True
    assert result["versions"] == [version["id"]]
    assert not directory.exists()
    with pytest.raises(ValueError, match="课程不存在"):
        service.delete_lesson(lesson_id)
    with service.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM lessons WHERE id=?", (lesson_id,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM versions WHERE lesson_id=?", (lesson_id,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM export_jobs WHERE version_id=?", (version["id"],)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM classes WHERE id=?", (class_id,)).fetchone()[0] == 0


def test_delete_lesson_keeps_shared_source_and_removes_unshared(tmp_path):
    service = Service(tmp_path)
    inputs = tmp_path / "inputs" / "upload_test"
    inputs.mkdir(parents=True)
    path = inputs / "material.txt"
    path.write_text("分数教材内容", encoding="utf-8")
    source = service.ingest(path, "source_key")
    payload = fixture("subject", "request")
    payload["source_ids"] = [source["source_id"]]
    first = service.create(payload, "create_a")
    second = service.create(payload, "create_b")

    service.delete_lesson(first["lesson_id"])
    assert (tmp_path / "data" / "sources" / source["source_id"]).is_dir()

    service.delete_lesson(second["lesson_id"])
    assert not (tmp_path / "data" / "sources" / source["source_id"]).exists()
    with service.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sources WHERE id=?", (source["source_id"],)).fetchone()[0] == 0
