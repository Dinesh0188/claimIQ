"""Terminal node: persist the audit for the portfolio dashboard."""

from __future__ import annotations

from claimiq.state import ClaimState


def report_node(state: ClaimState) -> dict:
    # Persistence happens in graph.audit(), which owns the assembled AuditResult.
    # This node exists so the verifier has somewhere to route to.
    return {}
