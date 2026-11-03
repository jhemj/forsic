#!/usr/bin/env python3
"""Render the same immutable view through the retained report template.

The DOCX package supplies actual paragraph/table components, page geometry,
styles, headers and footer. HTML mirrors that template's measured layout.
Neither renderer infers facts or performs investigation/model calls.
"""
from __future__ import annotations
import argparse, hashlib, html, io, json, math, re, zipfile
from copy import deepcopy
from pathlib import Path
from docx import Document
from docx.shared import Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run
if __package__:
    from .template_contract import EVENT_KINDS
    from . import typography
else:  # Preserve the documented direct-file renderer CLI.
    from template_contract import EVENT_KINDS
    import typography

STYLE_PATH=Path(__file__).with_name('report_style.json')
STYLE_BYTES=STYLE_PATH.read_bytes()
STYLE=json.loads(STYLE_BYTES)
TEMPLATE_PATH=Path(__file__).parent/STYLE['template_file']
FONT=STYLE['font']['family']


def render_style_identity():
 return {'profile_id':STYLE['profile_id'],'version':STYLE['version'],
         'source_kind':STYLE['source_kind'],'source_file':STYLE_PATH.name,
         'sha256':hashlib.sha256(STYLE_BYTES).hexdigest(),
         'template_file':STYLE['template_file'],
         'template_sha256':hashlib.sha256(TEMPLATE_PATH.read_bytes()).hexdigest()}

# Layout/theme contains no typography; every render uses the resolved role scale.
LAYOUT_CSS = f""":root{{--ink:#{STYLE['font']['color']};--muted:#{STYLE['text']['muted_color']};--line:#{STYLE['table']['border_color']};--dark:#{STYLE['table']['header_fill']};--wash:#{STYLE['table']['callout_fill']}}}
*{{box-sizing:border-box}}
body{{margin:0;background:#ebebeb;color:var(--ink)}}
nav{{max-width:210mm;margin:20px auto 10px;display:flex;gap:12px;flex-wrap:wrap;padding:0 12px}}
a{{color:inherit}}
a:focus-visible{{outline:2px solid #555;outline-offset:3px}}
.sheet{{width:210mm;min-height:297mm;margin:0 auto 20px;background:#fff;padding:{STYLE['margins_mm']['top']}mm {STYLE['margins_mm']['right']}mm {STYLE['margins_mm']['bottom']}mm {STYLE['margins_mm']['left']}mm;box-shadow:0 2px 16px #0000000d;display:flex;flex-direction:column}}
.sheet.appendix{{min-height:0}}
.running{{text-align:right;color:var(--muted);margin:-9mm 0 6mm}}
.kicker{{color:var(--muted);margin:0 0 4pt}}
h1{{color:#{STYLE['text']['title_color']};margin:0 0 5pt;padding:0 0 4pt;border-bottom:1pt solid #{STYLE['text']['rule_color']}}}
h2{{color:#{STYLE['text']['title_color']};margin:10pt 0 5pt;break-after:avoid}}
h3{{color:#{STYLE['text']['title_color']};margin:10pt 0 5pt;break-after:avoid}}
p{{margin:0 0 4pt;overflow-wrap:anywhere;white-space:pre-line}}
.note{{color:var(--muted);margin:0 0 3pt}}
.callout{{background:var(--wash);border:.5pt solid var(--line);padding:4pt 5pt;margin:0 0 12pt;break-inside:auto}}
.callout strong{{display:block;margin-bottom:4pt}}
.callout p{{margin:0}}
table{{border-collapse:collapse;width:100%;table-layout:fixed;margin:0 0 12pt}}
th,td{{border:.5pt solid var(--line);padding:2pt 5pt;vertical-align:top;overflow-wrap:anywhere;white-space:pre-line}}
th{{background:var(--dark);color:white;text-align:left}}
tbody tr:nth-child(even){{background:#{STYLE['table']['stripe_fill']}}}
thead{{display:table-header-group}}
tr{{break-inside:avoid}}
tr.long-row{{break-inside:auto}}
table.keep-together{{break-inside:avoid}}
.reader-cards{{max-width:210mm;margin:12px auto;padding:16px;background:white}}
.reader-cards summary{{cursor:pointer}}
.judgment-card,.time-card{{border-left:3px solid var(--line);padding:8px 12px;margin:12px 0}}
.numeric-axis{{position:relative;height:32px;margin:12px 0;border-bottom:1px solid var(--ink)}}
.axis-mark{{position:absolute;bottom:-9px;transform:translateX(-50%)}}
caption{{text-align:left;color:var(--muted);padding:0 0 3pt}}
.sheet-foot{{margin-top:auto;padding-top:10mm;color:var(--muted);text-align:right}}
.page-body{{min-width:0}}
@page{{size:A4;margin:{STYLE['margins_mm']['top']}mm {STYLE['margins_mm']['right']}mm {STYLE['margins_mm']['bottom']}mm {STYLE['margins_mm']['left']}mm}}
@page{{@bottom-right{{content:counter(page);color:#6F6F6F}}}}
@media screen{{table[data-region="timeline"],table[data-region="major_findings"]{{display:block}}
table[data-region="timeline"] colgroup,table[data-region="major_findings"] colgroup{{display:none}}
table[data-region="timeline"] tbody,table[data-region="major_findings"] tbody{{display:grid;gap:10pt}}
table[data-region="timeline"] tr,table[data-region="major_findings"] tr{{display:block;border:1px solid var(--line);padding:6pt}}
table[data-region="timeline"] thead,table[data-region="major_findings"] thead{{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}}
table[data-region="timeline"] td,table[data-region="major_findings"] td{{display:block;border:0;padding:3pt 5pt}}
table[data-region="timeline"] td::before,table[data-region="major_findings"] td::before{{content:attr(data-label) ' · '}}}}
@media print{{.reader-cards{{display:none}}
body{{background:#fff}}
nav{{display:none}}
.sheet{{width:auto;min-height:0;display:block;margin:0;padding:0;box-shadow:none;break-after:page}}
.sheet:last-child,.sheet.appendix{{break-after:auto}}
.sheet.appendix+.sheet.appendix{{padding-top:5mm}}
.running{{margin:0 0 6mm}}
.sheet-foot{{display:none}}
a{{text-decoration:none}}}}
@media screen and (max-width:850px){{.sheet{{width:100%;min-height:0;padding:24px 20px;margin-bottom:12px}}
nav{{margin:12px}}
.running{{margin:0 0 18px}}
th,td{{padding:7px 6px}}
.page-body{{overflow-x:auto}}}}
@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important;animation:none!important}}}}
.authored-lead{{display:block}}
.authored-overall{{display:block}}
.authored-detail{{display:block;color:var(--report-muted)}}
@media screen{{table[data-region="major_findings"] td>.authored-lead,table[data-region="timeline"] td>.authored-lead{{display:inline}}
table[data-region="major_findings"] td>.authored-detail,table[data-region="timeline"] td>.authored-detail{{margin-top:2pt}}}}
.view-choice{{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}}
.view-controls,.document-status{{max-width:210mm;margin:10px auto;padding:10px 14px;background:white;border:1px solid var(--line)}}
.view-controls{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
.view-controls label{{cursor:pointer;padding:5px 10px;border:1px solid var(--line)}}
.view-controls p{{margin:0;color:#5F6368}}
.document-status .note{{margin:5px 0}}
.snapshot-provenance{{color:#5F6368}}
#body-view:checked~.view-controls label[for="body-view"],#reader-view:checked~.view-controls label[for="reader-view"],#audit-view:checked~.view-controls label[for="audit-view"]{{background:#292929;color:white}}
#body-view:focus-visible~.view-controls label[for="body-view"],#reader-view:focus-visible~.view-controls label[for="reader-view"],#audit-view:focus-visible~.view-controls label[for="audit-view"]{{outline:2px solid #292929;outline-offset:3px}}
.citation-returns{{margin:4px 0 12px;padding:0}}
.citation-returns a{{color:#5F6368}}
.prose-record{{margin:12px 0;padding:12px;border:1px solid var(--line);break-inside:auto}}
.prose-record.short-record{{break-inside:avoid}}
.metadata-detail{{margin-top:4px;color:#5F6368}}
.source-summary{{margin:8px 0 12px}}
.source-summary dd{{margin:0 0 6px;white-space:pre-line;overflow-wrap:anywhere}}
.prose-record dl{{margin:0}}
.prose-record dt{{margin:8px 0 4px}}
.prose-record dd{{margin:0 0 12px;white-space:pre-line;overflow-wrap:anywhere}}
.source-excerpt{{white-space:pre-wrap;overflow-wrap:anywhere;background:#F2F2F2;padding:12px}}
.source-line{{display:inline}}
.citation-choice{{margin-right:6px}}
.citation-group label{{cursor:pointer;text-decoration:underline}}
.citation-choice:not(:checked)~.citation-list{{display:none}}
.citation-group:has(:target) .citation-list{{display:inline}}
.line-number{{color:#5F6368;user-select:none;margin-right:1em}}
mark{{background:#fff1b8;color:#292929}}
@media screen{{.sheet{{padding-top:24px}}
.running{{margin-bottom:12px}}
.authored-detail,.note{{color:var(--report-muted)}}
details:has(:target)>:not(summary),h3:target+.citation-returns+.source-support>details>:not(summary),h2:target+.citation-returns+.source-support>details>:not(summary){{display:block}}
details:has(:target)::details-content,h3:target+.citation-returns+.source-support>details::details-content,h2:target+.citation-returns+.source-support>details::details-content{{content-visibility:visible}}
:target{{scroll-margin-top:20px;outline:2px solid #767676;outline-offset:3px}}}}
@media print{{.view-controls,.view-choice,.citation-returns,.citation-choice,.citation-group label{{display:none}}
.citation-choice:not(:checked)~.citation-list{{display:none!important}}
.document-status{{margin:0 0 8mm;padding:4mm;max-width:none}}
#body-view:checked~main [data-presentation-scope="technical"],#body-view:checked~main [data-presentation-scope="audit"],#reader-view:checked~main [data-presentation-scope="audit"]{{display:none!important}}
main article.sheet details.exact-original>:not(summary){{display:block!important}}
main article.sheet details.exact-original::details-content{{content-visibility:visible!important}}
main article.sheet details.exact-original>summary{{display:none}}
#audit-view:checked~main article[data-presentation-scope]{{display:block!important}}
#audit-view:checked~main details>:not(summary){{display:block!important}}
#audit-view:checked~main details::details-content{{content-visibility:visible!important}}
#audit-view:checked~main details>summary{{display:none}}
#audit-view:checked~main .citation-group .citation-list{{display:inline!important}}
#body-view:checked~main article.sheet[data-presentation-scope="body"]:not(:has(~article.sheet[data-presentation-scope="body"])){{break-after:auto}}
#reader-view:checked~main article.sheet:not([data-presentation-scope="audit"]):not(:has(~article.sheet:not([data-presentation-scope="audit"]))){{break-after:auto}}}}

body{{background:var(--report-canvas)}}
table[data-region="key_indicators"] td:first-child{{overflow-wrap:anywhere}}
table[data-region="key_indicators"] th:first-child{{width:34%}}
table[data-region="response_actions"] td:first-child{{width:12%;color:var(--report-accent)}}
@media screen and (min-width:850px){{.finding-stack{{grid-template-columns:repeat(2,minmax(0,1fr))}}
.finding-stack .finding-card:first-child{{grid-column:1/-1}}}}
.sheet h1,.sheet h2,.sheet h3{{color:var(--report-ink)}}
.sheet h1{{border-bottom:2px solid var(--report-accent);padding-bottom:10px}}
.sheet h2{{border-bottom:1px solid var(--report-border);padding-bottom:6px;margin-top:22px}}
.kicker{{color:var(--report-accent)}}
.hierarchy-line{{display:block;margin:3px 0}}
.hierarchy-line.nested{{padding-left:1.1em}}
.authored-lead,.authored-overall,.authored-detail{{white-space:normal}}
.callout{{padding:18px;border:1px solid var(--report-border);border-top:4px solid var(--report-accent);background:var(--report-canvas)}}
table.metadata th{{background:var(--report-ink);padding:7px 10px}}
table.metadata td{{padding:10px;vertical-align:top}}
.finding-stack{{display:grid;gap:18px;margin-bottom:20px}}
.finding-card{{border:1px solid var(--report-border);border-radius:6px;padding:18px;background:white}}
.finding-card>h3{{margin:0 0 12px;display:flex;gap:12px;align-items:center}}
.finding-number{{color:var(--report-accent)}}
.finding-content{{display:grid;gap:14px;margin-top:14px}}
.field-label{{display:block;color:var(--report-muted);margin-bottom:4px}}
.finding-citations{{color:var(--report-muted);margin:8px 0 0}}
.assessment-gauge{{margin:0 0 16px;break-inside:avoid;white-space:normal}}
.assessment-title{{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:10px;color:var(--report-muted)}}
.assessment-title b{{color:var(--report-ink)}}
.assessment-steps{{padding:0;margin:0 0 8px;list-style:none;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:5px}}
.assessment-steps li{{position:relative;min-width:0;text-align:center;color:var(--report-muted);padding-bottom:18px}}
.step-bar{{display:block;background:var(--report-inset);border:1px solid var(--report-border);border-radius:3px;margin-bottom:5px}}
.assessment-steps li.selected{{color:var(--report-ink)}}
.selected .step-bar{{outline:2px solid var(--report-ink);outline-offset:2px;background:var(--report-ink);color:white}}
.step-label{{display:block;overflow-wrap:anywhere}}
.step-marker{{position:absolute;bottom:0;left:0;right:0}}
.step-marker:before{{content:'▲ '}}
.unassessed .assessment-title b{{color:var(--report-muted)}}
.assessment-steps .deferred{{margin-right:5px;border-right:1px solid var(--report-border);padding-right:6px}}
.deferred .step-bar{{border-style:dashed}}
.deferred.selected .step-bar{{background:var(--report-muted);outline-color:var(--report-muted)}}
.assessment-rationale{{color:var(--report-muted);margin:4px 0 0}}
.time-range{{display:flex;gap:4px;flex-wrap:wrap;align-items:baseline;background:var(--report-inset);border-left:3px solid var(--report-muted);padding:10px;margin-top:6px}}
.time-range>span:first-child{{flex-basis:100%;color:var(--report-muted)}}
.time-range b{{overflow-wrap:anywhere}}
.visual-timeline>ol{{list-style:none;padding:0;margin:20px 0}}
.timeline-event{{display:flex;gap:12px;padding-bottom:22px}}
.timeline-time{{flex:0 0 145px;padding-top:5px;min-width:0}}
.timeline-rail{{flex:0 0 28px;position:relative}}
.timeline-rail:after{{content:'';position:absolute;top:30px;bottom:-22px;left:13px;width:2px;background:var(--report-border)}}
.timeline-event:last-child .timeline-rail:after{{bottom:0}}
.timeline-dot{{position:relative;z-index:1;display:flex;align-items:center;justify-content:center;width:28px;height:28px;border:1px solid var(--report-accent);border-radius:50%;background:white;color:var(--report-accent);margin-top:5px}}
.timeline-card{{flex:1;min-width:0;border:1px solid var(--report-border);border-top:3px solid var(--report-ink);padding:14px 18px;background:var(--report-canvas);border-radius:0 0 6px 6px}}
.timeline-event-title{{margin:0 0 7px}}
.timeline-meaning{{margin:0;white-space:normal}}
.timeline-card .authored-detail{{margin:5px 0 10px}}
.event-kind{{color:var(--report-ink);display:inline-block;background:var(--report-inset);padding:3px 7px;border-radius:3px;margin-bottom:9px}}
.response .event-kind{{color:#347367;background:#EFF6F2}}
.response .timeline-card{{border-top-color:#347367}}
.acquisition .event-kind{{color:#725E91;background:#F4F0F8}}
.acquisition .timeline-card{{border-top-color:#725E91}}
.unknown .event-kind{{color:var(--report-muted)}}
@media screen and (max-width:560px){{.finding-card,.callout{{padding:14px}}
.assessment-title{{align-items:flex-start}}
.timeline-event{{display:grid;grid-template-columns:28px minmax(0,1fr);gap:8px}}
.timeline-time{{grid-column:2;padding:0}}
.timeline-rail{{grid-column:1;grid-row:1 / 3;margin:0}}
.timeline-card{{grid-column:2}}}}
@media print{{body{{background:white}}
.assessment-gauge,.finding-card,.timeline-card,.callout{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}
.finding-card{{border-radius:0;padding:10pt;break-inside:auto}}
.finding-card>h3{{break-after:avoid}}
.finding-content{{display:block}}
.finding-field{{margin:7pt 0}}
.timeline-event{{margin:0;padding:0 0 12pt;gap:9pt;break-inside:avoid}}
.timeline-time{{flex:0 0 90pt;break-after:avoid;margin-bottom:5pt}}
.timeline-event.long-event{{break-inside:auto}}
.timeline-rail{{flex:0 0 18pt}}
.timeline-rail:after{{top:20pt;left:8.5pt;bottom:-12pt}}
.timeline-dot{{width:18pt;height:18pt}}
.timeline-card{{padding:8pt}}
.callout{{padding:10pt}}
#body-view:checked~main article[data-presentation-scope="body"] details.metadata-detail>:not(summary),#reader-view:checked~main article[data-presentation-scope="body"] details.metadata-detail>:not(summary){{display:block!important}}
#body-view:checked~main article[data-presentation-scope="body"] details.metadata-detail::details-content,#reader-view:checked~main article[data-presentation-scope="body"] details.metadata-detail::details-content{{content-visibility:visible!important}}
article[data-presentation-scope="body"] details.metadata-detail>summary{{display:none}}}}
.risk .selected[data-level="1"] .step-bar{{background:#{STYLE['visual_theme']['risk_steps'][0]}}}
.risk .selected[data-level="2"] .step-bar{{background:#{STYLE['visual_theme']['risk_steps'][1]}}}
.risk .selected[data-level="3"] .step-bar{{background:#{STYLE['visual_theme']['risk_steps'][2]}}}
.risk .selected[data-level="4"] .step-bar{{background:#{STYLE['visual_theme']['risk_steps'][3]}}}
.risk .selected[data-level="5"] .step-bar{{background:#{STYLE['visual_theme']['risk_steps'][4]}}}
.appendix .appendix-guide{{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;border-bottom:1px solid var(--report-border);padding:0 0 12px;margin-bottom:20px;color:var(--report-muted)}}
.appendix-guide strong{{color:var(--report-accent)}}
.appendix-guide span+span:before{{content:' / ';color:var(--report-border)}}
.appendix h3{{border-left:3px solid var(--report-accent);padding:6px 10px;background:var(--report-inset);break-after:avoid}}
.appendix .note,.appendix .citation-returns a{{color:var(--report-muted)}}
.appendix .prose-record{{padding:14px;border-color:var(--report-border);border-radius:4px;background:var(--report-surface)}}
.appendix .prose-record>dl{{display:grid;grid-template-columns:minmax(95px,22%) minmax(0,1fr);gap:8px 14px}}
.appendix .prose-record dt{{margin:0;color:var(--report-muted)}}
.appendix .prose-record dd{{margin:0;padding-bottom:9px;border-bottom:1px solid var(--report-border)}}
.appendix .source-support{{margin:10px 0 28px;border:1px solid var(--report-border);border-radius:4px;padding:14px;min-width:0}}
.appendix .source-summary{{display:grid;grid-template-columns:minmax(95px,22%) minmax(0,1fr);gap:7px 14px;margin:0 0 10px}}
.appendix .source-summary dt{{color:var(--report-muted)}}
.appendix .source-summary dd{{margin:0;min-width:0}}
.appendix details.exact-original>summary{{cursor:pointer;color:var(--report-accent);padding:10px 0;border-top:1px solid var(--report-border)}}
.source-fields{{margin:0}}
.source-field{{margin:12px 0;break-inside:auto}}
.source-field>dt{{color:var(--report-muted);margin-bottom:5px}}
.source-field>dd{{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}}
.appendix .source-excerpt{{background:var(--report-inset);border-left:3px solid var(--report-muted);padding:12px;max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}}
.appendix .line-number{{color:var(--report-muted)}}
.appendix .source-line{{white-space:pre-wrap}}
.appendix .exact-original{{min-width:0}}
.appendix .prose-records>p{{color:var(--report-accent);break-after:avoid}}
@media screen and (max-width:560px){{.appendix .prose-record>dl,.appendix .source-summary{{grid-template-columns:1fr;gap:3px}}
.appendix .source-summary dd{{margin-bottom:9px}}
.appendix .prose-record dd{{margin-bottom:7px}}
.appendix .source-support{{padding:10px}}}}
@media print{{.appendix h3,.appendix .source-excerpt{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}
.appendix .appendix-guide{{margin-bottom:10pt;padding-bottom:6pt}}
.appendix .prose-record>dl,.appendix .source-summary{{display:block}}
.appendix .prose-record dt,.appendix .source-summary dt,.source-field>dt{{break-after:avoid}}
.appendix .prose-record dd,.appendix .source-summary dd,.source-field>dd{{margin:0 0 6pt}}
.appendix .source-support,.appendix .prose-record{{padding:8pt;break-inside:auto;border-radius:0}}
.appendix .source-excerpt{{padding:7pt;break-inside:auto}}
.source-field{{break-inside:auto;margin:6pt 0}}
.source-field[data-compact="true"]{{display:grid;grid-template-columns:22% minmax(0,1fr);gap:10pt;break-inside:avoid}}
.source-field>dd{{break-inside:auto}}
.appendix .short-record{{break-inside:avoid}}
.appendix .short-record>dl{{display:grid;grid-template-columns:22% minmax(0,1fr);gap:5pt 10pt}}
.appendix .short-record dd{{padding-bottom:4pt;margin:0}}
.appendix .source-summary[data-compact="true"]{{display:grid;grid-template-columns:22% minmax(0,1fr);gap:5pt 10pt}}
.reader-card-section{{display:none!important}}}}
"""
LAYOUT_CSS += ":root{" + "".join("--report-" + key.replace("_", "-") + ":#" + value + ";" for key, value in STYLE["visual_theme"].items() if key not in ("name", "source", "risk_steps")) + "}"
CSS = LAYOUT_CSS + typography.css(STYLE)


def text(v):return html.escape(str(v),quote=True)
def bookmark_id(v):return 'r_'+hashlib.sha256(str(v).encode()).hexdigest()[:28]
def linked_text(value,targets):
 value=str(value)
 if not targets:return text(value)
 pattern='('+'|'.join(re.escape(x) for x in sorted(targets,key=len,reverse=True))+')'
 return ''.join('<a href="#'+bookmark_id(part)+'">'+text(part)+'</a>' if part in targets else text(part) for part in re.split(pattern,value))


def _hierarchy_lines(value,targets):
 lines=str(value).split('\n')
 if len(lines)==1:return linked_text(value,targets)
 return '\n'.join('<span class="hierarchy-line'+(' nested' if line.startswith('  ') else '')+'">'+linked_text(line,targets)+'</span>' for line in lines)


def _hierarchical_html(lead,detail,targets,role='lead'):
 return '<span class="authored-'+role+'">'+_hierarchy_lines(lead,targets)+'</span>'+('<span class="authored-detail">'+_hierarchy_lines(detail,targets)+'</span>' if detail else '')


def _display_time_point(value):
 # Preserve supplied precision and timezone; no year/time inference.
 match=re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})([+]09:00|Z)',value)
 if not match:return value
 y,m,d,h,mi,se,zone=match.groups()
 return f'{y}년 {m}월 {d}일 {h}시 {mi}분 {se}초 '+('KST' if zone=='+09:00' else 'UTC')


def _time_window_text(window,readable=False):
 """Format the analyst's declared relation without deriving a date or judgment."""
 if readable:window={**window,**{k:_display_time_point(window[k]) for k in ('start','end') if window.get(k) is not None}}
 relation=window.get('relation','range')
 if relation=='before':return window['end']+' 이전'
 if relation=='by':return '늦어도 '+window['end']
 if relation=='after':return window['start']+' 이후'
 if relation=='from':return window['start']+'부터'
 return window['start']+' ~ '+window['end']


def _time_window_label(window):
 return window.get('subject','판단 시각 범위')+(' · '+window['qualifier'] if window.get('qualifier') else '')


def _assessment_html(a):
 if a is None:return ''
 labels=a['labels'];level=a['level']
 selected=labels[level-1] if level is not None else '평가 보류'
 status=(str(level)+'/5 · ' if level is not None else '')+selected
 steps=''
 for value,label in [(None,'평가 보류'),*enumerate(labels,1)]:
  classes=('deferred ' if value is None else '')+('selected' if value==level else '')
  steps+='<li class="'+classes.strip()+'" data-level="'+('deferred' if value is None else str(value))+'"'+(' aria-current="step"' if value==level else '')+'><span class="step-bar">'+('—' if value is None else str(value))+'</span><span class="step-label">'+text(label)+'</span>'+('<span class="step-marker">현재 판단</span>' if value==level else '')+'</li>'
 window=a.get('time_range')
 time_html=''
 if window:
  value=('<b>'+text(_display_time_point(window['start']))+'</b><span aria-hidden="true">~</span><b>'+text(_display_time_point(window['end']))+'</b>'
         if window.get('relation','range')=='range' else '<b>'+text(_time_window_text(window,readable=True))+'</b>')
  time_html='<div class="time-range"><span>'+text(_time_window_label(window))+'</span>'+value+'</div>'
 rationale='<p class="assessment-rationale">'+text(a['rationale'])+'</p>' if a.get('rationale') else ''
 return '<section class="assessment-gauge '+('unassessed' if level is None else 'assessed')+' '+text(a.get('tone','risk'))+'" aria-label="평가 보류 및 5단계 평가: '+text(status)+'"><div class="assessment-title"><span>근거에 따른 평가</span><b>'+text(status)+'</b></div><ol class="assessment-steps">'+steps+'</ol>'+time_html+rationale+'</section>'


def _finding_cards(b,targets):
 cards=[]
 for i,row in enumerate(b['rows']):
  details=(b.get('cell_details') or [['']*len(row) for _ in b['rows']])[i]
  a=b['row_assessments'][i]
  content=''.join('<div class="finding-field"><span class="field-label">'+text(b['headers'][j])+'</span>'+_hierarchical_html(row[j],details[j],targets)+'</div>' for j in (1,2))
  cards.append('<article class="finding-card"><h3><span class="finding-number">'+str(i+1).zfill(2)+'</span>'+text(row[0])+'</h3>'+_assessment_html(a)+'<div class="finding-content">'+content+'</div>'+('<p class="finding-citations">'+linked_text(row[3],targets)+'</p>' if row[3] else '')+'</article>')
 return '<section data-region="major_findings" class="finding-stack">'+''.join(cards)+'</section>'


def _timeline_html(b,targets):
 items=[]
 for i,row in enumerate(b['rows']):
  details=(b.get('cell_details') or [['']*len(row) for _ in b['rows']])[i]
  kind=(b.get('event_kinds') or ['unknown']*len(b['rows']))[i]
  content='<p class="timeline-event-title">'+_hierarchy_lines(row[1],targets)+'</p>'+('<div class="authored-detail">'+_hierarchy_lines(details[1],targets)+'</div>' if details[1] else '')
  content+='<p class="timeline-meaning">'+_hierarchy_lines(row[2],targets)+'</p>'+('<div class="authored-detail">'+_hierarchy_lines(details[2],targets)+'</div>' if details[2] else '')
  long_event=' long-event' if sum(len(str(v)) for v in [*row,*details])>900 else ''
  items.append('<li class="timeline-event '+text(kind)+long_event+'"><div class="timeline-time">'+_hierarchical_html(row[0],details[0],targets)+'</div><div class="timeline-rail" aria-hidden="true"><span class="timeline-dot">'+str(i+1).zfill(2)+'</span></div><article class="timeline-card"><span class="event-kind">'+text(EVENT_KINDS[kind])+'</span>'+content+('<p class="finding-citations">근거 · '+linked_text(row[3],targets)+'</p>' if len(row)>3 and row[3] else '')+'</article></li>')
 return '<section data-region="timeline" class="visual-timeline"><p class="note">기록별 시간축 · 항목 간 간격은 실제 경과시간과 무관</p><ol>'+''.join(items)+'</ol></section>'


def hblock(b,targets=(),anchors=None):
 if b.get('literal'):targets=()
 region=' data-region="'+text(b['region'])+'"' if b.get('region') else ''
 kind=b['kind']
 if kind=='heading':
  tag='h3' if b.get('level')==2 else 'h2'
  ident=' id="'+bookmark_id(b['id'])+'"' if b.get('id') else ''
  return '<'+tag+ident+'>'+text(b['text'])+'</'+tag+'>'
 if b.get('authored_hierarchy') and kind in ('paragraph','callout'):
  content=_hierarchical_html(b['text'],b.get('detail',''),targets,'overall' if b.get('region')=='overall_judgment' else 'lead')
  if b.get('executive_summary') and '\n' in b['text']:
   headline,explanation=b['text'].split('\n',1)
   content=_hierarchical_html(headline,'',targets,'overall')+_hierarchical_html(explanation,b.get('detail',''),targets)
  if kind=='paragraph':return '<p'+region+'>'+content+'</p>'
  return '<aside'+region+' class="callout"><strong>'+text(b['label'])+'</strong>'+_assessment_html(b.get('assessment'))+'<p>'+content+'</p></aside>'
 if kind in ('paragraph','note'):return '<p'+region+(' class="note"' if kind=='note' else '')+(' id="'+bookmark_id(b['id'])+'"' if b.get('id') else '')+'>'+linked_text(b['text'],targets)+'</p>'
 if kind=='callout':return '<aside'+region+' class="callout"><strong>'+text(b['label'])+'</strong><p>'+linked_text(b['text'],targets)+'</p></aside>'
 if kind=='table':
  if b.get('row_assessments'):return _finding_cards(b,targets)
  if b.get('region')=='timeline' and b.get('authored_hierarchy'):return _timeline_html(b,targets)
  if b.get('practitioner') or b.get('literal') or any(len(str(value))>600 or str(value).count('\n')>8 for row in [*b['rows'],*b.get('cell_details',[])] for value in row):
   return _prose_table(b,targets,anchors)
  widths=b.get('widths') or [100/len(b['headers'])]*len(b['headers'])
  ids=b.get('row_ids',[]);rows=[]
  for i,row in enumerate(b['rows']):
   ident=''
   if i<len(ids) and (anchors is None or ids[i] not in anchors):
    ident=' id="'+bookmark_id(ids[i])+'"'
    if anchors is not None:anchors.add(ids[i])
   measured=[str(value)+('\n'+b['cell_details'][i][ci] if b.get('cell_details') and b['cell_details'][i][ci] else '') for ci,value in enumerate(row)]
   long_row=any(sum(max(1,math.ceil(len(line)/max(1,(STYLE['table']['width_mm']*width/100-4)*2.835/typography.resolved_roles(STYLE)['body']['print_pt']/2))) for line in str(value).split('\n'))>30 for value,width in zip(measured,widths))
   def cell(value,ci):
    if not b.get('authored_hierarchy'):return linked_text(value,targets)
    detail=(b.get('cell_details') or [['']*len(row) for _ in b['rows']])[i][ci]
    if b.get('region')=='metadata' and detail:
     return _hierarchical_html(value,'',targets)+'<details class="metadata-detail"><summary>출처·설명</summary><div>'+_hierarchical_html('',detail,targets)+'</div></details>'
    return _hierarchical_html(value,detail,targets)
   rows.append('<tr'+ident+(' class="long-row"' if long_row else '')+'>'+''.join('<td data-label="'+text(b['headers'][ci])+'">'+cell(x,ci)+'</td>' for ci,x in enumerate(row))+'</tr>')
  variant=b.get('variant','detail')
  if b.get('keep_together'):variant+=' keep-together'
  caption=('<caption id="'+bookmark_id(b['id'])+'">'+text(b['id'])+'</caption>') if b.get('id') else ''
  markup='<table'+(' data-region="'+text(b['region'])+'"' if b.get('region') else '')+' class="'+text(variant)+'">'+caption+'<colgroup>'+''.join(f'<col style="width:{float(w)}%">' for w in widths)+'</colgroup><thead><tr>'+''.join('<th scope="col">'+text(x)+'</th>' for x in b['headers'])+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table>'
  return '<details class="exact-original"><summary>보존 원문과 추적 정보 펼치기</summary>'+markup+'</details>' if b.get('literal') else markup
 raise ValueError('Unsupported block '+str(kind))


def _source_excerpt(value,start=None,highlight=()):
 """Number only explicitly supplied source lines, never byte coordinates."""
 if not isinstance(start,int) or isinstance(start,bool) or start<1:
  return '<pre class="source-excerpt">'+text(value)+'</pre>'
 lines=[]
 for index,line in enumerate(str(value).split('\n'),start):
  content=text(line)
  if index in highlight:content='<mark>'+content+'</mark>'
  lines.append('<span class="source-line"><span class="line-number" aria-label="원문 행 '+str(index)+'">'+str(index)+'</span>'+content+'</span>')
 return '<pre class="source-excerpt">'+'\n'.join(lines)+'</pre>'


def _prose_table(block,targets,anchors):
 """Lossless labelled records avoid page-height cells for long explanations."""
 if block.get('literal') and len(block['headers'])==2 and all(len(row)==2 for row in block['rows']):
  return _literal_source_pairs(block,anchors)
 ids=block.get('row_ids',[]);parts=[]
 if block.get('id'):parts.append('<p id="'+bookmark_id(block['id'])+'">'+text(block['id'])+'</p>')
 for ri,row in enumerate(block['rows']):
  ident=''
  if ri<len(ids) and (anchors is None or ids[ri] not in anchors):
   ident=' id="'+bookmark_id(ids[ri])+'"'
   if anchors is not None:anchors.add(ids[ri])
  cells=[]
  for ci,value in enumerate(row):
   label=block['headers'][ci]
   if block.get('literal') and ci and str(row[0])=='보존 인용':
    starts=block.get('cell_line_starts',{});highlights=block.get('cell_highlight_lines',{})
    key=str(ri)+':'+str(ci)
    content=_source_excerpt(value,starts.get(key),highlights.get(key,()))
   else:
    detail=block.get('cell_details',[])
    content=(_hierarchical_html(value,detail[ri][ci] if detail else '',targets)
             if block.get('authored_hierarchy') else linked_text(value,() if block.get('literal') else targets))
   cells.append('<dt>'+text(label)+'</dt><dd>'+content+'</dd>')
  short=all(len(str(value))<=600 and str(value).count('\n')<=8 for value in
            [*row,*(block.get('cell_details',[])[ri] if block.get('cell_details') else [])])
  parts.append('<article class="prose-record'+(' short-record' if short else '')+'"'+ident+' aria-label="'+text('기록 '+str(ri+1))+'"><dl>'+''.join(cells)+'</dl></article>')
 markup='<section class="prose-records"'+(' data-region="'+text(block['region'])+'"' if block.get('region') else '')+'>'+''.join(parts)+'</section>'
 if not block.get('literal'):return markup
 fields={'원래 식별자','보존 버전','자료','보존 위치','연결 출처','반환 필드','보존 필드 내 위치','제시 범위','좌표 기준','해석 한계','현재 대조 상태','실제 반환 범위'}
 summary=''.join('<dt>'+text(row[0])+'</dt><dd>'+text(row[1])+'</dd>' for row in block['rows'] if len(row)==2 and row[0] in fields)
 return '<section class="source-support">'+('<dl class="source-summary" aria-label="근거 위치와 해석 한계">'+summary+'</dl>' if summary else '')+'<details class="exact-original"><summary>보존 원문과 전체 추적 정보 · 펼친 항목은 독자 보기에서 인쇄</summary>'+markup+'</details></section>'


def _literal_source_pairs(block,anchors):
 """Present each retained key/value once, preserving literal bytes and anchors."""
 visible={'자료','보존 위치','연결 출처','반환 필드','보존 필드 내 위치','제시 범위','좌표 기준','해석 한계','현재 대조 상태','실제 반환 범위'}
 summary=[];details=[];ids=block.get('row_ids',[])
 caption='<p id="'+bookmark_id(block['id'])+'">'+text(block['id'])+'</p>' if block.get('id') else ''
 for ri,(label,value) in enumerate(block['rows']):
  ident=''
  if ri<len(ids) and (anchors is None or ids[ri] not in anchors):
   ident=' id="'+bookmark_id(ids[ri])+'"'
   if anchors is not None:anchors.add(ids[ri])
  key=str(ri)+':1'
  content=(_source_excerpt(value,block.get('cell_line_starts',{}).get(key),block.get('cell_highlight_lines',{}).get(key,())) if label=='보존 인용' else text(value))
  pair='<dt'+ident+'>'+text(label)+'</dt><dd>'+content+'</dd>'
  if label in visible:summary.append(pair)
  else:details.append('<div class="source-field" data-compact="'+('true' if label!='보존 인용' and len(str(value))<600 else 'false')+'">'+pair+'</div>')
 return '<section class="source-support">'+caption+('<dl class="source-summary" data-compact="'+('true' if sum(len(str(value)) for label,value in block['rows'] if label in visible)<1200 else 'false')+'" aria-label="근거 위치와 해석 한계">'+''.join(summary)+'</dl>' if summary else '')+('<details class="exact-original"><summary>정확 원문·식별자·검증 정보 · 인쇄 시 전체 포함</summary><dl class="source-fields">'+''.join(details)+'</dl></details>' if details else '')+'</section>'


def _hierarchy(doc,para,detail,targets,role='lead'):
 """Register explicit presentation roles; never split or infer authored prose."""
 typography.register(doc,para,role)
 if detail:
  element=deepcopy(para._p);para._p.addnext(element)
  detail_para=_fill_paragraph(Paragraph(element,doc._body),detail,targets)
  # The lead owns any bookmark; a detail paragraph must not duplicate it.
  for mark in list(element.iter(qn('w:bookmarkStart')))+list(element.iter(qn('w:bookmarkEnd'))):mark.getparent().remove(mark)
  typography.register(doc,detail_para,'detail')


def _executive_summary(doc,para,value,targets):
 """Style the author's explicit headline break; never derive a summary."""
 headline,separator,explanation=value.partition('\n')
 _fill_paragraph(para,headline,targets)
 _hierarchy(doc,para,'',targets,'overall')
 if separator:
  element=deepcopy(para._p);para._p.addnext(element)
  for mark in list(element.iter(qn('w:bookmarkStart')))+list(element.iter(qn('w:bookmarkEnd'))):mark.getparent().remove(mark)
  body_para=_fill_paragraph(Paragraph(element,doc._body),explanation,targets)
  _hierarchy(doc,body_para,'',targets,'lead')


def _card_references(refs,targets):
 # The short label opens the canonical endnote with exact id/version/coordinates.
 # Technical identities remain there instead of crowding every reader card.
 return ' · '.join(linked_text(ref.get('label',ref['id']),targets) for ref in refs)


def render_reader_cards(cards,targets):
 if not cards:return ''
 chunks=['<details class="reader-cards"><summary>저장 시점의 판단·시각 카드</summary><p class="note">판단 스냅샷 '+text(cards['snapshot_revision'])+'</p>']
 for judgment in cards['judgments']:
  answer_link=('<a href="#'+bookmark_id(judgment['question_id'])+'">보존된 현재 답과 범위 보기</a>') if judgment['question_id'] in targets else '보존된 현재 답과 범위는 조사 질문 부록 참조'
  chunks.append('<article class="judgment-card"><h3>'+text(judgment['title'])+'</h3><p>'+answer_link+'</p><p class="note">질문 버전 '+text(judgment['revision'])+' · '+('재검토 필요' if judgment['review_required'] else '저장 시점의 답')+'</p><p>적용 범위 · '+text(judgment['scope'])+'</p>')
  for claim in judgment['judgments']:
   chunks.append('<p>'+_card_references(claim['source_refs'],targets)+'</p>')
   for limit in claim['limitations']:chunks.append('<p>한계 · '+text(limit)+'</p>')
   for assumption in claim['assumptions']:chunks.append('<p>가정 · '+text(assumption)+'</p>')
   if claim['counterevidence_refs']:chunks.append('<p>반론 근거 · '+_card_references(claim['counterevidence_refs'],targets)+'</p>')
  for alternative in judgment['alternatives']:chunks.append('<p>경쟁 설명 · '+text(alternative)+'</p>')
  chunks.append('</article>')
 times=cards['timeline'];clocks=list(dict.fromkeys(t['clock'] for t in times if t['stamp'] is not None))
 for clock in clocks:
  rows=sorted([t for t in times if t['clock']==clock and t['stamp'] is not None],key=lambda t:t['stamp'])
  lo=rows[0]['stamp'];hi=rows[-1]['stamp'];span=hi-lo or 1
  chunks.append('<section class="clock-cards"><h3>'+text(clock)+'</h3><div class="numeric-axis" role="img" aria-label="명시된 관측 시각의 상대 위치">')
  for row in rows:chunks.append('<a class="axis-mark" href="#'+bookmark_id('card-'+row['id'])+'" style="left:'+str(2+96*(row['stamp']-lo)/span)+'%" title="'+text(row['display_time']+' · '+row['title'])+'">●</a>')
  chunks.append('</div><p class="note">'+text(rows[0]['display_time'])+' — '+text(rows[-1]['display_time'])+'</p>')
  chunks.extend(_time_card(row,targets) for row in rows);chunks.append('</section>')
 off_axis=[row for row in times if row['stamp'] is None]
 if off_axis:chunks.append('<section><h3>추정·시각 미확정·재검토 사항</h3>'+''.join(_time_card(row,targets) for row in off_axis)+'</section>')
 chunks.append('</details>')
 return ''.join(chunks)


def _time_card(row,targets):
 model=row['temporal'];limits=[*row['limitations'],*model.get('assumptions',[])]
 declared=[]
 if model.get('observed_or_inferred')=='inferred':
  for candidate in model.get('candidates',[]) or [model]:
   if candidate.get('earliest') or candidate.get('latest'):
    declared.append('선언된 추정 구간 · '+str(candidate.get('earliest') or '하한 미기재')+' ~ '+str(candidate.get('latest') or '상한 미기재'))
 for field,label in (('confidence_basis','시각 판단 근거'),('timezone_basis','시간대 근거')):
  if model.get(field):declared.append(label+' · '+str(model[field]))
 qualification=''.join('<p>'+text(value)+'</p>' for value in declared)
 return '<article class="time-card" id="'+bookmark_id('card-'+row['id'])+'"><h4>'+text(row['display_time'])+'</h4><p class="note">'+text({'record':'기록 시각','event':'사건 시각','file_metadata':'파일 메타데이터 시각'}.get(row['time_kind'],'시각 종류 미확정'))+'</p><p>'+text(row['title'])+'</p>'+qualification+'<p>'+_card_references(row['source_refs'],targets)+'</p>'+''.join('<p>한계·가정 · '+text(limit)+'</p>' for limit in limits)+('<p>시간 연결 재검토 필요</p>' if row['freshness']=='recheck' else '')+'</article>'


def report_status(v):
 return {'template':'미기입 표준양식','synthetic':'가상자료 예시','live':'판단 기준시점의 보고서'}[v['data_mode']]


def _presentation_scope(page):
 declared=page.get('presentation_view')
 if declared in ('body','technical','audit'):return declared
 if page.get('section')=='appendix':
  return 'audit' if page['id'].startswith('appendix-') else 'technical'
 return 'body'


def _audit_page(page):
 return _presentation_scope(page)=='audit'


def _citation_navigation(markup,targets):
 """Address each citation occurrence and link back from its exact endnote."""
 references={bookmark_id(value):value for value in targets if re.fullmatch(r'\[\d+\]',value)}
 uses={key:[] for key in references}
 def occurrence(match):
  dest=match.group(1)
  if dest not in references:return match.group(0)
  ident='cite-'+dest+'-'+str(len(uses[dest])+1);uses[dest].append(ident)
  return '<a class="citation" id="'+ident+'" href="#'+dest+'">'+match.group(2)+'</a>'
 markup=re.sub(r'<a href="#([^"]+)">(.*?)</a>',occurrence,markup)
 for dest,locations in uses.items():
  if not locations:continue
  back='<nav class="citation-returns" aria-label="'+text(references[dest])+' 인용 위치로 돌아가기">'+''.join(
   '<a href="#'+where+'">본문 인용 '+str(i)+'로 돌아가기 ↑</a>' for i,where in enumerate(locations,1))+'</nav>'
  markup=re.sub(r'(<h[23] id="'+dest+r'">.*?</h[23]>)',lambda m:m.group(0)+back,markup,count=1)
 # Only the exact citation sequence is folded. Evidence selection, order,
 # qualifications and all links remain unchanged in the document and in print.
 sequence=r'((?:<a class="citation"[^>]*>\[\d+\]</a>(?:, | · | )?){5,})'
 clusters=[0]
 def cluster(match):
  clusters[0]+=1;ident='citation-set-'+str(clusters[0])
  links=list(re.finditer(r'<a class="citation"[^>]*>\[\d+\]</a>',match.group(0)))
  cutoff=links[1].end();count=len(links)-2
  visible=match.group(0)[:cutoff];remaining=match.group(0)[cutoff:]
  return '<span class="citation-group"><span class="citation-representatives">'+visible+'</span> <input type="checkbox" class="citation-choice" id="'+ident+'"><label for="'+ident+'">추가 근거 '+str(count)+'건 펼치기</label><span class="citation-list">'+remaining+'</span></span>'
 return re.sub(sequence,cluster,markup)


def _snapshot_status(status):
 """Display supplied status facts without inferring forensic integrity."""
 labels={'investigation':'조사','semantic':'의미 검토','layout':'문서 배치','integrity':'보고 묶음 무결성'}
 rows=[]
 for key,label in labels.items():
  value=status.get(key)
  if value is None:continue
  if isinstance(value,dict):
   subject=value.get('subject')
   display=value.get('status','미확인')
   if key=='integrity':label=subject or label
  else:display=value
  rows.append(label+' · '+str(display))
 if status.get('timestamp'):rows.append('상태 기록 시각 · '+str(status['timestamp']))
 return '<p class="note">생성 시점 검토 상태: '+text(' / '.join(rows))+'</p>' if rows else ''


def render_html(v):
 audience=v.get('template_contract')=='forsic-integrated-audience-1'
 targets={x for p in v['pages'] for b in p['blocks'] for x in [*b.get('row_ids',[]),*([b['id']] if b.get('id') else [])]}
 nav='<nav class="section-nav" aria-label="보고서 섹션">'+''.join('<a data-presentation-scope="'+_presentation_scope(p)+'" href="#'+bookmark_id(p['id'])+'">'+text(p['title'])+'</a>' for p in v['pages'])+'</nav>'
 controls='<input class="view-choice" type="radio" name="report-view" id="body-view"><input class="view-choice" type="radio" name="report-view" id="reader-view"><input class="view-choice" type="radio" name="report-view" id="audit-view" checked><div class="view-controls" role="group" aria-label="인쇄 범위"><strong>인쇄 범위</strong><label for="body-view">본문</label><label for="reader-view">본문 + 기술 근거</label><label for="audit-view">전체 · 부록 포함</label><p>웹에서는 항상 전체 보고서 표시 · 인쇄만 선택한 범위 적용</p></div>'
 if audience:
  controls=controls.replace('본문 + 기술 근거','본문 + 실무자 별첨')

 publication={'internal_partial_snapshot':'초안 · 생성 시점의 검토 상태','finalized':'최종본으로 선택된 보고서 스냅샷'}.get(v.get('publication_status'),'발행 상태 미확인 · 저장된 보고서 스냅샷')
 status='<aside class="document-status" aria-label="문서 발행 상태"><strong>'+text(publication)+'</strong><details class="snapshot-provenance"><summary>검토 상태와 보고서 버전</summary>'+_snapshot_status(v.get('snapshot_status',{}))+'<p>이 문서는 생성 시점의 상태를 보존합니다. 이후 최종본 선택 상태는 사건 화면에서 확인합니다.</p><p>판단 스냅샷 '+text(v.get('snapshot_revision','미기재'))+' · '+text(report_status(v))+'</p></details></aside>'
 body=[];anchors=set();cards_added=False
 for i,p in enumerate(v['pages'],1):
  if p.get('section')=='appendix' and not cards_added:
   body.append('<section class="reader-card-section" data-presentation-scope="'+('audit' if audience else 'technical')+'">'+render_reader_cards(v.get('reader_cards'),targets)+'</section>');cards_added=True
  kicker=p.get('kicker') or ('통합보고서 / 실무 상세 부록' if p.get('section')=='appendix' else '통합보고서')
  guide=('<div class="appendix-guide"><strong>'+('감사 기록' if _audit_page(p) else '실무 기술 근거')+'</strong><span>상세 내용·범위</span><span>원문·출처 대조</span><span>추적·검증 정보</span></div>' if p.get('section')=='appendix' else '')
  body.append('<article class="sheet'+(' appendix' if p.get('section')=='appendix' else '')+(' audit-only' if _audit_page(p) else '')+'" data-presentation-scope="'+_presentation_scope(p)+'" id="'+bookmark_id(p['id'])+'" aria-label="'+text(p['title'])+'"><header class="running">FORSIC | 침해사고 조사 통합보고서 | '+text(report_status(v))+'</header><div class="page-body"><p class="kicker">'+text(kicker)+'</p><h1>'+text(p['title'])+'</h1>'+guide+''.join(hblock({**b,'practitioner':True} if p.get('section')=='appendix' and b['kind']=='table' else b,targets,anchors) for b in [*p['blocks'],*(v.get('reader_notices',[]) if i==1 else [])])+'</div><footer class="sheet-foot">'+text(v['security']+' | '+report_status(v))+' · '+str(i)+'</footer></article>')
 if not cards_added:body.append('<section class="reader-card-section" data-presentation-scope="'+('audit' if audience else 'technical')+'">'+render_reader_cards(v.get('reader_cards'),targets)+'</section>')
 content=_citation_navigation(nav+''.join(body),targets)
 return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src \'none\'; base-uri \'none\'; form-action \'none\'"><title>'+text(v['title'])+'</title><style>'+CSS+'</style></head><body'+' data-typography="audience"'+'>'+controls+'<main>'+status+content+'</main></body></html>'


def _fill_paragraph(p,value,targets=()):
 """Replace the editable text, retaining the source paragraph/run formatting."""
 runs=list(p.runs)
 props=next((r._r.rPr for r in runs if r.text and r._r.rPr is not None),None)
 if props is None:props=next((r._r.rPr for r in runs if r._r.rPr is not None),None)
 props=deepcopy(props)
 for child in list(p._p):
  if child.tag!=qn('w:pPr'):p._p.remove(child)
 pattern='('+'|'.join(re.escape(x) for x in sorted(targets,key=len,reverse=True))+')' if targets else None
 for part in re.split(pattern,str(value)) if pattern else [str(value)]:
  run=p.add_run(part)
  if props is not None:run._r.insert(0,deepcopy(props))
  if part in targets:
   link=OxmlElement('w:hyperlink');link.set(qn('w:anchor'),bookmark_id(part));p._p.remove(run._r);link.append(run._r);p._p.append(link)
 return p


def _bookmark(p,key,seen):
 if key in seen:return
 seen.add(key);ident=str(len(seen))
 start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),ident);start.set(qn('w:name'),bookmark_id(key))
 end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),ident)
 p._p.insert(1 if p._p.pPr is not None else 0,start);p._p.append(end)


def _cell_text(cell,value,targets):
 ps=list(cell.paragraphs)
 for p in ps[1:]:cell._tc.remove(p._p)
 return _fill_paragraph(ps[0],value,targets)


def _table(doc,prototype,b,targets,seen,style):
 b=deepcopy(b)
 if b.get('event_kinds'):
  for row,kind in zip(b['rows'],b['event_kinds']):row[1]=EVENT_KINDS[kind]+'\n'+row[1]
 headers=b['headers'];n=len(headers)
 if not n or any(len(row)!=n for row in b['rows']):raise ValueError('Table column mismatch')
 element=deepcopy(prototype);table=Table(element,doc._body)
 samples=[deepcopy(row._tr) for row in table.rows]
 old_n=len(table.columns)
 for row in list(table.rows):element.remove(row._tr)
 widths=b.get('widths') or [100/n]*n
 if len(widths)!=n or any(w<=0 for w in widths):raise ValueError('Invalid table widths')
 fixed=bool(b.get('variant')) and old_n==n
 if not fixed:
  grid=element.tblGrid
  for child in list(grid):grid.remove(child)
  for width in widths:
   col=OxmlElement('w:gridCol');col.set(qn('w:w'),str(round(9752*width/sum(widths))));grid.append(col)
 line_estimates=[max(sum(max(1,math.ceil(len(line)/max(1,(style['table']['width_mm']*w/sum(widths)-4)*2.835/typography.resolved_roles(style)['body']['print_pt']))) for line in str(v).split('\n')) for v,w in zip(values,widths)) for values in [headers,*b['rows']]]
 keep_together=b.get('keep_together') and sum(line_estimates)<35
 for ri,values in enumerate([headers,*b['rows']]):
  source=samples[0] if ri==0 else samples[min(1+(ri-1)%2,len(samples)-1)]
  row=deepcopy(source)
  cells=list(row.tc_lst)
  while len(cells)<n:row.append(deepcopy(cells[-1]));cells=list(row.tc_lst)
  for cell in cells[n:]:row.remove(cell)
  element.append(row)
  trpr=row.get_or_add_trPr()
  for flag in list(trpr):
   if flag.tag in (qn('w:tblHeader'),qn('w:cantSplit')):trpr.remove(flag)
  if ri==0:trpr.append(OxmlElement('w:tblHeader'))
  # Preserve ordinary row integrity, but allow long exact original text to flow.
  longest=line_estimates[ri]
  if ri==0 or longest<30:trpr.append(OxmlElement('w:cantSplit'))
  for ci,(cell,value,width) in enumerate(zip(table.rows[-1].cells,values,widths)):
   pr=cell._tc.get_or_add_tcPr()
   if not fixed:pr.find(qn('w:tcW')).set(qn('w:w'),str(round(9752*width/sum(widths))))
   for shade in list(pr.findall(qn('w:shd'))):pr.remove(shade)
   fill=style['table']['header_fill'] if ri==0 else style['table']['stripe_fill'] if ri%2==0 else None
   if fill:
    sh=OxmlElement('w:shd');sh.set(qn('w:fill'),fill);pr.append(sh)
   para=_cell_text(cell,value,targets if ri else ())
   typography.register(doc,para,'table_header' if ri==0 else 'body',literal=bool(b.get('literal')))
   if ri and b.get('authored_hierarchy'):
    _hierarchy(doc,para,(b.get('cell_details') or [['']*n for _ in b['rows']])[ri-1][ci],targets)
   if ri and b.get('region')=='metadata':typography.register(doc,para,'metadata')
   if keep_together:para.paragraph_format.keep_with_next=ri<len(b['rows'])
   if ri and ci==0 and ri<=len(b.get('row_ids',[])):_bookmark(para,b['row_ids'][ri-1],seen)
 doc.element.body.insert(-1,element)
 return table


def _render_flow_docx(v,path,*,template_bytes=None,style_profile=None):
 # Missing or corrupt template is an error, never a silent generic fallback.
 original=TEMPLATE_PATH.read_bytes() if template_bytes is None else template_bytes
 style=deepcopy(STYLE if style_profile is None else style_profile)
 doc=Document(io.BytesIO(original));body=doc.element.body
 audience=v.get('template_contract')=='forsic-integrated-audience-1'
 doc._forsic_type_roles=[]
 if audience and not any(p.get('presentation_view')=='audit' for p in v['pages']):
  v=_compact_print_references(v)
 components=_components(body,style)
 for child in list(body):
  if child.tag!=qn('w:sectPr'):body.remove(child)
 targets={x for p in v['pages'] for b in p['blocks'] for x in [*b.get('row_ids',[]),*([b['id']] if b.get('id') else [])]};seen=set()
 def paragraph(role,value,links=True):
  el=deepcopy(components[role]);body.insert(-1,el)
  para=_fill_paragraph(Paragraph(el,doc._body),value,targets if links else ())
  typography.register(doc,para,'body' if role=='paragraph' else role)
  return para
 def spacer():body.insert(-1,deepcopy(components['spacer']))
 # Only the template's content/status fields change; placement and PAGE field stay.
 for section in doc.sections:
  for p in section.header.paragraphs:
   if 'FORSIC' in p.text:_fill_paragraph(p,'FORSIC  |  침해사고 조사 통합보고서  |  '+report_status(v))
  for p in section.footer.paragraphs:
   for node in p._p.xpath('.//w:t'):
    if '미기입 표준양식' in (node.text or ''):node.text=v['security']+'  |  '+report_status(v)+'  ·  '
 cp=doc.core_properties;cp.title=v['title'];cp.subject='FORSIC snapshot:'+v['snapshot_revision']
 cp.author='';cp.last_modified_by='';cp.comments=''
 for i,page in enumerate(v['pages']):
  continuing=i>0 and page.get('section')=='appendix' and v['pages'][i-1].get('section')=='appendix'
  if not continuing:
   p=paragraph('kicker',page.get('kicker') or ('통합보고서 / 실무 상세 부록' if page.get('section')=='appendix' else '통합보고서'),False)
   p.paragraph_format.page_break_before=bool(i);p.paragraph_format.keep_with_next=True
   p=paragraph('title',page['title'],False)
  else:
   p=paragraph('heading',page['title'],False)
  _bookmark(p,page['id'],seen)
  for block in page['blocks']:
   kind=block['kind']
   if kind in ('paragraph','note'):
    p=paragraph(kind,block['text'])
    if block.get('authored_hierarchy'):_hierarchy(doc,p,block.get('detail',''),targets)
    if block.get('id'):_bookmark(p,block['id'],seen)
   elif kind=='heading':
    p=paragraph('subheading' if block.get('level')==2 else 'heading',block['text'])
    if block.get('id'):_bookmark(p,block['id'],seen)
   elif kind=='table':
    if block.get('id'):_bookmark(paragraph('note',block['id'],False),block['id'],seen)
    if (audience and block.get('region')=='timeline' and block.get('authored_hierarchy')
        and style.get('readability',{}).get('timeline_presentation')=='event_rail_html_and_word'):
     _word_timeline(doc,block,targets,seen,style,paragraph)
     continue
    if audience and block.get('row_assessments'):
     # Keep each finding's heading, scale and answer in one ordinary card.
     # A separate gauge row can otherwise strand the scale on the prior page.
     for row,a in zip(block['rows'],block['row_assessments']):
      card=dict(kind='table',headers=[row[0]],rows=[['\n'.join(x for x in row[1:] if x)]],keep_together=True)
      table=_table(doc,components['summary'],card,targets,seen,style)
      _word_assessment(table.cell(1,0),a,style,doc=doc)
      spacer()
     continue
    variant=block.get('variant');component=variant if variant in components else 'flow' if len(block['headers'])==3 else 'summary'
    table=_table(doc,components[component],block,() if block.get('literal') else targets,seen,style)
    for row,a in zip(list(table.rows)[1:],block.get('row_assessments',[])):
     if a is None:continue
     gauge_row=table.add_row();cell=gauge_row.cells[0].merge(gauge_row.cells[-1]);cell.text=''
     _word_assessment(cell,a,style,doc=doc);gauge_row._tr.getparent().remove(gauge_row._tr);row._tr.addprevious(gauge_row._tr)
    spacer()
   elif kind=='callout':
    el=deepcopy(components['callout']);body.insert(-1,el);table=Table(el,doc._body)
    ps=table.cell(0,0).paragraphs
    _fill_paragraph(ps[0],block['label']);_fill_paragraph(ps[1],block['text'],targets)
    typography.register(doc,ps[0],'label')
    if block.get('executive_summary'):_executive_summary(doc,ps[1],block['text'],targets)
    elif block.get('authored_hierarchy'):_hierarchy(doc,ps[1],block.get('detail',''),targets,'overall' if block.get('region')=='overall_judgment' else 'lead')
    typography.register(doc,ps[0],'label')
    _word_assessment(table.cell(0,0),block.get('assessment'),style,ps[1]._p,doc=doc)
    for p in ps[2:]:p._p.getparent().remove(p._p)
    spacer()
   else:raise ValueError('Unsupported block '+str(kind))
 typography.apply(doc,style)
 generated=io.BytesIO();doc.save(generated)
 # python-docx writes only supported parts back through its object model. Keep
 # every unrelated original package part byte-identical, including the real
 # template styles/theme/numbering and opaque XML. No external relationships.
 changed={'word/document.xml','word/header1.xml','word/footer1.xml','docProps/core.xml'}
 with zipfile.ZipFile(io.BytesIO(original)) as source,zipfile.ZipFile(generated) as output,zipfile.ZipFile(path,'w') as final:
  if set(source.namelist())!=set(output.namelist()):raise ValueError('Unexpected template package parts')
  for item in source.infolist():
   payload=output.read(item.filename) if item.filename in changed else source.read(item.filename)
   if item.filename.endswith('.rels') and b'TargetMode="External"' in payload:raise ValueError('External relationship')
   final.writestr(item,payload)


def _compact_print_references(view):
 """Compact contiguous endnote numbers without dropping any reference.

 Body/practitioner exports omit the audit endnotes. Their complete mapping
 remains in the paired HTML audit view; literal source quotes are untouched.
 Full audit exports retain individual clickable labels.
 """
 result=deepcopy(view)
 def compact(value,body=False):
  def replace(match):
   values=[int(x) for x in re.findall(r'\d+',match.group(0))]
   if body:
    return '['+', '.join(str(x) for x in values[:2])+'] 외 '+str(len(values)-2)+'건'
   values=sorted(set(values))
   parts=[];start=last=values[0]
   for number in values[1:]:
    if number==last+1:last=number;continue
    parts.append(str(start) if start==last else str(start)+'–'+str(last));start=last=number
   parts.append(str(start) if start==last else str(start)+'–'+str(last))
   return '['+', '.join(parts)+']'
  return re.sub(r'\[\d+\](?:, \[\d+\]){2,}',replace,value)
 for page in result['pages']:
  short=page.get('presentation_view')=='body'
  for block in page['blocks']:
   if block.get('literal'):continue
   for key in ('text','detail'):
    if isinstance(block.get(key),str):block[key]=compact(block[key],short)
   for key in ('rows','cell_details'):
    if key in block:block[key]=[[compact(str(value),short) for value in row] for row in block[key]]
 return result



def _components(body,style):
 result={}
 for name,index in style['components'].items():
  element=body[index]
  if element.tag==qn('w:sdt'):element=element.find(qn('w:sdtContent'))[0]
  result[name]=deepcopy(element)
 return result


def _word_timeline(doc,block,targets,seen,style,paragraph):
 """An event rail built from paragraphs, not a four-column evidence table.

 Text, saved order and sentence citations are untouched. Long technical notes
 may flow across pages; each date and event headline stays with its explanation.
 """
 paragraph('note','기록별 시간축 · 항목 간 간격은 실제 경과시간과 무관')
 for index,row in enumerate(block['rows']):
  detail=(block.get('cell_details') or [['']*len(r) for r in block['rows']])[index]
  kind=(block.get('event_kinds') or ['unknown']*len(block['rows']))[index]
  color={'response':'347367','acquisition':'725E91'}.get(kind,style['visual_theme']['accent'])
  def part(value,role,keep=False):
   p=paragraph('paragraph',value)
   typography.register(doc,p,role)
   p.paragraph_format.left_indent=Pt(16)
   p.paragraph_format.keep_with_next=keep
   p.paragraph_format.widow_control=True
   borders=OxmlElement('w:pBdr');left=OxmlElement('w:left')
   for key,val in {'val':'single','sz':'12','space':'10','color':color}.items():left.set(qn('w:'+key),val)
   borders.append(left);p._p.get_or_add_pPr().append(borders)
   return p
  head=part(str(index+1).zfill(2)+'  ·  '+row[0]+'  /  '+EVENT_KINDS[kind],'timeline_time',True)
  if index<len(block.get('row_ids',[])):_bookmark(head,block['row_ids'][index],seen)
  if detail[0]:part(detail[0],'detail',True)
  event_lines=str(row[1]).split('\n')
  part(event_lines[0],'timeline_title',True)
  if len(event_lines)>1:part('\n'.join(event_lines[1:]),'body',True)
  part(row[2],'body')
  for value in detail[1:3]:
   if value:part(value,'detail')
  if len(row)>3 and row[3]:part('근거 · '+row[3],'citation')
  paragraph('note','')


def _word_assessment(cell,a,style,anchor=None,*,doc):
 """Render the same authored scale in retained Word cells, without scoring."""
 if a is None:return
 anchor=anchor if anchor is not None else cell.paragraphs[0]._p
 level=a['level'];labels=a['labels']
 label=(str(level)+'/5 · '+labels[level-1]) if level is not None else '평가 보류'
 heading=cell.add_paragraph(label);heading.paragraph_format.keep_with_next=True
 typography.register(doc,heading,'assessment')
 steps=list(enumerate(labels,1))
 if style.get('readability',{}).get('assessment_cells')==6:steps.insert(0,(None,'평가 보류'))
 gauge=cell.add_table(rows=1,cols=len(steps))
 for slot,(i,name) in zip(gauge.rows[0].cells,steps):
  p=slot.paragraphs[0];p.paragraph_format.keep_with_next=True
  marker='\n▲ 현재 판단' if i==level else ''
  p.add_run(('—' if i is None else str(i))+' · '+name+marker)
  from docx.enum.text import WD_ALIGN_PARAGRAPH
  p.alignment=WD_ALIGN_PARAGRAPH.CENTER
  typography.register(doc,p,'gauge_selected' if i==level else 'gauge')
  theme=style['visual_theme'];fill=theme['muted'] if i is None else theme['risk_steps'][i-1] if a.get('tone')=='risk' else theme['ink']
  pr=slot._tc.get_or_add_tcPr();shade=OxmlElement('w:shd');shade.set(qn('w:fill'),fill if i==level else theme['inset']);pr.append(shade)
 parts=[heading._p,gauge._tbl]
 if a.get('time_range'):
  window=a['time_range']
  p=cell.add_paragraph(_time_window_label(window));parts.append(p._p);typography.register(doc,p,'label')
  p=cell.add_paragraph(_time_window_text(window,readable=True));parts.append(p._p)
  typography.register(doc,p,'time')
 if a.get('rationale'):
  rationale=cell.add_paragraph(a['rationale']);parts.append(rationale._p)
  typography.register(doc,rationale,'detail')
 for element in parts:
  element.getparent().remove(element);anchor.addprevious(element)


def _existing_table(doc,element,block,targets,seen,style):
 """Edit retained cells and clone only additional repeated rows in place."""
 table=Table(element,doc._body);n=len(block['headers'])
 if len(table.columns)!=n or any(len(row)!=n for row in block['rows']):
  raise ValueError('Retained template table column mismatch')
 values=deepcopy([block['headers'],*block['rows']])
 if block.get('event_kinds'):
  for row,kind in zip(values[1:],block['event_kinds']):row[1]=EVENT_KINDS[kind]+'\n'+row[1]
 sample=deepcopy(table.rows[-1]._tr)
 while len(table.rows)<len(values):element.append(deepcopy(sample))
 while len(table.rows)>len(values):element.remove(table.rows[-1]._tr)
 widths=[int(col.get(qn('w:w'))) for col in element.tblGrid]
 for ri,(row,texts) in enumerate(zip(table.rows,values)):
  trpr=row._tr.get_or_add_trPr()
  for flag in list(trpr):
   if flag.tag in (qn('w:cantSplit'),qn('w:trHeight')):trpr.remove(flag)
  if ri==0 and trpr.find(qn('w:tblHeader')) is None:trpr.append(OxmlElement('w:tblHeader'))
  # A long row must continue onto the following printed page, never clip or
  # shrink the font. Ordinary rows keep their original page integrity.
  measured=[str(value)+('\n'+block['cell_details'][ri-1][ci] if ri and block.get('cell_details') and block['cell_details'][ri-1][ci] else '') for ci,value in enumerate(texts)]
  lines=max(sum(max(1,math.ceil(len(line)/max(1,(width/20-10)/typography.resolved_roles(style)['body']['print_pt'])))
                for line in value.split('\n')) for width,value in zip(widths,measured))
  if ri==0 or lines<30:trpr.append(OxmlElement('w:cantSplit'))
  for ci,(cell,value) in enumerate(zip(row.cells,texts)):
   para=_cell_text(cell,value,() if ri==0 else targets)
   if ri==0 and style.get('visual_theme'):
    pr=cell._tc.get_or_add_tcPr();shade=pr.find(qn('w:shd'))
    if shade is None:shade=OxmlElement('w:shd');pr.append(shade)
    shade.set(qn('w:fill'),style['table']['header_fill'])
   typography.register(doc,para,'table_header' if ri==0 else 'metadata' if block.get('region')=='metadata' else 'body',literal=bool(block.get('literal')))
   if ri and block.get('authored_hierarchy'):
    _hierarchy(doc,para,(block.get('cell_details') or [['']*n for _ in block['rows']])[ri-1][ci],targets)
   if ri and block.get('region')=='metadata':typography.register(doc,para,'metadata')
   para.paragraph_format.keep_with_next=False if ri else True
   if ri and ci==0 and ri<=len(block.get('row_ids',[])):_bookmark(para,block['row_ids'][ri-1],seen)
 if block.get('row_assessments'):
  original_rows=list(table.rows)[1:]
  for row,a in zip(original_rows,block['row_assessments']):
   if a is None:continue
   header=table.add_row();cell=header.cells[0].merge(header.cells[-1])
   cell.text='';_word_assessment(cell,a,style,doc=doc)
   header._tr.getparent().remove(header._tr);row._tr.addprevious(header._tr)
 return table


def _render_retained_docx(v,path,original,style):
 doc=Document(io.BytesIO(original));body=doc.element.body
 doc._forsic_type_roles=[]
 for para in doc.paragraphs:
  role={'Title':'title','Heading 1':'heading','Heading 2':'subheading'}.get(para.style.name)
  if role:typography.register(doc,para,role)
 components=_components(body,style)
 controls={}
 for control in body.findall(qn('w:sdt')):
  tag=control.find(qn('w:sdtPr')+'/'+qn('w:tag'))
  if tag is not None:controls[tag.get(qn('w:val'))]=control.find(qn('w:sdtContent'))
 def region(name):
  if 'report.'+name not in controls:raise ValueError('Missing retained template region '+name)
  return controls['report.'+name]
 targets={x for page in v['pages'] for block in page['blocks'] for x in
          [*block.get('row_ids',[]),*([block['id']] if block.get('id') else [])]};seen=set()
 # Static body titles and section paragraphs are kept in their original order.
 body_pages=[p for p in v['pages'] if p.get('section')!='appendix']
 if [p['id'] for p in body_pages]!=['I01','I02','I03']:raise ValueError('Invalid v2 body pages')
 for page,index in zip(body_pages,(1,17,30)):
  para=_fill_paragraph(Paragraph(body[index],doc._body),page['title']);_bookmark(para,page['id'],seen)
  typography.register(doc,para,'title')
  if style.get('visual_theme'):
   for run in para.runs:run.font.color.rgb=RGBColor.from_string(style['visual_theme']['ink'])
   pr=para._p.get_or_add_pPr();borders=pr.find(qn('w:pBdr'))
   if borders is None:borders=OxmlElement('w:pBdr');pr.append(borders)
   bottom=borders.find(qn('w:bottom'))
   if bottom is None:bottom=OxmlElement('w:bottom');borders.append(bottom)
   for key,val in {'val':'single','sz':'8','color':style['visual_theme']['accent']}.items():bottom.set(qn('w:'+key),val)
 for page,index in zip(body_pages,(0,16,29)):
  para=_fill_paragraph(Paragraph(body[index],doc._body),page['kicker'])
  typography.register(doc,para,'kicker')
  if style.get('visual_theme'):
   for run in para.runs:run.font.color.rgb=RGBColor.from_string(style['visual_theme']['accent'])
 for page in body_pages:
  for block in page['blocks']:
   if not block.get('region'):continue
   content=region(block['region']);element=content[0];kind=block['kind']
   if kind=='table':_existing_table(doc,element,block,targets,seen,style)
   elif kind=='callout':
    ps=Table(element,doc._body).cell(0,0).paragraphs
    _fill_paragraph(ps[0],block['label']);_fill_paragraph(ps[1],block['text'],targets)
    typography.register(doc,ps[0],'label')
    if block.get('authored_hierarchy'):_hierarchy(doc,ps[1],block.get('detail',''),targets,'overall' if block.get('region')=='overall_judgment' else 'lead')
    _word_assessment(Table(element,doc._body).cell(0,0),block.get('assessment'),style,ps[1]._p,doc=doc)
   elif kind in ('paragraph','note'):
    para=_fill_paragraph(Paragraph(element,doc._body),block['text'],targets)
    typography.register(doc,para,'body' if kind=='paragraph' else kind)
    if block.get('authored_hierarchy'):_hierarchy(doc,para,block.get('detail',''),targets)
   else:raise ValueError('Unsupported retained body region')
 # Appendix regions repeat complete, lossless technical blocks at the source
 # appendix positions. Existing headings and all three main pages stay intact.
 def append_blocks(content,blocks):
  for child in list(content):content.remove(child)
  for block in blocks:
   kind=block['kind']
   if kind in ('paragraph','note','heading'):
    role=('subheading' if block.get('level')==2 else 'heading') if kind=='heading' else kind
    element=deepcopy(components[role]);content.append(element)
    para=_fill_paragraph(Paragraph(element,doc._body),block['text'],() if block.get('literal') else targets)
    typography.register(doc,para,'body' if role=='paragraph' else role,literal=bool(block.get('literal')))
    if block.get('id'):_bookmark(para,block['id'],seen)
    if kind=='heading':_word_appendix_heading(para,style)
   elif kind=='table':
    if block.get('id'):
     caption=deepcopy(components['note']);content.append(caption)
     para=_fill_paragraph(Paragraph(caption,doc._body),block['id']);para.paragraph_format.keep_with_next=True
     _bookmark(para,block['id'],seen)
    variant=block.get('variant');prototype=components.get(variant,components['flow' if len(block['headers'])==3 else 'summary'])
    table=_table(doc,prototype,block,() if block.get('literal') else targets,seen,style)
    content.append(table._tbl)
   elif kind=='callout':
    element=deepcopy(components['callout']);content.append(element);ps=Table(element,doc._body).cell(0,0).paragraphs
    _fill_paragraph(ps[0],block['label']);_fill_paragraph(ps[1],block['text'],targets)
    typography.register(doc,ps[0],'label')
    if block.get('authored_hierarchy'):_hierarchy(doc,ps[1],block.get('detail',''),targets,'overall' if block.get('region')=='overall_judgment' else 'lead')
   else:raise ValueError('Unsupported appendix block '+kind)
 appendix_pages={p['id']:p for p in v['pages'] if p.get('section')=='appendix'}
 # These headings are source nodes, found by their fixed role before text changes.
 headings={}
 for element in body:
  if element.tag==qn('w:p'):
   para=Paragraph(element,doc._body)
   for letter in 'ABCDEF':
    if para.text.startswith('부록 '+letter+'.'):headings['appendix-'+letter]=para
 for letter in 'ABCDEFG':
  key='appendix-'+letter;page=appendix_pages.pop(key,None);content=region(key)
  if letter!='G':
   para=headings[key]
   _fill_paragraph(para,page['title'] if page else '부록 '+letter)
   _bookmark(para,key,seen)
   _word_appendix_heading(para,style)
   typography.register(doc,para,'heading')
  blocks=deepcopy(page['blocks']) if page else []
  if letter=='G':
   blocks.insert(0,dict(kind='heading',text=page['title'] if page else '부록 G',id=key))
  append_blocks(content,blocks)
 remaining=[]
 for page in appendix_pages.values():
  remaining.append(dict(kind='heading',text=page['title'],id=page['id']))
  remaining.extend(page['blocks'])
 append_blocks(region('references'),remaining)
 for section in doc.sections:
  for para in section.header.paragraphs:
   if 'FORSIC' in para.text:_fill_paragraph(para,'FORSIC  |  침해사고 조사 통합보고서  |  '+report_status(v))
  for para in section.footer.paragraphs:
   for node in para._p.xpath('.//w:t'):
    if '미기입 표준양식' in (node.text or ''):node.text=v['security']+'  |  '+report_status(v)+'  ·  '
 cp=doc.core_properties;cp.title=v['title'];cp.subject='FORSIC snapshot:'+v['snapshot_revision'];cp.author='';cp.last_modified_by='';cp.comments=''
 typography.apply(doc,style)
 _save_package(doc,path,original)


def _word_appendix_heading(para,style):
 theme=style.get('visual_theme')
 if not theme:return
 for run in para.runs:run.font.color.rgb=RGBColor.from_string(theme['ink'])
 para.paragraph_format.keep_with_next=True
 pr=para._p.get_or_add_pPr();borders=pr.find(qn('w:pBdr'))
 if borders is None:borders=OxmlElement('w:pBdr');pr.append(borders)
 left=OxmlElement('w:left')
 for key,value in {'val':'single','sz':'12','space':'6','color':theme['accent']}.items():left.set(qn('w:'+key),value)
 borders.append(left)


def _save_package(doc,path,original):
 generated=io.BytesIO();doc.save(generated)
 changed={'word/document.xml','word/header1.xml','word/footer1.xml','docProps/core.xml'}
 with zipfile.ZipFile(io.BytesIO(original)) as source,zipfile.ZipFile(generated) as output,zipfile.ZipFile(path,'w') as final:
  if set(source.namelist())!=set(output.namelist()):raise ValueError('Unexpected template package parts')
  for item in source.infolist():
   payload=output.read(item.filename) if item.filename in changed else source.read(item.filename)
   if item.filename.endswith('.rels') and b'TargetMode="External"' in payload:raise ValueError('External relationship')
   final.writestr(item,payload)


def render_docx(v,path,*,template_bytes=None,style_profile=None):
 original=TEMPLATE_PATH.read_bytes() if template_bytes is None else template_bytes
 style=deepcopy(STYLE if style_profile is None else style_profile)
 style.setdefault('audience_typography',deepcopy(STYLE['audience_typography']))
 if v.get('template_contract')=='forsic-integrated-v2':
  return _render_retained_docx(v,path,original,style)
 # Content layout contracts retain their geometry; typography uses the same roles.
 return _render_flow_docx(v,path,template_bytes=original,style_profile=style)


def render_pair(view_file,outdir):
 v=json.loads(Path(view_file).read_text(encoding='utf-8'));out=Path(outdir);out.mkdir(parents=True,exist_ok=True)
 stem=Path(view_file).name.removesuffix('.view.json')+'_template'
 hp,dp=out/(stem+'.html'),out/(stem+'.docx')
 if hp.exists() or dp.exists():raise FileExistsError('Create-only outputs: choose a new directory')
 hp.write_text(render_html(v),encoding='utf-8');render_docx(v,dp)
 return hp,dp

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('view');ap.add_argument('--out',required=True);args=ap.parse_args()
 for p in render_pair(args.view,args.out):print(p)
