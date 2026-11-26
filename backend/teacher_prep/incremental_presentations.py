"""Reuse reviewed visual plans and illustration files across immutable lesson versions."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import re
import shutil

from .exporters import sha256,write_json
from .models import LessonDesign
from .presentations import PageVisual,PptVisualPlan
from .store import digest
from .teaching_visuals import illustration_prompt,validate_page_plan


def load(file):
    return json.loads(file.read_text(encoding='utf-8'))


def indexed_pages(brief):
    counts={};result={}
    for page in brief['pages']:
        slide=page['slide_id'];counts[slide]=counts.get(slide,0)+1
        result[(slide,counts[slide])]=page
    return result


def signature(design,page):
    slide=next(s for s in design['slides'] if s['id']==page['slide_id'])
    activity=next(a for a in design['activities'] if a['id']==page['activity_id'])
    return digest({'title':page['title'],'points':page['points'],'visual':page['visual'],'activity':activity,
                   'questions':[q for q in design['questions'] if q['id'] in slide['question_ids']],
                   'knowledge':[k for k in design['knowledge_points'] if k['id'] in activity['knowledge_ids']]})


class IncrementalPresentations:
    def __init__(self,presentations):
        self.ppt=presentations;self.service=presentations.service

    def prepare(self,run_id):
        directory=self.ppt.run_dir(run_id);record=directory/'reuse.json'
        if record.exists():return load(record)
        brief=load(directory/'brief.json');version=self.service.get('version',brief['version_id'])
        if not version['parent_id']:return None
        parent=self.service.get('version',version['parent_id'])
        source=None
        runs=sorted(directory.parent.glob('task_*'),key=lambda p:p.stat().st_mtime,reverse=True)
        for candidate in runs:
            try:
                if candidate==directory:continue
                old=load(candidate/'brief.json')
                if old['version_id']!=parent['id'] or old['design_hash']!=digest(parent['design']) or old.get('requested_style','')!=brief.get('requested_style',''):continue
                receipt=load(candidate/'result.json')
                if not receipt.get('valid') or receipt.get('version_id')!=parent['id']:continue
                deck=next(f for f in receipt['manifest']['files'] if f['name']=='课件.pptx')
                output=self.service.root/'deliverables'
                delivery=Path(receipt['directory']).resolve()
                if not delivery.is_relative_to(output.resolve()) or sha256(delivery/'课件.pptx')!=deck['sha256']:continue
                source=(candidate,old,PptVisualPlan.model_validate(load(candidate/'visual-plan.json')).model_dump());break
            except (OSError,ValueError,KeyError,StopIteration):continue
        if source is None:return None
        source_dir,old,plan=source;original_pages=indexed_pages(old);old_plan={p['page_number']:p for p in plan['pages']}
        new_design=LessonDesign.model_validate(version['design']).model_dump(mode='json')
        old_design=LessonDesign.model_validate(parent['design']).model_dump(mode='json')
        assets={a['id']:a for a in self.ppt.candidate_assets(version['id'])}
        reused=[];mapping=[]
        global_same=all(old_design[k]==new_design[k] for k in ('title','grade','subject','course_type','goals'))
        for key,page in indexed_pages(brief).items():
            previous=original_pages.get(key)
            if not global_same or previous is None or signature(new_design,page)!=signature(old_design,previous):continue
            visual=deepcopy(old_plan.get(previous['number']))
            if not visual or any(id not in assets for id in visual['asset_ids']):continue
            visual['page_number']=page['number']
            if visual.get('diagram_id'):
                original=next((d for d in previous.get('diagram_candidates',[]) if d['id']==visual['diagram_id']),None)
                match=next((d for d in page.get('diagram_candidates',[]) if original and {k:v for k,v in d.items() if k!='id'}=={k:v for k,v in original.items() if k!='id'}),None)
                if match is None:continue
                visual['diagram_id']=match['id']
            try:
                validate_page_plan(page,PageVisual.model_validate(visual),[assets[id] for id in visual['asset_ids']])
                if visual.get('image_prompt'):self.source_image(source_dir,old,previous['number'])
            except (OSError,ValueError,KeyError,StopIteration):continue
            reused.append(visual);mapping.append({'source_page':previous['number'],'page_number':page['number']})
        if not reused:return None
        data={'source_run_id':source_dir.name,'source_version_id':parent['id'],'version_id':version['id'],
              'style':plan['style'],'palette':plan['palette'],'reused_pages':reused,'page_mapping':mapping,
              'pending_pages':[p['number'] for p in brief['pages'] if p['number'] not in {v['page_number'] for v in reused}]}
        write_json(record,data);return data

    def source_image(self,directory,brief,number):
        index=load(directory/'generated/index.json')
        if index['version_id']!=brief['version_id'] or index['design_hash']!=brief['design_hash'] or index['plan_sha256']!=sha256(directory/'visual-plan.json'):
            raise ValueError('原插画与原视觉方案不一致')
        entry=next(e for e in index['images'] if e['page_number']==number)
        if not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|webp)',entry['file']) or sha256(directory/'generated'/entry['file'])!=entry['sha256']:
            raise ValueError('原插画文件未通过校验')
        return entry

    def save(self,run_id,raw,image_config):
        directory=self.ppt.run_dir(run_id);reuse=load(directory/'reuse.json')
        plan=PptVisualPlan.model_validate(raw).model_dump()
        if plan['style']!=reuse['style'] or plan['palette']!=reuse['palette']:raise ValueError('增量课件应沿用原视觉风格和配色')
        if [p['page_number'] for p in plan['pages']]!=reuse['pending_pages']:raise ValueError('仅按顺序提交本次待规划页码，不得遗漏、增加或覆盖复用页')
        merged={**plan,'pages':sorted([*reuse['reused_pages'],*plan['pages']],key=lambda p:p['page_number'])}
        # An all-reused deck bypasses PptVisualPlan's nonempty partial-page constraint.
        return self.save_merged(run_id,merged,reuse,image_config)

    def save_merged(self,run_id,merged,reuse,image_config):
        result=self.ppt.save_prompt(run_id,merged)
        directory=self.ppt.run_dir(run_id);brief=load(directory/'brief.json')
        source_dir=self.ppt.run_dir(reuse['source_run_id']);source_brief=load(source_dir/'brief.json');source_plan=load(source_dir/'visual-plan.json')
        source_pages={p['page_number']:p for p in source_plan['pages']}
        generated=directory/'generated';items={};images=[]
        for row in reuse['page_mapping']:
            old=source_pages[row['source_page']]
            if not old.get('image_prompt'):continue
            current=next(p for p in merged['pages'] if p['page_number']==row['page_number'])
            prompt=illustration_prompt(brief['grade'],merged['style'],current['image_prompt'])
            original=illustration_prompt(source_brief['grade'],source_plan['style'],old['image_prompt'])
            if prompt!=original:raise ValueError('复用插画的实际提示词已改变')
            entry=self.source_image(source_dir,source_brief,row['source_page'])
            generated.mkdir(exist_ok=True);destination=generated/entry['file']
            if destination.exists() and sha256(destination)!=entry['sha256']:raise ValueError('新版已保存插画与复用文件不一致')
            if not destination.exists():shutil.copy2(source_dir/'generated'/entry['file'],destination)
            identity=hashlib.sha256(prompt.encode()).hexdigest()
            items[identity]={'status':'ready','file':entry['file'],'sha256':entry['sha256'],'model':entry['model'],'reused_from':reuse['source_run_id']}
            images.append({**entry,'page_number':row['page_number']})
        # Never overwrite a ledger created by an interrupted image-generation request.
        if items and not (generated/'ledger.json').exists():
            write_json(generated/'ledger.json',{'plan_sha256':sha256(directory/'visual-plan.json'),'config':image_config,'items':items})
            write_json(generated/'index.json',{'version_id':brief['version_id'],'design_hash':brief['design_hash'],
                       'plan_sha256':sha256(directory/'visual-plan.json'),'images':images})
        return {**result,'reused_pages':len(reuse['reused_pages']),'planned_pages':len(reuse['pending_pages']),'reused_images':len(images)}

    def save_all(self,run_id,image_config):
        reuse=load(self.ppt.run_dir(run_id)/'reuse.json')
        if reuse['pending_pages']:raise ValueError('仍有页面需要规划')
        return self.save_merged(run_id,{k:reuse[k] for k in ('style','palette')}|{'pages':reuse['reused_pages']},reuse,image_config)
