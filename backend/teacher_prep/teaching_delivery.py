"""One teacher-facing bundle, independent of intermediate provider artifacts."""
from __future__ import annotations

import json
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from .exporters import sha256, write_json
from .presentations import Presentations
from .store import digest

MATERIALS = ('课件.pptx', '教案.docx', '逐字稿.docx', '检测题与答案.docx',
             '教学设计.json', '信息密度.json', '修改说明.md', '内容预览.html')


def read_receipt(destination, source_files):
    """Validate and reuse an immutable bundle already published by this or another task."""
    receipt_path = destination/'delivery.json'
    if not receipt_path.exists():
        raise ValueError('交付目录已存在但缺少交付记录，请检查后恢复')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt.get('source_files') != source_files:
        raise ValueError('已交付材料来源发生变化，拒绝覆盖')
    for entry in receipt['manifest']['files']:
        if sha256(destination/entry['name']) != entry['sha256']:
            raise ValueError('已交付材料被修改，拒绝覆盖')
    if sha256(destination/'交付包.zip') != receipt['zip_sha256']:
        raise ValueError('已交付材料包被修改，拒绝覆盖')
    return receipt


def publish(staging, destination):
    """Publish a staging directory. Windows may briefly deny the rename while an
    antivirus or another reader still holds a handle, so retry briefly."""
    for attempt in range(6):
        try:
            staging.rename(destination)
            return True
        except OSError as error:
            if destination.exists():
                return False
            if getattr(error, 'winerror', None) not in (5, 32, 183) or attempt == 5:
                raise
            time.sleep(0.15 * (attempt + 1))
    return False


def prepare_delivery(service, version_id, run_id=None):
    version = service.get('version', version_id)
    if version['status'] != 'ready':
        raise ValueError('整套材料尚未导出完成')
    service.check(version_id)
    source = Path(version['manifest']['directory'])
    source_manifest = version['manifest']['manifest']
    if run_id:
        pipeline = Presentations(service)
        run_dir = pipeline.run_dir(run_id)
        brief = json.loads((run_dir/'brief.json').read_text(encoding='utf-8'))
        if brief['version_id'] != version_id or brief['design_hash'] != digest(version['design']):
            raise ValueError('不能将其他教学版本的课件交付给本课')
        accepted = json.loads((run_dir/'result.json').read_text(encoding='utf-8'))
        if not accepted.get('valid') or accepted['version_id'] != version_id:
            raise ValueError('课件尚未通过校验')
        source = Path(accepted['directory']).resolve()
        if not source.is_relative_to((service.root/'deliverables').resolve()):
            raise ValueError('课件不在交付目录')
        source_manifest = accepted['manifest']
    entries = {entry['name']: entry for entry in source_manifest['files']}
    for name in MATERIALS:
        if name not in entries or sha256(source/name) != entries[name]['sha256']:
            raise ValueError('教学材料完整性检查未通过，请恢复材料后再下载')
    destination = (service.root/'deliverables'/version['lesson_id']/
                   f"v{version['number']}-{version_id}"/'teaching-bundles'/(run_id or 'native'))
    source_files = {name:entries[name]['sha256'] for name in MATERIALS}
    receipt_path = destination/'delivery.json'
    if receipt_path.exists():
        return read_receipt(destination, source_files)
    if destination.exists():
        raise ValueError('交付目录存在未完成结果，请检查后恢复')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent/('.staging-'+uuid.uuid4().hex)
    staging.mkdir()
    try:
        for name in MATERIALS:
            shutil.copy2(source/name, staging/name)
        manifest = {key: value for key,value in source_manifest.items() if key not in {'provider','files','presentation_run_id'}}
        manifest['files'] = [{key:entries[name][key] for key in ('name','bytes','sha256')} for name in MATERIALS]
        write_json(staging/'交付清单.json', manifest)
        with zipfile.ZipFile(staging/'交付包.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for name in (*MATERIALS,'交付清单.json'):
                archive.write(staging/name,name)
        receipt = {'version_id':version_id,'run_id':run_id,'directory':str(destination),'valid':True,
                   'source_files':source_files,'manifest':manifest,
                   'pages':sum(len(pages) for pages in manifest['page_mapping'].values()),
                   'zip_sha256':sha256(staging/'交付包.zip')}
        write_json(staging/'delivery.json', receipt)
        if not publish(staging, destination):
            # A concurrent task published the identical immutable bundle first.
            shutil.rmtree(staging, ignore_errors=True)
            return read_receipt(destination, source_files)
        return receipt
    except Exception:
        # Only this newly created staging directory is removed; originals are immutable.
        if staging.resolve().is_relative_to((service.root/'deliverables').resolve()) and staging.name.startswith('.staging-'):
            shutil.rmtree(staging, ignore_errors=True)
        raise
