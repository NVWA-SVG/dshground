"""Teacher text must drive a reviewable revision without fabricated statistics."""
import copy
import json
from pathlib import Path

import pytest

from teacher_prep.analysis import analyze
from teacher_prep.models import Feedback, LessonDesign
from teacher_prep.service import Service

ROOT=Path(__file__).resolve().parents[1]

def fixture(name):
    return json.loads((ROOT/'examples'/'subject'/f'{name}.json').read_text(encoding='utf-8'))

def simple(**changes):
    return {'raw_text':'学生把分母和分子混淆，折纸活动参与得很好。','observed_at':'2026-11-11',**changes}

def report(**changes):
    return analyze(LessonDesign.model_validate(fixture('design')),Feedback.model_validate(simple(**changes)))

def test_text_only_feedback_proposes_adjustments_without_inventing_measurements():
    result=report()
    proposal=result['suggestions'][0]
    assert proposal['id']=='qualitative_feedback'
    assert proposal['observation']==simple()['raw_text']
    assert proposal['evidence_ids']==['qualitative:raw_text']
    assert any('分步' in action for action in proposal['suggested_actions'])
    assert not result['participation'] and not result['assessment_results']
    assert set(result['missing'])=={'actual_timing','participation','assessment'}
    assert all(row['actual_minutes'] is None and row['actual_points_per_activity_minute'] is None for row in result['density'])
    assert result['overall_score'] is None and result['causal_conclusion'] is None
    assert result['feedback_summary']['measurement_inference'] is False

def test_explicit_request_is_preserved_and_does_not_change_raw_observation():
    request='减少连续讲解，增加分母含义的对比练习。'
    result=report(requested_changes=request,teacher_keep=['保留折纸活动'])
    proposal=result['suggestions'][0]
    assert proposal['suggested_actions']==[request]
    assert 'teacher_request:requested_changes' in proposal['evidence_ids']
    assert proposal['observation']==simple()['raw_text']
    assert result['teacher_keep']==['保留折纸活动']
    assert set(proposal['target_activity_ids']) <= {a['id'] for a in fixture('design')['activities']}

@pytest.mark.parametrize('raw,adjustment',[
    ('这节课很顺利，学生都能独立完成任务。',''),
    ('本次先记录，保持原样。',''),
    ('学生还会混淆分母。','暂不修改，先记录'),
    ('学生不再混淆，没有明显困难，效果很好。',''),
])
def test_positive_or_record_only_feedback_does_not_force_revision(raw,adjustment):
    result=report(raw_text=raw,requested_changes=adjustment)
    assert result['feedback_summary']['status']=='record_only'
    assert not any(s['id']=='qualitative_feedback' for s in result['suggestions'])

def test_positive_report_with_a_specific_request_can_still_be_improved():
    result=report(raw_text='课堂很顺利，学生都能独立完成。',requested_changes='增加一道拓展练习。')
    assert result['suggestions'][0]['suggested_actions']==['增加一道拓展练习。']

def test_explicit_new_request_after_keep_statement_is_not_suppressed():
    result=report(requested_changes='保持现有折纸活动，增加一道对比练习。')
    assert result['feedback_summary']['status']=='proposal_ready'

def test_local_adjustment_request_with_keep_rest_does_not_become_record_only():
    result=report(requested_changes='只调整指定内容，其他保持不变。')
    assert result['feedback_summary']['status']=='proposal_ready'

def test_legacy_payload_idempotency_and_simple_revision_audit(tmp_path):
    service=Service(tmp_path)
    lesson=service.create(fixture('request'),'request')
    first=service.save_design(lesson['lesson_id'],fixture('design'),'design')
    original=service.export(first['version_id'])
    old_ppt=(Path(original['directory'])/'课件.pptx').read_bytes()
    payload=simple(requested_changes='围绕分母含义补充对比与独立练习。',teacher_keep=['保留折纸导入'])
    saved=service.feedback(first['version_id'],payload,'feedback')
    record=service.get('feedback',saved['feedback_id'])
    assert record['payload']['requested_changes']==payload['requested_changes']
    assert record['payload']['observations']==[]
    decision=fixture('decision')
    decision['keep_requirements']=payload['teacher_keep']
    decision['choices']=[{'suggestion_id':'qualitative_feedback','action':'modify','instructions':payload['requested_changes']}]
    chosen=service.decide(saved['feedback_id'],decision,'decision')
    revision=fixture('revision')
    for link in revision['change_links']:link['suggestion_ids']=['qualitative_feedback']
    second=service.revise(chosen['decision_id'],revision,'revision')
    service.export(second['version_id'])
    assert second['parent_id']==first['version_id'] and second['number']==2
    assert second['changes']
    assert all('qualitative:raw_text' in c['evidence_ids'] and 'teacher_request:requested_changes' in c['evidence_ids'] for c in second['changes'])
    assert all(c['evidence_summary'] and c['verify_next_time'] for c in second['changes'])
    assert (Path(original['directory'])/'课件.pptx').read_bytes()==old_ppt
    assert service.get('feedback',saved['feedback_id'])['payload']['raw_text']==payload['raw_text']
    profile=service.class_profile(lesson['class_id'],query='分母')
    assert profile['evidence'][0]['requested_changes']==payload['requested_changes']
    assert not profile['evidence'][0]['assessments']
    # An empty new optional field canonicalizes exactly like the former schema.
    legacy=fixture('feedback')
    original_feedback=service.feedback(first['version_id'],legacy,'legacy')
    replay=service.feedback(first['version_id'],{**copy.deepcopy(legacy),'requested_changes':''},'legacy')
    assert replay['feedback_id']==original_feedback['feedback_id']
    assert 'requested_changes' not in service.get('feedback',replay['feedback_id'])['payload']
