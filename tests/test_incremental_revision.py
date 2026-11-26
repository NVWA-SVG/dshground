import copy,json
from pathlib import Path
import pytest
from teacher_prep.models import LessonDesign
from teacher_prep.revision_patches import merge_edits,outline,read_targets
from teacher_prep.service import Service

ROOT=Path(__file__).resolve().parents[1]
def fixture(name):return json.loads((ROOT/'examples/subject'/f'{name}.json').read_text(encoding='utf-8'))
def edits(*operations):return {'parent_version_id':'version_parent','operations':list(operations)}
def update(target,**fields):return {'op':'update','target':target,'fields':fields}

def setup(tmp_path):
    service=Service(tmp_path);lesson=service.create(fixture('request'),'request');first=service.save_design(lesson['lesson_id'],fixture('design'),'design');service.export(first['version_id'])
    feedback=service.feedback(first['version_id'],{'raw_text':'分母含义会混淆，希望增加对比。','observed_at':'2026-11-11'},'feedback')
    choice=fixture('decision');choice['choices']=[{'suggestion_id':'qualitative_feedback','action':'modify','instructions':'调整分母讲解与独立练习。'}];choice['protected_ids']=[]
    decision=service.decide(feedback['feedback_id'],choice,'decision')
    return service,first,decision

def patch_for(service,first,decision,*ops):
    patch={'parent_version_id':first['version_id'],'operations':list(ops)}
    diff=service.preview_revision_patch(decision['decision_id'],patch)
    return {**patch,'summary':'根据反馈作局部调整','change_links':[{'target':c['target'],'suggestion_ids':['qualitative_feedback'],'explanation':'教师确认后补充分母解释'} for c in diff['changes']]}

def test_partial_fields_are_merged_and_unmodified_script_bytes_are_preserved(tmp_path):
    service,first,decision=setup(tmp_path);before=service.get('version',first['version_id'])['design']
    script=before['slides'][0]['teacher_script']+'请再用一句话说明每份的含义。'
    patch=patch_for(service,first,decision,update('slide:'+before['slides'][0]['id'],teacher_script=script))
    result=service.revise_patch(decision['decision_id'],patch,'patch')
    after=service.get('version',result['version_id'])['design']
    expected=copy.deepcopy(before);expected['slides'][0]['teacher_script']=script
    assert after==expected and len(result['changes'])==1
    assert result['changes'][0]['evidence_ids']==['qualitative:raw_text']
    assert service.revise_patch(decision['decision_id'],patch,'patch')['version_id']==result['version_id']
    service.export(result['version_id']);assert service.check(result['version_id'])['valid']
    assert service.get('version',first['version_id'])['design']==before

@pytest.mark.parametrize('mutation',['parent','missing','rename','unknown','duplicate','time','reference','empty'])
def test_bad_edits_never_create_a_new_version(tmp_path,mutation):
    service,first,decision=setup(tmp_path);op=update('slide:S01',teacher_script='请观察图形，解释平均分的含义，再独立说一说。')
    raw={'parent_version_id':first['version_id'],'operations':[op],'summary':'改动','change_links':[{'target':'slide:S01','suggestion_ids':['qualitative_feedback'],'explanation':'调整'}]}
    if mutation=='parent':raw['parent_version_id']='version_wrong'
    if mutation=='missing':op['target']='slide:MISSING'
    if mutation=='rename':op['fields']={'id':'NEW'}
    if mutation=='unknown':op['fields']={'made_up':'value'}
    if mutation=='duplicate':raw['operations'].append(copy.deepcopy(op))
    if mutation=='time':op.update(target='activity:A1',fields={'duration_minutes':99})
    if mutation=='reference':op['fields']={'activity_id':'MISSING'}
    if mutation=='empty':op['fields']={'teacher_script':fixture('design')['slides'][0]['teacher_script']}
    with pytest.raises(ValueError):service.revise_patch(decision['decision_id'],raw,'invalid')
    assert len(service.list_lessons()['lessons'][0]['versions'])==1

def test_goals_protection_and_rejected_evidence_remain_enforced(tmp_path):
    service,first,decision=setup(tmp_path)
    patch=patch_for(service,first,decision,update('goal:G1',description='更换目标'))
    with pytest.raises(ValueError,match='目标'):service.revise_patch(decision['decision_id'],patch,'goals')
    patch=patch_for(service,first,decision,update('lesson:board_design',board_design='分数：整体、平均分、几分之一；用图对比。'))
    patch['change_links'][0]['suggestion_ids']=['collect_evidence']
    with pytest.raises(ValueError,match='拒绝|未采纳'):service.revise_patch(decision['decision_id'],patch,'rejected')

def test_add_remove_and_reorder_preserve_references_and_require_exact_orders():
    raw=fixture('design');slide=copy.deepcopy(raw['slides'][0]);slide['id']='S_extra'
    patch=edits({'op':'add','target':'slide:S_extra','fields':slide,'after_id':raw['slides'][0]['id']})
    after=merge_edits(raw,patch,'version_parent')
    assert after.slides[1].id=='S_extra'
    removed=merge_edits(after.model_dump(mode='json'),edits({'op':'remove','target':'slide:S_extra'}),'version_parent')
    assert removed==LessonDesign.model_validate(raw)
    with pytest.raises(ValueError,match='全部ID'):merge_edits(raw,edits({'op':'reorder','target':'sequence:slides','fields':{'ids':['S01']}}),'version_parent')

def test_outline_omits_long_scripts_but_on_demand_reads_are_exact():
    raw=fixture('design');summary=outline(raw)
    assert all('teacher_script' not in s and 'points' not in s for s in summary['slides'])
    assert all('instructions' not in a for a in summary['activities'])
    result=read_targets(raw,['slide:S01','lesson:board_design'])
    assert result[0]['content']==LessonDesign.model_validate(raw).slides[0].model_dump()
    assert result[1]['content']=={'board_design':raw['board_design']}
    with pytest.raises(ValueError):read_targets(raw,['slide:MISSING'])
    assert len(json.dumps(summary,ensure_ascii=False))<len(json.dumps(LessonDesign.model_validate(raw).model_dump(),ensure_ascii=False))
