"""Turn teacher-authored text into an editable proposal, without inventing measurements."""
import re
from .retrieval import tokens

NO_CHANGE=re.compile(r'(?:暂时|暂|先)?不(?:用|需要|必)?(?:再)?(?:修改|调整|优化)|无需(?:修改|调整|优化)|先(?:只)?(?:记录|保存)|保持(?:现有|原样|不变)')
POSITIVE=re.compile(r'挺好|很好|顺利|效果不错|都能|全部会|没有(?:明显)?问题|掌握得不错')
ISSUES=[
    (r'不懂|不会|混淆|分不清|卡住|跟不上|没跟上|做错|算错|错误|理解.{0,8}困难|不熟练|不熟悉','围绕反馈中容易混淆或卡住的内容，把讲解分步展开，补充一个对比或分步示例，再安排同目标的独立练习。'),
    (r'讲.{0,8}太(?:长|久)|讲解.{0,8}多|时间.{0,8}不够|来不及|超时|拖堂|太快|太慢|字太多|文字.{0,8}多|读不完|密集','核对相关环节的讲解和任务安排，在原总课时内压缩非核心内容，给学生保留思考和操作时间。'),
    (r'练习.{0,8}(?:少|不足|不够)|多.{0,5}练|增加.{0,5}练|巩固|作业.{0,8}错','围绕同一教学目标增加有针对性的练习与讲评，并在原总课时内同步调整其他环节。'),
    (r'参与.{0,8}少|不敢|不举手|不愿.{0,5}(?:说|答)|只有.{0,8}发言|没人.{0,5}(?:回答|举手)|不活跃|沉默','在相关环节安排先独立思考、再同伴交流或抽样解释的参与方式，同时查看学生实际作答。'),
]
DIRECTIVE=re.compile(r'希望|建议|想(?:让|要|增加|减少|补充)|请|增加|补充|调整|修改|优化|改为|多安排|少一点')


def propose_from_text(design,feedback,structured_suggestions):
    raw=feedback.raw_text.strip();request=feedback.requested_changes.strip()
    clauses=[part.strip() for part in re.split(r'[。！？；;\n]+',raw) if part.strip()]
    meaningful=[part for part in clauses if not NO_CHANGE.search(part) and not re.search(r'没有.{0,4}困难|不再.{0,4}混淆',part)]
    issue_text='。'.join(meaningful)
    issue_actions=[action for pattern,action in ISSUES if re.search(pattern,issue_text)]
    directives=[part for part in meaningful if DIRECTIVE.search(part)]
    structured=[s for s in structured_suggestions if s['id']!='collect_evidence']
    request_record_only=bool(request and NO_CHANGE.search(request) and not DIRECTIVE.search(NO_CHANGE.sub('',request)))
    observation_record_only=bool(not request and not issue_actions and not directives and not structured and (NO_CHANGE.search(raw) or POSITIVE.search(raw)))
    if request_record_only or observation_record_only:
        return None,{'method':'local_teacher_feedback','status':'record_only','measurement_inference':False}
    actions=[request] if request else directives or issue_actions
    derived=[]
    if not actions and structured:
        for item in structured[:3]:
            for action in item['suggested_actions']:
                if action not in actions:actions.append(action)
            derived.append(item)
    if not actions:actions=['围绕这次反馈中的具体表现核对讲解和任务，补充有针对性的解释、示例或练习，并保留原有有效安排。']
    evidence=['qualitative:raw_text']+(['teacher_request:requested_changes'] if request else [])
    evidence=sorted(set(evidence+[e for item in derived for e in item['evidence_ids']]))
    # Association is a suggestion for the revision model, never a fabricated observation.
    query=tokens(raw+' '+request);ranked=[]
    knowledge={point.id:point.title for point in design.knowledge_points}
    for activity in design.activities:
        context=' '.join([activity.title,*(knowledge.get(id,'') for id in activity.knowledge_ids),
                          *(slide.title+' '+' '.join(slide.points) for slide in design.slides if slide.activity_id==activity.id)])
        score=len(query&tokens(context))
        if score>=2:ranked.append((score,activity.id,activity.title))
    ranked.sort(key=lambda row:(-row[0],row[1]));targets=ranked[:2]
    observation=raw if len(raw)<=600 else raw[:600]+'…（完整原话保存在本条反馈中）'
    proposal={'id':'qualitative_feedback','title':'按你的反馈优化本课','source':'teacher_feedback','default_selected':True,
              'evidence_ids':evidence,'observation':observation,
              'hypothesis':'这是根据教师原话整理的待确认调整方向，不代表已判断原因或量化掌握程度。',
              'suggested_actions':actions,'verify_next_time':'下次关注同一困难是否减少，可用学生解释、典型作答或同目标新题检查；没有准确统计时继续记录具体观察。',
              'target_activity_ids':[row[1] for row in targets],'target_activity_titles':[row[2] for row in targets],
              'target_association':'依据课程内容匹配的参考环节，可由教师调整，不能视为已核实的环节观察。','status':'awaiting_teacher_choice'}
    return proposal,{'method':'local_teacher_feedback','status':'proposal_ready','measurement_inference':False,
                    'requested_changes_provided':bool(request),'proposal_id':proposal['id']}
