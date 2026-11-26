"""Render a local acceptance copy of the saved lesson; no model/provider calls."""
import json,sys,urllib.request,shutil
from uuid import uuid4
from pathlib import Path
project=Path(__file__).resolve().parents[1];sys.path.insert(0,str(project/'backend'))
from teacher_prep.service import Service
from teacher_prep.presentations import Presentations
from teacher_prep.teaching_delivery import prepare_delivery

def get(kind,id):
    with urllib.request.urlopen('http://127.0.0.1:3081/teacher/api/entity?kind='+kind+'&id='+id) as response:return json.load(response)
version=get('version','version_804aa2d1544f4e62');lesson=get('lesson',version['lesson_id'])
root=project/('.test-runs/teaching-preview-'+uuid4().hex[:8]);output=project/'.test-runs/teaching-preview-evidence'
if root.exists():raise SystemExit('Preview root already exists')
output.mkdir(parents=True,exist_ok=True)
request=dict(lesson['payload']);request.pop('class_id',None);request.update(source_ids=[],knowledge_ids=[],class_label='图解排版验收')
raw=dict(version['design']);raw.update(source_ids=[],knowledge_ids=[])
service=Service(root);created=service.create(request,'request');saved=service.save_design(created['lesson_id'],raw,'design');service.export(saved['version_id'])
pipeline=Presentations(service);run='task_teaching_preview';prepared=pipeline.prepare(saved['version_id'],run,'清晰的可编辑数学教学图解')
goals=['用数量差解释减法的意义','解释进位加法的进位去向','解释跨零退位的数位变化','完成退位计算并核对结果','独立完成计算并检查方法','联系预算解释加减法的用途']
tasks=['比较两个数量，说明怎样求相差多少。','观察数位对齐与进位标记，说明进位去了哪里。','观察上方退位标记，说明各数位怎样变化。','按数位说明计算结果，并说出检查方法。','先独立计算，再说明你的验算方法。','列式说明预算是否足够，并检查计算。']
focus=['起点、移动方向和相差的数量','十位满十后向百位进一','百位、十位和个位连续退位的关系','退位后的各数位与减法结果','独立计算顺序与验算','总价、预算与剩余金额之间的关系']
plan={'style':'清晰的三年级数学课件，统一青绿色重点标记与浅色底，数字、竖式和进退位标记均为原生对象。','palette':'mint','pages':[]}
for index,page in enumerate(prepared['brief']['pages']):
    desired='number_line' if index==0 else 'column_calculation';candidates=page['diagram_candidates']
    candidate=next((d for d in candidates if d['kind']==desired),next(iter(candidates),None))
    stage='complete' if index in (1,3) else 'regroup' if index==2 else 'setup'
    if candidate and stage not in candidate['allowed_stages']:stage='setup'
    context=page['teaching_context']
    plan['pages'].append({'page_number':page['number'],'layout':'原文在左、主要图解在右' if candidate else '宽版卡片组织原有问题','layout_kind':'split_right' if candidate else 'comparison',
        'visual_prompt':'依据具体观察任务呈现'+focus[index],'teaching_goal':goals[index],'student_task':tasks[index],'learning_focus':focus[index],
        'asset_reason':'原生图解准确呈现数位或数量关系，学生能直接比较标记。' if candidate else '原题以文字卡片完整呈现，为独立列式和说明理由留出空间。',
        'density_note':f"本环节首次引入{context['activity_new_knowledge_count']}个知识点，用时{context['activity_minutes']:g}分钟，思考练习{context['thinking_minutes']:g}分钟；本页聚焦{focus[index]}。",
        'diagram_id':candidate['id'] if candidate else '', 'diagram_stage':stage,'asset_ids':[],'image_prompt':'','image_reason':''})
pipeline.save_prompt(run,plan);result=pipeline.compose_local(saved['version_id'],run);delivery=prepare_delivery(service,saved['version_id'],run)
shutil.copy2(Path(result['directory'])/'课件.pptx',output/'教学图解验收.pptx');shutil.copy2(pipeline.run_dir(run)/'课件提示词.md',output/'课件提示词.md')
shutil.copy2(pipeline.run_dir(run)/'visual-plan.json',output/'visual-plan.json')
report={'valid':result['valid'],'pages':len(prepared['brief']['pages']),'native_diagrams':result['native_diagram_count'],'generated_images':result['generated_image_count'],
        'model_calls':0,'source_version':version['id'],'existing_lesson_changed':False,'description':'仅复用已保存设计验证新视觉方案与本地装配，不代表新模型生成或真实课堂验收。'}
(output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
