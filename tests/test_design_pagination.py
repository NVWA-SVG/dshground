import json
from pathlib import Path
import pytest
from docx import Document
from pptx import Presentation
from teacher_prep.models import LessonDesign
from teacher_prep.exporters import pagination
from teacher_prep.service import Service
from teacher_prep.presentations import Presentations

ROOT=Path(__file__).resolve().parents[1]


def example(name):return json.loads((ROOT/'examples/subject'/f'{name}.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('count',[6,8,12])
def test_logical_points_paginate_without_losing_steps_or_script(count):
    raw=example('design');raw['slides'][1]['points']=[f'第{i+1}步：保留这一步的完整内容。' for i in range(count)]
    design=LessonDesign.model_validate(raw);pages,mapping=pagination(design)
    source=design.slides[1];parts=[page for page in pages if page['slide_id']==source.id]
    assert len(parts)>=2 and all(len(page['points'])<=5 for page in pages)
    assert [point for page in parts for point in page['points']]==raw['slides'][1]['points']
    assert all(page['script']==source.teacher_script for page in parts)
    assert mapping[source.id]==[page['number'] for page in parts]


def test_logical_page_still_has_a_bound_and_does_not_accept_too_long_points():
    raw=example('design');raw['slides'][0]['points']=['完整要点']*13
    with pytest.raises(ValueError,match='at most 12'):LessonDesign.model_validate(raw)
    raw['slides'][0]['points']=['长'*111]
    with pytest.raises(ValueError,match='110'):LessonDesign.model_validate(raw)


def test_full_export_keeps_all_steps_and_one_shared_script_section(tmp_path):
    service=Service(tmp_path);request=example('request');lesson=service.create(request,'create')
    raw=example('design');source=raw['slides'][1];source['points']=[f'计算步骤{i+1}：按数位进行运算。' for i in range(8)]
    saved=service.save_design(lesson['lesson_id'],raw,'design');export=service.export(saved['version_id'])
    assert service.check(saved['version_id'])['valid']
    directory=Path(export['directory']);deck=Presentation(directory/'课件.pptx')
    content='\n'.join(shape.text for slide in deck.slides for shape in slide.shapes if shape.has_text_frame)
    assert all(point in content for point in source['points'])
    script=Document(directory/'逐字稿.docx');script_lines=[paragraph.text for paragraph in script.paragraphs]
    assert script_lines.count(source['teacher_script'])==1
    prepared=Presentations(service).prepare(saved['version_id'],'task_overflow')
    assert len(prepared['brief']['pages'])==len(deck.slides)
    assert all(len(page['points'])<=5 for page in prepared['brief']['pages'])


def test_missing_introduction_error_identifies_the_actual_knowledge_point():
    raw=example('design');new=next(point for point in raw['knowledge_points'] if point['is_new'])
    for activity in raw['activities']:activity['introduces']=[key for key in activity['introduces'] if key!=new['id']]
    with pytest.raises(ValueError,match='缺少首次引入：'+new['id']):LessonDesign.model_validate(raw)


def test_two_line_textbook_caption_does_not_fail_as_if_slide_points_are_too_long(tmp_path):
    from PIL import Image
    from teacher_prep.hybrid_slides import make_hybrid_pptx
    design=LessonDesign.model_validate(example('design'));pages,_=pagination(design)
    image=tmp_path/'textbook.png';Image.new('RGB',(300,180),'#98cab5').save(image)
    caption='这是一段需要换成两行显示的教材图片说明，用来确保原有图注不会被错误地当成课件正文溢出。'
    output=tmp_path/'caption.pptx'
    make_hybrid_pptx(design,output,'测试',pages,{1:[{'path':str(image),'caption':caption,'kind':'scene'}]},
        {'pages':[{'page_number':page['number'],'layout_kind':'split_right'} for page in pages]})
    deck=Presentation(output);text=''.join(shape.text for shape in deck.slides[0].shapes if shape.has_text_frame).replace('\n','')
    assert caption[:50] in text
    assert all(shape.left+shape.width<=deck.slide_width+10 and shape.top+shape.height<=deck.slide_height+10 for shape in deck.slides[0].shapes)
