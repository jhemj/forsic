#!/usr/bin/env python3
"""Offline structure/reference audit and report-gap projection.

No model, tools, network, scheduling, approval, or semantic truth certification.
The host must revalidate actual source bytes, adoption receipts and permissions.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from .contracts import ReportState

RECORD_KINDS={'sources':'source','observations':'observation','claims':'claim',
 'questions':'question','hypotheses':'hypothesis','tests':'test','assessments':'assessment',
 'gaps':'gap','missions':'mission','actions':'action'}
CORE_REQUIREMENTS={'REQ-CORE-ANSWER','REQ-SCOPE','REQ-EVIDENCE','REQ-FINDINGS',
 'REQ-ALTERNATIVES','REQ-TIME','REQ-IMPACT','REQ-ACTIONS','REQ-GAPS',
 'REQ-TRACEABILITY','REQ-PUBLICATION'}
SUBSTANTIVE_ACTIONS={'evaluate_result','read_source','design_test','repair_internal'}

def find_refs(value,path='$'):
 if isinstance(value,dict):
  if set(value)=={'kind','id','version'}:yield path,value
  for k,v in value.items():yield from find_refs(v,path+'.'+k)
 elif isinstance(value,list):
  for i,v in enumerate(value):yield from find_refs(v,f'{path}[{i}]')

def validate_state(raw):
 data=ReportState.model_validate(raw).model_dump()
 issues=[];registry={('scope',data['scope']['id']):data['scope']}
 def issue(code,path,detail):issues.append({'code':code,'path':path,'detail':detail})
 for field,kind in RECORD_KINDS.items():
  for i,r in enumerate(data[field]):
   key=(kind,r['id'])
   if key in registry:issue('duplicate_id',f'$.{field}[{i}]','Duplicate ID within the same kind')
   registry[key]=r
 for path,r in find_refs(data):
  target=registry.get((r['kind'],r['id']))
  if target is None:issue('missing_reference',path,'Reference is absent from this snapshot')
  elif target['version']!=r['version']:issue('stale_reference',path,'Reference version does not match the snapshot')
 for i,c in enumerate(data['claims']):
  if c['status']=='adopted' and (not c['source_refs'] or not c['adoption_receipt']):
   issue('adopted_claim_without_basis',f'$.claims[{i}]','Adoption requires source refs and a host receipt')
 for i,q in enumerate(data['questions']):
  for r in q['claim_refs']:
   target=registry.get((r['kind'],r['id']))
   if r['kind']!='claim' or not target or target.get('status')!='adopted':
    issue('question_uses_unadopted_claim',f'$.questions[{i}]','Current answers cannot endorse candidate or retracted claims')
  if q['work_state']=='scoped_closed' and not q['closure_rationale']:
   issue('closure_without_rationale',f'$.questions[{i}]','Scope closure needs a reason, even for an unknown answer')
  for gid in q['remaining_gap_ids']:
   if ('gap',gid) not in registry:issue('missing_gap',f'$.questions[{i}]',gid)
 for i,t in enumerate(data['tests']):
  if t['purpose']=='discover' and (t['support_rule'] or t['refute_rule']):
   issue('discovery_has_hypothesis_predicate',f'$.tests[{i}]','Discovery findings and hypothesis discrimination are separate')
  if t['purpose']!='discover' and not(t['support_rule'] or t['refute_rule']):
   issue('no_discriminating_condition',f'$.tests[{i}]','At least one actual predicate is required')
  if t['assessment_state']=='assessed' and not any(a['test_ref']['id']==t['id'] and a['test_ref']['version']==t['version'] for a in data['assessments']):
   issue('assessment_state_without_record',f'$.tests[{i}]','No canonical assessment record')
 for i,a in enumerate(data['assessments']):
  t=registry.get(('test',a['test_ref']['id']))
  if t and a['result_ref']!=t['result_ref']:issue('result_version_mismatch',f'$.assessments[{i}]','Assessment is not bound to the test result')
  if a['outcome'] in ('supports','refutes','found') and not a['observation_refs']:
   issue('positive_outcome_without_source',f'$.assessments[{i}]','Positive outcomes need evidence')
 for i,r in enumerate(data['requirements']):
  if r['applicability']=='not_applicable' and (r['disposition']!='not_applicable' or not r['rationale'].strip()):
   issue('invalid_not_applicable_requirement',f'$.requirements[{i}]','N/A needs a matching disposition and rationale')
  for gid in r['gap_ids']:
   if ('gap',gid) not in registry:issue('requirement_gap_missing',f'$.requirements[{i}]',gid)
 for i,g in enumerate(data['gaps']):
  if g['disposition']=='resolved' and not g['resolution_refs']:
   issue('gap_resolution_without_proof',f'$.gaps[{i}]','A new label or mission ID is not resolution proof')
  if g['kind']=='internal_error' and g['feasible_next_action']=='request_external_input':
   issue('internal_error_as_external_gap',f'$.gaps[{i}]','Do not turn an internal reference error into missing external evidence')
  if not g['reason'].strip():issue('gap_without_reason',f'$.gaps[{i}]','A gap needs its actual cause')
 for i,m in enumerate(data['missions']):
  b=m['budget']
  native_linked=bool(m.get('compact_contract') and m.get('question_definition_version') and m.get('execution_start_ids'))
  if m['state']=='running' and m.get('compact_contract') and not native_linked:
   issue('execution_without_receipt',f'$.missions[{i}]','Native execution requires an actual start receipt')
  if m['state'] in ('ready','queued','running') and not native_linked and (b['authority']!='host_reserved' or any(b[k] is None for k in ('model_calls','input_tokens','output_tokens','wall_seconds'))):
   issue('execution_without_budget',f'$.missions[{i}]','Draft missions are not executable reservations')
 for i,t in enumerate(data['timeline']):
  if t['comparable'] and (not t['normalized_values'] or not t['timezone_basis'] or not t['year_basis']):
   issue('unsupported_time_normalization',f'$.timeline[{i}]','Comparable absolute time requires explicit basis')
  if t['time_kind']=='file_metadata' and not t['file_time_type']:
   issue('file_time_kind_missing',f'$.timeline[{i}]','mtime/ctime/etc. must not be implicit')
 for i,a in enumerate(data['actions']):
  if a['action_kind']=='performed' and not a['execution_receipt']:
   issue('recommendation_as_performed',f'$.actions[{i}]','An actual action needs an execution receipt')
 for i,o in enumerate(data['observations']):
  if o['byte_end']<o['byte_start']:issue('invalid_span',f'$.observations[{i}]','Invalid source coordinate')
  if data['meta']['data_mode']=='synthetic' and hashlib.sha256(o['literal'].encode()).hexdigest()!=o['canonical_sha256']:
   issue('synthetic_literal_hash_mismatch',f'$.observations[{i}]','Synthetic fixture text hash mismatch')
 return data,issues

def audit(raw):
 data,issues=validate_state(raw)
 reqs={r['requirement_id']:r for r in data['requirements']}
 missing=sorted(CORE_REQUIREMENTS-set(reqs))
 unknown=[r['requirement_id'] for r in data['requirements'] if r['applicability']=='conditional_unassessed']
 open_requirements=[r['requirement_id'] for r in data['requirements'] if r['disposition']=='open']
 meaningful=[g for g in data['gaps'] if g['disposition'] not in ('resolved','not_applicable') and g['kind'] not in ('editorial_only','publication_issue')]
 available=[g for g in meaningful if g['feasible_next_action'] in SUBSTANTIVE_ACTIONS and not g['missing_preconditions']]
 output={
  'schema':'forsic-gap-audit-1','snapshot_id':data['meta']['snapshot_id'],
  'structural_issues':issues,'missing_requirement_records':missing,
  'applicability_unassessed':unknown,'open_requirement_ids':open_requirements,
  'unresolved_gap_ids':[g['id'] for g in meaningful],
  'ready_to_plan_gap_ids':[g['id'] for g in available],
  'external_input_gap_ids':[g['id'] for g in meaningful if g['feasible_next_action']=='request_external_input'],
  'editorial_gap_ids':[g['id'] for g in data['gaps'] if g['kind']=='editorial_only' and g['disposition']!='resolved'],
  'scope_final_blockers':[],
  'publication_recommendation':'draft_only',
  'execution_authorized':False,
  'limitation':'Only schema, exact-reference and selected contract invariants checked. Source truth, natural-language entailment, actual receipts, scope completeness and permissions require host validation.'}
 blockers=output['scope_final_blockers']
 if issues:blockers.append('structural_or_reference_errors')
 if missing or unknown or open_requirements:blockers.append('requirements_incomplete')
 if available:blockers.append('material_feasible_work_remains')
 if any(g['kind'] in ('internal_error','budget_deferred') for g in meaningful):blockers.append('unfinished_internal_or_budget_work')
 if data['status']['investigation']!='supported_scope_closed':blockers.append('investigation_scope_not_closed')
 if any(q['work_state']!='scoped_closed' for q in data['questions']):blockers.append('question_work_not_closed')
 if any(g['disposition'] in ('open','in_progress') for g in meaningful):blockers.append('unadjudicated_report_gaps')
 if data['status']['integrity_gate']!='passed':blockers.append('host_evidence_gate_not_passed')
 if data['status']['semantic_gate']!='reviewed_for_scope':blockers.append('semantic_scope_review_not_complete')
 if data['status']['layout_gate']!='reviewed':blockers.append('layout_not_reviewed')
 if not issues and data['status']['integrity_gate']=='passed' and data['status']['layout_gate']=='reviewed' and data['status']['explicit_limitations']:
  output['publication_recommendation']='partial_review_candidate'
 if not blockers:output['publication_recommendation']='scope_final_review_candidate'
 if data['status']['report']=='published' and data['status']['approval_gate']!='approved':
  issues.append({'code':'publication_without_approval','path':'$.status','detail':'No host publication approval'})
  output['publication_recommendation']='draft_only'
 return output

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('snapshot');ap.add_argument('--out');args=ap.parse_args()
 result=audit(json.loads(Path(args.snapshot).read_text(encoding='utf-8')))
 payload=json.dumps(result,ensure_ascii=False,indent=2)
 if args.out:
  p=Path(args.out)
  with p.open('x',encoding='utf-8') as f:f.write(payload)
 else:print(payload)
