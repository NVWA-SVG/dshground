"""Chapter/range references into one stored PDF and one asset collection."""
import json
import re
from pathlib import Path
from uuid import uuid4
from .store import identifier,now,digest
from .book_index import build_book_index,scope_chunks,link_assets


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)


class BookScopes:
    def _owner(self,knowledge_id):
        entry=self.get(knowledge_id);visited=set()
        while entry.get('storage_mode')=='scope':
            if entry['id'] in visited:raise ValueError('教材范围引用形成循环')
            visited.add(entry['id']);entry=self.get(entry['scope']['root_id'])
        return entry

    def _scope_range(self,knowledge_id):
        entry=self.get(knowledge_id)
        if entry.get('storage_mode')=='scope':return entry['scope']['start_page'],entry['scope']['end_page']
        return 1,entry.get('pdf_pages',0)

    def _index_file(self,knowledge_id):return self.directory(self._owner(knowledge_id)['id'])/'book-index.json'

    def book_index(self,knowledge_id):
        entry=self.get(knowledge_id);owner=self._owner(knowledge_id);file=self.directory(owner['id'])/'book-index.json'
        if not file.is_file():raise ValueError('该资料尚无章节索引，请先重新整理教材')
        index=json.loads(file.read_text(encoding='utf-8'));start,end=self._scope_range(knowledge_id)
        chapters=[chapter for chapter in index['chapters'] if chapter['end_page']>=start and chapter['start_page']<=end]
        return {**index,'knowledge_id':knowledge_id,'root_id':owner['id'],'title':entry['title'],'chapters':chapters,
                'scope_start_page':start,'scope_end_page':end}

    def _structured_chunks(self,knowledge_id):
        owner=self._owner(knowledge_id);file=self.directory(owner['id'])/'chunks.json'
        items=json.loads(file.read_text(encoding='utf-8')) if file.exists() else []
        if not items or not isinstance(items[0],dict):return None
        start,end=self._scope_range(knowledge_id)
        result=scope_chunks(items,start,end)
        title=self.get(knowledge_id)['title']
        return [{**item,'knowledge_id':knowledge_id,'canonical_knowledge_id':owner['id'],'chunk_id':item['id'],
                 'chunk':i,'title':title,'page_kind':'original_pdf_page'} for i,item in enumerate(result,1)]

    def chunk_preview(self,knowledge_id,chapter_id=None,offset=0,limit=30):
        items=self._structured_chunks(knowledge_id)
        if items is None:raise ValueError('这份资料尚未建立结构化分块')
        if chapter_id:items=[item for item in items if item['chapter_id']==chapter_id]
        offset=max(0,int(offset));limit=max(1,min(100,int(limit)))
        return {'knowledge_id':knowledge_id,'total':len(items),'offset':offset,'chunks':items[offset:offset+limit]}

    def _effective_ids(self,ids):
        ids=list(dict.fromkeys(ids or []));scoped_owners={self._owner(key)['id'] for key in ids if self.get(key).get('storage_mode')=='scope'}
        return [key for key in ids if self.get(key).get('storage_mode')=='scope' or key not in scoped_owners]

    def _scope_assets(self,knowledge_id,include_path=False):
        entry=self.get(knowledge_id);owner=self._owner(knowledge_id);start,end=self._scope_range(knowledge_id)
        references=self.directory(knowledge_id)/'assets.json'
        saved=json.loads(references.read_text(encoding='utf-8')) if references.exists() else []
        if saved and all(item.get('storage_knowledge_id') for item in saved):items=saved
        else:
            items=[{**item,'knowledge_id':knowledge_id,'source_page':item['page'],'page':item['page']-start+1,
                    'storage_knowledge_id':owner['id'],'storage_file':item['file']}
                   for item in self.assets(owner['id']) if start<=item.get('page',0)<=end]
        overrides_file=self.directory(knowledge_id)/'asset-overrides.json'
        overrides=json.loads(overrides_file.read_text(encoding='utf-8')) if overrides_file.exists() else {}
        owner_by_hash={item['sha256']:item for item in self.assets(owner['id'])}
        result=[]
        for item in items:
            target=self.asset_file(item['storage_knowledge_id'],item['storage_file'])
            if not target.is_file():continue
            value={**item,**overrides.get(item['id'],{})}
            for key in ('description','vision_model','described_at'):
                if not value.get(key) and owner_by_hash.get(item['sha256'],{}).get(key):value[key]=owner_by_hash[item['sha256']][key]
            if include_path:value['path']=str(target)
            result.append(value)
        return result

    def _scope_asset_file(self,knowledge_id,name):
        # A scope can serve only its own allowlisted asset references.
        for item in self._scope_assets(knowledge_id):
            if item['file']==name:return self.asset_file(item['storage_knowledge_id'],item['storage_file'])
        raise ValueError('素材不在当前教材范围内')

    def _scope_update_asset(self,knowledge_id,asset_id,fields):
        target=next((item for item in self.assets(knowledge_id) if item['id']==asset_id),None)
        if target is None:raise ValueError('素材不存在')
        file=self.directory(knowledge_id)/'asset-overrides.json';overrides=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
        allowed={'caption':500,'dialogue':500,'description':1000,'model':100,'vision_model':100,'described_at':100}
        update={key:str(value or '').strip()[:allowed[key]] for key,value in fields.items() if key in allowed}
        overrides[asset_id]={**overrides.get(asset_id,{}),**update};write_json(file,overrides)
        return {**target,**update}

    def _scope_text(self,knowledge_id):
        owner=self._owner(knowledge_id);start,end=self._scope_range(knowledge_id)
        pages=json.loads((self.directory(owner['id'])/'pages.json').read_text(encoding='utf-8'))
        text='\n'.join(f"[原册PDF第{page['page']}页]\n{page['text']}" for page in pages if start<=page['page']<=end)
        entry=self.get(knowledge_id)
        return {'id':knowledge_id,'title':entry['title'],'characters':len(text),'chunk_count':len(self._structured_chunks(knowledge_id) or []),'text':text[:200000],'truncated':len(text)>200000}

    def _create_scope(self,knowledge_id,start,end,title,key):
        parent=self.get(knowledge_id);owner=self._owner(knowledge_id)
        if owner.get('storage_mode')!='book':raise ValueError('该教材尚未整理章节，请先更新索引')
        parent_start,parent_end=self._scope_range(knowledge_id)
        if any(isinstance(v,bool) or not isinstance(v,int) for v in (start,end)):raise ValueError('PDF起止页必须为整数')
        if start<1 or end<start or end>parent_end-parent_start+1:raise ValueError('页码范围不正确')
        start,end=parent_start+start-1,parent_start+end-1
        title=str(title or f"{owner['title'][:80]} · 第{start}—{end}页").strip()
        if not title or len(title)>120:raise ValueError('范围名称应为1—120字')
        source_book={'knowledge_id':owner['id'],'title':owner['title'],'sha256':owner['sha256'],'start_page':start,'end_page':end,'page_numbering':'original_pdf_page'}
        request={'sha256':source_book,'title':title,'subject':owner.get('subject',''),'grade':owner.get('grade','')}
        chunks=scope_chunks(self._structured_chunks(owner['id']) or [],start,end)
        pages=json.loads((self.directory(owner['id'])/'pages.json').read_text(encoding='utf-8'))
        assets=[item for item in self.assets(owner['id']) if start<=item.get('page',0)<=end]
        matching=[item['id'] for item in self.book_index(owner['id'])['chapters'] if item['start_page']==start and item['end_page']==end]
        payload={'title':title,'subject':owner.get('subject',''),'grade':owner.get('grade',''),'original_name':f'unit-{start}-{end}.pdf',
                 'sha256':digest(source_book),'fingerprint_kind':'scope_digest','characters':sum(len(page['text']) for page in pages if start<=page['page']<=end),
                 'chunk_count':len(chunks),'asset_count':len(assets),'bytes':0,'pdf_pages':end-start+1,'import_warnings':[],
                 'storage_mode':'scope','source_book':source_book,'scope':{'root_id':owner['id'],'start_page':start,'end_page':end,'chapter_ids':matching}}
        def action(db):
            for row in db.execute('SELECT id,payload FROM knowledge'):
                value=json.loads(row['payload'])
                if value.get('source_book')==source_book and value.get('title')==title:return {'knowledge_id':row['id'],**value}
            new_id=identifier('knowledge');db.execute('INSERT INTO knowledge VALUES(?,?,?)',(new_id,json.dumps(payload,ensure_ascii=False,sort_keys=True),now()))
            return {'knowledge_id':new_id,**payload}
        result=self.service.store.idempotent('knowledge_create',key,request,action)
        return {**result,**{name:value for name,value in self.get(result['knowledge_id']).items() if name not in ('id','created')}}

    def _index_summary(self,knowledge_id):
        file=self.directory(knowledge_id)/'book-index.json'
        if not file.exists():return {}
        data=json.loads(file.read_text(encoding='utf-8'))
        return {'chapter_count':len(data['chapters']),'directory_source':data['directory_source'],'index_version':2,
                'chapters':[{key:item[key] for key in ('id','title','start_page','end_page','chunk_count','overview')} for item in data['chapters']]}

    def rebuild_book_index(self,knowledge_id):
        owner=self._owner(knowledge_id);directory=self.directory(owner['id']);original=directory/'original.pdf'
        if not original.is_file():raise ValueError('这份资料没有PDF原件')
        index,pages,chunks=build_book_index(original,owner['title'],self.root)
        link_assets(chunks,self.assets(owner['id']))
        write_json(directory/'pages.json',pages);write_json(directory/'book-index.json',index);write_json(directory/'chunks.json',chunks)
        payload={key:value for key,value in owner.items() if key not in ('id','created')}
        payload.update({'storage_mode':'book','index_version':2,'chapter_count':len(index['chapters']),'chunk_count':len(chunks),'directory_source':index['directory_source']})
        with self.service.store.transaction() as db:db.execute('UPDATE knowledge SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False,sort_keys=True),owner['id']))
        return {'knowledge_id':owner['id'],**payload}

    def adopt_scope(self,knowledge_id,root_id,start,end):
        """Reuse an existing ID/asset IDs after caller has backed up its files."""
        entry=self.get(knowledge_id);owner=self.get(root_id)
        if entry.get('storage_mode')=='scope':return {'knowledge_id':knowledge_id,'already_shared':True}
        if knowledge_id==root_id or owner.get('storage_mode')!='book':raise ValueError('共享教材来源不正确')
        if not 1<=start<=end<=owner['pdf_pages']:raise ValueError('范围不正确')
        old_assets=self.assets(knowledge_id)
        lookup={}
        for item in self.assets(root_id):lookup.setdefault(item['sha256'],[]).append(item)
        refs=[]
        for old in old_assets:
            source_page=old.get('source_page') or start+old.get('page',1)-1
            candidates=lookup.get(old['sha256'],[])
            if not candidates or not start<=source_page<=end:raise ValueError('旧素材无法无损关联到整册，保留独立资料：'+old['id'])
            canonical=next((item for item in candidates if item['page']==source_page),candidates[0])
            refs.append({**{key:value for key,value in old.items() if key!='path'},'source_page':source_page,
                         'storage_knowledge_id':root_id,'storage_file':canonical['file']})
        directory=self.directory(knowledge_id).resolve();storage=(self.data/'knowledge').resolve()
        if not directory.is_relative_to(storage) or directory==storage:raise ValueError('旧教材目录不在存储范围内')
        payload={key:value for key,value in entry.items() if key not in ('id','created')}
        chunks=scope_chunks(self._structured_chunks(root_id) or [],start,end)
        chapter_ids=[chapter['id'] for chapter in self.book_index(root_id)['chapters'] if chapter['start_page']==start and chapter['end_page']==end]
        payload.update({'storage_mode':'scope','bytes':0,'asset_count':len(refs),'chunk_count':len(chunks),
                        'source_book':{'knowledge_id':root_id,'title':owner['title'],'sha256':owner['sha256'],'start_page':start,'end_page':end,'page_numbering':'original_pdf_page'},
                        'scope':{'root_id':root_id,'start_page':start,'end_page':end,'chapter_ids':chapter_ids}})
        before=sum(file.stat().st_size for file in directory.rglob('*') if file.is_file())
        write_json(directory/'assets.json',refs)
        with self.service.store.transaction() as db:db.execute('UPDATE knowledge SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False,sort_keys=True),knowledge_id))
        # Caller owns a complete backup. Remove only known duplicated storage;
        # retain ID, source provenance and all per-asset teacher annotations.
        import shutil
        assets_directory=(directory/'assets').resolve()
        if not assets_directory.is_relative_to(directory):raise ValueError('旧素材目录越界')
        if assets_directory.exists():shutil.rmtree(assets_directory)
        for name in ('original.pdf','extracted.txt','chunks.json','pages.json','book-index.json'):
            file=directory/name
            if file.exists() and file.resolve().is_relative_to(directory):file.unlink()
        after=sum(file.stat().st_size for file in directory.rglob('*') if file.is_file())
        return {'knowledge_id':knowledge_id,'retained_asset_ids':len(refs),'active_bytes_released':before-after}

    def pool_legacy_assets(self,root_id,specifications):
        """Move exact old PNG variants into one shared pool, preserving bytes."""
        import shutil
        owner=self.get(root_id);directory=self.directory(root_id);assets=self.assets(root_id)
        usage=next(entry['used_by'] for entry in self.list()['entries'] if entry['id']==root_id)
        preserve_root=bool(usage['lessons'] or usage['versions'])
        shared=[];used_ids=set()
        for specification in specifications:
            start=specification['start_page']
            for old in self.assets(specification['knowledge_id'],include_path=True):
                page=old.get('source_page') or start+old.get('page',1)-1
                filename='shared-'+old['sha256']+'.png';target=directory/'assets'/filename
                if not target.exists():shutil.copy2(old['path'],target)
                canonical=next((item for item in assets if item['sha256']==old['sha256']),None)
                if canonical is None:
                    box=old.get('bbox',[])
                    canonical=next((item for item in assets if not preserve_root and item['id'] not in used_ids and item.get('page')==page and len(box)==len(item.get('bbox',[]))==4 and max(abs(a-b) for a,b in zip(box,item['bbox']))<1.1),None)
                    if canonical is None:
                        canonical={**{key:value for key,value in old.items() if key not in ('path','source_page')},'id':'asset_'+root_id.removeprefix('knowledge_')+'_shared_'+old['sha256'][:16],'knowledge_id':root_id,'page':page}
                        assets.append(canonical)
                    canonical.update({'file':filename,'sha256':old['sha256'],'width':old['width'],'height':old['height'],'bytes':old['bytes']})
                else:canonical['file']=filename
                used_ids.add(canonical['id'])
                for key in ('description','described_at','vision_model'):
                    if old.get(key) and not canonical.get(key):canonical[key]=old[key]
                shared.append({**canonical})
        shared=list({item['id']:item for item in shared}.values())
        files_by_hash={item['sha256']:item['file'] for item in shared}
        for item in assets:
            if item['sha256'] in files_by_hash:item['file']=files_by_hash[item['sha256']]
        write_json(directory/'shared-assets.json',shared);write_json(directory/'assets.json',assets)
        payload={key:value for key,value in owner.items() if key not in ('id','created')};payload['asset_count']=len(assets)
        with self.service.store.transaction() as db:db.execute('UPDATE knowledge SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False,sort_keys=True),root_id))
        self._prune_render_files(root_id,assets)
        return {'shared_records':len(shared),'shared_files':len({item['file'] for item in shared})}

    def _merge_shared_assets(self,knowledge_id,assets):
        file=self.directory(knowledge_id)/'shared-assets.json'
        if not file.exists():return assets
        shared=json.loads(file.read_text(encoding='utf-8'));by_id={item['id']:item for item in shared}
        merged=[by_id.pop(item['id'],item) for item in assets]+list(by_id.values())
        files_by_hash={item['sha256']:item['file'] for item in shared}
        for item in merged:
            if item['sha256'] in files_by_hash:item['file']=files_by_hash[item['sha256']]
        return merged

    def _prune_render_files(self,knowledge_id,assets):
        directory=(self.directory(knowledge_id)/'assets').resolve()
        if not directory.is_relative_to((self.data/'knowledge').resolve()):raise ValueError('素材目录越界')
        keep={item['file'] for item in assets}
        for file in directory.glob('page-*.png'):
            if file.name not in keep and file.resolve().parent==directory:file.unlink()
