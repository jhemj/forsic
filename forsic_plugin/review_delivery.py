"""Read-only review navigation for ordinary tool returns, separate from the inbox."""


def automatic_review_context(advice):
    """Leave complete findings, quotes and proposed checks in explicit review reads.

    This changes presentation only. The producer still owns dispatch, receipts,
    finding dispositions and applicability; unknown state remains unknown.
    """
    from .context_results import review_navigation
    selected={key:advice[key] for key in
              ('status','review_id','pending_finding_ids','requires_followup') if key in advice}
    return review_navigation({'independent_review':selected})['independent_review']
