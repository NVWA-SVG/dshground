import json
import hashlib
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from teacher_prep.service import Service
from teacher_prep.presentations import Presentations, normalized, shape_text
from teacher_prep.exporters import sha256, write_json
from teacher_prep.teaching_delivery import prepare_delivery

ROOT = Path(__file__).resolve().parents[1]


def setup(root, layout='split_right', generated=True, question=False):
    fixture = lambda name: json.loads((ROOT/'examples/subject'/(name+'.json')).read_text(encoding='utf-8'))
    service = Service(root)
    lesson = service.create(fixture('request'), 'create')
    version = service.save_design(lesson['lesson_id'], fixture('design'), 'design')
    service.export(version['version_id'])
    pipeline = Presentations(service)
    prepared = pipeline.prepare(version['version_id'], 'task_hybrid')
    original = root/'original.png'; Image.new('RGB', (480,240), '#5599aa').save(original)
    assets = [{'id':'asset_original','kind':'question' if question else 'scene','path':str(original),'caption':'教材原图'}]
    plan = {'style':'清晰课堂插画','palette':'mint','pages':[
        {'page_number':p['number'],'layout':'按教学目标规划','layout_kind':layout,'visual_prompt':'使用教材原图及必要的补充情景',
         'asset_ids':['asset_original'] if p['number']==1 else [],'image_prompt':'辅助场景' if generated and p['number']==1 else ''}
        for p in prepared['brief']['pages']]}
    pipeline.save_prompt('task_hybrid', plan, allowed_assets=assets)
    directory = pipeline.run_dir('task_hybrid')/'generated'; directory.mkdir()
    if generated:
        name = 'a'*64+'.png'; Image.new('RGB',(480,300),'#ddbb66').save(directory/name)
        write_json(directory/'index.json', {'version_id':version['version_id'],'design_hash':prepared['brief']['design_hash'],
          'plan_sha256':sha256(directory.parent/'visual-plan.json'),'images':[{'page_number':1,'file':name,'sha256':sha256(directory/name)}]})
    return service, pipeline, version, prepared, assets


@pytest.mark.parametrize('layout',['split_left','split_right','image_top','comparison','steps'])
def test_agent_layout_combines_original_and_ai_image_without_flattening_text(tmp_path, layout):
    service,pipeline,version,prepared,assets = setup(tmp_path, layout)
    before = sha256(assets[0]['path'])
    result = pipeline.compose_local(version['version_id'], 'task_hybrid', assets=assets)
    assert result['generated_image_count']==1
    deck = Presentation(Path(result['directory'])/'课件.pptx')
    assert len(deck.slides)==len(prepared['brief']['pages'])
    images=[s for s in deck.slides[0].shapes if s.shape_type==MSO_SHAPE_TYPE.PICTURE]
    assert len(images)==2
    assert {s.name for s in images}=={'Textbook original','AI illustration'}
    text=[s for s in deck.slides[0].shapes if s.has_text_frame and s.text.strip()]
    if layout=='split_left':
        body=[s for s in text if any(normalized(point) in normalized(s.text) for point in prepared['brief']['pages'][0]['points'])]
        assert body and max(s.left+s.width for s in images)<min(s.left for s in body)
    for page,slide in zip(prepared['brief']['pages'],deck.slides):
        content=normalized('\n'.join(t for s in slide.shapes for t in shape_text(s)))
        assert all(normalized(value) in content for value in [page['title'],*page['points']])
        assert slide.notes_slide.notes_text_frame.text==page['script']
        assert all(s.left>=0 and s.top>=0 and s.left+s.width<=deck.slide_width+10 and s.top+s.height<=deck.slide_height+10 for s in slide.shapes)
    assert sha256(assets[0]['path'])==before
    receipt=prepare_delivery(service, version['version_id'], 'task_hybrid')
    assert sha256(Path(receipt['directory'])/'课件.pptx')==sha256(Path(result['directory'])/'课件.pptx')


def test_textbook_question_remains_bottom_original_image(tmp_path):
    _,pipeline,version,_,assets=setup(tmp_path, question=True)
    result=pipeline.compose_local(version['version_id'],'task_hybrid',assets=assets)
    deck=Presentation(Path(result['directory'])/'课件.pptx')
    original=next(s for s in deck.slides[0].shapes if s.name=='Textbook original')
    assert original.top>4.9*914400
    assert hashlib.sha256(original.image.blob).hexdigest()==sha256(assets[0]['path'])


def test_incomplete_or_tampered_ai_images_cannot_be_delivered(tmp_path):
    _,pipeline,version,_,assets=setup(tmp_path)
    directory=pipeline.run_dir('task_hybrid')/'generated'
    image=directory/('a'*64+'.png')
    image.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='已被修改'):
        pipeline.compose_local(version['version_id'],'task_hybrid',assets=assets)
    (directory/'index.json').unlink()
    with pytest.raises(ValueError,match='尚未生成完整'):
        pipeline.compose_local(version['version_id'],'task_hybrid',assets=assets)
