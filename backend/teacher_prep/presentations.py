"""Immutable prompt handoff and validated external presentation variants."""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import Field
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from .models import Model, Text, LessonDesign
from .exporters import pagination, sha256, write_json
from .store import digest, now
from .teaching_visuals import enrich_pages,validate_page_plan,illustration_prompt


class PageVisual(Model):
    page_number: int = Field(ge=1, le=200)
    layout: str = Field(min_length=1, max_length=100)
    visual_prompt: str = Field(min_length=1, max_length=600)
    asset_ids: list[str] = Field(default_factory=list, max_length=2)
    layout_kind: Literal['auto', 'split_right', 'split_left', 'image_top', 'comparison', 'steps', 'text'] = 'auto'
    image_prompt: str = Field(default='', max_length=1600)
    image_reason: str = Field(default='', max_length=300)
    teaching_goal: str = Field(default='', max_length=160,description='本页服务的具体教学任务，依据原教学设计')
    student_task: str = Field(default='', max_length=48,description='学生可见的简短观察或操作要求；不新增题目、数值和答案')
    learning_focus: str = Field(default='', max_length=120,description='学生重点观察的关系、位置或步骤')
    asset_reason: str = Field(default='', max_length=300,description='原图、生图或原生图解的选择依据')
    density_note: str = Field(default='', max_length=200,description='结合本页文字量、环节新增知识与思考时间说明如何突出重点；不是质量评分')
    diagram_id: str = Field(default='', max_length=80,description='仅选本页diagram_candidates中的真实id；无需图解时为空')
    diagram_stage: Literal['setup','regroup','complete'] = 'setup'


class PptVisualPlan(Model):
    style: str = Field(min_length=1, max_length=800)
    pages: list[PageVisual] = Field(min_length=1, max_length=200)
    palette: Literal['navy', 'mint', 'amber'] = 'navy'


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).replace("\u200b", "")


def shape_text(shape):
    if shape.has_text_frame:
        yield shape.text_frame.text
    if shape.has_table:
        for row in shape.table.rows:
            for cell in row.cells:
                yield cell.text
    if hasattr(shape, "shapes"):
        for child in shape.shapes:
            yield from shape_text(child)


class PresentationTextMismatch(ValueError):
    code = "ppt_text_mismatch"


class Presentations:
    def __init__(self, service):
        self.service = service
        self.root = service.root

    def run_dir(self, run_id):
        if not re.fullmatch(r"task_[A-Za-z0-9_-]{1,90}", run_id):
            raise ValueError("课件任务标识不正确")
        return self.root / "data" / "ppt-runs" / run_id

    def prepare(self, version_id, run_id, style=""):
        version = self.service.get("version", version_id)
        if version["status"] != "ready":
            raise ValueError("请先导出该版本的基础材料")
        self.service.check(version_id)
        design = LessonDesign.model_validate(version["design"])
        pages, mapping = pagination(design)
        activities = {a.id: a for a in design.activities}
        brief = {"version_id": version_id, "lesson_id": version["lesson_id"], "version_number": version["number"],
                 "design_hash": digest(version["design"]), "title": design.title, "grade": design.grade,
                 "course_type": design.course_type, "subject": design.subject, "duration_minutes": design.duration_minutes,
                 "goals": [g.model_dump() for g in design.goals], "page_mapping": mapping,
                 "requested_style": str(style)[:1000],
                 "pages": [{**p, "activity_title": activities[p["activity_id"]].title,
                            "thinking_minutes": activities[p["activity_id"]].thinking_minutes} for p in pages]}
        directory = self.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "brief.json"
        if target.exists():
            existing=json.loads(target.read_text(encoding='utf-8'))
            core={key:value for key,value in existing.items() if key!='planning_version'}
            core['pages']=[{key:value for key,value in page.items() if key not in ('teaching_context','diagram_candidates')} for page in core['pages']]
            if core!=brief:raise ValueError("同一课件任务不能改变教学版本或视觉要求")
            brief=existing
        else:
            brief={**brief,'planning_version':2,'pages':enrich_pages(design,brief['pages'])}
            write_json(target, brief)
        assets = [{key: asset.get(key) for key in
                   ("id", "knowledge_id", "page", "file", "width", "height", "caption", "description",
                    "dialogue", "kind", "question_text", "context")}
                  for asset in self.candidate_assets(version_id)]
        for asset in assets:
            asset["caption"] = str(asset.get("caption") or "")[:240]
            asset["description"] = str(asset.get("description") or "")[:400]
            asset["dialogue"] = str(asset.get("dialogue") or "")[:300]
            asset["question_text"] = str(asset.get("question_text") or "")[:300]
            asset["context"] = str(asset.get("context") or "")[:400]
        return {"run_id": run_id, "brief": brief, "schema": PptVisualPlan.model_json_schema(), "assets": assets}

    def version_assets(self, version_id, include_path=False):
        version = self.service.get("version", version_id)
        design = LessonDesign.model_validate(version["design"])
        lesson = self.service.get("lesson", version["lesson_id"])
        knowledge_ids = design.knowledge_ids or lesson["payload"].get("knowledge_ids") or []
        from .knowledge import KnowledgeBase
        return KnowledgeBase(self.service).assets_for(knowledge_ids, include_path=include_path)

    def candidate_assets(self, version_id, include_path=False):
        """Assets this version may use. Explicit knowledge selection wins; for
        older versions without one, match the whole local library by subject/grade."""
        version = self.service.get("version", version_id)
        design = LessonDesign.model_validate(version["design"])
        lesson = self.service.get("lesson", version["lesson_id"])
        knowledge_ids = design.knowledge_ids or lesson["payload"].get("knowledge_ids") or []
        from .knowledge import KnowledgeBase
        knowledge = KnowledgeBase(self.service)
        if knowledge_ids:
            return knowledge.assets_for(knowledge_ids, include_path=include_path)
        subject = lesson["payload"].get("subject") or ""
        grade = lesson["payload"].get("grade") or ""

        def compatible(value, target):
            value, target = str(value or ""), str(target or "")
            return not value or not target or value == target or value in target or target in value

        matches = [entry for entry in knowledge.list()["entries"]
                   if compatible(entry.get("subject"), subject) and compatible(entry.get("grade"), grade)]
        return knowledge.assets_for([entry["id"] for entry in matches], include_path=include_path)

    def save_prompt(self, run_id, raw, allowed_assets=None):
        directory = self.run_dir(run_id)
        brief = json.loads((directory / "brief.json").read_text(encoding="utf-8"))
        plan = PptVisualPlan.model_validate(raw)
        if [p.page_number for p in plan.pages] != [p["number"] for p in brief["pages"]]:
            raise ValueError("视觉方案必须按顺序逐页覆盖原课件，不得增加、删除或重复页码")
        assets = {asset["id"]: asset for asset in
                  (allowed_assets if allowed_assets is not None else self.candidate_assets(brief["version_id"]))}
        for page in plan.pages:
            unknown = [asset_id for asset_id in page.asset_ids if asset_id not in assets]
            if unknown:
                raise ValueError("视觉方案引用了本课知识库之外的教材素材")
        design=LessonDesign.model_validate(self.service.get('version',brief['version_id'])['design'])
        canonical_pages=enrich_pages(design,pagination(design)[0])
        for page,visual in zip(canonical_pages,plan.pages):validate_page_plan(page,visual,[assets[a] for a in visual.asset_ids])
        plan_path = directory / "visual-plan.json"
        if plan_path.exists():
            existing=PptVisualPlan.model_validate(json.loads(plan_path.read_text(encoding='utf-8'))).model_dump()
            if existing!=plan.model_dump():raise ValueError("已保存提示词不可覆盖；改变视觉方案请创建新任务")
            # Preserve bytes and generated-image hashes for legacy tasks as well.
            return {"run_id":run_id,"version_id":brief['version_id'],"pages":len(plan.pages),"prompt_sha256":sha256(directory/'课件提示词.md')}
        write_json(plan_path, plan.model_dump())
        image_requests=[{'page_number':p.page_number,'raw_prompt':p.image_prompt,'reason':p.image_reason,
                         'effective_prompt':illustration_prompt(brief['grade'],plan.style,p.image_prompt)} for p in plan.pages if p.image_prompt.strip()]
        write_json(directory/'image-requests.json',{'version_id':brief['version_id'],'design_hash':brief['design_hash'],
                   'plan_sha256':sha256(plan_path),'requests':image_requests})
        notes = [f"# {brief['title']}｜课件生成提示词", "", f"教学版本：{brief['version_id']}；共{len(brief['pages'])}页；16:9；语言：简体中文。",
                 "", "## 全局视觉要求", plan.style, f"配色：{plan.palette}", "",
                 "正文、页序、教学目标和知识点保持不变。下方版式与配图说明只用于设计，不应出现在学生可见正文中。",
                 "图片中的数学数量、平均分、几何结构、地图和实验现象须核对；无法保证准确时使用可编辑图形。",
                 "标题、正文、公式与教学图示尽量保留为原生可编辑对象，不能用一张整页图片代替课件。",
                 "不要额外增加封面、目录、致谢页或新知识；留足留白与思考时间，不把逐字稿塞入页面。", ""]
        for page, visual in zip(canonical_pages, plan.pages):
            notes += [f"## 第{page['number']}页｜{page['title']}", f"教学环节：{page['activity_title']}；源页面：{page['slide_id']}。",
                      "### 必须保留的页面文字", page["title"], *["- " + point for point in page["points"]],
                      "### 仅供设计使用", "布局类型："+visual.layout_kind,"版式：" + visual.layout,
                      "本页教学任务："+visual.teaching_goal,"学生观察或操作："+visual.student_task,
                      "观察重点："+visual.learning_focus,"视觉说明：" + visual.visual_prompt,
                      "素材选择理由："+visual.asset_reason,"内容与节奏安排："+visual.density_note,
                      "环节与本页上下文："+json.dumps(page['teaching_context'],ensure_ascii=False)]
            if visual.diagram_id:
                selected=next(d for d in page['diagram_candidates'] if d['id']==visual.diagram_id)
                notes += ['### 原生可编辑教学图解',json.dumps(selected,ensure_ascii=False),'揭示阶段：'+visual.diagram_stage,
                          '整数计算、进退位标记及数轴坐标由本地程序计算；不是交给生图模型绘制。']
            if visual.asset_ids:
                notes += ["### 教材素材"]
                notes += [f"- {asset_id}（第{assets[asset_id].get('page') or '—'}页）：{assets[asset_id].get('caption') or '无图注'}"
                          for asset_id in visual.asset_ids]
            notes += ['### AI插画请求','生图提示词：'+(visual.image_prompt or '本页不请求生图'),
                      '生图教学用途：'+(visual.image_reason or '无额外插画请求')]
            if visual.image_prompt.strip():notes += ['实际提交的完整生图提示词：',illustration_prompt(brief['grade'],plan.style,visual.image_prompt)]
            notes += [""]
        content = "\n".join(notes)
        (directory / "课件提示词.md").write_text(content, encoding="utf-8")
        return {"run_id": run_id, "version_id": brief["version_id"], "pages": len(plan.pages), "prompt_sha256": sha256(directory / "课件提示词.md")}

    def read_prompt(self, run_id):
        directory = self.run_dir(run_id)
        return {"brief": json.loads((directory / "brief.json").read_text(encoding="utf-8")),
                "plan": json.loads((directory / "visual-plan.json").read_text(encoding="utf-8")),
                "markdown": (directory / "课件提示词.md").read_text(encoding="utf-8")}

    def accept(self, run_id, provider, remote_id, credits=None, *, repair_text=False):
        directory = self.run_dir(run_id)
        data = self.read_prompt(run_id)
        brief = data["brief"]
        version = self.service.get("version", brief["version_id"])
        if digest(version["design"]) != brief["design_hash"]:
            raise ValueError("教学设计已改变，不能关联此课件")
        self.service.check(version["id"])
        candidate = directory / "download.pptx"
        receipt_path = directory / "result.json"
        if receipt_path.exists():
            result = json.loads(receipt_path.read_text(encoding="utf-8"))
            for entry in result["manifest"]["files"]:
                if sha256(Path(result["directory"]) / entry["name"]) != entry["sha256"]:
                    raise ValueError("已交付图文课件被修改，拒绝覆盖")
            return result
        if candidate.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("外部课件超过50MB")
        with zipfile.ZipFile(candidate) as archive:
            if len(archive.infolist()) > 5000 or sum(e.file_size for e in archive.infolist()) > 300 * 1024 * 1024:
                raise ValueError("课件解压大小或文件数量超过限制")
            if archive.testzip() is not None:
                raise ValueError("外部课件文件损坏")
        deck = Presentation(candidate)
        if len(deck.slides) != len(brief["pages"]):
            raise ValueError(f"外部PPT有{len(deck.slides)}页，原设计要求{len(brief['pages'])}页；为保证教案与逐字稿页码一致，未将其标为可交付课件")
        if abs(deck.slide_width / deck.slide_height - 16 / 9) > .04:
            raise ValueError("外部课件不是16:9页面")
        repairs = []
        mismatches = []
        for page, slide in zip(brief["pages"], deck.slides):
            text = normalized("\n".join(t for shape in slide.shapes for t in shape_text(shape)))
            missing = [required for required in [page["title"], *page["points"]] if normalized(required) not in text]
            geometry = None
            if repair_text and brief.get('subject') == '数学':
                from .geometry_visuals import geometry_spec
                geometry = geometry_spec(page, brief['pages'])
            if missing or geometry:
                if repair_text:
                    if len(text) < 20:
                        raise ValueError(f"第{page['number']}页没有足够的原生文字，无法自动恢复；请查看候选PPT")
                    from .presentation_repair import restore_page
                    restored = restore_page(deck, slide, page, geometry)
                    restored['missing_or_reformatted_text'] = missing
                    repairs.append(restored)
                else:
                    mismatches.append(page['number'])
            slide.notes_slide.notes_text_frame.text = page["script"]
        if mismatches:
            pages = '、'.join(str(n) for n in mismatches)
            raise PresentationTextMismatch(f"第{pages}页的正文被拆分、改写或缺失，尚未通过逐页文字校验。已保留下载文件，可点击“本地修复正文并校验”，无需重新生成或使用Gamma积分。")
        # Repairs restore the source text; they do not bypass the original check.
        for page, slide in zip(brief['pages'], deck.slides):
            text = normalized('\n'.join(t for shape in slide.shapes for t in shape_text(shape)))
            if any(normalized(required) not in text for required in [page['title'], *page['points']]):
                raise ValueError(f"第{page['number']}页本地恢复后仍未通过文字校验，请查看候选PPT")
        destination = self.root / "deliverables" / version["lesson_id"] / f"v{version['number']}-{version['id']}" / "ppt-variants" / run_id
        if destination.exists():
            raise ValueError("交付目录已存在，拒绝覆盖；请核对该任务")
        staging = directory / "bundle"
        staging.mkdir(exist_ok=True)
        for entry in version["manifest"]["manifest"]["files"]:
            shutil.copy2(Path(version["manifest"]["directory"]) / entry["name"], staging / entry["name"])
        deck.save(staging / "课件.pptx")
        shutil.copy2(directory / "课件提示词.md", staging / "课件提示词.md")
        write_json(staging / "课件视觉方案.json", data["plan"])
        record = {"run_id": run_id, "version_id": version["id"], "design_hash": brief["design_hash"],
                  "provider": str(provider)[:80], "remote_generation_id": str(remote_id)[:150], "credits": credits,
                  "created": now(), "text_coverage": "all_required_titles_and_points", "page_order_verified": True,
                  "teacher_notes_restored": True, "source_pptx_sha256": sha256(candidate),
                  "local_text_repairs": repairs,
                  "office_visual_review": "pending", "teaching_content_review": "pending"}
        write_json(staging / "课件生成记录.json", record)
        names = sorted(p.name for p in staging.iterdir() if p.is_file() and p.name not in {"交付清单.json", "交付包.zip"})
        manifest = {**version["manifest"]["manifest"], "presentation_run_id": run_id, "provider": provider,
                    "files": [{"name": n, "bytes": (staging / n).stat().st_size, "sha256": sha256(staging / n)} for n in names],
                    "checks": {"openxml_structure": True, "text_coverage": True, "page_mapping": True,
                               "office_visual_review": "pending", "teaching_content_review": "pending"}}
        write_json(staging / "交付清单.json", manifest)
        with zipfile.ZipFile(staging / "交付包.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name in names + ["交付清单.json"]:
                archive.write(staging / name, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)
        result = {"run_id": run_id, "version_id": version["id"], "directory": str(destination), "manifest": manifest, "valid": True,
                  "local_text_repairs": repairs}
        write_json(receipt_path, result)
        return result

    def compose_local(self, version_id, run_id, assets=None):
        directory = self.run_dir(run_id)
        receipt_path = directory / "result.json"
        if receipt_path.exists():
            result = json.loads(receipt_path.read_text(encoding="utf-8"))
            if result.get("valid") and Path(result.get("directory", "")).is_dir():
                return result
        data = self.read_prompt(run_id)
        brief, plan = data["brief"], data["plan"]
        if brief["version_id"] != version_id:
            raise ValueError("教材素材课件与教学版本不一致")
        version = self.service.get("version", version_id)
        if digest(version["design"]) != brief["design_hash"]:
            raise ValueError("教学设计已改变，不能关联此课件")
        self.service.check(version_id)
        design = LessonDesign.model_validate(version["design"])
        pages=enrich_pages(design,pagination(design)[0])
        asset_map = {asset["id"]: asset for asset in
                     (assets if assets is not None else self.candidate_assets(version_id, include_path=True))}
        for page,visual in zip(pages,PptVisualPlan.model_validate(plan).pages):validate_page_plan(page,visual,[asset_map[a] for a in visual.asset_ids if a in asset_map])
        assets_by_page, selected = {}, []
        for plan_page in plan["pages"]:
            chosen = []
            for asset_id in plan_page.get("asset_ids", []):
                asset = asset_map.get(asset_id)
                if asset and asset not in chosen:
                    chosen.append(asset)
            if chosen:
                assets_by_page[plan_page["page_number"]] = chosen
                selected.extend(asset["id"] for asset in chosen)
        selection = "model"
        if not assets_by_page and asset_map and not any(p.get('image_prompt') for p in plan['pages']):
            # Safe fallback when the text model returned no asset selection:
            # match page terms against the figure's page context and caption.
            from .knowledge import terms
            used = set()
            for page in brief["pages"]:
                if any(p['page_number']==page['number'] and p.get('diagram_id') for p in plan['pages']):continue
                query = terms(f"{page['title']} {' '.join(page['points'])}")
                ranked = []
                for asset in asset_map.values():
                    if asset["id"] in used:
                        continue
                    score = len(query & terms(f"{asset.get('caption', '')} {asset.get('description', '')} "
                                               f"{asset.get('question_text', '')} {asset.get('context', '')}"))
                    if score >= 2:
                        ranked.append((score, asset))
                ranked.sort(key=lambda pair: -pair[0])
                if ranked:
                    assets_by_page[page["number"]] = [ranked[0][1]]
                    used.add(ranked[0][1]["id"])
                    selected.append(ranked[0][1]["id"])
            if assets_by_page:
                selection = "fallback"
        # Generated illustrations are run-local files, never knowledge-base edits.
        generated = []
        index_path = directory / 'generated' / 'index.json'
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding='utf-8'))
            if (index.get('version_id') != version_id or index.get('design_hash') != brief['design_hash']
                    or index.get('plan_sha256') != sha256(directory / 'visual-plan.json')):
                raise ValueError('生图素材与当前课件方案不一致')
            from PIL import Image
            for entry in index.get('images', []):
                number = entry['page_number']
                visual = next((p for p in plan['pages'] if p['page_number'] == number), None)
                if not visual or not visual.get('image_prompt'):
                    raise ValueError('生图素材没有对应的页面请求')
                if not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|webp)', entry['file']):
                    raise ValueError('生图素材文件名不正确')
                image_path = directory / 'generated' / entry['file']
                if sha256(image_path) != entry['sha256']:
                    raise ValueError('生图素材已被修改')
                with Image.open(image_path) as image:
                    image.verify()
                asset = {'id': 'generated_'+entry['file'][:16], 'kind': 'generated',
                         'path': str(image_path), 'caption': 'AI 生成插画', 'page_number': number}
                assets_by_page.setdefault(number, []).append(asset)
                generated.append(entry)
        requested = {p['page_number'] for p in plan['pages'] if p.get('image_prompt')}
        if requested != {entry['page_number'] for entry in generated}:
            raise ValueError('本课插画尚未生成完整，请继续生成整套材料')
        destination = (self.root / "deliverables" / version["lesson_id"] /
                       f"v{version['number']}-{version_id}" / "local-variants" / run_id)
        if destination.exists():
            raise ValueError("本地教材素材课件目录已存在，拒绝覆盖")
        staging = directory / "local-bundle"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        base = Path(version["manifest"]["directory"])
        for name in ("教案.docx", "逐字稿.docx", "检测题与答案.docx", "教学设计.json",
                     "信息密度.json", "修改说明.md", "内容预览.html"):
            shutil.copy2(base / name, staging / name)
        from .exporters import make_pptx
        make_pptx(design, staging / "课件.pptx", f"第{version['number']}版", pages, assets_by_page, plan=plan)
        deck = Presentation(staging / "课件.pptx")
        if len(deck.slides) != len(pages):
            raise ValueError("教材素材课件页数与教学设计不一致")
        image_count = 0
        for page, slide in zip(pages, deck.slides):
            text = normalized("\n".join(part for shape in slide.shapes for part in shape_text(shape)))
            if any(normalized(required) not in text for required in [page["title"], *page["points"]]):
                raise ValueError(f"第{page['number']}页教材素材合成后正文不完整")
            image_count += sum(1 for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE)
            for shape in slide.shapes:
                if (min(shape.left, shape.top) < 0 or shape.left + shape.width > deck.slide_width + 10
                        or shape.top + shape.height > deck.slide_height + 10):
                    raise ValueError(f"第{page['number']}页有对象超出页面")
        deck.save(staging / "课件.pptx")
        names = sorted(path.name for path in staging.iterdir() if path.is_file())
        base_manifest = version["manifest"]["manifest"]
        native_count=sum(bool(p.get('diagram_id')) for p in plan['pages'])
        manifest = {**base_manifest, "provider": "local-hybrid" if generated else "local-textbook", "presentation_run_id": run_id,
                    "files": [{"name": name, "bytes": (staging / name).stat().st_size,
                               "sha256": sha256(staging / name)} for name in names],
                    "checks": {**base_manifest.get("checks", {}), "textbook_assets": True,
                               "image_count": image_count, "generated_image_count": len(generated),'native_diagram_count':native_count, "office_visual_review": "pending",
                               "teaching_content_review": "pending"}}
        write_json(staging / "交付清单.json", manifest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)
        result = {"run_id": run_id, "version_id": version_id, "directory": str(destination),
                  "manifest": manifest, "valid": True, "local_text_repairs": [],
                  "textbook_assets": sorted(set(selected)), "asset_selection": selection,
                  "image_count": image_count, "generated_image_count": len(generated),'native_diagram_count':native_count}
        write_json(receipt_path, result)
        return result

    def pages(self, version_id):
        version = self.service.get("version", version_id)
        design = LessonDesign.model_validate(version["design"])
        pages, _ = pagination(design)
        activities = {activity.id: activity.title for activity in design.activities}
        assets = [{key: asset.get(key) for key in
                   ("id", "knowledge_id", "page", "file", "caption", "description", "dialogue",
                    "kind", "question_text", "width", "height")}
                  for asset in self.candidate_assets(version_id)]
        return {"version_id": version_id,
                "pages": [{"number": page["number"], "title": page["title"], "points": page["points"],
                           "activity_title": activities.get(page["activity_id"], "")} for page in pages],
                "assets": assets}

    def compose_manual(self, version_id, run_id, mapping):
        candidates = self.candidate_assets(version_id, include_path=True)
        prepared = self.prepare(version_id, run_id, "教材素材")
        pages = []
        for page in prepared["brief"]["pages"]:
            ids = mapping.get(str(page["number"]), mapping.get(page["number"], [])) if mapping else []
            if not isinstance(ids, list):
                ids = []
            pages.append({"page_number": page["number"], "layout": "教材素材",
                          "visual_prompt": "教师手动选择的教材素材", "asset_ids": ids[:2]})
        self.save_prompt(run_id, {"style": "教师手动选择的教材素材", "pages": pages}, allowed_assets=candidates)
        return self.compose_local(version_id, run_id, assets=candidates)
