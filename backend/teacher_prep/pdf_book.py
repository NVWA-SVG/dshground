"""PDF page metadata and outline-derived teaching ranges; no guessed printed pages."""
import re


def _heading_core(value):
    value=re.sub(r'^\s*(?:第[一二三四五六七八九十百零〇0-9]+单元\s*|[一二三四五六七八九十0-9]+[\s、.．]+)','',value)
    value=re.sub(r'\s*[0-9]+\s*$','',value)
    return value.strip()


def _heading_key(value):
    return re.sub(r'[^\w]','',_heading_core(value)).replace('的','')


def _confirm_heading(document,item):
    """Check adjacent physical pages: some real PDFs have off-by-one bookmarks."""
    target=_heading_key(item['title']);page=item['start_page']
    for candidate in (page,page+1,page-1,page+2,page-2):
        if not 1<=candidate<=len(document):continue
        for line in document[candidate-1].get_text('text',sort=True).splitlines()[:12]:
            core=_heading_core(line)
            if core and _heading_key(core)==target:
                confirmed={**item,'bookmark_page':page,'start_page':candidate,'page_verified_by':'nearby_heading'}
                # Use the visible chapter title if the bookmark has a typo.
                prefix=re.match(r'^\s*([0-9]+)\s+',item['title'])
                if prefix and re.sub(r'[^\w]','',_heading_core(item['title']))!=re.sub(r'[^\w]','',core):
                    confirmed['title']=prefix.group(1)+' '+core
                return confirmed
    return {**item,'bookmark_page':page,'page_verified_by':'bookmark_only'}


def pdf_structure(path,max_pages=1000):
    import fitz
    with fitz.open(path) as document:
        if document.needs_pass:raise ValueError('暂不支持加密教材PDF')
        pages=len(document)
        if not pages:raise ValueError('教材PDF没有页面')
        if pages>max_pages:raise ValueError(f'教材PDF超过{max_pages}页，请先按册拆分')
        top=[{'title':str(title).strip(),'start_page':page} for level,title,page in document.get_toc() if level==1 and 1<=page<=pages]
        # Confirm title pages against the PDF itself before proposing boundaries.
        top=[_confirm_heading(document,item) for item in top]
    numbered=re.compile(r'^(?:第[一二三四五六七八九十百零〇0-9]+单元|[一二三四五六七八九十0-9]+[\s、.．])')
    auxiliary=re.compile(r'^(?:练习|整理和复习|整理与复习|自我评价)')
    front=re.compile(r'^(?:封面|版权|编者的话|目录|目\s*录|前言|扉页|致读者)')
    back=re.compile(r'^(?:后记|封底|出版说明)')
    boundaries=[];started=False
    for item in sorted(top,key=lambda row:row['start_page']):
        title=item['title']
        if numbered.search(title):
            started=True;boundaries.append({**item,'kind':'unit'})
        elif started and back.search(title):
            boundaries.append({**item,'kind':'stop'})
        elif started and not front.search(title) and not auxiliary.search(title):
            boundaries.append({**item,'kind':'section'})
    ranges=[]
    for index,item in enumerate(boundaries):
        if item['kind']=='stop':continue
        end=boundaries[index+1]['start_page']-1 if index+1<len(boundaries) else pages
        if end>=item['start_page']:
            ranges.append({**item,'end_page':end})
    return {'pdf_pages':pages,'suggested_ranges':ranges,'range_source':'pdf_bookmarks_with_nearby_heading_check','page_numbering':'PDF文件页序，从1开始；不等同于教材印刷页码'}


def save_page_range(source,destination,start,end,max_pages=1000):
    import fitz
    if isinstance(start,bool) or isinstance(end,bool) or not isinstance(start,int) or not isinstance(end,int):
        raise ValueError('PDF起止页必须为整数')
    with fitz.open(source) as original:
        if original.needs_pass:raise ValueError('暂不支持加密教材PDF')
        if len(original)>max_pages:raise ValueError(f'教材PDF超过{max_pages}页')
        if start<1 or end<start or end>len(original):raise ValueError(f'页码范围需在1至{len(original)}之间，且起始页不大于结束页')
        with fitz.open() as selected:
            selected.insert_pdf(original,from_page=start-1,to_page=end-1)
            selected.save(destination,garbage=4,deflate=True,no_new_id=True)
