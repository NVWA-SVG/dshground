"""Teacher-managed subject knowledge base: immutable text, chunks and lexical retrieval."""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
import zipfile
from io import BytesIO
from pathlib import Path

from .documents import extract_text
from .exporters import sha256
from .store import identifier, now, digest
from .import_limits import import_limits
from .pdf_book import pdf_structure, save_page_range
from .book_scopes import BookScopes,write_json
from .book_index import build_book_index,link_assets
from .import_progress import report as report_import
from .vision_assets import VisionAssets

SUFFIXES = {".txt", ".md", ".docx", ".pdf"}
CHUNK_SIZE = 900


def normalize(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).lower()


def terms(value):
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    found = set(re.findall(r"[a-z0-9]{2,}", text))
    for run in re.findall(r"[一-鿿]+", text):
        if len(run) == 1:
            found.add(run)
        else:
            found.update(run[index:index + 2] for index in range(len(run) - 1))
    return found


def overlap_ratio(first, second):
    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    area_first = max(1, (first[2] - first[0]) * (first[3] - first[1]))
    area_second = max(1, (second[2] - second[0]) * (second[3] - second[1]))
    return intersection / min(area_first, area_second)


BLANK_PATTERN = re.compile(r"[（(][\s　]*[）)]|_{2,}|＿{2,}")


def clean_text(value, limit=500):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def nearby_text(page, box, margin=45, limit=400):
    parts = []
    for block in page.get_text("blocks"):
        if len(block) < 7 or block[6] != 0:
            continue
        text = clean_text(block[4], 180)
        if not text:
            continue
        if block[2] < box[0] - margin or block[0] > box[2] + margin:
            continue
        if block[1] >= box[3] - 5:
            distance = block[1] - box[3]
        elif block[3] <= box[1] + 5:
            distance = box[1] - block[3]
        else:
            distance = 0
        if distance <= margin and text not in parts:
            parts.append(text)
        if sum(len(part) for part in parts) >= limit:
            break
    return " ".join(parts)[:limit]


def image_kind(caption, dialogue, description=""):
    text = f"{caption} {dialogue} {description}"
    if any(word in text for word in ("人物", "学生", "教师", "男孩", "女孩", "教室", "斑马线", "跳绳", "跑步", "对话")):
        return "scene"
    if any(word in text for word in ("钟", "秒表", "刻度", "时间线", "表格", "单位", "数轴", "分针", "秒针")):
        return "diagram"
    return "scene"


def extract_question_regions(page, page_number, knowledge_id, directory, page_text, seen, limit=20):
    """Render page regions that contain fill-in-the-blank textbook exercises.
    These are text/vector content, so ordinary image extraction misses them."""
    import fitz
    assets = []
    blocks = [block for block in page.get_text("blocks")
              if len(block) >= 7 and block[6] == 0 and BLANK_PATTERN.search(str(block[4]))]
    blocks.sort(key=lambda block: (block[1], block[0]))
    kept = []
    for block in blocks:
        visible = fitz.Rect(block[0] - 4, block[1] - 4, block[2] + 4, block[3] + 4) & page.rect
        if visible.is_empty or visible.is_infinite:
            continue
        box = tuple(visible)
        if box[2] - box[0] < 40 or box[3] - box[1] < 12:
            continue
        if any(overlap_ratio(box, other) > 0.65 for _, other in kept):
            continue
        kept.append((clean_text(block[4], 500), box))
        if len(kept) >= limit:
            break
    for index, (text, box) in enumerate(kept, 1):
        pixmap = page.get_pixmap(clip=fitz.Rect(*box), dpi=200, alpha=False)
        filename = f"page-{page_number:02d}-q{index:02d}.png"
        target = directory / filename
        pixmap.save(target)
        fingerprint = sha256(target)
        if fingerprint in seen:
            target.unlink(missing_ok=True)
            filename=seen[fingerprint];target=directory/filename
        else:seen[fingerprint]=filename
        assets.append({
            "id": f"asset_{knowledge_id.replace('knowledge_', '')}_p{page_number}_q{index}",
            "knowledge_id": knowledge_id, "page": page_number, "file": filename,
            "width": pixmap.width, "height": pixmap.height, "bytes": target.stat().st_size,
            "sha256": fingerprint, "bbox": [round(value, 1) for value in box],
            "caption": text, "description": "", "dialogue": "", "kind": "question",
            "question_text": text, "context": page_text[:600],
        })
    return assets


def nearest_text(page, box):
    candidates = []
    for block in page.get_text("blocks"):
        if len(block) < 7 or block[6] != 0:
            continue
        text = re.sub(r"\s+", " ", str(block[4])).strip()
        if not text:
            continue
        if block[2] < box[0] - 10 or block[0] > box[2] + 10:
            continue
        if block[1] >= box[3] - 5:
            distance = block[1] - box[3]
        elif block[3] <= box[1] + 5:
            distance = box[1] - block[3]
        else:
            distance = 0
        candidates.append((distance, len(text), text))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2][:240] if candidates else ""


def extract_pdf_assets(path, directory, knowledge_id):
    try:
        import fitz
    except Exception:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    assets, seen = [], {}
    document = fitz.open(path)
    try:
        for page_number, page in enumerate(document, 1):
            page_text = page.get_text("text")
            page_area = page.rect.width * page.rect.height
            boxes = []
            for info in page.get_image_info(xrefs=True):
                try:
                    visible = fitz.Rect(info["bbox"]) & page.rect
                    if visible.is_empty or visible.is_infinite:
                        continue
                    x0, y0, x1, y1 = visible
                except (KeyError, TypeError, ValueError):
                    continue
                width, height = x1 - x0, y1 - y0
                if width < 45 or height < 45 or width * height < 4500:
                    continue
                if width * height > page_area * 0.85 or width / height > 10 or height / width > 10:
                    continue
                boxes.append((x0, y0, x1, y1))
            boxes.sort(key=lambda box: -(box[2] - box[0]) * (box[3] - box[1]))
            kept = []
            for box in boxes:
                if any(overlap_ratio(box, other) > 0.6 for other in kept):
                    continue  # fragment or mask inside a larger illustration
                kept.append(box)
            for index, box in enumerate(kept[:6], 1):
                pixmap = page.get_pixmap(clip=fitz.Rect(*box), dpi=200, alpha=False)
                filename = f"page-{page_number:02d}-{index:02d}.png"
                target = directory / filename
                pixmap.save(target)
                fingerprint = sha256(target)
                if fingerprint in seen:
                    target.unlink(missing_ok=True)
                    filename=seen[fingerprint];target=directory/filename
                else:seen[fingerprint]=filename
                caption = nearest_text(page, box)
                dialogue = nearby_text(page, box)
                assets.append({
                    "id": f"asset_{knowledge_id.replace('knowledge_', '')}_p{page_number}_{index}",
                    "knowledge_id": knowledge_id, "page": page_number, "file": filename,
                    "width": pixmap.width, "height": pixmap.height, "bytes": target.stat().st_size,
                    "sha256": fingerprint, "bbox": [round(value, 1) for value in box],
                    "caption": caption, "dialogue": dialogue, "kind": image_kind(caption, dialogue),
                    "description": "", "context": page_text[:600],
                })
            assets.extend(extract_question_regions(page, page_number, knowledge_id, directory,
                                                   page_text, seen))
    finally:
        document.close()
    return assets


def extract_docx_assets(path, directory, knowledge_id, text):
    try:
        from PIL import Image
    except Exception:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    assets, seen = [], set()
    with zipfile.ZipFile(path) as archive:
        names = sorted(name for name in archive.namelist()
                       if name.startswith("word/media/") and Path(name).suffix.lower() in {".png", ".jpg", ".jpeg"})
        for index, name in enumerate(names, 1):
            data = archive.read(name)
            if len(data) < 10000:
                continue
            try:
                image = Image.open(BytesIO(data))
                width, height = image.size
            except Exception:
                continue
            if width < 80 or height < 80:
                continue
            suffix = Path(name).suffix.lower()
            filename = f"docx-{index:02d}{suffix}"
            target = directory / filename
            target.write_bytes(data)
            fingerprint = sha256(target)
            if fingerprint in seen:
                target.unlink(missing_ok=True)
                continue
            seen.add(fingerprint)
            assets.append({
                "id": f"asset_{knowledge_id.replace('knowledge_', '')}_docx_{index}",
                "knowledge_id": knowledge_id, "page": 0, "file": filename,
                "width": width, "height": height, "bytes": len(data), "sha256": fingerprint,
                "bbox": [], "caption": "", "context": text[:600],
            })
    return assets


def split_text(text, size=CHUNK_SIZE):
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks, current = [], ""

    def flush():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        if len(paragraph) > size * 2:
            flush()
            for sentence in re.split(r"(?<=[。！？!?；;\n])", paragraph):
                if current and len(current) + len(sentence) > size:
                    flush()
                current += sentence
            flush()
        elif current and len(current) + len(paragraph) + 2 > size:
            flush()
            current = paragraph
        else:
            current = (current + "\n\n" + paragraph).strip() if current else paragraph
    flush()
    return chunks or [text.strip()[:size]]


class KnowledgeBase(BookScopes,VisionAssets):
    def __init__(self, service):
        self.service = service
        self.root = service.root
        self.data = service.store.data

    def directory(self, knowledge_id):
        if not re.fullmatch(r"knowledge_[A-Za-z0-9]{1,64}", str(knowledge_id)):
            raise ValueError("知识库标识不正确")
        return self.data / "knowledge" / knowledge_id

    def get(self, knowledge_id):
        row = self.service.store.read("knowledge", knowledge_id)
        return {**json.loads(row["payload"]), "id": row["id"], "created": row["created"]}

    def list(self):
        with self.service.store.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM knowledge ORDER BY created DESC,id")]
            lessons = [dict(row) for row in db.execute("SELECT id,payload FROM lessons")]
            versions = [dict(row) for row in db.execute("SELECT lesson_id,design FROM versions")]
        usage = {row["id"]: {"lessons": set(), "versions": 0} for row in rows}
        for lesson in lessons:
            for knowledge_id in json.loads(lesson["payload"]).get("knowledge_ids") or []:
                if knowledge_id in usage:
                    usage[knowledge_id]["lessons"].add(lesson["id"])
        for version in versions:
            for knowledge_id in json.loads(version["design"]).get("knowledge_ids") or []:
                if knowledge_id in usage:
                    usage[knowledge_id]["versions"] += 1
        entries = []
        for row in rows:
            payload = json.loads(row["payload"])
            entries.append({**payload, "id": row["id"], "created": row["created"],
                            "asset_count": payload["asset_count"] if "asset_count" in payload else len(self.assets(row["id"])),
                            **(self._index_summary(row["id"]) if payload.get("storage_mode")=="book" else {}),
                            "used_by": {"lessons": len(usage[row["id"]]["lessons"]),
                                        "versions": usage[row["id"]]["versions"]}})
        return {"entries": entries}

    def assets(self, knowledge_id, include_path=False):
        if self.get(knowledge_id).get('storage_mode')=='scope':return self._scope_assets(knowledge_id,include_path)
        directory = self.directory(knowledge_id)
        path = directory / "assets.json"
        if not path.exists():
            return []
        result = []
        descriptions=self.vision_descriptions(knowledge_id)
        for item in json.loads(path.read_text(encoding="utf-8")):
            file = directory / "assets" / item["file"]
            if not file.is_file():
                continue
            entry = {**item}
            if entry.get('kind')!='question' and not entry.get('description') and entry.get('sha256') in descriptions:
                entry.update(descriptions[entry['sha256']])
            entry.setdefault("dialogue", "")
            if entry.get("kind") != "question":
                entry["kind"] = image_kind(entry.get("caption", ""), entry.get("dialogue", ""),
                                           entry.get("description", ""))
            if include_path:
                entry["path"] = str(file)
            result.append(entry)
        return result

    def assets_for(self, ids, include_path=False):
        result = [];seen=set()
        for knowledge_id in self._effective_ids(ids):
            for item in self.assets(knowledge_id, include_path=include_path):
                key=item.get('sha256') or item['id']
                if key not in seen:result.append(item);seen.add(key)
        return result

    def text(self, knowledge_id):
        if self.get(knowledge_id).get('storage_mode')=='scope':return self._scope_text(knowledge_id)
        row = self.service.store.read("knowledge", knowledge_id)
        payload = json.loads(row["payload"])
        path = self.directory(knowledge_id) / "extracted.txt"
        if not path.is_file():
            raise ValueError("知识库原文不存在")
        text = path.read_text(encoding="utf-8")
        return {"id": knowledge_id, "title": payload.get("title", ""), "characters": len(text),
                "chunk_count": payload.get("chunk_count", 0), "text": text[:200000],
                "truncated": len(text) > 200000}

    def update_asset(self, knowledge_id, asset_id, fields):
        if self.get(knowledge_id).get('storage_mode')=='scope':return self._scope_update_asset(knowledge_id,asset_id,fields)
        path = self.directory(knowledge_id) / "assets.json"
        if not path.exists():
            raise ValueError("素材不存在")
        items = json.loads(path.read_text(encoding="utf-8"))
        target = next((item for item in items if item["id"] == asset_id), None)
        if target is None:
            raise ValueError("素材不存在")
        if "caption" in fields:
            target["caption"] = str(fields["caption"] or "").strip()[:500]
        if "description" in fields:
            target["description"] = str(fields["description"] or "").strip()[:1000]
        if "dialogue" in fields:
            target["dialogue"] = str(fields["dialogue"] or "").strip()[:500]
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**target, "knowledge_id": knowledge_id}

    def describe_assets(self, knowledge_id, updates):
        if self.get(knowledge_id).get('storage_mode')=='scope':
            existing={item['id'] for item in self.assets(knowledge_id)};changed=0
            for update in updates or []:
                if update.get('id') in existing:
                    self._scope_update_asset(knowledge_id,update['id'],{'description':update.get('description',''),'vision_model':update.get('model',''),'described_at':now()});changed+=1
            return {'updated':changed,'remaining':sum(not item.get('description') for item in self.assets(knowledge_id))}
        path = self.directory(knowledge_id) / "assets.json"
        if not path.exists():
            raise ValueError("素材不存在")
        items = json.loads(path.read_text(encoding="utf-8"))
        by_id = {item["id"]: item for item in items}
        changed = 0
        for update in updates or []:
            item = by_id.get(update.get("id"))
            if item is None:
                continue
            item["description"] = str(update.get("description") or "").strip()[:1000]
            item["described_at"] = now()
            item["vision_model"] = str(update.get("model") or "")[:100]
            changed += 1
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"updated": changed, "remaining": sum(1 for item in items if not item.get("description"))}

    def asset_file(self, knowledge_id, name):
        if self.get(knowledge_id).get('storage_mode')=='scope':return self._scope_asset_file(knowledge_id,name)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(png|jpg|jpeg|gif|webp)", str(name), re.IGNORECASE):
            raise ValueError("素材文件名不正确")
        directory = (self.directory(knowledge_id) / "assets").resolve()
        target = (directory / name).resolve()
        if not target.is_relative_to(directory) or not target.is_file():
            raise ValueError("素材不存在")
        return target

    def refresh_assets(self, knowledge_id):
        if self.get(knowledge_id).get('storage_mode')=='scope':
            self.refresh_assets(self._owner(knowledge_id)['id'])
            return {**self.get(knowledge_id),'assets':self.assets(knowledge_id)}
        row = self.service.store.read("knowledge", knowledge_id)
        payload = json.loads(row["payload"])
        directory = self.directory(knowledge_id)
        original = next((item for item in directory.glob("original.*") if item.is_file()), None)
        if original is None:
            return {**payload, "id": knowledge_id, "assets": []}
        text = (directory / "extracted.txt").read_text(encoding="utf-8")
        old_assets = []
        old_path = directory / "assets.json"
        if old_path.exists():
            old_assets = json.loads(old_path.read_text(encoding="utf-8"))
        old_by_hash = {item.get("sha256"): item for item in old_assets if item.get("sha256")}
        asset_directory=(directory/'assets').resolve()
        if not asset_directory.is_relative_to((self.data/'knowledge').resolve()):raise ValueError('素材目录超出教材存储范围')
        # Shared PNGs retain the exact bytes used by existing courses.
        if not (directory/'shared-assets.json').exists():shutil.rmtree(asset_directory, ignore_errors=True)
        if original.suffix.lower() == ".pdf":
            assets = extract_pdf_assets(original, directory / "assets", knowledge_id)
        elif original.suffix.lower() == ".docx":
            assets = extract_docx_assets(original, directory / "assets", knowledge_id, text)
        else:
            assets = []
        assets=self._merge_shared_assets(knowledge_id,assets)
        for item in assets:
            if payload.get('source_book') and item.get('page'):
                item['source_page']=payload['source_book']['start_page']+item['page']-1
            previous = old_by_hash.get(item.get("sha256"))
            if previous:
                for key in ("description", "described_at", "vision_model", "caption", "dialogue"):
                    if previous.get(key):
                        item[key] = previous[key]
        (directory / "assets.json").write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
        self._prune_render_files(knowledge_id,assets)
        chunks_file=directory/'chunks.json'
        chunks=json.loads(chunks_file.read_text(encoding='utf-8')) if chunks_file.exists() else []
        if chunks and isinstance(chunks[0],dict):
            link_assets(chunks,assets);write_json(chunks_file,chunks)
        payload["asset_count"] = len(assets)
        payload['import_warnings'] = []
        with self.service.store.transaction() as db:
            db.execute("UPDATE knowledge SET payload=? WHERE id=?",
                       (json.dumps(payload, ensure_ascii=False, sort_keys=True), knowledge_id))
        return {**payload, "id": knowledge_id, "assets": assets}

    def create(self, path, meta, key, *, provenance=None):
        path = Path(path).resolve()
        inputs = (self.root / "inputs").resolve()
        if not path.is_relative_to(inputs) or not path.is_file():
            raise ValueError(f"请将知识库文件放入 {inputs}，只接入该目录内明确提供的文件")
        suffix = path.suffix.lower()
        if suffix not in SUFFIXES:
            raise ValueError("知识库支持 TXT、MD、DOCX 和可提取文本的 PDF")
        limits = import_limits(self.root)
        max_mb = limits['max_pdf_mb'] if suffix == '.pdf' else limits['max_other_mb']
        if not path.stat().st_size or path.stat().st_size > max_mb*1024*1024:
            raise ValueError(f"知识库文件需为非空文件，当前类型上限为{max_mb}MB")
        meta = meta if isinstance(meta, dict) else {}
        title = str(meta.get("title") or path.stem).strip()
        subject, grade = str(meta.get("subject") or "").strip(), str(meta.get("grade") or "").strip()
        if not title or len(title)>120:raise ValueError("知识库名称应为1—120字")
        if len(subject)>30 or len(grade)>30:raise ValueError("学科或年级名称过长")
        if not key or len(key)>150:raise ValueError('需要不超过150字符的幂等键')
        fingerprint = sha256(path)
        identity = provenance if provenance is not None else fingerprint
        request = {"sha256":identity,"title":title,"subject":subject,"grade":grade}
        with self.service.store.connect() as db:
            cached=db.execute("SELECT payload_hash,result FROM ledger WHERE operation=? AND key=?",('knowledge_create',key)).fetchone()
        if cached:
            if cached['payload_hash']!=digest(request):raise ValueError('同一幂等键不能用于不同输入')
            result=json.loads(cached['result'])
            return {**result,**{key:value for key,value in self.get(result['knowledge_id']).items() if key not in ('id','created')}}
        def existing_import(db):
            for row in db.execute('SELECT id,payload FROM knowledge'):
                value=json.loads(row['payload'])
                same_identity=(value.get('source_book')==provenance) if provenance else (value.get('fingerprint_kind')!='scope_digest' and value.get('sha256')==fingerprint)
                if same_identity and all(value.get(name,'')==expected for name,expected in [('title',title),('subject',subject),('grade',grade)]):
                    return {'knowledge_id':row['id'],**value}
            return None
        with self.service.store.connect() as db:
            existing=existing_import(db)
        if existing:
            return self.service.store.idempotent('knowledge_create',key,request,lambda db: existing_import(db) or existing)
        structure=pdf_structure(path,limits['max_pdf_pages']) if suffix=='.pdf' else None
        # Parse outside the SQLite write transaction so whole-book extraction
        # does not block unrelated course/feedback operations for minutes.
        book_data=build_book_index(path,title,self.root,lambda phase,counts:report_import(self.root,key,phase,counts)) if suffix=='.pdf' else None
        text_content='\n'.join(f"[第{page['page']}页]\n{page['text']}" for page in book_data[1]) if book_data else extract_text(path)
        chunks=book_data[2] if book_data else split_text(text_content)
        knowledge_id=identifier('knowledge');directory=self.directory(knowledge_id)
        directory.mkdir(parents=True)
        retained=False
        try:
            shutil.copy2(path,directory/('original'+suffix))
            (directory/'extracted.txt').write_text(text_content,encoding='utf-8')
            (directory/'chunks.json').write_text(json.dumps(chunks,ensure_ascii=False),encoding='utf-8')
            warnings=[]
            if book_data:report_import(self.root,key,'assets',{'pages':structure['pdf_pages']})
            try:
                assets=extract_pdf_assets(path,directory/'assets',knowledge_id) if suffix=='.pdf' else extract_docx_assets(path,directory/'assets',knowledge_id,text_content) if suffix=='.docx' else []
            except Exception as error:
                assets=[];warnings.append('教材文字已提取，图片提取未完成：'+str(error)[:200])
            if provenance:
                for asset in assets:
                    if asset.get('page'):
                        asset['source_page']=provenance['start_page']+asset['page']-1
            if book_data:
                link_assets(chunks,assets)
                write_json(directory/'pages.json',book_data[1]);write_json(directory/'book-index.json',book_data[0]);write_json(directory/'chunks.json',chunks)
            (directory/'assets.json').write_text(json.dumps(assets,ensure_ascii=False,indent=2),encoding='utf-8')
            payload={'title':title,'subject':subject,'grade':grade,'original_name':path.name,'sha256':fingerprint,
                     'characters':len(text_content),'chunk_count':len(chunks),'asset_count':len(assets),'bytes':path.stat().st_size,
                     'import_warnings':warnings}
            if structure:payload['pdf_pages']=structure['pdf_pages']
            if book_data:payload.update({'storage_mode':'book','index_version':2,'chapter_count':len(book_data[0]['chapters']),'directory_source':book_data[0]['directory_source']})
            if provenance:payload['source_book']=provenance
            def action(db):
                existing=existing_import(db)
                if existing:return existing
                db.execute('INSERT INTO knowledge VALUES(?,?,?)',(knowledge_id,json.dumps(payload,ensure_ascii=False,sort_keys=True),now()))
                return {'knowledge_id':knowledge_id,**payload}
            result=self.service.store.idempotent('knowledge_create',key,request,action)
            retained=result['knowledge_id']==knowledge_id
            if book_data:report_import(self.root,key,'ready',{'chapters':len(book_data[0]['chapters']),'chunks':len(chunks)})
            return result
        finally:
            # Only this newly-created owned knowledge directory is eligible.
            if not retained and directory.resolve().is_relative_to((self.data/'knowledge').resolve()) and directory.name==knowledge_id:
                shutil.rmtree(directory,ignore_errors=True)

    def pdf_structure(self, knowledge_id):
        entry=self.get(knowledge_id)
        original=self.directory(knowledge_id)/'original.pdf'
        if not original.is_file():raise ValueError('该知识库不是PDF教材')
        return {'knowledge_id':knowledge_id,'title':entry['title'],**pdf_structure(original,import_limits(self.root)['max_pdf_pages'])}

    def create_unit(self, knowledge_id, start, end, title, key):
        owner=self._owner(knowledge_id)
        if owner.get('storage_mode')!='book':self.rebuild_book_index(owner['id'])
        return self._create_scope(knowledge_id,start,end,title,key)

    def delete(self, knowledge_id):
        self.get(knowledge_id)
        with self.service.store.connect() as db:
            children=[row['id'] for row in db.execute('SELECT id,payload FROM knowledge') if json.loads(row['payload']).get('scope',{}).get('root_id')==knowledge_id]
        if children:raise ValueError('该教材仍有章节或范围引用，请先删除这些引用')
        with self.service.store.connect() as db:
            lessons = [dict(row) for row in db.execute("SELECT id,payload FROM lessons")]
            versions = [dict(row) for row in db.execute("SELECT id,design FROM versions")]
        used_lessons = {lesson["id"] for lesson in lessons
                        if knowledge_id in (json.loads(lesson["payload"]).get("knowledge_ids") or [])}
        used_versions = [version["id"] for version in versions
                         if knowledge_id in (json.loads(version["design"]).get("knowledge_ids") or [])]
        if used_lessons or used_versions:
            raise ValueError(f"该知识库仍被 {len(used_lessons)} 门课程使用，暂不能删除")
        with self.service.store.transaction() as db:
            db.execute("DELETE FROM knowledge WHERE id=?", (knowledge_id,))
            for row in list(db.execute("SELECT operation,key,result FROM ledger")):
                if knowledge_id in row["result"]:
                    db.execute("DELETE FROM ledger WHERE operation=? AND key=?", (row["operation"], row["key"]))
        directory=self.directory(knowledge_id).resolve()
        if not directory.is_relative_to((self.data/'knowledge').resolve()):raise ValueError('教材目录超出存储范围')
        shutil.rmtree(directory, ignore_errors=True)
        return {"deleted": True, "knowledge_id": knowledge_id}

    def context(self, ids, query, limit=10, max_chars=16000):
        from .retrieval import select_excerpts
        entries, chunks = [], []
        for knowledge_id in self._effective_ids(ids):
            entry = self.get(knowledge_id)
            entries.append({"id": entry["id"], "title": entry["title"],
                            "subject": entry.get("subject", ""), "grade": entry.get("grade", ""),
                            **({"source_book":entry["source_book"]} if entry.get("source_book") else {})})
            structured=self._structured_chunks(knowledge_id)
            if structured is not None:
                from .retrieval import tokens
                outlines=self.book_index(knowledge_id)['chapters'];query_tokens=tokens(query)
                outlines=sorted(outlines,key=lambda item:-len(tokens(item['title']+' '+' '.join(item['overview']['topics']))&query_tokens))[:6]
                entries[-1]['chapter_overviews']=[{'title':item['title'],'page_refs':[item['start_page'],item['end_page']],
                    'topics':item['overview']['topics'][:8],'content_types':item['overview']['content_types'],'source_excerpt':item['overview']['excerpt'][:120],
                    'method':'source_extract'} for item in outlines]
                chunks.extend(structured);continue
            path = self.directory(knowledge_id) / "chunks.json"
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            last_page = None
            for index, content in enumerate(items, 1):
                markers = re.findall(r"\[第(\d+)页\]", content)
                pages = ([last_page] if last_page and not re.match(r'\s*\[第\d+页\]',content) else []) + [int(value) for value in markers]
                if markers:
                    last_page = int(markers[-1])
                chunks.append({"knowledge_id": knowledge_id, "title": entry["title"], "chunk": index,
                               "page": pages[0] if len(set(pages)) == 1 else None,
                               "page_refs": sorted(set(pages)), "page_kind": "pdf_page" if pages else None,
                               "text": content})
        selected = select_excerpts(chunks, query, limit=limit, max_chars=max_chars)
        return {"entries": entries, **selected}
