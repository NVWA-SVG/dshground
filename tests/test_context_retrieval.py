import json
from pathlib import Path

from teacher_prep.retrieval import select_excerpts, source_context
from teacher_prep.service import Service

ROOT=Path(__file__).resolve().parents[1]
def fixture(name):return json.loads((ROOT/'examples/subject'/(name+'.json')).read_text(encoding='utf-8'))


def test_retrieval_keeps_relevant_passage_neighbors_and_source_location():
    rows=[{'knowledge_id':'knowledge_a','chunk':1,'text':'先把一张纸看成一个整体。'},
          {'knowledge_id':'knowledge_a','chunk':2,'text':'分母表示把整体平均分成的份数。'},
          {'knowledge_id':'knowledge_a','chunk':3,'text':'每一份必须同样大，才能用分数表示。'},
          {'knowledge_id':'knowledge_b','chunk':1,'text':'其他单元介绍千米和吨。'}]
    result=select_excerpts(rows,'分母含义',limit=3,max_chars=500)
    assert result['retrieval']['matched'] is True
    assert result['excerpts'][0]['chunk']==2
    assert {row['chunk'] for row in result['excerpts']}=={1,2,3}
    assert all(row['knowledge_id']=='knowledge_a' for row in result['excerpts'])


def test_retrieval_removes_identical_text_but_preserves_different_numbers():
    rows=[{'knowledge_id':'a','chunk':1,'text':'本次独立检测正确人数为12人。'},
          {'knowledge_id':'b','chunk':1,'text':'本次独立检测 正确人数为12人。'},
          {'knowledge_id':'c','chunk':1,'text':'本次独立检测正确人数为13人。'}]
    result=select_excerpts(rows,'独立检测正确人数',max_chars=300)
    assert len(result['excerpts'])==2
    assert any('13人' in row['text'] for row in result['excerpts'])
    assert sum(len(row['text']) for row in result['excerpts'])<=300


def test_long_unbroken_text_is_bounded_and_search_can_find_late_material():
    result=select_excerpts([{'knowledge_id':'a','chunk':1,'text':'日常活动'*3000+'秒针走一圈是60秒。'}],'秒针60秒',max_chars=600,limit=2)
    assert result['retrieval']['matched']
    assert any('60秒' in row['text'] for row in result['excerpts'])
    assert sum(len(row['text']) for row in result['excerpts'])<=600


def test_unmatched_excerpts_are_explicitly_marked_as_fallback():
    result=select_excerpts([{'knowledge_id':'a','chunk':1,'text':'三角形有三条边。'}],'分母',max_chars=100)
    assert result['retrieval']['matched'] is False
    assert result['excerpts'][0]['selection']=='fallback_not_matched'


def test_uploaded_sources_are_retrieved_without_sending_the_entire_document(tmp_path):
    service=Service(tmp_path)
    folder=tmp_path/'inputs';folder.mkdir(exist_ok=True)
    path=folder/'notes.txt';path.write_text(('学校活动安排。\n'*1500)+'分母表示平均分成的份数。',encoding='utf-8')
    source=service.ingest(path,'source')
    result=source_context(service,[source['source_id']],'分母',max_chars=1200)
    assert result['sources'][0]['id']==source['source_id']
    assert 'text' not in result['sources'][0]
    assert any('分母' in row['text'] for row in result['sources'][0]['excerpts'])
    assert result['retrieval']['selected_chars']<=1200


def test_class_memory_carries_question_meaning_and_preserves_record_conditions(tmp_path):
    service=Service(tmp_path);lesson=service.create(fixture('request'),'create')
    version=service.save_design(lesson['lesson_id'],fixture('design'),'design');service.export(version['version_id'])
    service.feedback(version['version_id'],fixture('feedback'),'feedback')
    profile=service.class_profile(lesson['class_id'],query='分母')
    record=profile['evidence'][0];result=record['assessments'][0]
    question=next(q for q in fixture('design')['questions'] if q['id']=='Q2')
    assert record['version_id']==version['version_id']
    assert result['question_prompt']==question['prompt']
    assert result['goals'] and result['knowledge_points']
    assert result['correct']==12 and result['total']==30
    assert result['independent'] is True and result['timing']=='after_class'
    assert result['basis']=='observed' and result['days_after']==0


def test_relevant_old_feedback_can_be_selected_ahead_of_new_unrelated_feedback(tmp_path):
    service=Service(tmp_path);lesson=service.create(fixture('request'),'create')
    version=service.save_design(lesson['lesson_id'],fixture('design'),'design');service.export(version['version_id'])
    old=service.feedback(version['version_id'],{'raw_text':'分母含义仍需通过独立作答核对。','observed_at':'2026-11-01'},'old')
    new=service.feedback(version['version_id'],{'raw_text':'今日教室设备正常，活动按计划开展。','observed_at':'2026-11-09'},'new')
    with service.store.transaction() as db:
        db.execute('UPDATE feedback SET created=? WHERE id=?',('2026-11-01',old['feedback_id']))
        db.execute('UPDATE feedback SET created=? WHERE id=?',('2026-11-09',new['feedback_id']))
    profile=service.class_profile(lesson['class_id'],query='分母',limit=1)
    assert profile['feedback_count']==2
    assert profile['evidence'][0]['feedback_id']==old['feedback_id']
