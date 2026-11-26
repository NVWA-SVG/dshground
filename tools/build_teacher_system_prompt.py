"""Compile the canonical Markdown persona into DSH's supported prefix config."""
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
plugin=root/'plugins/teacher-workbench'
prompt=(plugin/'prompts/system.md').read_text(encoding='utf-8').strip()
patch=plugin/'cordis.patch.yml'
original=patch.read_text(encoding='utf-8-sig')
lines=original.splitlines()
starts=[i for i,line in enumerate(lines) if line.lstrip().startswith('prefix:')]
if len(starts)!=1:
    raise SystemExit('Expected exactly one teacher persona prefix; inspect preset before building')
start=starts[0]
indent=lines[start][:len(lines[start])-len(lines[start].lstrip())]
end=next((i for i in range(start+1,len(lines)) if lines[i].startswith(indent+'complete:')),None)
if end is None:
    raise SystemExit('Teacher persona complete setting not found')
replacement=[indent+'prefix: |-',*[indent+'  '+line for line in prompt.splitlines()]]
rendered='\n'.join([*lines[:start],*replacement,*lines[end:]])+'\n'
if '--check' in sys.argv:
    if original!=rendered:
        raise SystemExit('System prompt Markdown and compiled DSH preset are out of sync')
    print('Teacher system prompt and DSH persona are synchronized.')
else:
    patch.write_text(rendered,encoding='utf-8')
    print('Teacher system prompt compiled into DSH persona.')
