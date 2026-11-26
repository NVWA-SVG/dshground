import json
import shutil
from pathlib import Path
import fitz
import pytest
from teacher_prep.book_index import build_book_index,chunking_settings
from teacher_prep.knowledge import KnowledgeBase
from teacher_prep.service import Service
from teacher_prep.retrieval import select_excerpts
from teacher_prep.exporters import sha256
from teacher_prep.pdf_book import save_page_range
from teacher_prep.presentations import Presentations
from book_fixture import make_book

ROOT=Path(__file__).resolve().parents[1]


def semantic_book(path,bookmarks=True,printed=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    with fitz.open() as document:
        if printed:
            page=document.new_page();page.insert_text((60,70),'目录',fontname='china-s',fontsize=20)
            page.insert_text((60,110),'1 时分秒 ........ 1\n2 长方形 ........ 4',fontname='china-s',fontsize=14)
        first=1+int(printed)
        for heading,body in [
            ('1 时分秒','1分钟是60秒。认识时针、分针和秒针。'*20),
            ('例1 计算经过时间','从8时20分到8时50分经过多久？\n解答：同一小时内用结束时刻减开始时刻。'*12+'最终答案是30分钟。'),
            ('做一做','题目一：观察钟面。\n题目二：计算经过时间。\n题目三：说明你的方法。\n题目四：检查结果。'),
            ('2 长方形','长方形有四条边，对边相等。'*20),
            ('练习一','先量一量，再计算周长。长是6厘米，宽是4厘米。'*12)]:
            page=document.new_page();page.insert_text((60,70),heading,fontname='china-s',fontsize=20)
            page.insert_textbox(fitz.Rect(60,110,530,760),body,fontname='china-s',fontsize=12)
        if bookmarks:document.set_toc([[1,'1 时分秒',first],[2,'例1 计算经过时间',first+1],[1,'2 长方形',first+3]])
        document.save(path)
    return path


def test_directory_first_index_preserves_example_answer_practice_and_provenance(tmp_path):
    file=semantic_book(tmp_path/'book.pdf');phases=[]
    index,pages,chunks=build_book_index(file,'教材',tmp_path,lambda phase,counts:phases.append(phase))
    assert phases.index('directory')<phases.index('reading')<phases.index('chunking')
    assert len(index['chapters'])==2 and index['model_calls']==0
    example=next(chunk for chunk in chunks if '最终答案是30分钟' in chunk['text'])
    assert '从8时20分到8时50分' in example['text'] and '解答' in example['text']
    assert example['kind']=='example' and example['atomic']
    practice=next(chunk for chunk in chunks if '题目一' in chunk['text'])
    assert practice['kind']=='practice' and all(word in practice['text'] for word in ('题目二','题目三','题目四'))
    for chunk in chunks:
        chapter=next(item for item in index['chapters'] if item['id']==chunk['chapter_id'])
        assert all(chapter['start_page']<=page<=chapter['end_page'] for page in chunk['page_refs'])
        assert chunk['block_refs'] and chunk['path'][0]=='教材'


def test_long_example_has_explicit_continuations_and_retrieval_keeps_them(tmp_path):
    file=semantic_book(tmp_path/'book.pdf');config=tmp_path/'config';config.mkdir()
    (config/'knowledge-chunking.json').write_text(json.dumps({'target_chars':180,'min_chars':80,'max_chars':250,'atomic_max_chars':300}),encoding='utf-8')
    index,pages,chunks=build_book_index(file,'教材',tmp_path)
    hit=next(chunk for chunk in chunks if '最终答案' in chunk['text']);assert hit['part_count']>1
    parts=[{**chunk,'knowledge_id':'knowledge_book','chunk_id':chunk['id'],'chunk':i} for i,chunk in enumerate(chunks) if chunk['semantic_group_id']==hit['semantic_group_id']]
    result=select_excerpts(parts,'最终答案',max_chars=8000,limit=20,neighbors=False)
    assert [chunk['part_index'] for chunk in result['excerpts']]==list(range(1,hit['part_count']+1))
    assert all(len(chunk['text'])<=250 for chunk in parts)
    bounded=select_excerpts(parts,'最终答案',max_chars=300,limit=1,neighbors=False)
    assert '最终答案' in bounded['excerpts'][0]['text']


def test_atomic_example_is_not_resliced_by_legacy_1000_character_limit():
    text='完整例题'+('计算步骤。'*210)+'最终答案。'
    result=select_excerpts([{'knowledge_id':'a','chunk':1,'text':text,'atomic':True}],'最终答案',max_chars=3000)
    assert len(result['excerpts'])==1 and result['excerpts'][0]['text']==text
    assert select_excerpts([{'knowledge_id':'a','chunk':1,'text':text,'atomic':True}],'最终答案',max_chars=300)['excerpts']==[]


def test_scope_shares_files_and_parent_plus_child_does_not_broaden_retrieval(tmp_path):
    file=make_book(tmp_path/'inputs/book.pdf');kb=KnowledgeBase(Service(tmp_path))
    book=kb.create(file,{'title':'整册'},'root');unit=kb.create_unit(book['knowledge_id'],4,5,'第二单元','scope')
    assert unit['storage_mode']=='scope' and unit['bytes']==0
    assert not (kb.directory(unit['knowledge_id'])/'original.pdf').exists()
    assert len(list((tmp_path/'data/knowledge').rglob('original.pdf')))==1
    assets=kb.assets(unit['knowledge_id'],include_path=True);assert assets
    assert all(kb.directory(book['knowledge_id']).resolve() in Path(asset['path']).resolve().parents for asset in assets)
    context=kb.context([book['knowledge_id'],unit['knowledge_id']],'SECOND_UNIT_ADDITION')
    assert context['excerpts'] and all(item['knowledge_id']==unit['knowledge_id'] for item in context['excerpts'])
    assert all(set(item['page_refs'])<={4,5} for item in context['excerpts'])
    assert 'OTHER_UNIT_CONTENT' not in kb.text(unit['knowledge_id'])['text']
    with pytest.raises(ValueError,match='引用'):kb.delete(book['knowledge_id'])
    with pytest.raises(ValueError):kb.asset_file(unit['knowledge_id'],'../original.pdf')


def test_repeated_illustration_has_page_occurrences_without_repeated_pixels(tmp_path):
    file=make_book(tmp_path/'inputs/book.pdf');kb=KnowledgeBase(Service(tmp_path))
    book=kb.create(file,{'title':'重复题图'},'root');assets=kb.assets(book['knowledge_id'])
    assert {item['page'] for item in assets}==set(range(1,11))
    assert len({item['file'] for item in assets})<len(assets)
    alias=kb.create_unit(book['knowledge_id'],4,5,'第4—5页','scope')
    assert {item['source_page'] for item in kb.assets(alias['knowledge_id'])}=={4,5}


def test_teacher_annotations_on_scope_do_not_replace_book_annotations(tmp_path):
    file=make_book(tmp_path/'inputs/book.pdf');kb=KnowledgeBase(Service(tmp_path));book=kb.create(file,{'title':'整册'},'root')
    unit=kb.create_unit(book['knowledge_id'],4,5,'范围','scope');item=kb.assets(unit['knowledge_id'])[0]
    original=kb.assets(book['knowledge_id'])[0]['caption']
    kb.update_asset(unit['knowledge_id'],item['id'],{'caption':'老师补充的图注','description':'本班用法'})
    assert kb.assets(unit['knowledge_id'])[0]['caption']=='老师补充的图注'
    assert kb.assets(book['knowledge_id'])[0]['caption']==original


def test_unbookmarked_textbook_reads_printed_contents_and_verifies_real_pages(tmp_path):
    file=semantic_book(tmp_path/'printed.pdf',bookmarks=False,printed=True)
    index,pages,chunks=build_book_index(file,'教材',tmp_path)
    assert index['directory_source']=='printed_contents_with_heading_check'
    assert [chapter['start_page'] for chapter in index['chapters']]==[2,5]


def test_missing_contents_is_explicit_and_does_not_invent_chapters(tmp_path):
    file=semantic_book(tmp_path/'plain.pdf',bookmarks=False)
    index,pages,chunks=build_book_index(file,'无目录教材',tmp_path)
    assert index['directory_confirmed'] is False and len(index['chapters'])==1
    assert index['chapters'][0]['kind']=='document' and chunks


def test_existing_asset_ids_and_course_references_survive_scope_migration(tmp_path):
    kb=KnowledgeBase(Service(tmp_path));source=make_book(tmp_path/'inputs/book.pdf')
    root=kb.create(source,{'title':'整册'},'root')
    fragment=tmp_path/'inputs/legacy.pdf';save_page_range(source,fragment,4,5)
    old=kb.create(fragment,{'title':'旧第二单元'},'legacy');old_id=old['knowledge_id'];old_assets=kb.assets(old_id,include_path=True)
    kb.update_asset(old_id,old_assets[0]['id'],{'description':'教师已确认的描述'})
    request=json.loads((ROOT/'examples/subject/request.json').read_text(encoding='utf-8'));request['knowledge_ids']=[old_id]
    lesson=kb.service.create(request,'lesson')
    design=json.loads((ROOT/'examples/subject/design.json').read_text(encoding='utf-8'));design['knowledge_ids']=[old_id]
    version=kb.service.save_design(lesson['lesson_id'],design,'design');kb.service.export(version['version_id'])
    hashes={asset['id']:sha256(Path(asset['path'])) for asset in old_assets}
    shutil.copytree(kb.directory(old_id),tmp_path/'backup')
    kb.pool_legacy_assets(root['knowledge_id'],[{'knowledge_id':old_id,'start_page':4,'end_page':5}])
    migrated=kb.adopt_scope(old_id,root['knowledge_id'],4,5)
    assert migrated['active_bytes_released']>0
    assert not (kb.directory(old_id)/'original.pdf').exists()
    after=kb.assets(old_id,include_path=True);assert {asset['id']:sha256(Path(asset['path'])) for asset in after}==hashes
    assert after[0]['description']=='教师已确认的描述'
    kb.refresh_assets(root['knowledge_id'])
    assert {asset['id']:sha256(Path(asset['path'])) for asset in kb.assets(old_id,include_path=True)}==hashes
    assert kb.service.get('lesson',lesson['lesson_id'])['payload']['knowledge_ids']==[old_id]
    presentation=Presentations(kb.service).compose_manual(version['version_id'],'task_migrated',{'1':[old_assets[0]['id']]})
    assert presentation['valid'] and presentation['image_count']>=1


def test_invalid_chunk_configuration_is_rejected(tmp_path):
    config=tmp_path/'config';config.mkdir();(config/'knowledge-chunking.json').write_text('{"target_chars": 10}',encoding='utf-8')
    with pytest.raises(ValueError):chunking_settings(tmp_path)


def test_heading_alone_is_attached_to_teaching_content(tmp_path):
    path=tmp_path/'heading.pdf'
    with fitz.open() as document:
        page=document.new_page();page.insert_text((60,70),'1 加法',fontname='china-s',fontsize=24)
        page.insert_text((60,140),'例1',fontname='china-s',fontsize=20)
        page.insert_text((60,180),'题目：计算35加34。解答：先加整十数，再加个位。',fontname='china-s',fontsize=12)
        document.set_toc([[1,'1 加法',1]]);document.save(path)
    _,_,chunks=build_book_index(path,'教材',tmp_path)
    assert chunks and all(chunk['text'].strip() not in ('加法','1 加法','例1') for chunk in chunks)
    assert '计算35加34' in chunks[0]['text'] and '例1' in chunks[0]['text']
