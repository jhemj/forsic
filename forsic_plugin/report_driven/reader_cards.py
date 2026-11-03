"""Read-only cards from note-owned judgments and structured time declarations.

No prose parsing, probability scoring, or date inference. A report stores this
projection in its immutable view; callers may project the current state again.
"""
from copy import deepcopy
from datetime import datetime
import re

from .current_judgment import current_judgment


def _instant(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})', value):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, OverflowError):
        return None


def reader_cards(state):
    from .reader_projection import evidence_references
    labels={(r['kind'],r['id'],r['version']):r['label'] for r in evidence_references(state)}
    def label_refs(refs):
        return [{**deepcopy(ref),'label':labels.get((ref['kind'],ref['id'],ref.get('version','')),ref['id'])} for ref in refs]
    judgments=[]
    for question in state.get('questions', []):
        current=current_judgment(state, question)
        for claim in current['judgments']:
            for key in ('source_refs','counterevidence_refs'):claim[key]=label_refs(claim[key])
        judgments.append({**{key:current[key] for key in ('question_id','revision','scope','answer','assessment','review_required','judgments','alternatives')}, 'title':question['question'],
            'version':question['version'],
            'freshness':'recheck' if current['review_required'] else 'snapshot'})
    timeline=[]
    for row in state.get('timeline', []):
        model=row.get('temporal') or {}
        values=row.get('normalized_values') or []
        # Only explicitly observed instants go on the numeric axis. Inferred
        # bounds/candidates and dates lacking timezone stay in separate cards.
        stamp=_instant(values[0]) if len(values)==1 else None
        clock=model.get('clock_domain') or '정규화된 비교 시각'
        stale=bool((row.get('temporal_context') or {}).get('temporal_requires_recheck'))
        plottable=(row.get('comparable') and row.get('shape')=='point'
            and row.get('time_kind') in ('record','event','file_metadata')
            and model.get('observed_or_inferred', 'observed')=='observed'
            and not re.search('unknown|incompatible|미상|불명',clock,re.I)
            and not stale)
        refs=([row['observation_ref']] if row.get('observation_ref') else [])+row.get('claim_refs', [])+(row.get('temporal_context') or {}).get('observation_refs', [])
        refs=list({(ref['kind'],ref['id'],ref.get('version','')):ref for ref in refs}.values())
        timeline.append({'id':row['id'],'version':row['version'],
            'title':row['explanation'],'display_time':'\n'.join(values or row.get('raw_values') or []) or '시각 미확정',
            'stamp':stamp if plottable else None,'clock':clock,'time_kind':row.get('time_kind','unknown'),
            'limitations':[row['limitation']] if row.get('limitation') else [],
            'temporal':deepcopy(model),'source_refs':label_refs(refs),
            'freshness':'recheck' if stale else 'snapshot'})
    return {'snapshot_revision':state['meta']['snapshot_id'],'judgments':judgments,'timeline':timeline}
