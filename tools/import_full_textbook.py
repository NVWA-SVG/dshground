"""Import a teacher-provided full PDF, optionally creating bookmark units."""
import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from teacher_prep.service import Service
from teacher_prep.knowledge import KnowledgeBase
from teacher_prep.import_limits import import_limits
from teacher_prep.pdf_book import pdf_structure
from teacher_prep.exporters import sha256


def main():
    parser=argparse.ArgumentParser(description='导入教师本机整册PDF，并按书签建立单元知识库')
    parser.add_argument('--file',required=True);parser.add_argument('--root',default=str(ROOT))
    parser.add_argument('--title',default='');parser.add_argument('--subject',default='数学');parser.add_argument('--grade',default='三年级')
    parser.add_argument('--split-units',action='store_true');parser.add_argument('--preview',action='store_true')
    args=parser.parse_args();root=Path(args.root).resolve();source=Path(args.file).expanduser().resolve()
    if not source.is_file() or source.suffix.lower()!='.pdf':raise ValueError('请提供本机已有PDF文件的完整路径')
    limits=import_limits(root)
    if source.stat().st_size>limits['max_pdf_mb']*1024*1024:raise ValueError(f"PDF上限为{limits['max_pdf_mb']}MB")
    structure=pdf_structure(source,limits['max_pdf_pages'])
    if args.preview:
        print(json.dumps({'ok':True,'bytes':source.stat().st_size,**structure},ensure_ascii=False));return
    knowledge=KnowledgeBase(Service(root));temporary=root/'inputs'/('import_'+uuid.uuid4().hex)
    temporary.mkdir(parents=True)
    try:
        copied=temporary/source.name;shutil.copy2(source,copied)
        title=args.title or source.stem
        book=knowledge.create(copied,{'title':title,'subject':args.subject,'grade':args.grade},'fullbook_'+sha256(source)[:32])
        book.update({name:value for name,value in knowledge.get(book['knowledge_id']).items() if name not in ('id','created')})
        units=[]
        if args.split_units:
            for unit in structure['suggested_ranges']:
                key=f"outline_{book['knowledge_id']}_{unit['start_page']}_{unit['end_page']}"
                result=knowledge.create_unit(book['knowledge_id'],unit['start_page'],unit['end_page'],unit['title'],key)
                result.update({name:value for name,value in knowledge.get(result['knowledge_id']).items() if name not in ('id','created')})
                units.append({'knowledge_id':result['knowledge_id'],'title':result['title'],'kind':unit['kind'],'start_page':unit['start_page'],'end_page':unit['end_page'],'bookmark_page':unit['bookmark_page'],'page_verified_by':unit['page_verified_by'],'characters':result['characters'],'asset_count':result['asset_count']})
        report={'ok':True,'source_file':str(source),'book':book,'units':units,'pdf_pages':structure['pdf_pages'],'model_calls':0}
        destination=root/'docs'/'整册教材导入结果.json';destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        if temporary.resolve().is_relative_to((root/'inputs').resolve()) and temporary.name.startswith('import_'):
            shutil.rmtree(temporary,ignore_errors=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False));raise SystemExit(2)
