import json,sys
from pathlib import Path
project=Path(__file__).resolve().parents[1];sys.path.insert(0,str(project/'backend'))
from teacher_prep.presentations import PptVisualPlan
roots=[p for p in (project/'.test-runs').glob('teaching-preview-*') if (p/'data/ppt-runs/task_teaching_preview/brief.json').exists()]
latest=max(roots,key=lambda p:p.stat().st_mtime)
output=project/'.test-runs/teaching-preview-evidence'
(output/'context-input.json').write_text(json.dumps({'brief':json.loads((latest/'data/ppt-runs/task_teaching_preview/brief.json').read_text(encoding='utf-8')),'schema':PptVisualPlan.model_json_schema()},ensure_ascii=False),encoding='utf-8')
