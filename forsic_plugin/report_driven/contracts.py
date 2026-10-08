"""Reference contracts for report-driven investigation, not an execution engine."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Ref(Strict):
    kind: Literal['scope','source','observation','claim','question','hypothesis','test','assessment','gap','mission','action']
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)

class BaseRecord(Strict):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)

class Meta(Strict):
    schema_version: Literal['forsic-report-state-1'] = 'forsic-report-state-1'
    data_mode: Literal['synthetic','live']
    case_id: str
    run_id: str
    report_id: str
    snapshot_id: str
    ledger_cutoff: str
    content_revision: str = ""
    generated_at: str
    classification: str
    prepared_by: str
    reviewed_by: str | None
    approved_by: str | None

class Scope(BaseRecord):
    original_question: str
    approved_scope: str
    excluded_scope: str
    display_timezone: str
    readonly_required: Literal[True] = True
    external_transmission_authorized: bool = False
    external_transmission_scope: str = ''
    evidence_labels: list[str]

class Source(BaseRecord):
    label: str
    source_role: Literal['preserved_evidence','tool_result','user_statement','reference_material']
    locator: str
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    provenance: str
    coverage: str
    source_generation_group: str

class Observation(BaseRecord):
    source_ref: Ref
    statement: str
    field_pointer: str
    literal: str
    canonical_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    presented_view: Literal['metadata','exact_excerpt','full_field']
    coordinate_basis: str
    byte_start: int = Field(ge=0)
    byte_end: int = Field(ge=0)
    interpretation_limit: str

class Claim(BaseRecord):
    title: str
    statement: str
    assertion_kind: Literal['fact','interpretation']
    status: Literal['candidate','adopted','superseded','retracted']
    scope: str
    source_refs: list[Ref]
    counterevidence_refs: list[Ref]
    limitations: list[str]
    adoption_receipt: str | None
    semantic_review: Literal['not_reviewed','sampled','reviewed_for_scope']

class Question(BaseRecord):
    goal_id: str = ""
    session_id: str = ""
    next_checks: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    revision: int = 1
    definition_version: str = ""
    priority: Literal["decision_critical","material","contextual","unassessed"] = "unassessed"
    reopen_conditions: list[str] = Field(default_factory=list)
    deferred_reason: str | None = None
    question: str
    target_proposition: str
    answer: str
    assessment: Literal['supported','refuted','conflicting','undetermined']
    work_state: Literal['open','active','held','scoped_closed','blocked_internal','blocked_external','budget_deferred']
    claim_refs: list[Ref]
    hypothesis_refs: list[Ref]
    remaining_gap_ids: list[str]
    scope: str
    closure_rationale: str | None

class Hypothesis(BaseRecord):
    question_ref: Ref
    explanation: str
    trigger_refs: list[Ref]
    support_refs: list[Ref]
    counterevidence_refs: list[Ref]
    assumptions: list[str]
    prediction: str
    compatibility: str
    status: Literal['candidate','under_review','supported_for_scope','refuted_for_scope','undetermined']

class Test(BaseRecord):
    question_ref: Ref
    target_proposition: str
    purpose: Literal['discover','discriminate','verify_reliability']
    immediate_observable: str
    input_refs: list[Ref]
    required_view: Literal['metadata','exact_excerpt','full_field']
    tool_capability: str
    target_scope: str
    support_rule: str | None
    refute_rule: str | None
    inconclusive_rule: str
    physical_job_ref: str | None
    result_ref: Ref | None
    result_scope: str | None
    execution_state: Literal['candidate','ready','queued','running','returned','failed','blocked']
    assessment_state: Literal['unassessed','assessed','stale']
    design_timing: Literal['before_result','after_result']

class Assessment(BaseRecord):
    test_ref: Ref
    result_ref: Ref
    observation_refs: list[Ref]
    outcome: Literal['supports','refutes','inconclusive','found','no_match_in_scope','partial','unavailable']
    reasoning_summary: str
    validation_receipt: str
    adoption_receipt: str
    resolved_gap_ids: list[str]
    remaining_gap_ids: list[str]

class Gap(BaseRecord):
    question_ref: Ref
    section_targets: list[str]
    original_obligation: str
    kind: Literal['unassessed_result','unpresented_source','missing_evidence','unsupported_tool','internal_error','contradiction','untested_alternative','identity_uncertainty','time_uncertainty','budget_deferred','editorial_only','publication_issue']
    disposition: Literal['open','in_progress','assessed_unresolved','resolved','not_applicable']
    impact: Literal['decision_critical','material','contextual','unassessed']
    reason: str
    basis_refs: list[Ref]
    feasible_next_action: Literal['evaluate_result','read_source','design_test','repair_internal','request_external_input','none','editorial']
    missing_preconditions: list[str]
    reopen_conditions: list[str]
    resolution_refs: list[Ref]

class Budget(Strict):
    model_calls: int | None = Field(default=None,ge=0)
    input_tokens: int | None = Field(default=None,ge=0)
    output_tokens: int | None = Field(default=None,ge=0)
    wall_seconds: int | None = Field(default=None,ge=0)
    authority: Literal['unallocated','host_reserved']

class Mission(BaseRecord):
    schema_version: Literal['forsic-report-mission-1'] = 'forsic-report-mission-1'
    original_obligation_id: str
    gap_ref: Ref
    question_ref: Ref
    report_targets: list[str]
    target_proposition: str
    why_it_matters: str
    current_answer: str
    competing_explanation_refs: list[Ref]
    mission_kind: Literal['evaluate_result','read_source','design_test','repair_internal','request_external_input','editorial']
    input_refs: list[Ref]
    required_view: Literal['metadata','exact_excerpt','full_field']
    capability_requirement: str
    proposed_tool_name: str | None
    exact_target_scope: str
    reuse_result_refs: list[Ref]
    design_timing: Literal['before_result','after_result']
    support_rule: str | None
    refute_rule: str | None
    inconclusive_rule: str
    preserved_counterevidence_refs: list[Ref]
    state: Literal['draft','candidate','ready','queued','running','unassessed','blocked','completed','partial','failed']
    budget: Budget
    material_change_required: str
    completion_proof: list[str]
    does_not_resolve: list[str]
    reopen_conditions: list[str]
    readonly: Literal[True] = True
    tool_arguments: dict = Field(default_factory=dict)
    question_definition_version: str = ""
    execution_start_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    compact_contract: bool = False

class TimeAssertion(BaseRecord):
    observation_ref: Ref
    claim_refs: list[Ref]
    time_kind: Literal['record','event','file_metadata','unknown']
    shape: Literal['point','interval','candidates','unknown']
    raw_values: list[str]
    normalized_values: list[str]
    timezone_basis: str | None
    year_basis: str | None
    comparable: bool
    explanation: str
    limitation: str
    file_time_type: str | None

class Action(BaseRecord):
    action_kind: Literal['performed','recommended','requested']
    title: str
    rationale: str
    basis_refs: list[Ref]
    execution_receipt: str | None
    owner: str | None
    due_at: str | None
    verification: str

class Requirement(Strict):
    requirement_id: str
    section_targets: list[str]
    applicability: Literal['applicable','conditional_unassessed','not_applicable']
    disposition: Literal['satisfied','open','scoped_unknown','not_applicable']
    rationale: str
    basis_refs: list[Ref]
    gap_ids: list[str]

class Status(Strict):
    execution: Literal['unknown','running','stopped','budget_exhausted','failed','user_stopped']
    investigation: Literal['in_progress','partial','input_wait','supported_scope_closed']
    report: Literal['draft','partial_snapshot','scope_final_candidate','published']
    integrity_gate: Literal['unverified','passed','failed']
    semantic_gate: Literal['not_reviewed','sampled','reviewed_for_scope']
    layout_gate: Literal['not_rendered','rendered_not_reviewed','reviewed']
    approval_gate: Literal['not_requested','pending','approved']
    explicit_limitations: list[str]

class ReportState(Strict):
    meta: Meta
    status: Status
    scope: Scope
    sources: list[Source]
    observations: list[Observation]
    claims: list[Claim]
    questions: list[Question]
    hypotheses: list[Hypothesis]
    tests: list[Test]
    assessments: list[Assessment]
    gaps: list[Gap]
    missions: list[Mission]
    timeline: list[TimeAssertion]
    actions: list[Action]
    requirements: list[Requirement]
    # Canonical case-local registry records validated by indicators.upsert.
    # They are observations/analyst labels, not adopted incident claims.
    indicators: list[dict] = Field(default_factory=list)
    coverage_summary: str
    untriaged_inventory_summary: str
    missing_metrics: list[str]
