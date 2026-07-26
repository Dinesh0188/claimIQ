"""The tool-call node: hand the numbers to the deterministic engine."""

from __future__ import annotations

from claimiq.state import ClaimState
from claimiq.tools.waterfall import PROFILES, compute_waterfall


def compute_node(state: ClaimState) -> dict:
    profiles = {
        name: compute_waterfall(state.packet, state.findings, name) for name in PROFILES
    }
    return {"profiles": profiles}
