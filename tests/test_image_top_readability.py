import json
from pathlib import Path
from PIL import Image
from pptx import Presentation
from teacher_prep.service import Service
from teacher_prep.presentations import Presentations
from teacher_prep.teaching_visuals import image_top_regions
from teacher_prep.exporters import sha256

ROOT=Path(__file__).resolve().parents[1]
POINTS=['新年钟声敲响，10、9、8、7、6、5、4、3、2、1！','我们听到的“10、9、8…”是用什么单位计时的？','秒是比分更小的时间单位。','生活中哪些事情用秒来计时？']

def build(tmp_path,count=1):
    raw=json.loads((ROOT/'examples/subject/design.json').read_text(encoding='utf-8'));request=json.loads((ROOT/'examples/subject/request.json').read_text(encoding='utf-8'))
    raw['slides'][0].update(title='新年倒计时',points=POINTS,visual=None)
    service=Service(tmp_path);lesson=service.create(request,'lesson');version=service.save_design(lesson['lesson_id'],raw,'design');service.export(version['version_id'])
    pipeline=Presentations(service);prepared=pipeline.prepare(version['version_id'],'task_image_top');assets=[]
    for i in range(count):
        file=tmp_path/f'original-{i}.png';Image.new('RGB',(640,320),('navy','green')[i]).save(file)
        assets.append({'id':f'asset_{i}','kind':'scene','path':str(file),'caption':f'教材素材{i+1}'})
    plan={'style':'清晰教材图文','pages':[{'page_number':p['number'],'layout':'上图下文','layout_kind':'image_top','visual_prompt':'教材情境帮助观察',
          'teaching_goal':'观察原页面任务','student_task':'观察画面，说明你的想法。','learning_focus':'本页情境与问题','asset_reason':'保留教材原图帮助观察','density_note':'保留全部正文和观察空间',
          'asset_ids':[a['id'] for a in assets] if p['number']==1 else []} for p in prepared['brief']['pages']]}
    pipeline.save_prompt('task_image_top',plan,allowed_assets=assets);result=pipeline.compose_local(version['version_id'],'task_image_top',assets=assets)
    return Presentation(Path(result['directory'])/'课件.pptx'),assets

def test_four_line_image_top_page_reflows_without_dropping_original_or_small_body_text(tmp_path):
    top,image_height,text_top,text_height=image_top_regions(POINTS,True,True)
    assert 1.2<=image_height<1.85 and text_height>1.96
    deck,assets=build(tmp_path);slide=deck.slides[0]
    picture=next(s for s in slide.shapes if s.name=='Textbook original');assert sha256(assets[0]['path'])==__import__('hashlib').sha256(picture.image.blob).hexdigest()
    for point in POINTS:
        text=next(s for s in slide.shapes if s.has_text_frame and point.replace(' ','') in s.text.replace('\n','').replace(' ',''))
        assert min(r.font.size.pt for p in text.text_frame.paragraphs for r in p.runs)>=18
        assert text.top>picture.top+picture.height

def test_two_top_images_are_side_by_side_and_inside_slide(tmp_path):
    deck,_=build(tmp_path,2);images=[s for s in deck.slides[0].shapes if s.name=='Textbook original']
    assert len(images)==2;images.sort(key=lambda s:s.left)
    assert images[0].left+images[0].width<images[1].left
    assert images[0].top==images[1].top
    assert all(s.left>=0 and s.top>=0 and s.left+s.width<=deck.slide_width+10 and s.top+s.height<=deck.slide_height+10 for s in deck.slides[0].shapes)
