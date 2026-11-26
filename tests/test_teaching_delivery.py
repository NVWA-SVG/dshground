import json
import zipfile
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches

from teacher_prep.service import Service
from teacher_prep.presentations import Presentations
from teacher_prep.teaching_delivery import prepare_delivery, MATERIALS
from teacher_prep.exporters import sha256

ROOT=Path(__file__).resolve().parents[1]


def setup(root,kind='subject'):
    service=Service(root)
    fixture=lambda name:json.loads((ROOT/'examples'/kind/(name+'.json')).read_text(encoding='utf-8'))
    lesson=service.create(fixture('request'),'create')
    version=service.save_design(lesson['lesson_id'],fixture('design'),'save')
    exported=service.export(version['version_id'])
    return service,version['version_id'],exported


@pytest.mark.parametrize('kind',['subject','theme'])
def test_single_native_teaching_bundle_contains_only_teacher_materials(tmp_path,kind):
    service,version_id,original=setup(tmp_path,kind)
    hashes={entry['name']:sha256(Path(original['directory'])/entry['name']) for entry in original['manifest']['files']}
    result=prepare_delivery(service,version_id)
    assert result['valid'] and result['pages']>0
    with zipfile.ZipFile(Path(result['directory'])/'交付包.zip') as archive:
        assert set(archive.namelist()) == set(MATERIALS)|{'交付清单.json'}
        assert [name for name in archive.namelist() if name.endswith('.pptx')] == ['课件.pptx']
    assert prepare_delivery(service,version_id)==result
    assert hashes=={name:sha256(Path(original['directory'])/name) for name in hashes}


def test_final_ppt_and_zip_select_the_same_validated_external_result(tmp_path):
    service,version_id,original=setup(tmp_path)
    pipeline=Presentations(service);run='task_final'
    prepared=pipeline.prepare(version_id,run)
    pipeline.save_prompt(run,{'style':'图文','pages':[{'page_number':p['number'],'layout':'对照','visual_prompt':'保留原设计的教学插图'} for p in prepared['brief']['pages']]})
    deck=Presentation(Path(original['directory'])/'课件.pptx')
    deck.slides[0].shapes.add_textbox(Inches(.5),Inches(6.5),Inches(4),Inches(.3)).text_frame.text='外部成品验收标记'
    deck.save(pipeline.run_dir(run)/'download.pptx')
    accepted=pipeline.accept(run,'test','remote_final')
    result=prepare_delivery(service,version_id,run)
    source=Path(accepted['directory'])/'课件.pptx';final=Path(result['directory'])/'课件.pptx'
    assert sha256(source)==sha256(final)!=sha256(Path(original['directory'])/'课件.pptx')
    with zipfile.ZipFile(Path(result['directory'])/'交付包.zip') as archive:
        assert archive.read('课件.pptx')==final.read_bytes()
        assert all('提示词' not in name and '视觉方案' not in name and '生成记录' not in name for name in archive.namelist())
        assert archive.read('教案.docx')==(Path(original['directory'])/'教案.docx').read_bytes()
    other=service.create(json.loads((ROOT/'examples'/'theme'/'request.json').read_text(encoding='utf-8')),'another')
    other_version=service.save_design(other['lesson_id'],json.loads((ROOT/'examples'/'theme'/'design.json').read_text(encoding='utf-8')),'another_design')
    service.export(other_version['version_id'])
    with pytest.raises(ValueError,match='其他教学版本'):
        prepare_delivery(service,other_version['version_id'],run)


@pytest.mark.parametrize('name',['课件.pptx','交付包.zip'])
def test_teacher_bundle_tampering_is_detected_without_overwriting(tmp_path,name):
    service,version_id,_=setup(tmp_path)
    result=prepare_delivery(service,version_id)
    changed=Path(result['directory'])/name
    with changed.open('ab') as stream:stream.write(b'tampered')
    before=sha256(changed)
    with pytest.raises(ValueError,match='被修改'):
        prepare_delivery(service,version_id)
    assert sha256(changed)==before
