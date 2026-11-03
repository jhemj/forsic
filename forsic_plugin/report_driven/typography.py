"""Shared report presentation roles; no author text or evidence edits."""
from copy import deepcopy
import re
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.text.paragraph import Paragraph
from docx.text.run import Run


REFERENCE = re.compile(r'\[\d+(?:[ ,–-]+\d+)*\](?:\s*외\s*\d+건)?')


def register(doc, paragraph, role, *, literal=False):
    doc._forsic_type_roles.append((paragraph, role, literal))


def _run_font(run, rule, style):
    run.font.name = rule.get('family', style['font']['family'])
    run.font.size = Pt(rule['print_pt'])
    run.bold = rule['weight'] >= 600
    run.font.color.rgb = RGBColor.from_string(rule.get('color', style['font']['color']))
    props = run._r.get_or_add_rPr()
    # Set East Asian and complex-script fonts too; retained template/theme parts stay intact.
    fonts = props.get_or_add_rFonts()
    for key in ('eastAsia', 'ascii', 'hAnsi', 'cs'):
        fonts.set(qn('w:' + key), rule.get('family', style['font']['family']))
    for key in ('szCs', 'bCs'):
        el = props.find(qn('w:' + key))
        if el is None:
            el = OxmlElement('w:' + key); props.append(el)
        el.set(qn('w:val'), str(round(rule['print_pt'] * 2)) if key == 'szCs' else ('1' if run.bold else '0'))


def _references(paragraph, rule, style):
    # Split text-only runs, preserving existing hyperlink parents and exact characters.
    # Source quotations never enter this pass.
    for node in list(paragraph._p.iter(qn('w:r'))):
        if any(child.tag not in (qn('w:rPr'), qn('w:t'), qn('w:br'), qn('w:tab')) for child in node):
            continue
        if any(br.attrib for br in node.findall(qn('w:br'))):
            continue
        value = Run(node, paragraph).text
        matches = list(REFERENCE.finditer(value))
        if not matches:
            continue
        parent = node.getparent(); offset = parent.index(node); cursor = 0
        pieces = []
        for match in matches:
            if cursor < match.start(): pieces.append((value[cursor:match.start()], False))
            pieces.append((match.group(), True)); cursor = match.end()
        if cursor < len(value): pieces.append((value[cursor:], False))
        for value, citation in pieces:
            clone = deepcopy(node); run = Run(clone, paragraph); run.text = value
            if citation: _run_font(run, rule, style)
            parent.insert(offset, clone); offset += 1
        parent.remove(node)


def apply(doc, style):
    rules = resolved_roles(style)
    roles = {p._p: (role, literal) for p, role, literal in doc._forsic_type_roles}
    for node in doc.element.body.iter(qn('w:p')):
        paragraph = Paragraph(node, doc._body)
        # Retained docGrid is package geometry, not a paragraph line-height rule.
        # Without this, LibreOffice rounds 1.55 body spacing to two grid lines.
        props = node.get_or_add_pPr()
        snap = props.find(qn('w:snapToGrid'))
        if snap is None:
            snap = OxmlElement('w:snapToGrid')
            props.append(snap)
        snap.set(qn('w:val'), '0')
        if not ''.join(node.itertext()).strip() and not list(node.iter(qn('w:fldChar'))):
            # Required empty paragraphs after nested tables and template spacers
            # are spacing, not a full-size body line.
            paragraph.paragraph_format.line_spacing = Pt(2)
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(3)
            continue
        role, literal = roles.get(node, ('body', False))
        rule = rules.get('source', rules[role]) if literal and role != 'table_header' else rules[role]
        # OOXML auto spacing multiplies the font's ascent/descent line box,
        # which is much taller than the em for Noto CJK in LibreOffice.
        # Resolve the same em-based leading used by CSS to points for Word.
        paragraph.paragraph_format.line_spacing = Pt(rule['print_pt'] * rule.get('line_height', 1.45))
        paragraph.paragraph_format.space_after = Pt(rule.get('after_pt', 4))
        paragraph.paragraph_format.space_before = Pt(rule.get('before_pt', 0))
        for child in node.iter(qn('w:r')):
            _run_font(Run(child, paragraph), rule, style)
        if not literal and role not in ('title', 'heading', 'subheading', 'table_header', 'gauge', 'gauge_selected'):
            _references(paragraph, rules['citation'], style)
    return True


def resolved_roles(style):
    """Resolve semantic aliases once for HTML and Word from the same scale."""
    roles = style['audience_typography']
    return {name: deepcopy(roles[rule['alias']] if 'alias' in rule else rule)
            for name, rule in roles.items()}


# Ordered from general content to semantic roles; nested authored wrappers inherit
# their owner's role rather than resetting a detail, time or metadata value.
SELECTORS = {
    'body': 'body[data-typography="audience"],.sheet p,.sheet td,.sheet dd,.sheet li',
    'title': '.sheet h1', 'heading': '.sheet h2',
    'subheading': '.sheet h3,.finding-card>h3',
    'detail': '.sheet p.assessment-rationale,.authored-detail,.metadata-detail,.assessment-rationale,.snapshot-provenance,.appendix .prose-record dd,.source-summary dd,.source-field>dd',
    'label': '.field-label,.event-kind,.callout>strong,.time-range>span:first-child,.sheet dt,.sheet summary,.view-controls label,.document-status>strong,.appendix .prose-records>p',
    'metadata': '.metadata .authored-lead',
    'table_header': '.sheet th',
    'assessment': '.assessment-title b',
    'gauge': '.assessment-title,.assessment-steps li,.step-label,.step-bar',
    'marker': '.step-marker,.timeline-dot,.finding-number,.assessment-steps li.selected .step-label',
    'gauge_selected': '.assessment-steps li.selected .step-bar',
    'time': '.time-range b',
    'timeline_time': '.timeline-time .authored-lead',
    'timeline_title': '.sheet p.timeline-event-title',
    'citation': '.sheet p.finding-citations,.sheet a.citation,.citation-group,.citation-group a,.citation-group label,.finding-citations,.citation-returns,.citation-returns a,.note,.sheet p.note,.running,.sheet-foot,.appendix-guide,.sheet caption,.view-controls p',
    'kicker': '.sheet p.kicker',
    'source': '.sheet .source-field>dd p,.sheet .source-field>dd code,.sheet .source-field>dd pre,.sheet .source-excerpt p,.sheet .source-field>dd,.sheet .source-excerpt,.sheet .source-excerpt code,.sheet .source-excerpt pre',
}


def css(style):
    if not style.get('audience_typography'):
        return ''
    rules = resolved_roles(style)
    def media(unit, key, spacing_unit):
        result = []
        for role, selectors in SELECTORS.items():
            rule = rules[role]
            selectors = ','.join(s if s.startswith('body[') else
                'body[data-typography="audience"] ' + s for s in selectors.split(','))
            result.append(selectors + '{font-size:' + str(rule[key]) + unit +
                ';font-weight:' + str(rule['weight']) + ';line-height:' + str(rule['line_height']) +
                ';color:#' + rule.get('color', style['font']['color']) +
                ';margin-top:' + str(rule['before_' + spacing_unit]) + unit +
                ';margin-bottom:' + str(rule['after_' + spacing_unit]) + unit +
                (';font-family:ui-monospace,"Noto Sans Mono",monospace' if role == 'source' else ';font-family:"' + style['font']['family'] + '",sans-serif') + ';}')
        return ''.join(result)
    return ('@media screen{' + media('px', 'screen_px', 'px') + '}' +
            '@media print{' + media('pt', 'print_pt', 'pt') + '}' +
            'body[data-typography="audience"] .sheet{word-break:keep-all;overflow-wrap:anywhere}' +
            'body[data-typography="audience"] .authored-lead,body[data-typography="audience"] .authored-overall,' +
            'body[data-typography="audience"] .hierarchy-line{font:inherit;color:inherit;line-height:inherit}' +
            'body[data-typography="audience"] .step-marker{white-space:nowrap}' +
            'body[data-typography="audience"] .sheet td{padding:8px 10px}')
