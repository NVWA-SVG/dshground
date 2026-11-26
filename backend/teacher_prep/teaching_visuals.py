"""Ground page planning in the saved lesson, with deterministic diagram candidates."""
import hashlib
import json
import re
import unicodedata
from math import ceil


def arithmetic(a,b,operation):
    if not all(isinstance(v,int) and not isinstance(v,bool) and 0<=v<=9999 for v in (a,b)):
        raise ValueError('竖式仅支持0—9999的非负整数')
    if operation not in ('+','-') or operation=='-' and a<b:raise ValueError('不支持此运算')
    result=a+b if operation=='+' else a-b
    width=max(len(str(a)),len(str(b)),len(str(result)))
    top=[int(v) for v in str(a).zfill(width)];bottom=[int(v) for v in str(b).zfill(width)]
    adjusted=top.copy();marks=[0]*width;events=[]
    if operation=='+':
        carry=0
        for index in range(width-1,-1,-1):
            total=top[index]+bottom[index]+carry;carry=total//10
            if carry and index:
                marks[index-1]=carry;events.append({'from':index,'to':index-1,'amount':1})
    else:
        for index in range(width-1,-1,-1):
            if adjusted[index]<bottom[index]:
                lender=index-1
                while lender>=0 and adjusted[lender]==0:lender-=1
                if lender<0:raise ValueError('退位运算不正确')
                # Each transfer is recorded, including passage through zero places.
                for position in range(lender,index):
                    adjusted[position]-=1;adjusted[position+1]+=10
                    events.append({'from':position,'to':position+1,'amount':1})
    return {'a':a,'b':b,'operation':operation,'result':result,'width':width,'top':top,'bottom':bottom,
            'adjusted':adjusted,'carry_marks':marks,'events':events}


def normalized(text):return unicodedata.normalize('NFKC',str(text)).replace('−','-').replace('—','-')


def illustration_prompt(grade,style,prompt):
    return f'教学插画，适合{grade}。统一视觉风格：{style}。{prompt}\n只生成辅助插画，不生成整页课件，不把标题、题干、公式、答案或标签画进图中；不添加本课之外的事实。'


def diagram_candidates(design,page,source):
    candidates=[];visible=normalized(' '.join([page['title'],*page['points']]))
    def add(kind,data,stages):
        identity=json.dumps([page['number'],source.id,kind,data,stages],ensure_ascii=False,sort_keys=True)
        candidates.append({'id':'diagram_'+hashlib.sha256(identity.encode()).hexdigest()[:16],
                           'kind':kind,**data,'allowed_stages':stages})
    if '数学' in design.subject or 'math' in design.subject.lower():
        text=normalized(' '.join([source.title,*source.points]));seen=set()
        expression=re.compile(r'(?<![\d./+\-*×÷^])(\d{1,4})\s*([+\-])\s*(\d{1,4})(?![\d./*×÷^])')
        for match in expression.finditer(text):
            if re.search(r'[+\-*/×÷^]\s*$',text[:match.start()]) or re.match(r'\s*[-+*/×÷^]\s*\d',text[match.end():]):continue
            a,op,b=int(match[1]),match[2],int(match[3]);key=(a,op,b)
            if key in seen or len(seen)>=3:continue
            seen.add(key)
            if op=='-' and a<b:continue
            calc=arithmetic(a,b,op);result=calc['result']
            tail=text[match.end():];explicit=re.match(r'\s*=\s*(\d+)',tail)
            if explicit and int(explicit[1])!=result:continue
            formula=rf'(?<!\d){a}\s*\{op}\s*{b}\s*=\s*{result}(?!\d)'
            result_visible=bool(re.search(formula,visible) or re.search(rf'(?:结果|答案)\s*[为是:：]?\s*{result}(?!\d)',visible))
            stages=['setup']
            if calc['events'] and re.search(r'进位|退位|借|退1|满十|进1',visible):stages.append('regroup')
            if result_visible:stages.append('complete')
            data={'a':a,'b':b,'operation':op,'expression':f'{a} {op} {b}'}
            add('column_calculation',data,stages)
            if b>0:add('number_line',data,['setup',*(['complete'] if result_visible else [])])
    if source.visual:
        add(source.visual.kind,{'labels':source.visual.labels},['setup'])
    return candidates


def enrich_pages(design,pages):
    activities={a.id:a for a in design.activities};sources={s.id:s for s in design.slides};result=[]
    for page in pages:
        activity=activities[page['activity_id']];source=sources[page['slide_id']]
        context={'activity_kind':activity.kind,'activity_minutes':activity.duration_minutes,
                 'activity_new_knowledge_count':len(activity.introduces),
                 'activity_planned_density':round(len(activity.introduces)/activity.duration_minutes,4),
                 'thinking_minutes':activity.thinking_minutes,'activity_knowledge_ids':activity.knowledge_ids,
                 'point_count':len(page['points']),'visible_characters':sum(len(re.sub(r'\s','',v)) for v in [page['title'],*page['points']]),
                 'interpretation':'密度是整个教学环节的首次引入知识点数/分钟；正文长度只描述本页文字量，两者不等同认知负荷或教学效果。'}
        result.append({**page,'activity_title':activity.title,'thinking_minutes':activity.thinking_minutes,
                       'teaching_context':context,'diagram_candidates':diagram_candidates(design,page,source)})
    return result


def image_top_regions(points,task=False,adaptive=False):
    from .exporters import wrap
    top=2.72 if task else 2.08;image_height=1.85 if task else 2.15;gap=.2;available=6.73-top
    text_height=available-image_height-gap
    if adaptive:
        def needed(size):return sum(len(wrap(point,(11.7-.1)*72/size*.94))*(size/72*1.15+3/72)+.06 for point in points)+.14*(len(points)-1)
        maximum_text=available-1.2-gap
        # Preserve both the illustration and every word; shift the boundary
        # rather than rejecting a four-line page over a few hundredths of an inch.
        preferred=needed(20) if needed(20)<=maximum_text else needed(18)
        if needed(18)<=maximum_text:
            text_height=min(max(text_height,preferred+.05),maximum_text)
            image_height=available-text_height-gap
    return top,image_height,top+image_height+gap,text_height


def validate_readability(page,visual,assets):
    if not visual.teaching_goal:return
    from .exporters import wrap
    question=any(a.get('kind')=='question' for a in assets)
    images=any(a.get('kind')!='question' for a in assets) or bool(visual.image_prompt.strip())
    task=bool(visual.student_task and not question)
    width=6.75 if images or visual.diagram_id else 11.85
    height=2.7 if question else 4.04 if task else 4.68
    if images and visual.layout_kind=='image_top' and not question:width,height=11.7,image_top_regions(page['points'],task,True)[3]
    layout=visual.layout_kind
    if layout=='auto':layout='comparison' if '对比' in visual.layout or '对照' in visual.layout else 'steps' if '步骤' in visual.layout or '分步' in visual.layout else 'split_right'
    if layout in ('steps','comparison'):
        columns=2 if layout=='comparison' and not images else 1;rows=ceil(len(page['points'])/columns)
        card_width=(width-.14*(columns-1))/columns;card_height=(height-.14*(rows-1))/rows
        indent=.48 if layout=='steps' else .15
        fits=all(len(wrap(point,(card_width-indent-.22)*72/18*.94))*(18/72*1.15+3/72)+.06<=card_height-.2 for point in page['points'])
    else:
        needed=sum(len(wrap(point,(width-.1)*72/18*.94))*(18/72*1.15+3/72)+.06 for point in page['points'])+.14*(len(page['points'])-1)
        fits=needed<=height
    if not fits:raise ValueError(f"第{page['number']}页所选布局会把正文压到18pt以下，请移除侧图、原生图解或改用更宽的文字布局；不要删改原文")


def validate_page_plan(page,visual,assets=()):
    validate_readability(page,visual,assets)
    if visual.teaching_goal and visual.image_prompt.strip() and not visual.image_reason.strip():raise ValueError('请求生图时请填写image_reason，说明具体教学用途')
    if visual.teaching_goal and len(assets)+bool(visual.image_prompt.strip())>2:raise ValueError('同一页最多安排两个图片对象（包括教材原图和AI插画），请减少辅助图片以保留观察空间')
    if visual.student_task:
        source_numbers=set(re.findall(r'\d+(?:\.\d+)?',normalized(' '.join([page['title'],*page['points']]))))
        if not set(re.findall(r'\d+(?:\.\d+)?',normalized(visual.student_task)))<=source_numbers:
            raise ValueError(f"第{page['number']}页学生任务出现原页面没有的数值，请只概括原有任务")
    if not visual.diagram_id:
        if visual.diagram_stage!='setup':raise ValueError('未选择原生图解时diagram_stage应为setup')
        return None
    selected=next((d for d in page.get('diagram_candidates',[]) if d['id']==visual.diagram_id),None)
    if not selected:raise ValueError(f"第{page['number']}页原生图解不在本页候选目录中")
    if visual.diagram_stage not in selected['allowed_stages']:
        raise ValueError(f"第{page['number']}页不允许此揭示阶段；不得在练习页提前显示答案")
    if visual.asset_ids or visual.image_prompt.strip():raise ValueError('原生图解页请留空asset_ids与image_prompt，保持图解清晰；原图与插画可用于其他页面')
    if visual.layout_kind in ('image_top','text'):raise ValueError('原生图解请使用左右布局、分步或对比布局')
    return selected
