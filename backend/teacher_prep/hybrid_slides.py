"""Render the agent's page plan with native text, original assets and AI images."""
from math import ceil

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches
from .teaching_diagrams import draw_diagram
from .teaching_visuals import image_top_regions

PALETTES = {'navy': ('246C88', 'E3EEF2'), 'mint': ('327665', 'E5EFEB'), 'amber': ('946222', 'F5EDDD')}


def make_hybrid_pptx(design, path, label, pages, assets_by_page, plan):
    from .exporters import textbox, wrap
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.3333), Inches(7.5)
    accent, tint = PALETTES.get(plan.get('palette'), PALETTES['navy'])
    activities = {a.id: a for a in design.activities}
    visuals = {p['page_number']: p for p in plan['pages']}

    def box(slide, region, text, preferred=23, bold=False, color='263B45',minimum=10):
        x, y, w, h = region
        for size in range(preferred, minimum-1, -1):
            lines = wrap(text, (w-.1)*72/size*.94)
            if len(lines)*(size/72*1.15+3/72)+.06 <= h:
                return textbox(slide, x, y, w, h, '\n'.join(lines), size, color, bold=bold)
        raise ValueError('页面文字超过可读排版范围，请缩短课件要点后重新生成')

    def card(slide, region):
        x, y, w, h = region
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        shape.fill.solid(); shape.fill.fore_color.rgb = RGBColor.from_string(tint)
        shape.line.fill.background()

    def pictures(slide, items, region,horizontal=False):
        x, y, w, h = region
        gap = .16
        slot = h if horizontal else (h-gap*(len(items)-1))/len(items)
        cell_width=(w-gap*(len(items)-1))/len(items) if horizontal else w
        for i, item in enumerate(items):
            cell_x=x+i*(cell_width+gap) if horizontal else x
            cell_y=y if horizontal else y+i*(slot+gap)
            with Image.open(item['path']) as bitmap:
                width, height = bitmap.size
            # Contain the entire original picture; textbook contents are not cropped.
            caption = 'AI 生成插画' if item.get('kind') == 'generated' else str(item.get('caption') or '')[:50]
            caption_lines=wrap(caption,(cell_width-.1)*72/10*.94) if caption else []
            caption_height=max(.32,len(caption_lines)*(10/72*1.15+3/72)+.08) if caption else 0
            available = slot-caption_height
            ratio = min(cell_width/width, available/height)
            iw, ih = width*ratio, height*ratio
            top = cell_y+(available-ih)/2
            picture = slide.shapes.add_picture(item['path'], Inches(cell_x+(cell_width-iw)/2), Inches(top), width=Inches(iw), height=Inches(ih))
            picture.name = 'AI illustration' if item.get('kind') == 'generated' else 'Textbook original'
            if caption:
                box(slide, (cell_x,cell_y+available,cell_width,caption_height), caption, 10, color='61737D')

    for page in pages:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.background.fill.solid(); slide.background.fill.fore_color.rgb = RGBColor.from_string('FAF9F5')
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, deck.slide_width, Inches(.12))
        bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor.from_string(accent); bar.line.fill.background()
        activity = activities[page['activity_id']]
        box(slide, (.65,.35,12,.4), f'{design.grade} / {activity.title} / 计划 {activity.duration_minutes:g} 分钟', 13, color=accent)
        box(slide, (.65,.95,12,.94), page['title'], 30, bold=True, color='183442')
        visual = visuals.get(page['number'], {})
        diagram=next((d for d in page.get('diagram_candidates',[]) if d['id']==visual.get('diagram_id')),None)
        items = assets_by_page.get(page['number'], [])
        questions = [a for a in items if a.get('kind') == 'question']
        images = [a for a in items if a.get('kind') != 'question']
        layout = visual.get('layout_kind', 'auto')
        if layout == 'auto':
            description = visual.get('layout', '')
            layout = 'comparison' if '对比' in description or '对照' in description else 'steps' if '步骤' in description or '分步' in description else 'split_right'
        student_task=visual.get('student_task','').strip()
        body = (.75,2.08,11.85,2.7 if questions else 4.68)
        if student_task and not questions:
            card(slide,(.75,1.98,11.85,.55))
            box(slide,(.92,2.04,11.5,.43),'观察 / 操作：'+student_task,16,True,accent)
            body=(.75,2.72,11.85,4.04)
        if diagram:
            left=layout=='split_left';region=(.75 if left else 7.9,body[1],4.75 if left else 4.7,body[3])
            draw_diagram(slide,diagram,visual.get('diagram_stage','setup'),region,accent,tint)
            body=(5.85 if left else .75,body[1],6.75,body[3])
        elif images:
            if layout == 'image_top' and not questions:
                image_top,image_height,text_top,text_height=image_top_regions(page['points'],bool(student_task),bool(visual.get('teaching_goal')))
                pictures(slide, images, (.8,image_top,11.7,image_height),horizontal=True)
                body = (.8,text_top,11.7,text_height)
            elif layout == 'split_left':
                pictures(slide, images, (.75,body[1],4.75,body[3]))
                body = (5.85,body[1],6.75,body[3])
            else:
                pictures(slide, images, (7.9,body[1],4.7,body[3]))
                body = (.75,body[1],6.75,body[3])
        x, y, w, h = body
        points = page['points']
        if layout in {'comparison','steps'}:
            columns = 2 if layout == 'comparison' and not images else 1
            rows = ceil(len(points)/columns)
            gap = .14; height = (h-gap*(rows-1))/rows; width = (w-gap*(columns-1))/columns
            for index, point in enumerate(points):
                left = x+(index%columns)*(width+gap); top = y+(index//columns)*(height+gap)
                card(slide,(left,top,width,height))
                indent = .48 if layout == 'steps' else .15
                if layout == 'steps':
                    box(slide,(left+.1,top+.1,.3,height-.2),str(index+1),18,True,accent)
                box(slide,(left+indent,top+.1,width-indent-.12,height-.2),point,21,minimum=18 if visual.get('teaching_goal') else 10)
        else:
            # Fit the whole text block to its actual region before positioning.
            for size in range(23,17 if visual.get('teaching_goal') else 9,-1):
                wrapped = [wrap(point,(w-.1)*72/size*.94) for point in points]
                heights = [len(lines)*(size/72*1.15+3/72)+.06 for lines in wrapped]
                if sum(heights)+.14*(len(points)-1) <= h:
                    break
            else:
                raise ValueError('本页文字过密，请减少本课要点')
            for point, height in zip(points, heights):
                box(slide,(x,y,w,height),point,size); y += height+.14
        if page.get('visual') and not images and not diagram and layout not in {'comparison','steps'}:
            # Keep mathematical/step labels editable when they are requested.
            # A compact label strip occupies the remaining space only.
            labels = page['visual']['labels']
            if y+.65 <= body[1]+body[3]:
                width = (w-.1*(len(labels)-1))/len(labels)
                for index, text in enumerate(labels):
                    card(slide,(x+index*(width+.1),body[1]+body[3]-.65,width,.65))
                    box(slide,(x+index*(width+.1)+.08,body[1]+body[3]-.59,width-.16,.5),text,15,True,accent)
        if questions:
            pictures(slide, questions, (.75,5.04,11.85,1.86))
        box(slide,(.65,7.04,12,.3),f'{design.title[:35]} · {label}    {page["number"]:02d} / {len(pages):02d}',10,color='61737D')
        notes=[page['script']]
        for key,note_label in [('teaching_goal','本页教学任务'),('student_task','学生观察或操作'),('learning_focus','观察重点'),('asset_reason','素材选择理由'),('density_note','内容与节奏安排')]:
            if visual.get(key):notes.append(note_label+'：'+visual[key])
        slide.notes_slide.notes_text_frame.text='\n\n'.join(notes)
    deck.save(path)
