import io
import json
from concurrent.futures import ThreadPoolExecutor
import fitz
import pytest
from PIL import Image,ImageDraw
from teacher_prep.knowledge import KnowledgeBase
from teacher_prep.service import Service


def fixture(tmp_path):
    source=tmp_path/'inputs'/'book.pdf';source.parent.mkdir(parents=True)
    with fitz.open() as document:
        for number in range(1,5):
            page=document.new_page();page.insert_text((60,60),f'Chapter {number}. One hour = ( ) minutes.')
            image=Image.new('RGB',(240,160),(number*40,100,180));ImageDraw.Draw(image).ellipse((40,10,180,150),fill='white')
            buffer=io.BytesIO();image.save(buffer,format='PNG');page.insert_image(fitz.Rect(60,130,300,290),stream=buffer.getvalue())
        document.set_toc([[1,'1 Time',1],[1,'2 Length',3]]);document.save(source)
    kb=KnowledgeBase(Service(tmp_path));root=kb.create(source,{'title':'测试教材'},'book')['knowledge_id']
    return kb,root


def test_queue_inventory_filters_physical_chapter_pages(tmp_path):
    kb,root=fixture(tmp_path);inventory=kb.vision_inventory(root);chapter=inventory['chapters'][1]
    selected=kb.vision_inventory(root,chapter['id']);assert selected['assets']
    assert all(chapter['start_page']<=a['source_page']<=chapter['end_page'] for a in selected['assets'])
    with pytest.raises(ValueError,match='章节'):kb.vision_inventory(root,'not_a_chapter')


def test_transactional_results_are_shared_and_preserve_teacher_overrides(tmp_path):
    kb,root=fixture(tmp_path);scope=kb.create_unit(root,1,2,'前两页','scope')['knowledge_id']
    images=[a for a in kb.assets(root) if a['kind']!='question'];assert len(images)>=4
    sha=images[0]['sha256'];scoped=next(a for a in kb.assets(scope) if a['sha256']==sha)
    kb.update_asset(scope,scoped['id'],{'description':'老师为本班写的用法'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda a:kb.publish_vision_description(root,a['sha256'],'画面有一个圆形。','test-model','a'*64),images))
    assert all(a['description']=='画面有一个圆形。' for a in kb.assets(root) if a['kind']!='question')
    assert next(a for a in kb.assets(scope) if a['sha256']==sha)['description']=='老师为本班写的用法'
    assert not any(a.get('description') for a in kb.assets(root) if a['kind']=='question')
    kb.publish_vision_description(root,sha,'重复提交不能覆盖原结果','test-model','b'*64)
    assert next(a for a in kb.assets(root) if a['sha256']==sha)['description']=='画面有一个圆形。'


def test_existing_root_description_and_image_bytes_are_preserved(tmp_path):
    kb,root=fixture(tmp_path);asset=next(a for a in kb.assets(root) if a['kind']!='question')
    original=kb.asset_file(root,asset['file']).read_bytes()
    kb.update_asset(root,asset['id'],{'description':'老师已有描述'})
    kb.publish_vision_description(root,asset['sha256'],'AI 描述','model','a'*64)
    assert next(a for a in kb.assets(root) if a['id']==asset['id'])['description']=='老师已有描述'
    assert kb.asset_file(root,asset['file']).read_bytes()==original
    kb.asset_file(root,asset['file']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='变化'):kb.publish_vision_description(root,asset['sha256'],'旧图描述','model','a'*64)
    with pytest.raises(ValueError):kb.publish_vision_description(root,'../bad','描述','model','a'*64)


def test_scope_results_reach_other_scopes_without_copying_files(tmp_path):
    kb,root=fixture(tmp_path);a=kb.create_unit(root,1,2,'范围A','scope_a')['knowledge_id'];b=kb.create_unit(root,1,2,'范围B','scope_b')['knowledge_id']
    asset=next(i for i in kb.assets(a) if i['kind']!='question')
    kb.publish_vision_description(a,asset['sha256'],'图中有圆形。','model','a'*64)
    assert next(i for i in kb.assets(b) if i['sha256']==asset['sha256'])['description']=='图中有圆形。'
    assert not (kb.directory(a)/'assets').exists()
