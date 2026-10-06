#!/usr/bin/env python3
"""Render a fixed report view to HTML and DOCX, without model or network calls.

Input is a presentation-only view. It does not validate the truth of its prose.
Output is create-only; publication/freshness authorization belongs to the host.
"""
from __future__ import annotations
import argparse, hashlib, html, json, math, re, zipfile
from pathlib import Path
from docx import Document
from docx.shared import Mm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

FONT='Noto Sans CJK KR'
CSS='''
:root{--ink:#202326;--muted:#596169;--line:#d6dade;--wash:#f1f3f4}
*{box-sizing:border-box}html{scroll-behavior:auto}body{margin:0;background:#e8eaec;color:var(--ink);font-family:"Noto Sans CJK KR","Malgun Gothic",sans-serif;font-size:10.3pt;line-height:1.55}
nav{max-width:210mm;margin:20px auto 10px;display:flex;gap:12px;flex-wrap:wrap;padding:0 12px;font-size:10pt}nav a{color:#202326}a:focus-visible{outline:2px solid #555;outline-offset:3px}
.sheet{width:210mm;min-height:297mm;margin:0 auto 20px;background:#fff;padding:17mm 18mm 15mm;box-shadow:0 2px 16px #0000000d;display:flex;flex-direction:column}
.running{display:flex;justify-content:space-between;font-size:8.1pt;letter-spacing:.03em;color:#596169;border-bottom:1px solid var(--line);padding-bottom:9px;margin-bottom:16px}
.kicker{font-size:9pt;font-weight:700;letter-spacing:.1em;color:#596169;margin:0 0 8px}h1{font-size:21pt;font-weight:750;line-height:1.25;letter-spacing:-.03em;margin:0 0 7px} .subtitle{color:#596169;font-size:9.1pt;margin:0 0 18px}
h2{font-size:12.3pt;line-height:1.35;margin:18px 0 8px;font-weight:700;break-after:avoid}p{margin:6px 0 9px;overflow-wrap:anywhere}.note{font-size:8.7pt;color:#596169;line-height:1.5;margin:8px 0 12px}
.callout{background:#f1f3f4;border-left:3px solid #3f454b;padding:11px 14px;margin:12px 0;break-inside:avoid}.callout strong{display:block;font-size:9.5pt;margin-bottom:5px}.callout p{margin:0}
table{border-collapse:collapse;width:100%;table-layout:fixed;font-size:9.2pt;line-height:1.48;margin:7px 0 11px}th,td{border:1px solid #d6dade;padding:7px 8px;vertical-align:top;overflow-wrap:anywhere}th{font-weight:700;background:#f1f3f4;text-align:left}thead{display:table-header-group}tr{break-inside:avoid}
.sheet-foot{margin-top:auto;padding-top:14px;border-top:1px solid #d6dade;font-size:8pt;color:#596169;display:flex;justify-content:space-between;gap:10px}.page-body{padding-bottom:15px;min-width:0}.sheet-foot span,.running span{min-width:0;overflow-wrap:anywhere}
@page{size:A4;margin:17mm 18mm 17mm}
@media print{body{background:#fff;line-height:1.32}nav{display:none}.sheet{width:auto;min-height:0;display:block;margin:0;padding:0;box-shadow:none;break-after:page}.sheet:last-child{break-after:auto}.running{margin-bottom:9px;padding-bottom:6px}.subtitle{margin-bottom:12px}h2{margin:13px 0 6px}table{font-size:9.5pt;line-height:1.25;margin:6px 0 9px}th,td{padding:5px 7px}.note{line-height:1.3;margin:6px 0 9px}.callout{padding:8px 12px;margin:9px 0}.sheet-foot{margin-top:9px;padding-top:9px}.page-body{padding-bottom:4px}a{color:inherit;text-decoration:none}}
@media screen and (max-width:850px){body{font-size:15px}.sheet{width:100%;min-height:0;padding:22px 18px;margin-bottom:12px}nav{margin:12px}h1{font-size:26px}h2{font-size:20px}table{font-size:13px}th,td{padding:7px 6px}.running{font-size:11px}.subtitle,.note{font-size:13px}.sheet-foot{margin-top:20px;font-size:11px}.page-body{overflow-x:auto}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;animation:none!important}}
'''

def text(v): return html.escape(str(v),quote=True)
def bookmark_id(v): return 'r_'+hashlib.sha256(str(v).encode()).hexdigest()[:28]
def linked_text(value, targets):
 value=str(value)
 if not targets:return text(value)
 pattern='('+'|'.join(re.escape(x) for x in sorted(targets,key=len,reverse=True))+')'
 return ''.join('<a href="#'+bookmark_id(part)+'">'+text(part)+'</a>' if part in targets else text(part) for part in re.split(pattern,value))

def hblock(b, targets=()):
 k=b['kind']
 if k=='heading':return '<h2>'+text(b['text'])+'</h2>'
 if k in ('paragraph','note'):return '<p'+(' class="note"' if k=='note' else '')+'>'+text(b['text'])+'</p>'
 if k=='callout':return '<aside class="callout"><strong>'+text(b['label'])+'</strong><p>'+text(b['text'])+'</p></aside>'
 if k=='table':
  widths=b.get('widths') or [100/len(b['headers'])]*len(b['headers'])
  ids=b.get('row_ids',[])
  return '<table><colgroup>'+''.join(f'<col style="width:{float(w)}%">' for w in widths)+'</colgroup><thead><tr>'+''.join('<th scope="col">'+text(x)+'</th>' for x in b['headers'])+'</tr></thead><tbody>'+''.join('<tr'+(' id="'+bookmark_id(ids[i])+'"' if i<len(ids) else '')+'>'+''.join('<td>'+linked_text(x,targets)+'</td>' for x in row)+'</tr>' for i,row in enumerate(b['rows']))+'</tbody></table>'
 raise ValueError('Unsupported block '+str(k))

def render_html(v):
 targets={x for p in v['pages'] for b in p['blocks'] for x in b.get('row_ids',[])}
 pages=v['pages']; nav='<nav aria-label="보고서 섹션">'+''.join('<a href="#'+bookmark_id(p['id'])+'">'+text(p['title'])+'</a>' for p in pages)+'</nav>'
 label={'template':'보고서 양식 · 실제 사건 아님','synthetic':'합성 예시 · 실제 사건 아님','live':'판단 기준시점의 보존 보고서'}[v['data_mode']]
 if v['data_mode']=='template':label='보고서 양식 · 실제 사건 아님'
 body=[]
 for i,p in enumerate(pages,1):
  body.append('<article class="sheet" id="'+bookmark_id(p['id'])+'"><header class="running"><b>FORSIC / REPORT</b><span>'+text(v['security'])+'</span></header><div class="page-body"><p class="kicker">'+text(label)+'</p><h1>'+text(p['title'])+'</h1><p class="subtitle">'+text(p['subtitle'])+'</p>'+''.join(hblock(b,targets) for b in p['blocks'])+'</div><footer class="sheet-foot"><span>'+text(v['report_id'])+' · '+text(v['snapshot_revision'])+'</span><span>'+text(p['id'])+'</span></footer></article>')
 return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src \'none\'; base-uri \'none\'; form-action \'none\'"><title>'+text(v['title'])+'</title><style>'+CSS+'</style></head><body>'+nav+''.join(body)+'</body></html>'

def font_run(r,size=None,bold=None):
 r.font.name=FONT
 r._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),FONT)
 if size:r.font.size=Pt(size)
 if bold is not None:r.font.bold=bold
 r.font.color.rgb=RGBColor.from_string('202326')

def set_cell_shade(cell,fill):
 sh=OxmlElement('w:shd');sh.set(qn('w:fill'),fill);cell._tc.get_or_add_tcPr().append(sh)

def table_doc(doc,b,targets=()):
 headers=b['headers'];widths=b.get('widths') or [100/len(headers)]*len(headers)
 if any(len(row)!=len(headers) for row in b['rows']):raise ValueError('Table column mismatch')
 table=doc.add_table(rows=1,cols=len(headers));table.autofit=False
 pr=table._tbl.tblPr
 borders=OxmlElement('w:tblBorders')
 for side in ('top','left','bottom','right','insideH','insideV'):
  edge=OxmlElement('w:'+side);edge.set(qn('w:val'),'single');edge.set(qn('w:sz'),'4');edge.set(qn('w:color'),'D6DADE');borders.append(edge)
 pr.append(borders)
 mar=OxmlElement('w:tblCellMar')
 for side,val in [('top','78'),('bottom','78'),('left','105'),('right','105')]:
  e=OxmlElement('w:'+side);e.set(qn('w:w'),val);e.set(qn('w:type'),'dxa');mar.append(e)
 pr.append(mar)
 for col,w in zip(table.columns,widths):col.width=Mm(174*w/100)
 for c,x,w in zip(table.rows[0].cells,headers,widths):
  c.width=Mm(174*w/100);c.text=str(x);set_cell_shade(c,'F1F3F4')
 repeat=OxmlElement('w:tblHeader');table.rows[0]._tr.get_or_add_trPr().append(repeat)
 for row in b['rows']:
  cells=table.add_row().cells
  for c,x,w in zip(cells,row,widths):c.width=Mm(174*w/100);c.text=str(x)
 for ri,row in enumerate(table.rows):
  # Keep ordinary rows together (including IDs). Very long original excerpts
  # must remain splittable instead of overflowing a physical page.
  estimated_lines=max(sum(max(1,math.ceil(len(line)/max(1,(174*w/100-8)*2.835/9.5))) for line in c.text.split('\n')) for c,w in zip(row.cells,widths))
  if ri==0 or estimated_lines<=30:
   cant=OxmlElement('w:cantSplit');row._tr.get_or_add_trPr().append(cant)
  for c in row.cells:
   for p in c.paragraphs:
    p.paragraph_format.space_after=Pt(0);p.paragraph_format.space_before=Pt(0);p.paragraph_format.line_spacing=1.12
    original=p.text
    if ri and targets:
     for r in list(p.runs):p._p.remove(r._r)
     pattern='('+'|'.join(re.escape(x) for x in sorted(targets,key=len,reverse=True))+')'
     for part in re.split(pattern,original):
      r=p.add_run(part);font_run(r,9.5,False)
      if part in targets:
       link=OxmlElement('w:hyperlink');link.set(qn('w:anchor'),bookmark_id(part));p._p.remove(r._r);link.append(r._r);p._p.append(link)
    else:
     for r in p.runs:font_run(r,9.5,ri==0)
  ids=b.get('row_ids',[])
  if ri and ri<=len(ids):
   p=row.cells[0].paragraphs[0];key=ids[ri-1]
   start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),str(int(hashlib.sha256(key.encode()).hexdigest()[:7],16)));start.set(qn('w:name'),bookmark_id(key))
   end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),start.get(qn('w:id')));p._p.insert(0,start);p._p.append(end)
 p=doc.add_paragraph();p.paragraph_format.space_after=Pt(0);p.paragraph_format.space_before=Pt(0);p.paragraph_format.line_spacing=Pt(3);p.add_run().font.size=Pt(2)
 return table

def render_docx(v,path):
 targets={x for p in v['pages'] for b in p['blocks'] for x in b.get('row_ids',[])}
 doc=Document();s=doc.sections[0]
 s.page_width=Mm(210);s.page_height=Mm(297)
 s.top_margin=Mm(17);s.bottom_margin=Mm(17);s.left_margin=s.right_margin=Mm(18)
 s.header_distance=s.footer_distance=Mm(8)
 cp=doc.core_properties;cp.author='';cp.last_modified_by='';cp.comments='';cp.title=v['title'];cp.subject='Forsic report template'
 for name,size in [('Normal',10.3),('Title',21),('Heading 1',12.4),('Heading 2',11.2),('Caption',8.3)]:
  st=doc.styles[name];st.font.name=FONT;st.font.size=Pt(size);st.font.color.rgb=RGBColor.from_string('202326');st.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),FONT)
  st.paragraph_format.space_after=Pt(6);st.paragraph_format.line_spacing=1.16;st.paragraph_format.widow_control=True
  if name.startswith('Heading'):st.font.bold=True;st.paragraph_format.keep_with_next=True;st.paragraph_format.space_before=Pt(10)
  for color in st.element.iter(qn('w:color')):
   for a in ('themeColor','themeTint','themeShade'):color.attrib.pop(qn('w:'+a),None)
 for border in list(doc.styles.element.iter(qn('w:pBdr'))):border.getparent().remove(border)
 header=s.header.paragraphs[0];header.text='FORSIC / REPORT                                      '+v['security'];header.style='Caption'
 foot=s.footer.paragraphs[0];foot.alignment=WD_ALIGN_PARAGRAPH.RIGHT;foot.style='Caption'
 r=foot.add_run(v['report_id']+' · '+v['snapshot_revision']+'  |  ');font_run(r,8)
 for instr in ('PAGE','NUMPAGES'):
  if instr=='NUMPAGES':foot.add_run(' / ')
  fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),instr);foot._p.append(fld)
 for i,pg in enumerate(v['pages']):
  if i:doc.add_page_break()
  p=doc.add_paragraph('보고서 양식 · 실제 사건 아님' if v['data_mode']=='template' else '합성 예시 · 실제 사건 아님' if v['data_mode']=='synthetic' else '판단 기준시점의 보존 보고서','Caption')
  p.paragraph_format.space_after=Pt(7)
  p=doc.add_paragraph(pg['title'],'Title');p.paragraph_format.space_after=Pt(5)
  start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),str(i+1));start.set(qn('w:name'),bookmark_id(pg['id']))
  end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),str(i+1));p._p.insert(0,start);p._p.append(end)
  p=doc.add_paragraph(pg['subtitle'],'Caption');p.paragraph_format.space_after=Pt(10)
  for b in pg['blocks']:
   k=b['kind']
   if k=='table':table_doc(doc,b,targets)
   elif k=='heading':doc.add_paragraph(b['text'],'Heading 1')
   elif k=='note':
    p=doc.add_paragraph(b['text'],'Caption');p.paragraph_format.space_after=Pt(7)
   elif k=='paragraph':doc.add_paragraph(b['text'])
   elif k=='callout':
    t=table_doc(doc,{'headers':[b['label']],'rows':[[b['text']]],'widths':[100]})
    set_cell_shade(t.cell(1,0),'F5F6F7')
   else:raise ValueError('Unsupported block')
 doc.save(path)
 with zipfile.ZipFile(path) as z:
  assert z.testzip() is None
  for name in z.namelist():
   if name.endswith('.rels') and b'TargetMode="External"' in z.read(name):raise ValueError('External relationship')

def render_pair(view_file,outdir):
 v=json.loads(Path(view_file).read_text(encoding='utf-8'));out=Path(outdir);out.mkdir(parents=True,exist_ok=True)
 stem=Path(view_file).name.removesuffix('.view.json')+'_template'
 hp,dp=out/(stem+'.html'),out/(stem+'.docx')
 if hp.exists() or dp.exists():raise FileExistsError('Create-only outputs: choose a new directory or remove generated outputs explicitly.')
 hp.write_text(render_html(v),encoding='utf-8');render_docx(v,dp)
 return hp,dp
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('view');ap.add_argument('--out',required=True);args=ap.parse_args()
 for p in render_pair(args.view,args.out):print(p)
