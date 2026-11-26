"""Deterministic lesson-focused excerpt selection with stable source locations."""
import math
import re
import unicodedata
from collections import Counter


def tokens(value):
    text=unicodedata.normalize('NFKC',str(value or '')).lower()
    result=set(re.findall(r'[a-z]{2,}|\d+',text))
    for run in re.findall(r'[\u4e00-\u9fff]+',text):
        if len(run)==1: result.add(run)
        result.update(run[i:i+2] for i in range(len(run)-1))
        result.update(char for char in run if char in '秒分时米克升角数')
    return result-{'数学','语文','英语','年级','教材','课程','学生','本课','小学'}


def passages(text,size=1000):
    """Bound a long sentence too; never let an unbroken paragraph defeat budgets."""
    current='';offset=0
    for sentence in re.split(r'(?<=[。！？!?；;\n])',str(text)):
        if not sentence: continue
        while len(sentence)>size:
            if current:
                yield {'text':current,'offset':offset};offset+=len(current);current=''
            yield {'text':sentence[:size],'offset':offset};offset+=size;sentence=sentence[size:]
        if current and len(current)+len(sentence)>size:
            yield {'text':current,'offset':offset};offset+=len(current);current=''
        current+=sentence
    if current.strip():yield {'text':current,'offset':offset}


def select_excerpts(items,query,limit=10,max_chars=16000,neighbors=True):
    limit=max(1,min(40,int(limit)));max_chars=max(1,min(64000,int(max_chars)))
    candidates=[]
    for item in items:
        pieces=[{'text':item['text'],'offset':0}] if item.get('atomic') else passages(item['text'],size=min(1000,max_chars))
        for piece in pieces:
            candidates.append({**item,**piece,'_tokens':tokens(piece['text']+' '+ ' '.join(item.get('path',[])))})
    q=tokens(query);df=Counter(t for item in candidates for t in item['_tokens']);count=len(candidates)
    for index,item in enumerate(candidates):
        item['_index']=index
        item['_score']=sum(math.log(1+(count-df[t]+.5)/(df[t]+.5)) for t in q&item['_tokens'])
    ranked=sorted(candidates,key=lambda x:(-x['_score'],x['_index']))
    matched=any(item['_score']>0 for item in ranked)
    if matched:ranked=[item for item in ranked if item['_score']>0]
    picked=[];seen=set();total=0

    def add(item,role):
        nonlocal total
        key=(item.get('canonical_knowledge_id') or item.get('knowledge_id') or item.get('source_id'),item.get('chunk_id') or item.get('chunk'),item['offset'])
        if key in seen or len(picked)>=limit:return
        # Only exact normalized duplicates are removed: a number or negation can
        # make an otherwise near-identical textbook statement materially different.
        normalized=lambda s:re.sub(r'\s+','',s).lower()
        if any(normalized(item['text'])==normalized(old['text']) for old in picked):return
        text=item['text']
        if total+len(text)>max_chars:return
        picked.append({**item,'selection':role});seen.add(key);total+=len(text)

    for item in ranked:
        if item.get('semantic_group_id') and item.get('part_count',1)>1:
            related=sorted((part for part in candidates if part.get('semantic_group_id')==item['semantic_group_id'] and part.get('knowledge_id')==item.get('knowledge_id')),key=lambda part:part['part_index'])
            if sum(len(part['text']) for part in related)<=max_chars-total and len(related)<=limit-len(picked):
                for part in related:add(part,'semantic_continuation')
        add(item,'match' if matched else 'fallback_not_matched')
        if len(picked)>=max(1,limit//2):break
    if neighbors:
        for item in list(picked):
            for index in (item['_index']-1,item['_index']+1):
                if 0<=index<count:
                    candidate=candidates[index]
                    if (candidate.get('knowledge_id') or candidate.get('source_id'))==(item.get('knowledge_id') or item.get('source_id')) and candidate.get('chapter_id')==item.get('chapter_id') and (not item.get('section_id') or candidate.get('section_id')==item.get('section_id')):add(candidate,'neighbor')
    for item in ranked:add(item,'match' if matched else 'fallback_not_matched')
    public=[{key:value for key,value in item.items() if not key.startswith('_')} for item in picked]
    return {'excerpts':public,'retrieval':{'method':'lexical_idf_with_neighbors_and_dedup','matched':matched,'selected_chars':total,'candidate_count':count,'selected_count':len(public),'omitted_count':count-len(public)}}


def source_context(service,ids,query,max_chars=18000):
    entries=[];items=[]
    for source_id in ids:
        from pathlib import Path
        source=service.get('source',source_id)
        entries.append({'id':source_id,'name':source['name']})
        text=Path(source['text_path']).read_text(encoding='utf-8')
        page=None;number=0;document_offset=0
        for block in re.split(r'(?=\[第\d+页\])',text):
            marker=re.match(r'\[第(\d+)页\]',block)
            if marker:page=int(marker.group(1))
            for piece in passages(block):
                number+=1
                items.append({'source_id':source_id,'name':source['name'],'chunk':number,'page':page,'page_kind':'pdf_page' if page else None,'source_offset':document_offset+piece['offset'],'text':piece['text']})
            document_offset+=len(block)
    selected=select_excerpts(items,query,limit=20,max_chars=max_chars)
    return {'sources':[{**entry,'excerpts':[e for e in selected['excerpts'] if e['source_id']==entry['id']]} for entry in entries],'retrieval':selected['retrieval']}
