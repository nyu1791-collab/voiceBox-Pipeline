"""実音声時刻から編集可能なモーフ付きPPTXを作る。LinuxでOfficeを模倣しない。"""
from pathlib import Path
import json
import shutil
import zipfile
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET
from motion_assets import checked_request, read, HERE

P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
MORPH = 'http://schemas.microsoft.com/office/powerpoint/2015/09/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
ROOT = f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}" xmlns:p159="{MORPH}" xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main">'
GROUP = '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'


def relationships(items):
    return f'<Relationships xmlns="{REL}">'+''.join(
        f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{target}"/>' for rid,kind,target in items)+'</Relationships>'


def shape(i, name, x, y, w, h, text='', font=24, fill='FFFFFF', line='D7DFD8'):
    lines = ''.join('<a:p><a:r><a:rPr lang="ja-JP" sz="'+str(round(font*75))+'">'
                    '<a:solidFill><a:srgbClr val="1E2430"/></a:solidFill>'
                    '<a:latin typeface="Noto Sans CJK JP"/><a:ea typeface="Noto Sans CJK JP"/>'
                    '</a:rPr><a:t>'+escape(s)+'</a:t></a:r></a:p>' for s in text.split('\n'))
    def emu(v): return str(round(v*9525))
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{i}" name="!!{escape(name)}"/>'
            '<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>'
            f'<a:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(max(1,w))}" cy="{emu(h)}"/></a:xfrm>'
            '<a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>'
            f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill><a:ln w="19050"><a:solidFill><a:srgbClr val="{line}"/></a:solidFill></a:ln>'
            '</p:spPr><p:txBody><a:bodyPr wrap="none" lIns="76200" tIns="19050" rIns="0" bIns="0"/><a:lstStyle/>'+lines+'</p:txBody></p:sp>')


def slide(req, visible, hold, duration):
    d = req['diagram']; w,h = req['width'],req['height']; row=(h-83)/len(d['elements'])
    shapes=[shape(2,'title',8,5,w-16,39,'\n'.join(req['typography']['title']),25,'FAF8F3','FAF8F3')]
    maxv=max(e.get('value',1) for e in d['elements'])
    for i,e in enumerate(d['elements']):
        x=12 if i<visible else w+40; y=49+i*row; ty=req['typography']['labels'][i]
        label='\n'.join(ty['lines']); prefix=f'{i+1}  ' if d['template']=='steps' else '●  '
        shapes.append(shape(10+i,e['id'],x,y,w-24,row-7,
                            ('' if d['template']=='bars' else prefix)+label,ty['font_size'],
                            line='E88014' if i==visible-1 else 'D7DFD8'))
        if d['template']=='bars':
            shapes.append(shape(30+i,e['id']+'_bar',x+8,y+row-30,(w-170)*e['value']/maxv,16,'',18,'269668','269668'))
            shapes.append(shape(40+i,e['id']+'_value',x+w-150,y+row-34,110,25,f"{e['value']:g}{e['unit']}",18,'FFFFFF','FFFFFF'))
    shapes.append(shape(60,'sources',6,h-38,w-12,30,'\n'.join(req['typography']['footer']),req['typography']['footer_font'],'FAF8F3','FAF8F3'))
    transition = f'<p:transition advClick="0" advTm="{round(hold*1000)}" p14:dur="{round(duration*1000)}">'
    transition += '<p159:morph option="byObject"/>' if visible else '<p:fade/>'
    return ROOT+'<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FAF8F3"/></a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree>'+GROUP+''.join(shapes)+'</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'+transition+'</p:transition></p:sld>'


def prepare_powerpoint(request_path, out):
    req=checked_request(read(request_path))
    if req['backend']!='powerpoint': raise ValueError('PowerPoint用の依頼ではない')
    out.mkdir(parents=True,exist_ok=False); d=req['diagram']; es=d['elements']
    slides=[slide(req,0,max(0,es[0]['start']-d['start']),0)]
    for i,e in enumerate(es):
        end = es[i+1]['start'] if i+1<len(es) else d['start']+req['frames']/req['fps']
        hold=end-e['start']-e['duration']
        if hold<0: raise ValueError('PowerPointのモーフと保持時間が重複')
        slides.append(slide(req,i+1,hold,e['duration']))
    types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
    overrides={'/ppt/presentation.xml':'presentationml.presentation.main', '/ppt/slideMasters/slideMaster1.xml':'presentationml.slideMaster', '/ppt/slideLayouts/slideLayout1.xml':'presentationml.slideLayout'}
    for i in range(len(slides)):overrides[f'/ppt/slides/slide{i+1}.xml']='presentationml.slide'
    types+=''.join(f'<Override PartName="{name}" ContentType="application/vnd.openxmlformats-officedocument.{kind}+xml"/>' for name,kind in overrides.items())+'</Types>'
    with zipfile.ZipFile(out/'source.pptx','w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',types)
        z.writestr('_rels/.rels',relationships([('rId1','officeDocument','ppt/presentation.xml')]))
        ids=''.join(f'<p:sldId id="{256+i}" r:id="rId{i+2}"/>' for i in range(len(slides)))
        z.writestr('ppt/presentation.xml',f'<p:presentation xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>'+ids+f'</p:sldIdLst><p:sldSz cx="{req["width"]*9525}" cy="{req["height"]*9525}"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
        z.writestr('ppt/_rels/presentation.xml.rels',relationships([('rId1','slideMaster','slideMasters/slideMaster1.xml')]+[(f'rId{i+2}','slide',f'slides/slide{i+1}.xml') for i in range(len(slides))]))
        master=f'<p:sldMaster xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>{GROUP}</p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>'
        z.writestr('ppt/slideMasters/slideMaster1.xml',master)
        z.writestr('ppt/slideMasters/_rels/slideMaster1.xml.rels',relationships([('rId1','slideLayout','../slideLayouts/slideLayout1.xml')]))
        z.writestr('ppt/slideLayouts/slideLayout1.xml',f'<p:sldLayout xmlns:p="{P}" xmlns:a="{A}" type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>{GROUP}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')
        z.writestr('ppt/slideLayouts/_rels/slideLayout1.xml.rels',relationships([('rId1','slideMaster','../slideMasters/slideMaster1.xml')]))
        for i,body in enumerate(slides,1):
            z.writestr(f'ppt/slides/slide{i}.xml',body)
            z.writestr(f'ppt/slides/_rels/slide{i}.xml.rels',relationships([('rId1','slideLayout','../slideLayouts/slideLayout1.xml')]))
        z.writestr('motion-request.json',json.dumps(req,ensure_ascii=False))
    (out/'request.json').write_text(json.dumps(req,ensure_ascii=False,indent=2)+'\n')
    shutil.copy2(HERE/'motion_backends/export.ps1',out/'export.ps1')
    inspect_morph(out/'source.pptx',req)
    return {'state':'WAIT','reason':'licensed_powerpoint_export_required','pptx':str(out/'source.pptx'),
            'export_script':str(out/'export.ps1'),'scope':'PPTX生成・XML検査済み。PowerPoint本体での再生・MP4書き出しは未実施'}


def inspect_morph(path, req):
    with zipfile.ZipFile(path) as z:
        if json.loads(z.read('motion-request.json'))!=req: raise ValueError('PPTXの依頼SHA不一致')
        names=[n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')]
        if len(names)!=len(req['diagram']['elements'])+1: raise ValueError('PPTXの状態数不一致')
        count=0
        for name in names:
            root=ET.fromstring(z.read(name));count+=len(root.findall(f'.//{{{MORPH}}}morph'))
        if count!=len(req['diagram']['elements']): raise ValueError('PPTXのモーフ遷移が欠落')
    return {'state':'OK','morph_transitions':count,'rendered':False}
