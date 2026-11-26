import hashlib
import json
from pathlib import Path
from .book_scopes import write_json
from .store import now


def _file(root,key):return Path(root)/'data/import-status'/(hashlib.sha256(str(key).encode('utf-8')).hexdigest()+'.json')


def report(root,key,phase,counts=None):
    write_json(_file(root,key),{'phase':phase,'counts':counts or {},'updated':now()})


def status(root,key):
    file=_file(root,key)
    return json.loads(file.read_text(encoding='utf-8')) if file.exists() else {'phase':'waiting','counts':{}}
