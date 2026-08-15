"""Turn computed numbers into something a billing clerk can act on.

The model is handed finished arithmetic and told to explain it. It is explicitly
forbidden from producing a figure that was not given to it -- every rupee in the
narrative traces to the deterministic tool, which is what makes the verifier node
(CP5) able to catch disagreement.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from claimiq.llm import try_client
from claimiq.state import ClaimState

SYSTEM = """You explain Indian health-insurance claim deductions to hospital billing staff.

Hard rules:
- Use ONLY the figures supplied in the input. Never calculate, estimate or invent a number.
- Write plainly. No jargon the billing desk would not already use.
- Say "estimated" -- these are projections under configurable insurer rules, not decisions.
- Distinguish clearly between money the PATIENT owes and money the HOSPITAL loses.
  Hospital-borne items are billing errors that repeat on every claim; that is the
  finding worth the most to them.

Return: a `narrative` of 3-5 short paragraphs, and an `action_list` of concrete,
prioritised, imperative steps."""


class Explanation(BaseModel):
    narrative: str = Field(description="3-5 short paragraphs of plain English")
    action_list: list[str] = Field(description="Prioritised imperative actions")


def _preauth_facts(state: ClaimState) -> dict | None:
    approved = state.packet.context.preauth_approved_amount
    if not approved or approved <= 0:
        return None
    variance = state.packet.gross_bill - approved
    return {
        "approved_amount": str(approved),
        "variance_amount": str(abs(variance)),
        "direction": "over" if variance > 0 else "under",
    }


def _facts(state: ClaimState) -> str:
    typical = state.profiles["typical"]
    deducted = [f for f in state.findings if f.deducted_amount > 0]
    return json.dumps(
        {
            "currency": "INR",
            "diagnosis": state.packet.context.primary_diagnosis,
            "procedure": state.packet.context.procedure_performed,
            "gross_bill": str(typical.gross_bill),
            "room": {
                "category": state.packet.room_stay.room_category,
                "billed_per_day": str(state.packet.room_stay.rate_per_day),
                "policy_cap_per_day": str(state.packet.policy.room_rent_cap_per_day),
                "days": state.packet.room_stay.days,
            },
            "settlement_range": {
                "low": str(min(p.projected_settlement for p in state.profiles.values())),
                "high": str(max(p.projected_settlement for p in state.profiles.values())),
                "typical": str(typical.projected_settlement),
            },
            "patient_liability_typical": str(typical.patient_liability),
            "hospital_writeoff_typical": str(typical.hospital_writeoff),
            # Supplied rather than left to be derived: the billing desk needs the
            # variance in rupees, and a figure the writer had to compute itself is a
            # figure the verifier cannot distinguish from an invented one.
            "preauth": _preauth_facts(state),
            "policy_deductions": [
                {"step": d.step, "basis": d.basis, "amount": str(d.amount)}
                for d in typical.policy_deductions
            ],
            # What each list means, so restating it is grounded rather than recalled.
            "list_definitions": {
                "LIST_I_OPTIONAL": "Optional/personal item. Non-payable; the PATIENT bears it.",
                "LIST_II_ROOM": "Already covered by the room tariff. The HOSPITAL absorbs it.",
                "LIST_III_PROCEDURE": "Already covered by the procedure package. The HOSPITAL absorbs it.",
                "LIST_IV_TREATMENT": "Already covered by the cost of treatment. The HOSPITAL absorbs it.",
            },
            "deducted_items": [
                {
                    "description": f.description,
                    "amount": str(f.amount),
                    "list": f.classification,
                    "borne_by": f.bearer,
                    # The rationale and its source. Without these the writer restates
                    # the list name from memory and the judge cannot check it.
                    "reason": f.reason,
                    "cited_rule": f.cited_chunk_id,
                }
                for f in deducted
            ],
            "unmapped_items": [
                f.description for f in state.findings if f.classification == "UNMAPPED"
            ],
            "missing_documents": [
                {"name": g.name, "severity": g.severity, "reason": g.reason}
                for g in state.document_gaps
            ],
            "consistency_flags": [
                {"id": c.check_id, "severity": c.severity, "message": c.message}
                for c in state.consistency_flags
            ],
        },
        indent=2,
    )


def _deterministic(state: ClaimState) -> tuple[str, list[str]]:
    """Used when AI is off. The app stays fully functional without a key."""
    t = state.profiles["typical"]
    low = min(p.projected_settlement for p in state.profiles.values())
    high = max(p.projected_settlement for p in state.profiles.values())
    lines = [
        f"Gross bill Rs {t.gross_bill:,}. Estimated settlement between Rs {low:,} and "
        f"Rs {high:,} depending on how the insurer treats associated charges.",
        f"Estimated patient liability Rs {t.patient_liability:,}; estimated hospital "
        f"write-off Rs {t.hospital_writeoff:,}.",
    ]
    actions = [f"{d.step.replace('_', ' ').title()}: {d.basis}" for d in t.policy_deductions]
    actions += [
        f"Re-bill '{f.description}' -- {f.reason}"
        for f in state.findings
        if f.bearer == "HOSPITAL"
    ]
    actions += [
        f"[{g.severity}] Attach {g.name} -- {g.reason}" for g in state.document_gaps
    ]
    actions += [f"[{c.severity}] {c.message}" for c in state.consistency_flags]
    unmapped = [f.description for f in state.findings if f.classification == "UNMAPPED"]
    if unmapped:
        actions.append(f"Manually review {len(unmapped)} unmatched line item(s): {', '.join(unmapped)}")
    return "\n\n".join(lines), actions


def explain_node(state: ClaimState) -> dict:
    client = try_client()
    facts = _facts(state)
    if client is None:
        narrative, actions = _deterministic(state)
        return {
            "narrative": narrative,
            "action_list": actions,
            "ai_used": False,
            "facts_json": facts,
        }

    user = facts
    if state.verify_problems:
        # Targeted repair: name what was wrong instead of re-running the same prompt.
        user += (
            "\n\nYour previous answer was rejected by the verifier for these reasons:\n"
            + "\n".join(f"- {p}" for p in state.verify_problems)
            + "\nRewrite it using only the figures above."
        )

    try:
        result = client.structured(
            node="explain",
            system=SYSTEM,
            user=user,
            schema=Explanation,
            # Repairs must not be served from cache or the loop can never converge.
            temperature=0.0 if not state.verify_problems else 0.2,
        )
    except Exception as exc:  # noqa: BLE001 - demo must not die on a 429
        narrative, actions = _deterministic(state)
        return {
            "narrative": narrative,
            "action_list": actions,
            "ai_used": False,
            "facts_json": facts,
            "errors": [*state.errors, f"explain: fell back to deterministic text ({exc})"],
        }

    return {
        "narrative": result.narrative,
        "action_list": result.action_list,
        "ai_used": True,
        "facts_json": facts,
    }
