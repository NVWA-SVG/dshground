import json
from pathlib import Path

DEFAULT_IMPORT_LIMITS={'max_pdf_mb':512,'max_other_mb':25,'max_pdf_pages':1000}


def import_limits(root):
    path=Path(root)/'config/knowledge-import.json'
    raw=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if set(raw)-set(DEFAULT_IMPORT_LIMITS):raise ValueError('未知教材导入限制字段')
    limits={**DEFAULT_IMPORT_LIMITS,**raw}
    for key,value in limits.items():
        maximum=5000 if key=='max_pdf_pages' else 2048
        if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=maximum:
            raise ValueError(f'{key}需要为1至{maximum}的整数')
    return limits
