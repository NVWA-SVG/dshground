"""Index teacher evidence for subsequent lessons; never infer a pupil score."""
from __future__ import annotations

import re

CATEGORIES = {
    'participation': ('课堂参与线索', re.compile(r'参与|发言|举手|交流|讨论|活跃|沉默|回答|积极|不敢')),
    'learning': ('学习表现线索', re.compile(r'掌握|理解|混淆|分不清|不会|不懂|会了|能|正确|错误|做错|作业|作答|检测|练习|解释|完成|记住|遗忘')),
    'pacing': ('节奏与时间线索', re.compile(r'时间|用时|超时|拖堂|太快|太慢|太多|太少|跟不上|来不及|思考|密集|密度')),
    'practices': ('教学做法与偏好', None),
}


def summarize_record(design, feedback):
    """Keep excerpts and their stated basis, including negative observations."""
    categories = {name: [] for name in CATEGORIES}

    def add(kind, text, ref, basis='teacher_report'):
        if text and not any(row['text'] == text for row in categories[kind]):
            categories[kind].append({'text': text[:600], 'truncated': len(text) > 600,
                                     'ref': ref, 'basis': basis})

    def classify(text, ref, basis='teacher_report'):
        for clause in filter(None, (part.strip() for part in re.split(r'[。！？；;\n]+', text))):
            for kind, (_, pattern) in CATEGORIES.items():
                if pattern and pattern.search(clause):
                    add(kind, clause, ref, basis)

    classify(feedback.raw_text, 'raw_text')
    for item in feedback.observations:
        classify(item.text, 'observation:' + item.id, item.basis)
    activities = {row.id: row for row in design.activities}
    questions = {row.id: row for row in design.questions}
    for index, item in enumerate(feedback.participation, 1):
        if item.participating is not None or item.eligible is not None:
            add('participation', f"{activities[item.activity_id].title}：去重参与 {item.participating if item.participating is not None else '未知'} / 应参与 {item.eligible if item.eligible is not None else '未知'} 人。",
                f'participation:{index}', item.basis)
    timing_names = {'in_class': '课内', 'after_class': '课后', 'delayed': '延迟检测'}
    for index, item in enumerate(feedback.assessments, 1):
        question = questions[item.question_id]
        count = item.met_criteria if question.mode == 'rubric' else item.correct
        count_label = '达到评价要点' if question.mode == 'rubric' else '正确'
        conditions = '独立完成' if item.independent is True else '非独立完成' if item.independent is False else '独立条件未确认'
        when = timing_names[item.timing] + (f'（距授课 {item.days_after} 天）' if item.days_after is not None else '')
        text = f"{question.prompt[:160]}：{count_label} {count if count is not None else '未知'} / 作答 {item.total if item.total is not None else '未知'} 人；{when}；{conditions}。"
        if item.errors:
            text += '记录的错误：' + '；'.join(item.errors)
        add('learning', text, f'assessment:{index}', item.basis)
    for index, item in enumerate(feedback.timings, 1):
        activity = activities[item.activity_id]
        add('pacing', f'{activity.title}：计划 {activity.duration_minutes:g} 分钟，记录实际 {item.actual_minutes:g} 分钟。',
            f'timing:{index}', item.basis)
    for index, text in enumerate(feedback.teacher_keep, 1):
        add('practices', text, f'teacher_keep:{index}', 'teacher_preference')
    return {'method': 'local_evidence_index', 'purpose': 'future_teaching',
            'categories': {kind: rows[:8] for kind, rows in categories.items()},
            'next_lesson_focus': feedback.next_lesson_focus,
            'interpretation': '分项归类用于查找原始记录；单次表现是后续教学线索，不是固定能力标签，参与活跃不等于已经掌握。'}


def class_characteristics(evidence):
    """Produce a bounded index with source records, without counting repetitions as proof."""
    result = []
    for kind, (label, _) in CATEGORIES.items():
        selected = []
        for record in evidence:
            summary = record.get('_learning_record', {})
            items = sorted(summary.get('categories', {}).get(kind, []),
                           key=lambda item: item['basis'] == 'teacher_report')
            for item in items:
                text = item['text'][:240]
                source = {'feedback_id': record['feedback_id'], 'lesson_title': record['lesson_title'],
                          'observed_at': record['observed_at'], 'ref': item['ref'], 'basis': item['basis']}
                previous = next((row for row in selected if row['text'] == text), None)
                if previous:
                    if len(previous['sources']) < 3:
                        previous['sources'].append(source)
                elif len(selected) < 2:
                    selected.append({'category': kind, 'label': label, 'text': text,
                                     'status': 'lesson_evidence_to_verify', 'sources': [source]})
        result.extend(selected)
    return result
