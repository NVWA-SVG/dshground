from __future__ import annotations

import hashlib
import html
import json
import math
import textwrap
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt as DocPt
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

from .analysis import analyze
from .models import LessonDesign
from .store import digest

MAX_POINTS_PER_PAGE = 5


def sha256(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):
            result.update(chunk)
    return result.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def wrap(text, units):
    lines = []
    for original in text.splitlines() or [""]:
        current = ""
        width = 0
        for char in original:
            cost = 0.55 if ord(char) < 128 else 1
            if current and width + cost > units:
                lines.append(current)
                current, width = "", 0
            current += char
            width += cost
        lines.append(current)
    return lines


def pagination(design):
    pages = []
    mapping = {}
    for slide in design.slides:
        # Reserve a separate right column for editable diagrams.
        units = 20 if slide.visual else 32
        groups, current, lines_used = [], [], 0
        for point in slide.points:
            count = len(wrap(point, units)) + 0.45
            if current and (len(current) >= MAX_POINTS_PER_PAGE or lines_used + count > 8):
                groups.append(current)
                current, lines_used = [], 0
            current.append(point)
            lines_used += count
        if current:
            groups.append(current)
        for index, points in enumerate(groups, 1):
            number = len(pages) + 1
            mapping.setdefault(slide.id, []).append(number)
            pages.append({"number": number, "slide_id": slide.id, "activity_id": slide.activity_id,
                          "title": slide.title + (f"（{index}/{len(groups)}）" if len(groups) > 1 else ""),
                          "points": points, "visual": slide.visual.model_dump() if slide.visual else None,
                          "script": slide.teacher_script})
    return pages, mapping


def textbox(slide, x, y, width, height, text, size, color, *, bold=False):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(width), Inches(height))
    frame = shape.text_frame
    frame.word_wrap = False
    frame.margin_left = frame.margin_right = Inches(0.03)
    frame.margin_top = frame.margin_bottom = Inches(0.02)
    for index, line in enumerate(text.split("\n")):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.space_before = Pt(0)
        paragraph.space_after = Pt(3)
        paragraph.line_spacing = 1.15
        run = paragraph.add_run()
        run.text = line
        run.font.name = "Microsoft YaHei"
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor.from_string(color)
    return shape


def make_pptx(design, path, version_label, pages, assets_by_page=None, plan=None):
    if plan is not None:
        from .hybrid_slides import make_hybrid_pptx
        return make_hybrid_pptx(design, path, version_label, pages, assets_by_page or {}, plan)
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.3333), Inches(7.5)
    accent = "246C88" if design.course_type == "subject" else "327665"
    activities = {a.id: a for a in design.activities}
    for page in pages:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor.from_string("FAF9F5")
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, deck.slide_width, Inches(0.12))
        bar.fill.solid()
        bar.fill.fore_color.rgb = RGBColor.from_string(accent)
        bar.line.fill.background()
        activity = activities[page["activity_id"]]
        textbox(slide, 0.65, 0.35, 11.8, 0.38, f"{design.grade[:20]}  /  {activity.title[:30]}  /  计划 {activity.duration_minutes:g} 分钟", 13, accent)
        title_size = 30 if len(page["title"]) <= 26 else 24 if len(page["title"]) <= 43 else 20
        title_lines = wrap(page["title"], 11.8 * 72 / title_size * 0.9)
        textbox(slide, 0.65, 0.93, 11.8, 1.0, "\n".join(title_lines), title_size, "183442", bold=True)
        page_assets = (assets_by_page or {}).get(page["number"], [])
        page_questions = [asset for asset in page_assets if asset.get("kind") == "question"][:2]
        page_images = [asset for asset in page_assets if asset.get("kind") != "question"][:2]
        if page_images and not page["visual"]:
            # The base pagination used the full-width column. Do not narrow a
            # dense page after the fact; keep the text readable and skip the figure.
            estimated = sum(len(wrap(point, 20)) for point in page["points"]) * 0.41 + len(page["points"]) * 0.15
            if estimated > 4.6:
                page_images = []
        has_side = bool(page["visual"]) or bool(page_images)
        cursor = 2.05
        for point in page["points"]:
            wrapped = wrap(point, 20 if has_side else 32)
            height = len(wrapped) * 0.41 + 0.15
            textbox(slide, 0.8, cursor, 7.2 if has_side else 11.7, height, "\n".join(wrapped), 23, "263B45")
            cursor += height + 0.15
        if page_images:
            from PIL import Image
            top, gap = 1.95, 0.12
            bottom = 4.55 if page_questions else 6.85
            items = page_images
            slot = (bottom - top - gap * (len(items) - 1)) / max(1, len(items))
            for index, asset in enumerate(items):
                try:
                    with Image.open(asset["path"]) as image:
                        width_px, height_px = image.size
                except Exception:
                    continue
                max_width, max_height = 4.05, slot - 0.28
                scale = min(max_width * 96 / width_px, max_height * 96 / height_px)
                width_in, height_in = width_px * scale / 96, height_px * scale / 96
                left = 8.55 + (4.05 - width_in) / 2
                y = top + index * (slot + gap) + (max_height - height_in) / 2
                slide.shapes.add_picture(asset["path"], Inches(left), Inches(y), width=Inches(width_in), height=Inches(height_in))
                caption = (asset.get("caption") or "").strip()
                if caption:
                    textbox(slide, 8.55, y + height_in + 0.04, 4.05, 0.3, caption[:60], 10, "61737D")
        elif page["visual"]:
            labels = page["visual"]["labels"]
            for index, label in enumerate(labels):
                y = 2.12 + index * 1.02
                card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8.6), Inches(y), Inches(3.65), Inches(0.88))
                card.fill.solid()
                card.fill.fore_color.rgb = RGBColor.from_string("E5EFEB" if design.course_type == "theme" else "E3EEF2")
                card.line.color.rgb = RGBColor.from_string(accent)
                lines = wrap(label, 13)
                size = min(19, 47 / max(1, len(lines)))
                textbox(slide, 8.78, y + 0.06, 3.25, 0.76, "\n".join(lines), size, "183442", bold=True)
        if page_questions:
            from PIL import Image
            top, bottom, gap = 5.05, 6.9, 0.08
            slot = (bottom - top - gap * (len(page_questions) - 1)) / max(1, len(page_questions))
            for index, question in enumerate(page_questions):
                try:
                    with Image.open(question["path"]) as image:
                        width_px, height_px = image.size
                except Exception:
                    continue
                max_width, max_height = 11.9, slot
                scale = min(max_width * 96 / width_px, max_height * 96 / height_px)
                width_in, height_in = width_px * scale / 96, height_px * scale / 96
                left = 0.65 + (11.9 - width_in) / 2
                y = top + index * (slot + gap) + (slot - height_in) / 2
                slide.shapes.add_picture(question["path"], Inches(left), Inches(y), width=Inches(width_in), height=Inches(height_in))
        textbox(slide, 0.65, 7.0, 11.9, 0.3, f"{design.title[:35]}  ·  {version_label}     {page['number']:02d} / {len(pages):02d}", 10, "61737D")
        slide.notes_slide.notes_text_frame.text = page["script"]
    deck.save(path)


def doc_font(run, size=11, bold=False):
    run.font.name = "Calibri"
    run.font.size = DocPt(size)
    run.bold = bold
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "宋体")


def document_base(title, design, label):
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.1)
    section.top_margin = section.bottom_margin = Cm(2)
    for name in ["Normal", "Heading 1", "Heading 2", "Title"]:
        style = document.styles[name]
        style.font.name = "Calibri"
        style.font.size = DocPt(11)
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "宋体")
    document.styles["Normal"].paragraph_format.line_spacing = 1.2
    document.add_heading(title, 0)
    document.add_paragraph(f"{design.title}｜{design.grade}｜{design.duration_minutes:g} 分钟｜{label}")
    if design.context_notes:
        document.add_paragraph(design.context_notes)
    footer = section.footer.paragraphs[0]
    footer.add_run(label + " · ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    return document


def page_range(numbers):
    return str(numbers[0]) if len(numbers) == 1 else f"{numbers[0]}—{numbers[-1]}"


def make_documents(design, directory, label, mapping):
    plan = document_base("教案", design, label)
    plan.add_heading("教学目标", 1)
    for goal in design.goals:
        plan.add_paragraph(f"{goal.id}　{goal.description}")
    plan.add_heading("知识与难点安排", 1)
    for point in design.knowledge_points:
        difficulty = {"basic": "基础", "intermediate": "中等", "complex": "较复杂"}[point.difficulty]
        plan.add_paragraph(f"{point.title}：{point.description}（{'新授' if point.is_new else '已有基础'}，难度建议：{difficulty}）")
    plan.add_heading("教学过程", 1)
    for index, activity in enumerate(design.activities, 1):
        pages = [page for slide in design.slides if slide.activity_id == activity.id for page in mapping[slide.id]]
        plan.add_heading(f"【环节{index}】{activity.title}（对应PPT第{page_range(pages)}页，约{activity.duration_minutes:g}分钟）", 2)
        plan.add_paragraph(activity.instructions)
        goal_descriptions = {g.id: g.description for g in design.goals}
        plan.add_paragraph(f"对应目标：{'；'.join(goal_descriptions[g] for g in activity.goal_ids)}；独立思考或练习安排：{activity.thinking_minutes:g}分钟。")
    plan.add_heading("板书设计", 1)
    plan.add_paragraph(design.board_design)
    plan.add_heading("检测与课后跟进", 1)
    for question in design.questions:
        timing_label = {"in_class": "课堂检测", "after_class": "课后检测", "delayed": "延迟复测"}[question.timing]
        plan.add_paragraph(f"{question.id}（{timing_label}）：{question.prompt}")
    plan.save(directory / "教案.docx")

    script = document_base("课堂逐字稿", design, label)
    activities = {a.id: a for a in design.activities}
    for slide in design.slides:
        script.add_heading(f"PPT第{page_range(mapping[slide.id])}页｜{slide.title}", 1)
        script.add_paragraph(f"对应环节：{activities[slide.activity_id].title}")
        script.add_paragraph(slide.teacher_script)
        if slide.question_ids:
            script.add_paragraph("关联检测：" + "、".join(slide.question_ids))
    script.save(directory / "逐字稿.docx")

    assessment = document_base("检测题与评价要点", design, label)
    for timing, title in [("in_class", "课堂检测"), ("after_class", "课后检测"), ("delayed", "延迟复测")]:
        assessment.add_heading(title, 1)
        questions = [q for q in design.questions if q.timing == timing]
        if not questions:
            assessment.add_paragraph("本版未配置此时点的检测。")
        for question in questions:
            assessment.add_heading(question.id + "｜" + "、".join(question.goal_ids), 2)
            assessment.add_paragraph(question.prompt)
            assessment.add_paragraph("答案或评价要点：" + question.answer)
            assessment.add_paragraph("讲评：" + question.explanation)
            for criterion in question.criteria:
                assessment.add_paragraph("评价观察：" + criterion)
    assessment.save(directory / "检测题与答案.docx")


def make_preview(design, directory, label, pages):
    cards = []
    for page in pages:
        points = "".join(f"<li>{html.escape(p)}</li>" for p in page["points"])
        visual = "".join(f"<div class='label'>{html.escape(p)}</div>" for p in page["visual"]["labels"]) if page["visual"] else ""
        cards.append(f"<section><small>第 {page['number']} 页 · {html.escape(label)}</small><h2>{html.escape(page['title'])}</h2><div class='content'><ul>{points}</ul><aside>{visual}</aside></div></section>")
    content = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(design.title)}</title>
<style>body{{margin:0;background:#ecefe9;color:#183442;font-family:'Microsoft YaHei',sans-serif}}header{{padding:24px;max-width:1080px;margin:auto}}section{{box-sizing:border-box;background:#faf9f5;border-top:7px solid #246c88;border-radius:12px;max-width:1080px;margin:24px auto;padding:32px;box-shadow:0 3px 18px #0001}}h2{{font-size:30px}}.content{{display:flex;gap:25px}}ul{{flex:2;font-size:23px;line-height:1.9}}aside{{flex:1}}.label{{background:#e3eef2;border-radius:12px;padding:18px;margin:14px}}small{{color:#61737d}}@media print{{section{{break-after:page;box-shadow:none}}}}</style>
<header><h1>{html.escape(design.title)}</h1><p>内容预览 · {html.escape(label)} · 与导出页面使用相同分页数据；这是内容预览，不是 Office 页面渲染截图。实际版式请打开 PPTX 核对。</p></header>{''.join(cards)}</html>"""
    (directory / "内容预览.html").write_text(content, encoding="utf-8")


def generate_bundle(design: LessonDesign, directory: Path, lesson_id, version_id, number, changes, summary):
    label = f"第{number}版"
    pages, mapping = pagination(design)
    make_pptx(design, directory / "课件.pptx", label, pages)
    make_documents(design, directory, label, mapping)
    make_preview(design, directory, label, pages)
    write_json(directory / "教学设计.json", design.model_dump(mode="json"))
    write_json(directory / "信息密度.json", analyze(design))
    details = [f"# {design.title} · v{number}", "", summary, ""]
    for change in changes:
        kind, entity_id = change["target"].split(":", 1)
        value = change["after"] or change["before"]
        kind_label = {"slide": "课件页面", "activity": "教学环节", "question": "检测题", "goal": "教学目标", "knowledge": "知识点", "lesson": "课程安排", "sequence": "内容顺序"}.get(kind, kind)
        name = value.get("title", value.get("description", entity_id)) if isinstance(value, dict) else entity_id
        if kind == "slide" and entity_id in mapping:
            name += f"（新版第{page_range(mapping[entity_id])}页）"
        details.extend([f"## {kind_label}：{name}", change.get("explanation", "")])
        before, after = change["before"], change["after"]
        labels = {"title": "标题", "points": "展示内容", "teacher_script": "教师讲解", "duration_minutes": "计划分钟数", "thinking_minutes": "思考练习分钟数", "instructions": "活动安排", "prompt": "题目", "answer": "答案或评价要点", "explanation": "讲评", "description": "说明", "goal_ids": "关联目标", "knowledge_ids": "关联知识点", "introduces": "首次引入知识", "reasoning_steps": "推理步骤数", "visual": "课堂图示"}
        if isinstance(before, dict) and isinstance(after, dict):
            for key in before.keys() | after.keys():
                if before.get(key) != after.get(key):
                    def describe(item):
                        return "；".join(item) if isinstance(item, list) and all(isinstance(v, str) for v in item) else str(item)
                    details.extend([f"{labels.get(key, key)}修改前：{describe(before.get(key))}", f"{labels.get(key, key)}修改后：{describe(after.get(key))}"])
        else:
            details.extend(["修改前：" + str(before), "修改后：" + str(after)])
        details.extend(["依据：" + "；".join(change.get("evidence_summary", change.get("evidence_ids", []))), "下次检查：" + "；".join(change.get("verify_next_time", [])), ""])
    if not changes:
        details.append("首次生成。学情与教学效果以教师后续输入为依据，当前没有课堂实测结论。")
    (directory / "修改说明.md").write_text("\n".join(details), encoding="utf-8")
    names = sorted(path.name for path in directory.iterdir() if path.is_file())
    files = [{"name": name, "sha256": sha256(directory / name), "bytes": (directory / name).stat().st_size} for name in names]
    manifest = {"lesson_id": lesson_id, "version_id": version_id, "version_number": number,
                "design_hash": digest(design.model_dump(mode="json")), "slide_count": len(pages), "page_mapping": mapping,
                "files": files, "checks": {"openxml_structure": True, "office_visual_review": "pending", "teaching_content_review": "pending"}}
    write_json(directory / "交付清单.json", manifest)
    with zipfile.ZipFile(directory / "交付包.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names + ["交付清单.json"]:
            archive.write(directory / name, name)
    validate_bundle(directory, manifest, design)
    return manifest


def validate_bundle(directory, manifest, design):
    expected = {"课件.pptx", "教案.docx", "逐字稿.docx", "检测题与答案.docx", "教学设计.json", "信息密度.json", "修改说明.md", "内容预览.html"}
    if {entry["name"] for entry in manifest["files"]} != expected:
        raise ValueError("交付清单文件集合不正确")
    for entry in manifest["files"]:
        path = Path(directory) / entry["name"]
        if not path.is_file() or path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
            raise ValueError(f"交付文件缺失或校验失败：{entry['name']}")
        if path.suffix in {".pptx", ".docx", ".zip"}:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise ValueError("交付文件压缩结构损坏")
    if digest(design.model_dump(mode="json")) != manifest["design_hash"]:
        raise ValueError("交付版本与教学设计不一致")
    pages, mapping = pagination(design)
    if manifest["page_mapping"] != mapping or manifest["slide_count"] != len(pages):
        raise ValueError("清单页码关联与实际教学设计不一致")
    if json.loads((Path(directory) / "交付清单.json").read_text(encoding="utf-8")) != manifest:
        raise ValueError("落盘交付清单与任务记录不一致")
    with zipfile.ZipFile(Path(directory) / "交付包.zip") as archive:
        if archive.testzip() is not None or set(archive.namelist()) != expected | {"交付清单.json"}:
            raise ValueError("交付包损坏或缺少文件")
        for entry in manifest["files"]:
            if hashlib.sha256(archive.read(entry["name"])).hexdigest() != entry["sha256"]:
                raise ValueError("交付包内文件与版本清单不一致")
        if json.loads(archive.read("交付清单.json")) != manifest:
            raise ValueError("交付包内清单不一致")
    deck = Presentation(Path(directory) / "课件.pptx")
    if len(deck.slides) != manifest["slide_count"]:
        raise ValueError("PPT 页数与清单不一致")
    for slide in deck.slides:
        for shape in slide.shapes:
            if min(shape.left, shape.top) < 0 or shape.left + shape.width > deck.slide_width + 10 or shape.top + shape.height > deck.slide_height + 10:
                raise ValueError("课件图形或文本框超出页面边界")
    for name in ["教案.docx", "逐字稿.docx", "检测题与答案.docx"]:
        if not Document(Path(directory) / name).paragraphs:
            raise ValueError("Word 文档为空")
    return {"valid": True, "slide_count": len(deck.slides), "files_checked": len(expected), "office_visual_review": "pending"}
