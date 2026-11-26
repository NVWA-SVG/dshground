import copy,hashlib,json
from pathlib import Path
import pytest
from PIL import Image
from pptx import Presentation
from teacher_prep.presentations import Presentations
from teacher_prep.incremental_presentations import IncrementalPresentations
from teacher_prep.exporters import write_json,sha256
from teacher_prep.teaching_visuals import illustration_prompt
from test_incremental_revision import setup,patch_for,update


IMAGE_CONFIG={'enabled':True,'provider':'siliconflow','base_url':'https://api.siliconflow.cn/v1','model':'Qwen/Qwen-Image',
              'image_size':'1472x1140','num_inference_steps':50,'max_images':4,'proxy_url':''}

def source(tmp_path,image=False,diagram=False):
    service,first,decision=setup(tmp_path)
    if diagram:
        # Use a separate valid source lesson with a real arithmetic diagram candidate.
        from test_teaching_visuals import setup as diagrams
        service,first,ppt,prepared,plan=diagrams(tmp_path/'arithmetic')
        feedback=service.feedback(first['version_id'],{'raw_text':'希望保留竖式，优化提示语。','observed_at':'2026-11-11'},'feedback')
        choice={'teacher_confirmed':True,'purpose':'same_lesson','choices':[{'suggestion_id':'qualitative_feedback','action':'modify','instructions':'优化提示语'}]}
        decision=service.decide(feedback['feedback_id'],choice,'decision')
        run='task_diagram';candidate=prepared['brief']['pages'][0]['diagram_candidates'][0]
        plan['pages'][0].update(diagram_id=candidate['id'],diagram_stage='setup')
    else:
        ppt=Presentations(service);run='task_original';prepared=ppt.prepare(first['version_id'],run)
        plan={'style':'清晰教学分步','palette':'mint','pages':[{'page_number':p['number'],'layout':'分步与留白','layout_kind':'steps',
          'visual_prompt':'突出本页原有教学关系','teaching_goal':'解释本页原有教学内容','student_task':'观察并说明你的想法。',
          'learning_focus':'本页核心关系','asset_reason':'用原生分步表达原有内容','density_note':'留出独立思考'} for p in prepared['brief']['pages']]}
    if image:plan['pages'][0].update(layout_kind='split_right',image_prompt='学生在桌边观察折纸，画面不含文字。',image_reason='辅助理解操作场景')
    ppt.save_prompt(run,plan)
    if image:
        folder=ppt.run_dir(run)/'generated';folder.mkdir()
        prompt=illustration_prompt(prepared['brief']['grade'],plan['style'],plan['pages'][0]['image_prompt'])
        filename=hashlib.sha256(prompt.encode()).hexdigest()+'.png'
        Image.new('RGB',(256,192),'lightblue').save(folder/filename)
        entry={'page_number':1,'file':filename,'sha256':sha256(folder/filename),'model':'Qwen/Qwen-Image'}
        write_json(folder/'index.json',{'version_id':first['version_id'],'design_hash':prepared['brief']['design_hash'],
                  'plan_sha256':sha256(ppt.run_dir(run)/'visual-plan.json'),'images':[entry]})
    result=ppt.compose_local(first['version_id'],run)
    return service,first,decision,ppt,prepared,plan,result

def revise(service,first,decision,ppt,*operations):
    saved=service.revise_patch(decision['decision_id'],patch_for(service,first,decision,*operations),'revision')
    service.export(saved['version_id']);prepared=ppt.prepare(saved['version_id'],'task_revised')
    return saved,prepared,IncrementalPresentations(ppt)

def test_script_only_revision_reuses_all_layouts_but_refreshes_presenter_notes(tmp_path):
    service,first,decision,ppt,prepared,plan,result=source(tmp_path)
    old_deck=Path(result['directory'])/'课件.pptx';original=old_deck.read_bytes()
    script=service.get('version',first['version_id'])['design']['slides'][0]['teacher_script']+'接着请学生独立解释自己的理由。'
    saved,new,pipeline=revise(service,first,decision,ppt,update('slide:S01',teacher_script=script))
    reuse=pipeline.prepare('task_revised')
    assert reuse['pending_pages']==[] and len(reuse['reused_pages'])==len(prepared['brief']['pages'])
    pipeline.save_all('task_revised',IMAGE_CONFIG)
    delivery=ppt.compose_local(saved['version_id'],'task_revised')
    assert Presentation(Path(delivery['directory'])/'课件.pptx').slides[0].notes_slide.notes_text_frame.text.startswith(script)
    assert old_deck.read_bytes()==original

def test_only_changed_page_is_planned_and_partial_plan_cannot_replace_retained_pages(tmp_path):
    service,first,decision,ppt,prepared,plan,_=source(tmp_path)
    before=service.get('version',first['version_id'])['design'];points=before['slides'][0]['points'].copy();points[0]='把同样大的纸平均分，观察每一份。'
    saved,new,pipeline=revise(service,first,decision,ppt,update('slide:S01',points=points))
    reuse=pipeline.prepare('task_revised');assert reuse['pending_pages']==[1]
    partial={**plan,'pages':[plan['pages'][0]]}
    with pytest.raises(ValueError,match='待规划页码'):pipeline.save('task_revised',plan,IMAGE_CONFIG)
    with pytest.raises(ValueError,match='风格'):pipeline.save('task_revised',{**partial,'style':'任意新风格'},IMAGE_CONFIG)
    receipt=pipeline.save('task_revised',partial,IMAGE_CONFIG)
    assert receipt['reused_pages']==len(prepared['brief']['pages'])-1
    assert ppt.compose_local(saved['version_id'],'task_revised')['valid']

def test_existing_illustrations_seed_a_new_verified_local_cache(tmp_path):
    service,first,decision,ppt,prepared,plan,result=source(tmp_path,image=True)
    saved,_,pipeline=revise(service,first,decision,ppt,update('lesson:board_design',board_design='整体 → 平均分 → 每一份。'))
    reuse=pipeline.prepare('task_revised');assert not reuse['pending_pages']
    receipt=pipeline.save_all('task_revised',IMAGE_CONFIG);assert receipt['reused_images']==1
    directory=ppt.run_dir('task_revised');index=json.loads((directory/'generated/index.json').read_text(encoding='utf-8'))
    item=index['images'][0];original=ppt.run_dir('task_original')/'generated'/item['file']
    assert (directory/'generated'/item['file']).read_bytes()==original.read_bytes()
    assert index['version_id']==saved['version_id'] and index['plan_sha256']==sha256(directory/'visual-plan.json')
    ledger=json.loads((directory/'generated/ledger.json').read_text(encoding='utf-8'));assert all(v['status']=='ready' for v in ledger['items'].values())
    assert ppt.compose_local(saved['version_id'],'task_revised')['generated_image_count']==1

def test_page_insertions_rebind_native_diagram_ids_instead_of_reusing_old_page_numbers(tmp_path):
    service,first,decision,ppt,prepared,plan,_=source(tmp_path,diagram=True)
    raw=service.get('version',first['version_id'])['design'];slide=copy.deepcopy(raw['slides'][0]);slide.update(id='S_new',title='先观察',points=['观察已有算式，想想从哪一位开始计算。'],visual=None)
    operations=[{'op':'add','target':'slide:S_new','fields':slide},
                {'op':'reorder','target':'sequence:slides','fields':{'ids':['S_new',*[s['id'] for s in raw['slides']]]}}]
    _,new,pipeline=revise(service,first,decision,ppt,*operations)
    reuse=pipeline.prepare('task_revised');assert reuse['pending_pages']==[1]
    rebound=next(p for p in reuse['reused_pages'] if p['page_number']==2)
    assert rebound['diagram_id']!=plan['pages'][0]['diagram_id']
    assert rebound['diagram_id'] in {d['id'] for d in new['brief']['pages'][1]['diagram_candidates']}

def test_missing_source_illustration_is_not_claimed_as_reusable(tmp_path):
    service,first,decision,ppt,_,_,_=source(tmp_path,image=True)
    _,_,pipeline=revise(service,first,decision,ppt,update('lesson:board_design',board_design='用图说明平均分和分数。'))
    original=json.loads((ppt.run_dir('task_original')/'generated/index.json').read_text(encoding='utf-8'))
    (ppt.run_dir('task_original')/'generated'/original['images'][0]['file']).write_bytes(b'broken')
    reuse=pipeline.prepare('task_revised');assert 1 in reuse['pending_pages']

def test_activity_instruction_changes_replan_only_that_activity(tmp_path):
    service,first,decision,ppt,prepared,_,_=source(tmp_path)
    _,_,pipeline=revise(service,first,decision,ppt,update('activity:A1',instructions='先独立观察纸片，再和同伴解释平均分的依据。'))
    reuse=pipeline.prepare('task_revised')
    assert reuse['pending_pages']==[p['number'] for p in prepared['brief']['pages'] if p['activity_id']=='A1']
