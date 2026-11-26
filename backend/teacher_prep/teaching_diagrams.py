"""Editable teaching diagrams. Numbers, regrouping and coordinates are computed locally."""
from math import ceil,floor,log10
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE,MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches,Pt
from .exporters import textbox,wrap
from .teaching_visuals import arithmetic


def draw_diagram(slide,candidate,stage,region,accent,tint):
    x,y,w,h=region;sx,sy=w/4.7,h/4.0;scale=min(sx,sy)
    prefix='Teaching '+candidate['kind']
    def text(name,left,top,width,height,value,size=24,color='183442',bold=False):
        preferred=max(12,int(size*scale));actual_w=width*sx;actual_h=height*sy
        for selected in range(preferred,11,-1):
            lines=wrap(str(value),(actual_w-.07)*72/selected*.94)
            if len(lines)*(selected/72*1.15+3/72)+.04<=actual_h:break
        else:raise ValueError('教学图解文字无法清晰排下，请选择其他布局或简化原教学标签')
        shape=textbox(slide,x+left*sx,y+top*sy,actual_w,actual_h,'\n'.join(lines),selected,color,bold=bold)
        shape.name=prefix+' '+name
        for paragraph in shape.text_frame.paragraphs:paragraph.alignment=PP_ALIGN.CENTER
        return shape
    def line(name,left,top,right,bottom,color=accent,width=1.5):
        shape=slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,Inches(x+left*sx),Inches(y+top*sy),Inches(x+right*sx),Inches(y+bottom*sy))
        shape.name=prefix+' '+name;shape.line.color.rgb=RGBColor.from_string(color);shape.line.width=Pt(width)
        return shape
    def shape(name,kind,left,top,width,height,fill=tint):
        item=slide.shapes.add_shape(kind,Inches(x+left*sx),Inches(y+top*sy),Inches(width*sx),Inches(height*sy))
        item.name=prefix+' '+name;item.fill.solid();item.fill.fore_color.rgb=RGBColor.from_string(fill);item.line.fill.background()
        return item
    shape('panel',MSO_SHAPE.ROUNDED_RECTANGLE,0,0,4.7,4.0)
    kind=candidate['kind']
    if kind=='column_calculation':
        calc=arithmetic(candidate['a'],candidate['b'],candidate['operation']);digits=calc['width'];cell=min(.85,3.4/digits);left=4.12-digits*cell
        text('expression',.15,.09,4.4,.45,candidate['expression'],23,bold=True)
        places=['个位','十位','百位','千位','万位']
        for index in range(digits):
            position=left+index*cell
            text('place '+str(index),position,.67,cell,.36,places[digits-index-1],13,accent)
            top=str(calc['top'][index]) if index>=digits-len(str(calc['a'])) else ''
            bottom=str(calc['bottom'][index]) if index>=digits-len(str(calc['b'])) else ''
            text('top digit '+str(index),position,1.51,cell,.61,top,33)
            text('bottom digit '+str(index),position,2.16,cell,.61,bottom,33)
            if stage!='setup':
                if calc['operation']=='+':
                    mark=calc['carry_marks'][index]
                    if mark:text('carry '+str(index),position,1.08,cell,.38,str(mark),18,accent,True)
                elif calc['adjusted'][index]!=calc['top'][index]:
                    text('borrow '+str(index),position,1.08,cell,.38,str(calc['adjusted'][index]),18,accent,True)
                    line('cross out '+str(index),position+.15,1.98,position+cell-.1,1.64,'946222')
        text('operation',max(.18,left-.5),2.17,.42,.6,'＋' if calc['operation']=='+' else '－',28,accent,True)
        line('answer rule',max(.2,left-.52),2.84,4.2,2.84,width=2)
        if stage=='complete':
            answer=str(calc['result']).rjust(digits)
            for index,digit in enumerate(answer):text('answer digit '+str(index),left+index*cell,2.96,cell,.62,digit,33,accent,True)
        else:text('answer hidden',left,2.96,digits*cell,.62,'？',30,accent,True)
        legend='先对齐数位，再独立计算' if stage=='setup' or not calc['events'] else '小标记表示进位' if calc['operation']=='+' else '上方标出退位后的数'
        text('legend',.18,3.65,4.34,.3,legend,12,accent)
    elif kind=='number_line':
        calc=arithmetic(candidate['a'],candidate['b'],candidate['operation']);a,result=calc['a'],calc['result']
        span=max(1,abs(a-result));raw=span/4;magnitude=10**floor(log10(max(raw,1)))
        tick=next(v*magnitude for v in (1,2,5,10) if v*magnitude>=raw)
        low=max(0,floor(min(a,result)/tick)*tick-tick);high=ceil(max(a,result)/tick)*tick+tick
        px=lambda value:.4+(value-low)/(high-low)*3.9
        text('expression',.15,.15,4.4,.5,candidate['expression'],23,bold=True)
        line('axis',.3,2.47,4.4,2.47,width=2)
        for value in range(low,high+1,tick):
            position=px(value);line('tick '+str(value),position,2.4,position,2.57)
            if value not in (a,result):text('tick label '+str(value),position-.3,2.7,.6,.4,str(value),12)
        for name,value,label in [('start',a,str(a)),('end',result,str(result) if stage=='complete' else '？')]:
            position=px(value);shape(name+' dot',MSO_SHAPE.OVAL,position-.065,2.405,.13,.13,accent)
            text(name+' label',max(.04,min(3.82,position-.42)),2.82,.84,.45,label,18,accent,True)
        left,right=sorted([px(a),px(result)])
        shape('operation arrow',MSO_SHAPE.RIGHT_ARROW if result>a else MSO_SHAPE.LEFT_ARROW,left,1.57,right-left,.38,accent)
        text('jump amount',.5,1.02,3.7,.48,('加上 ' if calc['operation']=='+' else '减去 ')+str(calc['b']),20,accent,True)
        line('start guide',px(a),1.98,px(a),2.33);line('end guide',px(result),1.98,px(result),2.33)
        text('legend',.2,3.5,4.3,.35,'观察起点、方向与终点',14,accent)
    else:
        labels=candidate['labels'];count=len(labels)
        text('heading',.15,.1,4.4,.5,{'steps':'按顺序观察','comparison':'比较关键特征','concepts':'梳理核心关系'}[kind],20,bold=True)
        if kind=='comparison':
            gap=.14;cw=2.05;ch=1.25
            for index,label in enumerate(labels):
                left=.22+(index%2)*(cw+gap);top=.9+(index//2)*1.45
                shape('card '+str(index),MSO_SHAPE.ROUNDED_RECTANGLE,left,top,cw,ch,'FFFFFF')
                text('label '+str(index),left+.09,top+.12,cw-.18,ch-.24,label,18,accent,True)
        else:
            height=min(.68,2.4/count);gap=(2.9-count*height)/max(1,count-1)
            for index,label in enumerate(labels):
                top=.8+index*(height+gap)
                shape('card '+str(index),MSO_SHAPE.ROUNDED_RECTANGLE,.4,top,3.9,height,'FFFFFF')
                text('label '+str(index),.53,top+.07,3.64,height-.14,label,18,accent,True)
                if kind=='steps' and index<count-1:shape('step arrow '+str(index),MSO_SHAPE.DOWN_ARROW,2.2,top+height+.02,.25,max(.08,gap-.04),accent)
