"""Document gaps and internal-consistency checks."""

from __future__ import annotations

from claimiq.state import ClaimState
from claimiq.tools.consistency import run_checks
from claimiq.tools.documents import check_documents


def readiness_node(state: ClaimState) -> dict:
    return {
        "document_gaps": check_documents(state.packet),
        "consistency_flags": run_checks(state.packet, state.findings),
    }
