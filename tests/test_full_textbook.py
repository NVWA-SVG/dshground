import json
import sqlite3
import pytest
from teacher_prep.knowledge import KnowledgeBase
from teacher_prep.service import Service
from teacher_prep.exporters import sha256
from teacher_prep.import_limits import import_limits
from teacher_prep.pdf_book import pdf_structure
from book_fixture import make_book


def setup_book(tmp_path,megabytes=0):
    file=make_book(tmp_path/'inputs/book.pdf',megabytes)
    knowledge=KnowledgeBase(Service(tmp_path))
    return knowledge,file


def test_large_pdf_over_25mb_is_imported_without_model_calls(tmp_path):
    knowledge,file=setup_book(tmp_path,26)
    assert file.stat().st_size>25*1024*1024
    result=knowledge.create(file,{'title':'整册','grade':'三年级','subject':'数学'},'large_book')
    assert result['bytes']==file.stat().st_size and result['pdf_pages']==10
    assert result['sha256']==sha256(file)
    assert result['chunk_count']>0 and result['asset_count']>0
    original=knowledge.directory(result['knowledge_id'])/'original.pdf'
    assert sha256(original)==sha256(file)


def test_outline_ranges_include_special_topic_and_keep_review_in_unit(tmp_path):
    _,file=setup_book(tmp_path)
    data=pdf_structure(file)
    assert [(x['start_page'],x['end_page'],x['kind']) for x in data['suggested_ranges']]==[(2,3,'unit'),(4,5,'unit'),(6,6,'section'),(7,9,'unit')]
    assert 'PDF' in data['page_numbering']


def test_unit_isolated_retrieval_provenance_deduplication_and_original_unchanged(tmp_path):
    knowledge,file=setup_book(tmp_path)
    original_hash=sha256(file)
    book=knowledge.create(file,{'title':'整册','subject':'数学','grade':'三年级'},'book')
    result=knowledge.create_unit(book['knowledge_id'],4,5,'第二单元','unit_two')
    repeated=knowledge.create_unit(book['knowledge_id'],4,5,'第二单元','unit_retry_new_key')
    assert result['knowledge_id']==repeated['knowledge_id']
    assert len(knowledge.list()['entries'])==2
    assert result['pdf_pages']==2
    assert result['source_book']['start_page']==4 and result['source_book']['end_page']==5
    text=knowledge.text(result['knowledge_id'])['text']
    assert 'SECOND_UNIT_ADDITION' in text and 'OTHER_UNIT_CONTENT' not in text
    context=knowledge.context([result['knowledge_id']],'SECOND_UNIT_ADDITION')
    assert [row['id'] for row in context['entries']]==[result['knowledge_id']]
    assert context['entries'][0]['source_book']['knowledge_id']==book['knowledge_id']
    assert all(item['source_page'] in (4,5) for item in knowledge.assets(result['knowledge_id']))
    assert sha256(file)==original_hash
    assert not list((tmp_path/'inputs').glob('unit_*'))
    # A connection retry must not repeat expensive extraction or clear metadata.
    again=knowledge.create(file,{'title':'整册','subject':'数学','grade':'三年级'},'book_new_request')
    assert again['knowledge_id']==book['knowledge_id']


@pytest.mark.parametrize('start,end',[(0,2),(4,3),(1,11),(True,2),(1.5,3)])
def test_invalid_unit_ranges_do_not_leave_temporary_files(tmp_path,start,end):
    knowledge,file=setup_book(tmp_path)
    book=knowledge.create(file,{'title':'整册'},'book')
    with pytest.raises(ValueError):knowledge.create_unit(book['knowledge_id'],start,end,'错误','invalid')
    assert len(knowledge.list()['entries'])==1
    assert not list((tmp_path/'inputs').glob('unit_*'))


def test_import_caps_are_configurable_and_other_files_stay_25mb(tmp_path):
    knowledge,file=setup_book(tmp_path,2)
    assert import_limits(tmp_path)['max_other_mb']==25
    config=tmp_path/'config';config.mkdir(exist_ok=True)
    settings=config/'knowledge-import.json'
    settings.write_text(json.dumps({'max_pdf_mb':1}),encoding='utf-8')
    with pytest.raises(ValueError,match='1MB'):knowledge.create(file,{},'oversized')
    settings.write_text(json.dumps({'max_pdf_mb':4,'max_pdf_pages':5}),encoding='utf-8')
    with pytest.raises(ValueError,match='5页'):knowledge.create(file,{},'too_many_pages')
    settings.write_text(json.dumps({'max_pdf_mb':-1}),encoding='utf-8')
    with pytest.raises(ValueError):import_limits(tmp_path)
    assert knowledge.list()['entries']==[]


def test_pdf_extraction_does_not_hold_sqlite_write_lock(tmp_path,monkeypatch):
    import teacher_prep.knowledge as module
    knowledge,file=setup_book(tmp_path)
    actual=module.extract_pdf_assets
    def checked(*args):
        with sqlite3.connect(knowledge.service.store.path,timeout=.2) as connection:
            connection.execute('BEGIN IMMEDIATE');connection.rollback()
        return actual(*args)
    monkeypatch.setattr(module,'extract_pdf_assets',checked)
    result=knowledge.create(file,{'title':'整册'},'outside_lock')
    assert result['asset_count']>0 and result['import_warnings']==[]


def test_pdf_without_bookmarks_has_manual_range_support(tmp_path):
    import fitz
    knowledge,file=setup_book(tmp_path)
    with fitz.open(file) as doc:
        doc.set_toc([]);doc.saveIncr()
    book=knowledge.create(file,{'title':'无书签'},'no_outline')
    assert knowledge.pdf_structure(book['knowledge_id'])['suggested_ranges']==[]
    assert knowledge.create_unit(book['knowledge_id'],4,5,'手动第二单元','manual')['pdf_pages']==2


def test_invisible_pdf_image_objects_do_not_abort_all_asset_extraction(tmp_path,monkeypatch):
    import fitz
    from PIL import Image
    knowledge,file=setup_book(tmp_path)
    actual=fitz.Page.get_image_info
    def with_invisible_image(page,*args,**kwargs):
        return [{'bbox':(2000,2000,2200,2200)},*actual(page,*args,**kwargs)]
    monkeypatch.setattr(fitz.Page,'get_image_info',with_invisible_image)
    result=knowledge.create(file,{'title':'带不可见对象的教材'},'invisible_object')
    assert result['asset_count']>0 and result['import_warnings']==[]
    for asset in knowledge.assets(result['knowledge_id']):
        with Image.open(knowledge.asset_file(result['knowledge_id'],asset['file'])) as image:
            assert image.width>0 and image.height>0


def test_bookmark_one_page_early_is_corrected_against_actual_chapter_heading(tmp_path):
    import fitz
    path=tmp_path/'incorrect_bookmarks.pdf'
    with fitz.open() as document:
        for page_number in range(1,9):
            page=document.new_page()
            heading={2:'1 时、分、秒',5:'万以内的加法和减法（一）2',7:'多位数乘一位数 3'}.get(page_number,'练习题')
            page.insert_text((70,70),heading,fontname='china-s',fontsize=16)
        document.set_toc([[1,'1 时、分、秒',2],[1,'2 万以内的加法和减法（一）',4],[1,'3 多位数的乘一位数',7]])
        document.save(path)
    data=pdf_structure(path)
    assert [(item['start_page'],item['end_page']) for item in data['suggested_ranges']]==[(2,4),(5,6),(7,8)]
    assert data['suggested_ranges'][1]['bookmark_page']==4
    assert data['suggested_ranges'][1]['page_verified_by']=='nearby_heading'
    assert data['suggested_ranges'][2]['title']=='3 多位数乘一位数'
