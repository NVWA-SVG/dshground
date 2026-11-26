import json,random
from pathlib import Path
import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from teacher_prep.models import LessonDesign
from teacher_prep.service import Service
from teacher_prep.presentations import Presentations,PptVisualPlan
from teacher_prep.exporters import pagination,sha256,write_json
from teacher_prep.teaching_visuals import arithmetic,enrich_pages
from teacher_prep.teaching_delivery import prepare_delivery

ROOT=Path(__file__).resolve().parents[1]
def fixture(name):return json.loads((ROOT/'examples/subject'/(name+'.json')).read_text(encoding='utf-8'))

def setup(tmp_path,expression='301 - 145',answer=156):
    request=fixture('request');raw=fixture('design')
    addition='+' in expression
    page=raw['slides'][0];page.update(title=('进位加法：' if addition else '连续退位：')+expression,points=[expression+' 的竖式计算','相同数位对齐，从个位算起','满十进一' if addition else '遇到不够减时进行连续退位','结果：'+str(answer)],visual=None)
    service=Service(tmp_path);lesson=service.create(request,'lesson');version=service.save_design(lesson['lesson_id'],raw,'design');service.export(version['version_id'])
    pipeline=Presentations(service);prepared=pipeline.prepare(version['version_id'],'task_diagram')
    plan={'style':'清晰数学图解','palette':'mint','pages':[{'page_number':p['number'],'layout':'左文右侧精确图解','layout_kind':'split_right','visual_prompt':'突出本页任务',
          'teaching_goal':'解释本页的关键步骤','student_task':'观察数位变化，说说你的发现。','learning_focus':'数位对齐与进退位标记','asset_reason':'使用本地准确计算的图解','density_note':'保留原文，留出观察和思考空间'} for p in prepared['brief']['pages']]}
    return service,version,pipeline,prepared,plan


def test_regrouping_arithmetic_invariants_with_zero_chains_and_carries():
    calc=arithmetic(301,145,'-');assert calc['adjusted']==[2,9,11] and calc['result']==156
    assert arithmetic(1000,1,'-')['adjusted']==[0,9,9,10]
    assert arithmetic(271,31,'+')['carry_marks']==[1,0,0]
    rng=random.Random(42)
    for _ in range(500):
        a,b=sorted([rng.randrange(10000),rng.randrange(10000)],reverse=True);calc=arithmetic(a,b,'-')
        assert all(top>=bottom for top,bottom in zip(calc['adjusted'],calc['bottom']))
        assert sum((v-calc['bottom'][i])*10**(calc['width']-i-1) for i,v in enumerate(calc['adjusted']))==a-b
    for args in [(True,1,'+'),(1,2,'-'),(10000,1,'+'),(1,1,'*')]:
        with pytest.raises(ValueError):arithmetic(*args)


@pytest.mark.parametrize('kind',['column_calculation','number_line'])
def test_real_native_export_contains_editable_correct_diagram_and_full_source_text(tmp_path,kind):
    service,version,pipeline,prepared,plan=setup(tmp_path)
    candidate=next(d for d in prepared['brief']['pages'][0]['diagram_candidates'] if d['kind']==kind and d.get('a')==301)
    plan['pages'][0].update(diagram_id=candidate['id'],diagram_stage='complete')
    original=sha256(Path(service.get('version',version['version_id'])['manifest']['directory'])/'课件.pptx')
    pipeline.save_prompt('task_diagram',plan);result=pipeline.compose_local(version['version_id'],'task_diagram')
    deck=Presentation(Path(result['directory'])/'课件.pptx');first=deck.slides[0]
    native=[s for s in first.shapes if s.name.startswith('Teaching '+kind)];assert len(native)>8
    assert not any(s.shape_type==MSO_SHAPE_TYPE.PICTURE for s in native)
    text=' '.join(s.text for s in native if s.has_text_frame)
    if kind=='column_calculation':assert all(v in text.split() for v in ('2','9','11','1','5','6'))
    else:assert '156' in text and '301' in text
    assert first.notes_slide.notes_text_frame.text.startswith(prepared['brief']['pages'][0]['script'])
    assert '观察重点' in first.notes_slide.notes_text_frame.text
    assert any(s.has_text_frame and '观察 / 操作：' in s.text for s in first.shapes)
    assert all(any(s.has_text_frame and '第1版' in s.text for s in slide.shapes) for slide in deck.slides)
    assert result['native_diagram_count']==1 and result['generated_image_count']==0
    assert sha256(Path(service.get('version',version['version_id'])['manifest']['directory'])/'课件.pptx')==original
    assert prepare_delivery(service,version['version_id'],'task_diagram')['valid']


def test_exercise_cannot_reveal_full_answer_from_teacher_script(tmp_path):
    _,_,pipeline,prepared,plan=setup(tmp_path)
    # Remove the visible answer in a separate valid lesson, retaining it in the script.
    raw=fixture('design');raw['slides'][0].update(title='独立计算301－145',points=['301－145＝？','从个位算起，相同数位对齐'],teacher_script='请先独立计算，教师参考答案为156，暂时不向学生展示。',visual=None)
    source=LessonDesign.model_validate(raw);page=enrich_pages(source,pagination(source)[0])[0]
    candidate=next(d for d in page['diagram_candidates'] if d['kind']=='column_calculation')
    assert candidate['allowed_stages']==['setup']
    from teacher_prep.teaching_visuals import validate_page_plan
    visual=PptVisualPlan.model_validate({'style':'验收','pages':[{'page_number':1,'layout':'左右','visual_prompt':'观察','diagram_id':candidate['id'],'diagram_stage':'complete'}]}).pages[0]
    with pytest.raises(ValueError,match='提前显示答案'):validate_page_plan(page,visual)


def test_prompt_md_and_image_manifest_include_actual_effective_image_request(tmp_path):
    _,_,pipeline,prepared,plan=setup(tmp_path)
    plan['pages'][0].update(image_prompt='白底卡通学生抬手交流，不含文字。',image_reason='作为观察交流的情境辅助')
    pipeline.save_prompt('task_diagram',plan);directory=pipeline.run_dir('task_diagram')
    md=(directory/'课件提示词.md').read_text(encoding='utf-8');manifest=json.loads((directory/'image-requests.json').read_text(encoding='utf-8'))
    request=manifest['requests'][0]
    assert request['effective_prompt'] in md and request['raw_prompt']==plan['pages'][0]['image_prompt']
    assert manifest['plan_sha256']==sha256(directory/'visual-plan.json')
    for text in ['布局类型：split_right','配色：mint','本页教学任务','观察重点','素材选择理由','内容与节奏安排','生图教学用途']:assert text in md


def test_unknown_diagram_new_task_numbers_and_decorations_are_rejected(tmp_path):
    _,_,pipeline,prepared,plan=setup(tmp_path)
    plan['pages'][0]['diagram_id']='invented'
    with pytest.raises(ValueError,match='候选目录'):pipeline.save_prompt('task_diagram',plan)
    candidate=next(d for d in prepared['brief']['pages'][0]['diagram_candidates'] if d['kind']=='column_calculation')
    plan['pages'][0].update(diagram_id=candidate['id'],image_prompt='装饰图',image_reason='验收不应与精确图解叠加')
    with pytest.raises(ValueError,match='留空asset_ids'):pipeline.save_prompt('task_diagram',plan)
    plan['pages'][0].update(diagram_id='',image_prompt='',student_task='计算9999与8888的和。')
    with pytest.raises(ValueError,match='原页面没有的数值'):pipeline.save_prompt('task_diagram',plan)


def test_context_distinguishes_activity_density_and_page_text_volume(tmp_path):
    _,_,_,prepared,_=setup(tmp_path)
    context=prepared['brief']['pages'][0]['teaching_context']
    assert context['activity_planned_density']==round(context['activity_new_knowledge_count']/context['activity_minutes'],4)
    assert context['visible_characters']>0 and context['point_count']==4
    assert '认知负荷' in context['interpretation']


def test_legacy_saved_prompt_bytes_and_brief_can_be_reused_without_invalidating_images(tmp_path):
    _,version,pipeline,prepared,plan=setup(tmp_path)
    pipeline.save_prompt('task_diagram',plan);directory=pipeline.run_dir('task_diagram');saved=json.loads((directory/'visual-plan.json').read_text(encoding='utf-8'))
    for page in saved['pages']:
        for key in ('teaching_goal','student_task','learning_focus','asset_reason','density_note','diagram_id','diagram_stage'):page.pop(key,None)
    write_json(directory/'visual-plan.json',saved)
    old_brief=json.loads((directory/'brief.json').read_text(encoding='utf-8'));old_brief.pop('planning_version')
    for page in old_brief['pages']:page.pop('teaching_context');page.pop('diagram_candidates')
    write_json(directory/'brief.json',old_brief)
    before=(directory/'visual-plan.json').read_bytes();md=(directory/'课件提示词.md').read_bytes()
    pipeline.prepare(version['version_id'],'task_diagram');pipeline.save_prompt('task_diagram',saved)
    assert (directory/'visual-plan.json').read_bytes()==before and (directory/'课件提示词.md').read_bytes()==md


def test_dense_native_plan_is_rejected_before_any_image_or_export_work(tmp_path):
    from teacher_prep.teaching_visuals import validate_readability
    page={'number':1,'points':['原文需要完整保留，不能为了配图删掉教学步骤。'*5]*5}
    visual=PptVisualPlan.model_validate({'style':'验收','pages':[{'page_number':1,'layout':'左右','visual_prompt':'观察','teaching_goal':'观察关系','diagram_id':'candidate','student_task':'观察关系'}]}).pages[0]
    with pytest.raises(ValueError,match='18pt以下'):validate_readability(page,visual,[])


@pytest.mark.parametrize('expression,answer',[('271 + 31',302),('9999 + 9999',19998),('1000 - 1',999),('1000 - 276',724)])
def test_edge_case_native_columns_remain_inside_slide_and_body_stays_readable(tmp_path,expression,answer):
    _,version,pipeline,prepared,plan=setup(tmp_path,expression,answer)
    candidate=next(d for d in prepared['brief']['pages'][0]['diagram_candidates'] if d['kind']=='column_calculation')
    plan['pages'][0].update(diagram_id=candidate['id'],diagram_stage='complete')
    pipeline.save_prompt('task_diagram',plan);result=pipeline.compose_local(version['version_id'],'task_diagram')
    deck=Presentation(Path(result['directory'])/'课件.pptx');slide=deck.slides[0]
    assert all(0<=s.left and 0<=s.top and s.left+s.width<=deck.slide_width+10 and s.top+s.height<=deck.slide_height+10 for s in slide.shapes)
    for point in prepared['brief']['pages'][0]['points']:
        body=next(s for s in slide.shapes if s.has_text_frame and point.replace(' ','') in s.text.replace('\n','').replace(' ',''))
        assert all(r.font.size.pt>=18 for p in body.text_frame.paragraphs for r in p.runs)


@pytest.mark.parametrize('kind',['steps','comparison','concepts'])
def test_source_relationship_labels_become_editable_native_cards(tmp_path,kind):
    _,version,pipeline,prepared,plan=setup(tmp_path)
    candidate=next(d for p in prepared['brief']['pages'] for d in p['diagram_candidates'] if d['kind']=='concepts' or d['kind']=='steps' or d['kind']=='comparison')
    index=next(i for i,p in enumerate(prepared['brief']['pages']) if candidate in p['diagram_candidates'])
    # This fixture exercises the source-declared kind; separate renderer kinds are checked below.
    from teacher_prep.teaching_diagrams import draw_diagram
    deck=Presentation();deck.slide_width=12192000;deck.slide_height=6858000;slide=deck.slides.add_slide(deck.slide_layouts[6])
    draw_diagram(slide,{'kind':kind,'labels':['明确任务','独立观察','解释理由','交流检查']},'setup',(7.9,2.1,4.7,4.0),'327665','E5EFEB')
    file=tmp_path/'cards.pptx';deck.save(file);actual=Presentation(file)
    text=' '.join(s.text for s in actual.slides[0].shapes if s.has_text_frame)
    assert all(v in text for v in ('明确任务','独立观察','解释理由','交流检查'))


@pytest.mark.parametrize('text',['1/2 + 1/2','1.2 + 3.4','-3 + 5','2026-11-10','301 - 145 = 166'])
def test_unsupported_or_inconsistent_math_is_not_reinterpreted_as_integer_diagram(text):
    raw=fixture('design');raw['slides'][0].update(title='观察原有内容',points=[text],visual=None)
    design=LessonDesign.model_validate(raw);page=enrich_pages(design,pagination(design)[0])[0]
    assert page['diagram_candidates']==[]
