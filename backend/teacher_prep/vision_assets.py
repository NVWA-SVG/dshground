"""Transactional description overlays shared by a book and its chapter scopes."""
import re
from .store import now
from .exporters import sha256 as file_sha256


class VisionAssets:
    def vision_descriptions(self,knowledge_id):
        with self.service.store.connect() as db:
            return {row['sha256']:{'description':row['description'],'vision_model':row['model'],'described_at':row['updated'],
                                    'vision_cache_key':row['cache_key'],'description_source':'vision_queue'}
                    for row in db.execute('SELECT * FROM asset_descriptions WHERE owner_id=?',(knowledge_id,))}

    def vision_inventory(self,knowledge_id,chapter_id=None):
        entry=self.get(knowledge_id);owner=self._owner(knowledge_id);assets=self.assets(knowledge_id)
        start,end=self._scope_range(knowledge_id);chapters=[]
        if self._index_file(knowledge_id).is_file():chapters=self.book_index(knowledge_id)['chapters']
        scope_title=entry['title']
        if chapter_id:
            chapter=next((row for row in chapters if row['id']==chapter_id),None)
            if not chapter:raise ValueError('章节不在所选教材范围内')
            start,end=max(start,chapter['start_page']),min(end,chapter['end_page']);scope_title=chapter['title']
            assets=[asset for asset in assets if start<=asset.get('source_page',asset.get('page',0))<=end]
        return {'knowledge_id':knowledge_id,'root_id':owner['id'],'book_title':owner['title'],'scope_title':scope_title,
                'start_page':start,'end_page':end,'chapter_id':chapter_id,'chapters':[
                    {key:row[key] for key in ('id','title','start_page','end_page')} for row in chapters],
                'assets':[{**asset,'knowledge_id':knowledge_id,'source_page':asset.get('source_page',asset.get('page',0))} for asset in assets]}

    def publish_vision_description(self,knowledge_id,image_hash,description,model,cache_key):
        if not re.fullmatch(r'[a-f0-9]{64}',str(image_hash)) or not re.fullmatch(r'[a-f0-9]{64}',str(cache_key)):
            raise ValueError('图片或识图缓存标识不正确')
        description=str(description or '').strip();model=str(model or '').strip()
        if not 1<=len(description)<=800 or not 1<=len(model)<=120:raise ValueError('识图描述或模型名称不正确')
        owner=self._owner(knowledge_id)
        candidate=next((asset for asset in self.assets(owner['id']) if asset.get('sha256')==image_hash and asset.get('kind')!='question'),None)
        if not candidate:raise ValueError('图片已移除或不属于当前教材，请重新检查任务范围')
        if file_sha256(self.asset_file(owner['id'],candidate['file']))!=image_hash:raise ValueError('图片内容已变化，未写入旧图片的识别结果')
        with self.service.store.transaction() as db:
            db.execute('''INSERT INTO asset_descriptions VALUES(?,?,?,?,?,?)
                          ON CONFLICT(owner_id,sha256) DO NOTHING''',(owner['id'],image_hash,description,model,cache_key,now()))
        return {'saved':True,'root_id':owner['id'],'sha256':image_hash}
