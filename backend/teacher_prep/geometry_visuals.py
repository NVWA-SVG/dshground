"""Small deterministic diagrams for recognized elementary geometry content."""
from __future__ import annotations

import math
import re
import unicodedata


def coordinates(text):
    text = unicodedata.normalize('NFKC', text)
    found = {}
    for name, x, y in re.findall(r'(?<![A-Za-z])([A-Z])\s*\(\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*\)', text):
        point = [float(x), float(y)]
        if max(abs(v) for v in point) > 100 or name in found and found[name] != point:
            return {}
        found[name] = point
    return found if 3 <= len(found) <= 6 else {}


def geometry_spec(page, pages):
    title = page['title']
    labels = (page.get('visual') or {}).get('labels', [])
    if labels == ['点', '线段', '围成的图形']:
        return {'kind': 'point_segment_polygon'}
    if any('不封闭' in s for s in labels) and any('无缺口' in s for s in labels):
        return {'kind': 'closed_and_open'}
    if '数边' in title:
        sides = sorted({int(n) for n in re.findall(r'([345])条边', ''.join(page['points']))})
        if sides == [3, 4, 5]:
            return {'kind': 'polygon_names', 'sides': sides}
    if '坐标建图' in title:
        vertices = coordinates(' '.join(page['points'])) or coordinates(page['script'])
        if vertices:
            return {'kind': 'coordinate_polygon', 'vertices': vertices, 'order': list(vertices),
                    'source': 'page_points' if coordinates(' '.join(page['points'])) else 'teacher_script'}
    if '错例' in title and all(any(word in p for p in page['points']) for word in ['缺口', '交叉', '变形']):
        return {'kind': 'three_errors'}
    if '只动一个顶点' in title and '顶点D向右移动两格' in page['script']:
        for previous in reversed(pages[:page['number']-1]):
            vertices = coordinates(' '.join(previous['points']))
            if set(vertices) == {'A', 'B', 'C', 'D'}:
                changed = {k:list(v) for k,v in vertices.items()}
                changed['D'][0] += 2
                return {'kind': 'move_vertex', 'vertices': vertices, 'after': changed, 'order':['A','B','C','D'], 'moved':'D'}
    return None


def draw_geometry(spec, box, text, line, dot, rect):
    x,y,w,h = box
    blue, orange, muted = '168578', 'DA7C2F', '93A6AD'

    def poly(points, color=blue, closed=True):
        for a,b in zip(points, points[1:]+points[:1] if closed else points[1:]):
            line(*a,*b,color)
        for px,py in points:
            dot(px,py,color)

    def regular(cx,cy,r,n):
        return [(cx+r*math.cos(-math.pi/2+2*math.pi*i/n),cy+r*math.sin(-math.pi/2+2*math.pi*i/n)) for i in range(n)]

    kind = spec['kind']
    if kind == 'point_segment_polygon':
        for i,label in enumerate(['点','线段','围成的图形']):
            cy=y+.65+i*1.35
            text(x+.18,cy-.25,w*.35,.4,label,19,bold=True)
            if i==0:dot(x+w*.7,cy,blue)
            elif i==1:
                line(x+w*.53,cy,x+w*.86,cy,blue)
                dot(x+w*.53,cy,blue);dot(x+w*.86,cy,blue)
            else:poly(regular(x+w*.7,cy,.54,4))
    elif kind in {'closed_and_open','polygon_names','three_errors'}:
        for i in range(3):
            cy=y+.7+i*1.35
            cx=x+w*.70
            if kind=='closed_and_open':
                label=['封闭：无缺口','不封闭：有缺口','封闭：无缺口'][i]
                pts=regular(cx,cy,.48,[5,4,4][i])
                poly(pts, orange if i==1 else blue,closed=i!=1)
            elif kind=='polygon_names':
                n=spec['sides'][i];label={3:'三角形 · 3条边',4:'四边形 · 4条边',5:'五边形 · 5条边'}[n]
                poly(regular(cx,cy,.5,n))
            else:
                label=['缺口：漏连','交叉：顺序错','变形：顶点错'][i]
                pts=[(cx-.46,cy+.45),(cx+.46,cy+.45),(cx+.46,cy-.45),(cx-.46,cy-.45)]
                if i==0:poly(pts,orange,closed=False)
                elif i==1:poly([pts[0],pts[2],pts[1],pts[3]],orange)
                else:
                    poly(pts,muted)
                    pts[3]=(cx-.1,cy-.7);poly(pts,orange)
            text(x+.12,cy-.2,w*.52,.65,label,17,bold=True)
    else:
        vertices=spec['vertices'];after=spec.get('after',vertices)
        allpoints=list(vertices.values())+list(after.values())
        xmin=min(0,math.floor(min(p[0] for p in allpoints)))
        ymin=min(0,math.floor(min(p[1] for p in allpoints)))
        xmax=math.ceil(max(p[0] for p in allpoints))+1
        ymax=math.ceil(max(p[1] for p in allpoints))+1
        if xmax-xmin>15 or ymax-ymin>15:
            # Labels list the source coordinates; avoid drawing a misleading dense grid.
            for i,(name,(vx,vy)) in enumerate(vertices.items()):
                text(x+.3,y+.3+i*.55,w-.6,.45,f'{name}({vx:g}, {vy:g})',20)
            return
        scale=min((w-.95)/(xmax-xmin),(h-1.1)/(ymax-ymin))
        left=x+.45+(w-.95-(xmax-xmin)*scale)/2
        bottom=y+h-.65
        def pos(v):return left+(v[0]-xmin)*scale,bottom-(v[1]-ymin)*scale
        for gx in range(xmin,xmax+1):
            a,b=pos((gx,ymin)),pos((gx,ymax));line(*a,*b,'DFE8EC',.6)
            tx,ty=pos((gx,0));text(tx-.1,ty+.08,.32,.3,str(gx),11)
        for gy in range(ymin,ymax+1):
            a,b=pos((xmin,gy)),pos((xmax,gy));line(*a,*b,'DFE8EC',.6)
            if gy:
                tx,ty=pos((0,gy));text(tx-.28,ty-.1,.3,.3,str(gy),11)
        line(*pos((xmin,0)),*pos((xmax,0)),'637882',1.4)
        line(*pos((0,ymin)),*pos((0,ymax)),'637882',1.4)
        order=spec['order']
        poly([pos(vertices[name]) for name in order],muted if kind=='move_vertex' else blue)
        if kind=='move_vertex':
            for first,second in zip(order,order[1:]+order[:1]):
                line(*pos(after[first]),*pos(after[second]),orange if 'D' in (first,second) else blue)
            for name in order:dot(*pos(after[name]),orange if name=='D' else blue)
            a,b=pos(vertices['D']),pos(after['D']);line(*a,*b,orange,1.3)
        for name,v in vertices.items():
            px,py=pos(v);text(px+.08,py-.31,1.45,.31,f'{name}({v[0]:g},{v[1]:g})',12)
        if kind=='move_vertex':
            px,py=pos(after['D']);text(px+.08,py-.31,1.3,.31,"D′",14,orange,True)
        order_text=('练习：' if spec.get('source')=='teacher_script' else '')+' → '.join(order+[order[0]])
        text(x+.2,y+h-.36,w-.4,.32,order_text,15,blue,True)
