"""Directory-first, source-grounded textbook indexing. No model calls."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

DEFAULT_CHUNKING={'target_chars':650,'min_chars':180,'max_chars':1200,'atomic_max_chars':2200}
KINDS={'concept':'概念与说明','example':'例题与解答','practice':'练习题组','review':'整理复习','reading':'拓展阅读'}


def chunking_settings(root):
    file=Path(root)/'config/knowledge-chunking.json'
    raw=json.loads(file.read_text(encoding='utf-8-sig')) if file.exists() else {}
    if not isinstance(raw,dict) or set(raw)-set(DEFAULT_CHUNKING):raise ValueError('未知教材分块设置')
    values={**DEFAULT_CHUNKING,**raw}
    if any(isinstance(v,bool) or not isinstance(v,int) for v in values.values()):raise ValueError('分块长度须为整数')
    if not 80<=values['min_chars']<=values['target_chars']<=values['max_chars']<=values['atomic_max_chars']<=4000:
        raise ValueError('分块长度需满足80≤最小≤目标≤普通上限≤完整题组上限≤4000')
    return values


def stable_id(prefix,value):
    return prefix+'_'+hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:16]


def _key(text):
    return re.sub(r'[^\w]','',text).replace('的','')


def _printed_contents(document):
    """Recover printed TOC entries only when their titles match real body pages."""
    toc_pages=[];candidates=[]
    for index in range(min(20,len(document))):
        text=document[index].get_text('text',sort=True)
        if re.search(r'目\s*录|\bcontents\b',text,re.I):toc_pages.append(index+1)
    if not toc_pages:return []
    toc_body_pages=set(toc_pages)
    for number in sorted(set(toc_pages+[p+1 for p in toc_pages if p<len(document)])):
        lines=[line.strip() for line in document[number-1].get_text('text',sort=True).splitlines() if line.strip()]
        matched_lines=0
        for i,line in enumerate(lines):
            match=re.match(r'^(.{2,70}?)[\s.．·…—\-]+(\d{1,4})\s*$',line)
            title=match.group(1).strip() if match else line if i+1<len(lines) and re.fullmatch(r'\d{1,4}',lines[i+1]) else ''
            if title:matched_lines+=1
            if title and re.match(r'^(?:第[一二三四五六七八九十百0-9]+[单元章节]|[0-9一二三四五六七八九十]+[\s、.．]|Unit\s+\d|Chapter\s+\d)',title,re.I):candidates.append(title)
        if matched_lines>=2:toc_body_pages.add(number)
    headings={}
    for index,page in enumerate(document):
        if index+1 in toc_body_pages:continue
        for line in page.get_text('text',sort=True).splitlines()[:8]:
            core=re.sub(r'^\s*(?:第[一二三四五六七八九十百0-9]+[单元章节]\s*|[0-9一二三四五六七八九十]+[\s、.．]+)','',line)
            core=re.sub(r'\s*\d+\s*$','',core).strip()
            if core:headings.setdefault(_key(core),index+1)
    found=[]
    for title in candidates:
        core=re.sub(r'^(?:第[一二三四五六七八九十百0-9]+[单元章节]\s*|[0-9一二三四五六七八九十]+[\s、.．]+)','',title)
        page=headings.get(_key(core))
        if page and not any(item['start_page']==page for item in found):found.append({'title':title,'start_page':page,'kind':'unit','page_verified_by':'printed_toc_and_heading','bookmark_page':None})
    found.sort(key=lambda row:row['start_page'])
    for i,item in enumerate(found):item['end_page']=found[i+1]['start_page']-1 if i+1<len(found) else len(document)
    return found


def _kind(text,current='concept'):
    start=text.strip()
    if re.match(r'^(?:例\s*[题0-9一二三四五六七八九十]|示例|Example\b)',start,re.I):return 'example'
    if re.match(r'^(?:练习|做一做|试一试|巩固练习|课后|习题|Exercise\b)',start,re.I):return 'practice'
    if re.match(r'^(?:整理[与和]复习|总复习|单元小结|总结)',start):return 'review'
    if re.match(r'^(?:你知道吗|阅读资料|数学文化|拓展阅读)',start):return 'reading'
    return current


def _split_large(text,limit):
    parts=[];current=''
    for line in re.split(r'(?<=[。！？；;\n])',text):
        while len(line)>limit:
            if current:parts.append(current);current=''
            parts.append(line[:limit]);line=line[limit:]
        if current and len(current)+len(line)>limit:parts.append(current);current=''
        current+=line
    if current.strip():parts.append(current)
    return parts


def build_book_index(path,title,root,progress=None):
    import fitz
    from .pdf_book import pdf_structure
    settings=chunking_settings(root)
    from .import_limits import import_limits
    structure=pdf_structure(path,import_limits(root)['max_pdf_pages'])
    if progress:progress('directory',{'chapters':len(structure['suggested_ranges'])})
    with fitz.open(path) as document:
        ranges=structure['suggested_ranges']
        source='bookmarks_with_heading_check'
        if not ranges:ranges=_printed_contents(document);source='printed_contents_with_heading_check' if ranges else 'manual_scope_required'
        if not ranges:ranges=[{'title':title,'start_page':1,'end_page':len(document),'kind':'document','page_verified_by':'unverified_document_scope'}]
        pages=[]
        for number,page in enumerate(document,1):
            if progress and (number==1 or number%10==0 or number==len(document)):progress('reading',{'page':number,'pages':len(document)})
            blocks=[]
            raw=page.get_text('dict',sort=True)
            all_sizes=[span['size'] for block in raw['blocks'] if block.get('type')==0 for line in block['lines'] for span in line['spans'] if len(span.get('text','').strip())>2]
            baseline=sorted(all_sizes)[len(all_sizes)//2] if all_sizes else 10
            for index,block in enumerate(raw['blocks']):
                if block.get('type')!=0:continue
                lines=[''.join(span['text'] for span in line['spans']).strip() for line in block['lines']]
                text='\n'.join(line for line in lines if line).strip()
                if not text or (re.fullmatch(r'[\d\s]+',text) and block['bbox'][1]>page.rect.height*.9):continue
                size=max((span['size'] for line in block['lines'] for span in line['spans']),default=baseline)
                first=lines[0] if lines else text
                short=2<=len(first)<=30 and len(text)<=60 and len(re.findall(r'[\u4e00-\u9fffA-Za-z]',first))>=2 and not re.search(r'[：:，。！？?=+−÷×]',first)
                heading=short and (size>=max(11,baseline*1.18) or bool(re.match(r'^(?:\d+[.．]\s*\D|练习[一二三四五六七八九十0-9]+|整理[与和]复习|做一做|你知道吗)',first)))
                colors={span['color'] for line in block['lines'] for span in line['spans']}
                marker=bool(re.fullmatch(r'\d{1,2}',text) and baseline*1.2<=size<=30 and 16777215 not in colors)
                blocks.append({'id':f'p{number}b{index}','page':number,'text':text,'bbox':[round(v,2) for v in block['bbox']],'heading':bool(heading),'heading_text':first if heading else None,'example_marker':marker})
            blocks.sort(key=lambda block:(round(block['bbox'][1]/4),block['bbox'][0]))
            pages.append({'page':number,'text':'\n'.join(block['text'] for block in blocks),'blocks':blocks})
    if not any(page['text'].strip() for page in pages):raise ValueError('PDF没有可提取文本，请先完成OCR再导入')
    if progress:progress('chunking',{'chapters':len(ranges)})
    chapters=[];chunks=[]
    for chapter_number,scope in enumerate(ranges,1):
        chapter_id=stable_id('chapter',(scope['title'],scope['start_page'],scope['end_page']))
        selected=[block for page in pages if scope['start_page']<=page['page']<=scope['end_page'] for block in page['blocks']]
        sections=[];groups=[];current=[];kind='concept';section_title=scope['title'];section_id=stable_id('section',(chapter_id,section_title))
        def flush():
            nonlocal current
            if current:groups.append({'blocks':current,'kind':kind,'section_title':section_title,'section_id':section_id});current=[]
        last_page=None;lesson_title=scope['title']
        for block in selected:
            if last_page is not None and last_page!=block['page'] and kind=='practice' and section_title=='做一做':
                flush();kind='concept';section_title=lesson_title;section_id=stable_id('section',(chapter_id,lesson_title))
            last_page=block['page']
            text=block['text'];first=text.splitlines()[0].strip();explicit_kind=_kind(first,None);next_kind=explicit_kind or kind
            is_heading=block['heading'] and _key(first)!=_key(scope['title'])
            # Reading/solution/reflection substeps remain attached to an example.
            if re.match(r'^(?:阅读与理解|分析与解答|回顾与反思|解答|解：|答：)',first):is_heading=False
            numeric_example=block.get('example_marker') or (kind not in ('practice','review') and bool(re.match(r'^\d{1,2}\s+[\u4e00-\u9fff].*[？?]',text)))
            if numeric_example:flush();next_kind='example'
            if is_heading and explicit_kind is None and not re.match(r'^(?:阅读与理解|分析与解答|回顾与反思)',first):next_kind='concept'
            if next_kind!=kind or is_heading:
                flush();kind=next_kind
                if is_heading:
                    section_title=first;section_id=stable_id('section',(chapter_id,first,block['page'],block['id']))
                    sections.append({'id':section_id,'title':first,'page':block['page'],'kind':kind})
                    if kind=='concept':lesson_title=first
            # Small ordinary paragraphs can be packed; complete examples and
            # exercise questions stay atomic until their semantic boundary.
            if current and kind=='concept' and sum(len(item['text']) for item in current)+len(text)>settings['target_chars']:flush()
            current.append(block)
        flush()
        # A heading locates the following teaching content; it is not a
        # standalone knowledge passage that should consume a retrieval slot.
        from .pdf_book import _heading_key
        semantic_groups=[];pending_headings=[]
        for group in groups:
            group_text='\n'.join(block['text'] for block in group['blocks']).strip()
            heading_only=len(group_text)<80 and _heading_key(group_text) in {_heading_key(scope['title']),_heading_key(group['section_title'])}
            if heading_only:
                pending_headings.extend(group['blocks']);continue
            if pending_headings:
                group={**group,'blocks':pending_headings+group['blocks']};pending_headings=[]
            semantic_groups.append(group)
        groups=semantic_groups
        packed=[]
        for group in groups:
            previous=packed[-1] if packed else None
            if previous and previous['kind']==group['kind']=='concept' and previous['section_id']==group['section_id'] and sum(len(b['text']) for b in previous['blocks'])<settings['min_chars'] and sum(len(b['text']) for b in previous['blocks']+group['blocks'])<=settings['max_chars']:
                previous['blocks'].extend(group['blocks'])
            else:packed.append(group)
        groups=packed
        for group_number,group in enumerate(groups,1):
            text='\n'.join(block['text'] for block in group['blocks']).strip()
            if re.fullmatch(r'[\d\s]{1,6}',text):continue
            atomic=group['kind'] in ('example','practice')
            cap=settings['atomic_max_chars'] if atomic else settings['max_chars']
            parts=_split_large(text,settings['max_chars']) if len(text)>cap else [text]
            group_id=stable_id('group',(chapter_id,group_number,text))
            spans=[];position=0
            for block in group['blocks']:
                spans.append((position,position+len(block['text']),block));position+=len(block['text'])+1
            search_offset=0
            for part_index,part in enumerate(parts,1):
                part_start=text.find(part,search_offset);part_end=part_start+len(part);search_offset=part_end
                used_blocks=[block for begin,end,block in spans if begin<part_end and end>part_start]
                page_refs=sorted({block['page'] for block in used_blocks})
                chunks.append({'id':stable_id('chunk',(group_id,part_index)),'text':part,'chapter_id':chapter_id,'chapter_title':scope['title'],
                    'section_id':group['section_id'],'section_title':group['section_title'],'path':[title,scope['title'],group['section_title']],
                    'kind':group['kind'],'page_refs':page_refs,'page':page_refs[0] if len(page_refs)==1 else None,
                    'block_refs':[block['id'] for block in used_blocks],'semantic_group_id':group_id,'part_index':part_index,'part_count':len(parts),
                    'atomic':True,'asset_ids':[],'characters':len(part),'source_kind':'pdf_original'})
        chapter_chunks=[chunk for chunk in chunks if chunk['chapter_id']==chapter_id]
        excerpt=next((chunk['text'][:200] for chunk in chapter_chunks if len(chunk['text'])>40),'')
        chapters.append({**scope,'id':chapter_id,'number':chapter_number,'sections':sections,
            'overview':{'method':'source_extract','topics':list(dict.fromkeys(section['title'] for section in sections if section['kind']=='concept'))[:30],
                        'excerpt':excerpt,'content_types':list(dict.fromkeys(KINDS[chunk['kind']] for chunk in chapter_chunks))},
            'chunk_count':len(chapter_chunks),'characters':sum(len(page['text']) for page in pages if scope['start_page']<=page['page']<=scope['end_page'])})
    index={'schema_version':2,'directory_source':source,'directory_confirmed':source!='manual_scope_required','pdf_pages':len(pages),
           'chapters':chapters,'settings':settings,'chunk_count':len(chunks),'summary_method':'source_extract','model_calls':0}
    return index,pages,chunks


def link_assets(chunks,assets):
    for chunk in chunks:
        chunk['asset_ids']=[asset['id'] for asset in assets if asset.get('page') in chunk['page_refs']]


def scope_chunks(chunks,start,end):
    # Do not leak an adjacent page into a manually constrained teaching scope.
    selected=[chunk for chunk in chunks if chunk.get('page_refs') and all(start<=page<=end for page in chunk['page_refs'])]
    counts={}
    for chunk in selected:counts[chunk['semantic_group_id']]=counts.get(chunk['semantic_group_id'],0)+1
    return [{**chunk,'scope_continuation_incomplete':counts[chunk['semantic_group_id']]<chunk['part_count']} for chunk in selected]
