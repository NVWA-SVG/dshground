import copy
import json
from pathlib import Path

import pytest

from teacher_prep.service import Service

ROOT = Path(__file__).resolve().parents[1]


def fixture(name):
    return json.loads((ROOT/'examples/subject'/f'{name}.json').read_text(encoding='utf-8'))


def setup_lesson(root, key='first', class_id=None):
    service = Service(root)
    request = fixture('request')
    if class_id:
        request['class_id'] = class_id
    lesson = service.create(request, key)
    version = service.save_design(lesson['lesson_id'], fixture('design'), key)
    exported = service.export(version['version_id'])
    return service, lesson, version, exported


def test_record_updates_same_class_context_without_revision_or_changed_deliverables(tmp_path):
    service, lesson, first, exported = setup_lesson(tmp_path)
    ppt = Path(exported['directory'])/'课件.pptx'
    original = ppt.read_bytes()
    payload = {'record_mode':'future_teaching','raw_text':'课堂参与很积极。部分学生独立作答仍混淆分子和分母。',
               'next_lesson_focus':'下一课先独立辨析，再按作答提供分步提示。','observed_at':'2026-11-11'}
    saved = service.feedback(first['version_id'], payload, 'record')
    assert saved['analysis']['suggestions'] == []
    assert saved['analysis']['feedback_summary']['status'] == 'record_only'
    assert saved['analysis']['participation'] == [] and saved['analysis']['assessment_results'] == []
    assert all(row['actual_minutes'] is None for row in saved['analysis']['density'])
    profile = service.class_profile(lesson['class_id'], query='分母')
    assert profile['evidence'][0]['next_lesson_focus'] == payload['next_lesson_focus']
    assert {row['category'] for row in profile['characteristics']} >= {'participation','learning'}
    assert all(row['sources'][0]['feedback_id'] == saved['feedback_id'] for row in profile['characteristics'])
    assert all(row['status'] == 'lesson_evidence_to_verify' for row in profile['characteristics'])
    with pytest.raises(ValueError, match='新建备课'):
        service.decide(saved['feedback_id'], fixture('decision'), 'not_a_revision')
    request = {**fixture('request'), 'class_id':lesson['class_id'], 'title':'下一课：同分母分数的比较'}
    next_lesson = service.create(request, 'next')
    assert next_lesson['class_id'] == lesson['class_id']
    assert service.lesson_context(next_lesson['lesson_id'])['versions'] == []
    assert len(service.lesson_context(lesson['lesson_id'])['versions']) == 1
    assert ppt.read_bytes() == original
    other = service.create(fixture('request'), 'different_class')
    assert service.class_profile(other['class_id'])['characteristics'] == []


def test_correction_supersedes_evidence_but_keeps_original_record_and_idempotency(tmp_path):
    service, lesson, version, _ = setup_lesson(tmp_path)
    payload = {'record_mode':'future_teaching','raw_text':'学生还不会辨析分母。','observed_at':'2026-11-11'}
    first = service.feedback(version['version_id'],payload,'record')
    correction = {**payload, 'raw_text':'更正：学生能独立辨析分母，仍需检查迁移。',
                  'supersedes_feedback_id':first['feedback_id']}
    second = service.feedback(version['version_id'],correction,'correction')
    assert service.feedback(version['version_id'],copy.deepcopy(correction),'correction')['feedback_id'] == second['feedback_id']
    profile = service.class_profile(lesson['class_id'])
    assert profile['feedback_count'] == 1 and profile['historical_record_count'] == 2
    assert [r['feedback_id'] for r in profile['evidence']] == [second['feedback_id']]
    assert service.get('feedback',first['feedback_id'])['payload']['raw_text'] == payload['raw_text']
    with pytest.raises(ValueError, match='已有更新'):
        service.feedback(version['version_id'],correction,'stale_correction')
    _, _, other, _ = setup_lesson(tmp_path, 'second', lesson['class_id'])
    with pytest.raises(ValueError, match='同一材料版本'):
        service.feedback(other['version_id'],correction,'different_version')


def test_structured_features_keep_estimate_and_completion_conditions(tmp_path):
    service, lesson, version, _ = setup_lesson(tmp_path)
    payload = fixture('feedback')
    payload['record_mode'] = 'future_teaching'
    payload['assessments'][0]['basis'] = 'estimated'
    payload['assessments'][0]['independent'] = False
    saved = service.feedback(version['version_id'],payload,'record')
    profile = service.class_profile(lesson['class_id'])
    records = profile['evidence'][0]['assessments']
    assert records[0]['basis'] == 'estimated' and records[0]['independent'] is False
    assert profile['evidence'][0]['activity_pacing'][0]['actual_minutes'] == payload['timings'][0]['actual_minutes']
    feature = next(row for row in profile['characteristics'] if any(source['ref'] == 'assessment:1' for source in row['sources']))
    assert '非独立完成' in feature['text']
    assert feature['sources'][0]['basis'] == 'estimated'
    assert saved['analysis']['overall_score'] is None and saved['analysis']['causal_conclusion'] is None
