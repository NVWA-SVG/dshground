"""Restore fixed teaching text locally, keeping useful provider illustrations."""
from __future__ import annotations

from io import BytesIO

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE, MSO_CONNECTOR
from pptx.util import Pt

from .exporters import wrap
from .geometry_visuals import draw_geometry


def restore_page(deck, slide, page, geometry=None):
    # Select substantial illustrations rather than tiny icons or flat card fills.
    # The provider's complete original file is retained separately for comparison.
    pictures = []
    def collect(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                collect(shape.shapes)
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                image = shape.image
                area = shape.width * shape.height / (deck.slide_width * deck.slide_height)
                if area >= .04 and min(image.size) >= 256 and len(image.blob) >= 16000:
                    pictures.append((area, shape.left, image.blob))
    collect(slide.shapes)
    pictures = sorted(sorted(pictures, reverse=True)[:3], key=lambda item: item[1])
    blobs = [item[2] for item in pictures]
    for shape in list(slide.shapes):
        shape._element.getparent().remove(shape._element)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string('F8FAFC')
    sx, sy = deck.slide_width / 13.333, deck.slide_height / 7.5
    font_scale = deck.slide_height / 6858000

    def rect(x, y, w, h, color, rounded=False):
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
                                       int(x*sx), int(y*sy), int(w*sx), int(h*sy))
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(color)
        shape.line.fill.background()
        return shape

    def text(x, y, w, h, value, size=23, color='193C46', bold=False):
        frame = slide.shapes.add_textbox(int(x*sx), int(y*sy), int(w*sx), int(h*sy)).text_frame
        frame.word_wrap = True
        frame.margin_left = frame.margin_right = 0
        frame.margin_top = frame.margin_bottom = 0
        for index, line in enumerate(value.split('\n')):
            paragraph = frame.paragraphs[0] if not index else frame.add_paragraph()
            paragraph.text = line
            paragraph.font.name = 'Microsoft YaHei'
            paragraph.font.size = Pt(size * font_scale)
            paragraph.font.bold = bold
            paragraph.font.color.rgb = RGBColor.from_string(color)
            paragraph.line_spacing = 1.18

    def picture(blob, x, y, w, h):
        shape = slide.shapes.add_picture(BytesIO(blob), 0, 0)
        ratio = min(w*sx / shape.width, h*sy / shape.height)
        shape.width, shape.height = int(shape.width*ratio), int(shape.height*ratio)
        shape.left = int(x*sx + (w*sx-shape.width)/2)
        shape.top = int(y*sy + (h*sy-shape.height)/2)

    def line(x1,y1,x2,y2,color='168578',width=2.8):
        shape=slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,int(x1*sx),int(y1*sy),int(x2*sx),int(y2*sy))
        shape.line.color.rgb=RGBColor.from_string(color)
        shape.line.width=Pt(width*font_scale)

    def dot(x,y,color):
        shape=slide.shapes.add_shape(MSO_SHAPE.OVAL,int((x-.045)*sx),int((y-.045)*sy),int(.09*sx),int(.09*sy))
        shape.fill.solid();shape.fill.fore_color.rgb=RGBColor.from_string(color);shape.line.fill.background()

    rect(0, 0, 13.333, .10, '168578')
    text(.6, .34, 12.1, .35, page['activity_title'], 12, '637882')
    title_size = 30 if len(page['title']) <= 26 else 25 if len(page['title']) <= 43 else 20
    text(.6, .88, 12.1, 1.03, '\n'.join(wrap(page['title'], 12.1*72/title_size*.88)), title_size, bold=True)
    points = page['points']
    if not geometry and len(blobs) == len(points) == 3:
        for index, (point, blob) in enumerate(zip(points, blobs)):
            x = .6 + index * 4.1
            rect(x, 2.0, 3.9, 4.75, 'FFFFFF', True)
            text(x+.2, 2.22, 3.5, 1.15, '\n'.join(wrap(point, 12)), 21, bold=True)
            picture(blob, x+.18, 3.52, 3.54, 3.0)
    else:
        cursor = 2.0
        lines_used = sum(len(wrap(point, 14)) for point in points)
        size = 23 if lines_used <= 9 else 20 if lines_used <= 12 else 17
        for index, point in enumerate(points, 1):
            lines = wrap(point, 5*72/size*.88)
            height = len(lines) * size/72*1.2 + .13
            rect(.6, cursor+.04, .38, .38, '168578', True)
            text(.68, cursor+.06, .25, .27, str(index), 13, 'FFFFFF', True)
            text(1.18, cursor, 5.0, height, point, size)
            cursor += height + .24
        rect(6.65, 1.98, 6.08, 4.85, 'FFFFFF', True)
        if geometry:
            draw_geometry(geometry,(6.85,2.15,5.68,4.48),text,line,dot,rect)
        elif blobs:
            # A single large illustration stays readable in the right column.
            largest = max(pictures, key=lambda item: item[0])[2]
            picture(largest, 6.85, 2.15, 5.68, 4.48)
        else:
            labels = (page.get('visual') or {}).get('labels') or points
            gap = min(1.1, 4.3 / len(labels))
            for index, label in enumerate(labels):
                y = 2.25 + index*gap
                rect(6.97, y, 5.44, gap-.15, 'E6F2EF', True)
                text(7.2, y+.12, 4.98, gap-.27, '\n'.join(wrap(label, 19)), 20, bold=True)
    text(.6, 7.03, 12.1, .25, f"{page['number']:02d}", 10, '637882')
    return {'page_number': page['number'], 'method': 'restore_source_text_and_rebuild_layout',
            'illustrations_retained': 0 if geometry else len(blobs) if len(blobs) == len(points) == 3 else min(1, len(blobs)),
            'editable_geometry': geometry}
