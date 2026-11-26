import json
import zipfile
from pathlib import Path

import pytest
from pptx import Presentation

from teacher_prep.presentations import Presentations, normalized, shape_text
from teacher_prep.exporters import sha256
from teacher_prep.service import Service

ROOT = Path(__file__).resolve().parents[1]
RUN = "task_presentation_acceptance"


def setup(root, kind="subject"):
    service = Service(root)
    fixture = lambda name: json.loads((ROOT / "examples" / kind / (name + ".json")).read_text(encoding="utf-8"))
    lesson = service.create(fixture("request"), "request")
    version = service.save_design(lesson["lesson_id"], fixture("design"), "design")
    exported = service.export(version["version_id"])
    pipeline = Presentations(service)
    brief = pipeline.prepare(version["version_id"], RUN, "纸艺风格")
    plan = {"style": "纸艺风格，留白充足", "pages": [{"page_number": p["number"], "layout": "左右对照", "visual_prompt": "根据本页教学原文制作准确的教学图形，不额外增加知识或学生表现。"} for p in brief["brief"]["pages"]]}
    return service, version, exported, pipeline, brief, plan


@pytest.mark.parametrize("kind", ["subject", "theme"])
def test_prompt_is_grounded_and_does_not_rewrite_design(tmp_path, kind):
    service, version, exported, pipeline, brief, plan = setup(tmp_path, kind)
    before = service.get("version", version["version_id"])
    pipeline.save_prompt(RUN, plan)
    handoff = pipeline.read_prompt(RUN)
    assert handoff["brief"]["page_mapping"] == exported["manifest"]["page_mapping"]
    assert all(p["title"] in handoff["markdown"] and all(t in handoff["markdown"] for t in p["points"]) for p in brief["brief"]["pages"])
    assert service.get("version", version["version_id"]) == before
    assert pipeline.save_prompt(RUN, plan)["prompt_sha256"]
    plan["style"] = "不同风格"
    with pytest.raises(ValueError, match="不可覆盖"):
        pipeline.save_prompt(RUN, plan)


def test_prompt_must_cover_exact_physical_pages(tmp_path):
    _, _, _, pipeline, _, plan = setup(tmp_path)
    plan["pages"].pop()
    with pytest.raises(ValueError, match="逐页覆盖"):
        pipeline.save_prompt(RUN, plan)
    with pytest.raises(ValueError, match="标识"):
        pipeline.run_dir("../../config")


@pytest.mark.parametrize("fault", ["page_count", "missing_text", "raster_only"])
def test_external_ppt_rejects_mismatched_or_uneditable_content(tmp_path, fault):
    _, _, exported, pipeline, _, plan = setup(tmp_path)
    pipeline.save_prompt(RUN, plan)
    deck = Presentation(Path(exported["directory"]) / "课件.pptx")
    if fault == "page_count":
        deck.slides.add_slide(deck.slide_layouts[6])
    elif fault == "missing_text":
        for shape in deck.slides[0].shapes:
            if shape.has_text_frame:
                shape.text_frame.clear()
    else:
        for slide in deck.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    shape.text_frame.clear()
    deck.save(pipeline.run_dir(RUN) / "download.pptx")
    with pytest.raises(ValueError, match="页|文字"):
        pipeline.accept(RUN, "mock", "remote_1")
    assert not (pipeline.run_dir(RUN) / "result.json").exists()


def test_external_variant_preserves_original_and_restores_notes(tmp_path):
    service, version, exported, pipeline, brief, plan = setup(tmp_path)
    original = Path(exported["directory"]) / "课件.pptx"
    original_hash = sha256(original)
    pipeline.save_prompt(RUN, plan)
    deck = Presentation(original)
    for slide in deck.slides:
        slide.notes_slide.notes_text_frame.text = "供应商的其他备注"
    deck.save(pipeline.run_dir(RUN) / "download.pptx")
    accepted = pipeline.accept(RUN, "mock", "remote_1", {"deducted": 1})
    assert accepted["valid"] and accepted["directory"] != exported["directory"]
    assert sha256(original) == original_hash and service.check(version["version_id"])["valid"]
    actual = Presentation(Path(accepted["directory"]) / "课件.pptx")
    assert [s.notes_slide.notes_text_frame.text for s in actual.slides] == [p["script"] for p in brief["brief"]["pages"]]
    with zipfile.ZipFile(Path(accepted["directory"]) / "交付包.zip") as archive:
        assert "课件提示词.md" in archive.namelist() and "教案.docx" in archive.namelist()
        assert json.loads(archive.read("交付清单.json"))["presentation_run_id"] == RUN
        assert archive.read("课件.pptx") == (Path(accepted["directory"]) / "课件.pptx").read_bytes()
    assert pipeline.accept(RUN, "mock", "remote_1") == accepted


def test_prompt_cannot_switch_version_or_style_with_same_id(tmp_path):
    _, version, _, pipeline, _, _ = setup(tmp_path)
    with pytest.raises(ValueError, match="不能改变"):
        pipeline.prepare(version["version_id"], RUN, "改变风格")


def test_local_repair_restores_exact_source_text_without_changing_originals(tmp_path):
    service, version, exported, pipeline, brief, plan = setup(tmp_path)
    pipeline.save_prompt(RUN, plan)
    original = Path(exported['directory']) / '课件.pptx'
    deck = Presentation(original)
    required = brief['brief']['pages'][0]['points'][0]
    for shape in deck.slides[0].shapes:
        if shape.has_text_frame and normalized(required) in normalized(shape.text_frame.text):
            shape.text_frame.text = '供应商另写的说明，不能代替教师已经确认的原文内容'
    candidate = pipeline.run_dir(RUN) / 'download.pptx'
    deck.save(candidate)
    before = sha256(candidate), sha256(original)
    with pytest.raises(ValueError, match='本地修复') as error:
        pipeline.accept(RUN, 'gamma:classic', 'remote_existing')
    assert error.value.code == 'ppt_text_mismatch'
    accepted = pipeline.accept(RUN, 'gamma:classic', 'remote_existing', repair_text=True)
    assert accepted['valid'] and len(accepted['local_text_repairs']) == 1
    assert (sha256(candidate), sha256(original)) == before
    repaired = Presentation(Path(accepted['directory']) / '课件.pptx')
    for page, slide in zip(brief['brief']['pages'], repaired.slides):
        text = normalized('\n'.join(t for s in slide.shapes for t in shape_text(s)))
        assert all(normalized(t) in text for t in [page['title'], *page['points']])
        assert slide.notes_slide.notes_text_frame.text == page['script']
    record = json.loads((Path(accepted['directory']) / '课件生成记录.json').read_text(encoding='utf-8'))
    assert record['source_pptx_sha256'] == before[0]
    assert record['local_text_repairs'][0]['page_number'] == 1
    assert record['office_visual_review'] == record['teaching_content_review'] == 'pending'
    assert pipeline.accept(RUN, 'gamma:classic', 'remote_existing') == accepted
    assert service.check(version['version_id'])['valid']


@pytest.mark.parametrize('fault', ['page_count', 'raster_only'])
def test_local_repair_does_not_override_structural_or_raster_only_failures(tmp_path, fault):
    _, _, exported, pipeline, _, plan = setup(tmp_path)
    pipeline.save_prompt(RUN, plan)
    deck = Presentation(Path(exported['directory']) / '课件.pptx')
    if fault == 'page_count':
        deck.slides.add_slide(deck.slide_layouts[6])
    else:
        for slide in deck.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    shape.text_frame.clear()
    deck.save(pipeline.run_dir(RUN) / 'download.pptx')
    with pytest.raises(ValueError, match='页|原生文字'):
        pipeline.accept(RUN, 'gamma:classic', 'remote_existing', repair_text=True)
    assert not (pipeline.run_dir(RUN) / 'result.json').exists()


def test_geometry_uses_exact_source_coordinates_and_movement():
    from teacher_prep.geometry_visuals import coordinates, geometry_spec
    assert coordinates('A(1,1)、B(5,1)、C(5,3)、D(1,3)') == {'A':[1,1],'B':[5,1],'C':[5,3],'D':[1,3]}
    assert coordinates('描出P(2,1)、Q(6,1)、R(2,4)，按顺序连线') == {'P':[2,1],'Q':[6,1],'R':[2,4]}
    assert coordinates('A(1,1) B(2,1) C(2,2) A(9,9)') == {}
    assert coordinates('A(1000,1) B(2,1) C(2,2)') == {}
    first = {'number':1,'title':'坐标建图第一步：定顶点','points':['A(1,1)、B(5,1)、C(5,3)、D(1,3)'],'script':'按顺序连线'}
    moved = {'number':2,'title':'变式：只动一个顶点','points':['只改变一个顶点的位置'],'script':'只把顶点D向右移动两格'}
    spec = geometry_spec(first,[first,moved])
    assert spec['order'] == ['A','B','C','D']
    result = geometry_spec(moved,[first,moved])
    assert result['after']['D'] == [3,3] and result['vertices']['D'] == [1,3]
    assert all(result['vertices'][name] == result['after'][name] for name in ['A','B','C'])


def test_geometry_renderer_draws_editable_coordinates_without_bitmap(tmp_path):
    from teacher_prep.geometry_visuals import geometry_spec
    from teacher_prep.presentation_repair import restore_page
    page={'number':1,'activity_title':'坐标建图','title':'坐标建图第一步：定顶点',
          'points':['依次描出各顶点','例：A(1,1)、B(5,1)、C(5,3)、D(1,3)'],'script':'按顺序连线'}
    deck=Presentation();deck.slide_width=12191695;deck.slide_height=6858000
    slide=deck.slides.add_slide(deck.slide_layouts[6])
    report=restore_page(deck,slide,page,geometry_spec(page,[page]))
    assert report['editable_geometry']['vertices']['C'] == [5,3]
    assert all(s.shape_type != 13 for s in slide.shapes)
    texts='\n'.join(t for s in slide.shapes for t in shape_text(s))
    assert all(f'{name}({x},{y})' in texts for name,x,y in [('A',1,1),('B',5,1),('C',5,3),('D',1,3)])
    assert len([s for s in slide.shapes if s.shape_type == 9]) >= 4
