import json
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from teacher_prep.knowledge import KnowledgeBase
from teacher_prep.presentations import Presentations
from teacher_prep.service import Service
from teacher_prep.teaching_delivery import prepare_delivery

ROOT = Path(__file__).resolve().parents[1]


def fixture(kind, name):
    return json.loads((ROOT / "examples" / kind / (name + ".json")).read_text(encoding="utf-8"))


def create_knowledge(tmp_path, service):
    inputs = tmp_path / "inputs" / "upload_kb"
    inputs.mkdir(parents=True)
    path = inputs / "fractions.txt"
    path.write_text(
        "分数是把一个整体平均分成若干份，表示其中一份或几份的数。\n\n"
        "平均分要求每一份同样大；先确定整体，再确定分成几份。\n\n"
        "分母表示平均分成的份数，分子表示取了其中的几份。\n",
        encoding="utf-8",
    )
    return KnowledgeBase(service).create(
        path, {"title": "分数教材", "subject": "数学", "grade": "三年级"}, "knowledge_create"
    )


def test_knowledge_is_chunked_retrieved_reused_and_protected(tmp_path):
    service = Service(tmp_path)
    knowledge = create_knowledge(tmp_path, service)
    assert knowledge["chunk_count"] > 0
    assert (tmp_path / "data" / "knowledge" / knowledge["knowledge_id"] / "chunks.json").is_file()

    request = fixture("subject", "request")
    request["knowledge_ids"] = [knowledge["knowledge_id"]]
    created = service.create(request, "create")
    entry = next(item for item in KnowledgeBase(service).list()["entries"]
                 if item["id"] == knowledge["knowledge_id"])
    assert entry["used_by"]["lessons"] == 1

    context = KnowledgeBase(service).context([knowledge["knowledge_id"]], "分数 平均分")
    assert context["entries"][0]["title"] == "分数教材"
    assert context["excerpts"] and "分数" in context["excerpts"][0]["text"]

    with pytest.raises(ValueError, match="仍被"):
        KnowledgeBase(service).delete(knowledge["knowledge_id"])

    design = fixture("subject", "design")
    with pytest.raises(ValueError, match="知识库依据"):
        service.save_design(created["lesson_id"], design, "design_missing")
    design["knowledge_ids"] = [knowledge["knowledge_id"]]
    service.save_design(created["lesson_id"], design, "design_ok")

    service.delete_lesson(created["lesson_id"])
    KnowledgeBase(service).delete(knowledge["knowledge_id"])
    assert not (tmp_path / "data" / "knowledge" / knowledge["knowledge_id"]).exists()


def test_class_profile_accumulates_feedback_and_updates_baseline(tmp_path):
    service = Service(tmp_path)
    request = fixture("subject", "request")
    created = service.create(request, "create")
    lesson = json.loads(service.store.read("lessons", created["lesson_id"])["payload"])
    class_id = lesson["class_id"]
    version = service.save_design(created["lesson_id"], fixture("subject", "design"), "design")
    service.export(version["version_id"])
    service.feedback(version["version_id"], {
        "raw_text": "学生在分数意义上容易把整体和部分混淆。",
        "observed_at": "2026-11-07",
        "participation": [{"activity_id": "A1", "participating": 20, "eligible": 30, "basis": "observed"}],
        "observations": [{"id": "O1", "activity_id": "A1", "text": "部分学生把整体和部分混淆", "basis": "observed"}],
        "assessments": [{"question_id": "Q1", "correct": 15, "total": 30, "timing": "in_class", "basis": "observed"}],
    }, "feedback")

    classes = service.list_classes()["classes"]
    row = next(item for item in classes if item["id"] == class_id)
    assert row["lesson_count"] == 1
    profile = service.class_profile(class_id)
    assert profile["baseline"]["class_label"] == request["class_label"]
    assert profile["feedback_count"] == 1
    assert profile["evidence"][0]["raw_text"].startswith("学生在分数意义")
    assert profile["evidence"][0]["observations"][0]["text"] == "部分学生把整体和部分混淆"
    assert profile["evidence"][0]["assessments"][0]["question_id"] == "Q1"

    service.update_class(class_id, {"prior_knowledge": "已学平均分", "difficulties": "分数意义混淆"}, "class_update")
    updated = service.class_profile(class_id)["baseline"]
    assert updated["prior_knowledge"] == "已学平均分"
    assert updated["difficulties"] == "分数意义混淆"


def test_older_version_without_knowledge_ids_can_use_matching_manual_assets(tmp_path):
    import fitz
    from PIL import Image
    instance = tmp_path / "instance"
    uploads = instance / "inputs" / "upload_assets"
    uploads.mkdir(parents=True)
    image_path = uploads / "figure.png"
    Image.new("RGB", (320, 220), "#88bbdd").save(image_path)
    pdf_path = uploads / "unit.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "第一单元 时、分、秒。1分等于60秒。")
    page.insert_image(fitz.Rect(72, 110, 392, 330), filename=str(image_path))
    document.save(pdf_path)
    document.close()

    service = Service(instance)
    knowledge = KnowledgeBase(service).create(
        pdf_path, {"title": "时分秒教材", "subject": "数学", "grade": "三年级"}, "knowledge_old"
    )
    request = fixture("subject", "request")
    created = service.create(request, "create_old")
    version = service.save_design(created["lesson_id"], fixture("subject", "design"), "design_old")
    service.export(version["version_id"])

    pipeline = Presentations(service)
    page_data = pipeline.pages(version["version_id"])
    assert page_data["assets"], "subject/grade matching should expose assets to an older version"
    prepared = pipeline.prepare(version["version_id"], "task_auto_old")
    assert prepared["assets"], "automatic presentation preparation should see matching assets"
    asset_id = page_data["assets"][0]["id"]
    result = pipeline.compose_manual(version["version_id"], "task_manual_old", {"1": [asset_id]})
    assert result["valid"] is True and result["image_count"] >= 1


def test_pdf_figure_assets_can_be_selected_and_composed_into_the_teacher_ppt(tmp_path):
    import fitz
    from PIL import Image
    instance = tmp_path / "instance"
    uploads = instance / "inputs" / "upload_assets"
    uploads.mkdir(parents=True)
    image_path = uploads / "figure.png"
    Image.new("RGB", (320, 220), "#88bbdd").save(image_path)
    pdf_path = uploads / "unit.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Unit 1. 1 min = 60 sec.")
    page.insert_text((72, 92), "1 min = ( ) sec.")
    page.insert_image(fitz.Rect(72, 110, 392, 330), filename=str(image_path))
    document.save(pdf_path)
    document.close()

    service = Service(instance)
    knowledge = KnowledgeBase(service).create(
        pdf_path, {"title": "时分秒教材", "subject": "数学", "grade": "三年级"}, "knowledge_assets"
    )
    assert knowledge["asset_count"] >= 2
    question_assets = [asset for asset in KnowledgeBase(service).assets(knowledge["knowledge_id"])
                       if asset.get("kind") == "question"]
    assert question_assets, "fill-in-the-blank region should be extracted"
    request = fixture("subject", "request")
    request["knowledge_ids"] = [knowledge["knowledge_id"]]
    created = service.create(request, "create")
    design = fixture("subject", "design")
    design["knowledge_ids"] = [knowledge["knowledge_id"]]
    version = service.save_design(created["lesson_id"], design, "design")
    service.export(version["version_id"])

    pipeline = Presentations(service)
    prepared = pipeline.prepare(version["version_id"], "task_assets")
    assert prepared["assets"]
    asset_id = prepared["assets"][0]["id"]
    plan = {
        "style": "教材插图与清晰文字",
        "pages": [{"page_number": page["number"], "layout": "图文对照",
                   "visual_prompt": "使用教材插图辅助说明",
                   "asset_ids": [asset_id] if page["number"] == 1 else []}
                  for page in prepared["brief"]["pages"]],
    }
    pipeline.save_prompt("task_assets", plan)
    result = pipeline.compose_local(version["version_id"], "task_assets")
    assert result["valid"] is True and result["image_count"] >= 1
    deck = Presentation(Path(result["directory"]) / "课件.pptx")
    assert sum(1 for shape in deck.slides[0].shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE) >= 1

    delivery = prepare_delivery(service, version["version_id"], "task_assets")
    delivered = Presentation(Path(delivery["directory"]) / "课件.pptx")
    assert sum(1 for shape in delivered.slides[0].shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE) >= 1

    page_data = pipeline.pages(version["version_id"])
    assert page_data["assets"][0]["id"] == asset_id
    manual = pipeline.compose_manual(version["version_id"], "task_manual", {"1": [asset_id]})
    assert manual["valid"] is True and manual["image_count"] >= 1
    question_id = next(asset["id"] for asset in page_data["assets"] if asset.get("kind") == "question")
    question_deck = pipeline.compose_manual(version["version_id"], "task_question", {"1": [question_id]})
    assert question_deck["valid"] is True and question_deck["image_count"] >= 1

    with pytest.raises(ValueError, match="知识库之外"):
        pipeline.save_prompt("task_assets", {
            "style": "x",
            "pages": [{"page_number": page["number"], "layout": "x", "visual_prompt": "x",
                       "asset_ids": ["asset_missing"] if page["number"] == 1 else []}
                      for page in prepared["brief"]["pages"]],
        })
