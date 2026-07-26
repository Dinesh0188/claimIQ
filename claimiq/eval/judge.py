"""LLM-as-judge scoring for the generated explanation.

Two things are scored separately, because they fail independently:

  groundedness  does every claim in the narrative follow from the supplied facts?
                This is the hallucination check.
  usefulness    would a hospital billing clerk know what to do after reading it?

A judge is not a measurement instrument in the way a unit test is. Its scores are
directional -- useful for catching a regression between prompt versions, not for
claiming an absolute quality level. The deterministic checks in verify.py are what
actually gate output.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from claimiq.llm import LLMClient
from claimiq.state import AuditResult

JUDGE_SYSTEM = """You grade explanations of Indian health-insurance claim deductions.

You are given the exact FACTS the writer was working from and their EXPLANATION.

A figure is supported if it appears in the FACTS, including inside a flag or reason
string. Arithmetic the FACTS state explicitly (for example a supplied variance amount)
is supported. Do not mark a number unsupported merely because it is restated in words.

Score two things from 1 to 5:

groundedness — is every factual and numerical claim supported by the FACTS?
  5 fully supported. 3 mostly supported with one vague claim.
  1 contains figures or assertions absent from the FACTS.

usefulness — could a hospital billing clerk act on this without asking questions?
  5 specific, prioritised, names the documents and items involved.
  3 correct but generic. 1 vague or evasive.

List any unsupported claims verbatim. Be strict: an invented number is a 1 for
groundedness no matter how well written the rest is."""


class Judgement(BaseModel):
    groundedness: int = Field(ge=1, le=5)
    usefulness: int = Field(ge=1, le=5)
    unsupported_claims: list[str] = Field(default_factory=list)
    comment: str = ""


def _fallback_facts(result: AuditResult) -> str:
    """Used only for results produced before facts_json existed."""
    typical = result.typical
    return (
        f"gross_bill={result.gross_bill}\n"
        f"settlement_typical={typical.projected_settlement}\n"
        f"settlement_range={result.settlement_low}..{result.settlement_high}\n"
        f"patient_liability={typical.patient_liability}\n"
        f"hospital_writeoff={typical.hospital_writeoff}\n"
        "policy_deductions="
        + "; ".join(f"{d.step}={d.amount}" for d in typical.policy_deductions)
        + "\ndeducted_items="
        + "; ".join(
            f"{f.description}={f.deducted_amount}({f.classification})"
            for f in result.findings
            if f.deducted_amount > 0
        )
        + "\nmissing_documents="
        + "; ".join(g.name for g in result.document_gaps)
    )


def judge_explanation(result: AuditResult, facts: str, client: LLMClient) -> Judgement:
    """Score the narrative against the exact fact set it was written from.

    `result.facts_json` is that fact set. An earlier version of this function rebuilt
    a smaller summary here, which quietly withheld the room rate, the pre-authorisation
    figures and the consistency-flag text -- so the judge marked perfectly grounded
    statements as hallucinations and scored 1.2/5 on output the deterministic verifier
    had already passed. Judging against less evidence than the writer had measures the
    harness, not the model.
    """
    evidence = facts or result.facts_json or _fallback_facts(result)
    summary = (
        f"FACTS\n{evidence}\n\n"
        f"EXPLANATION\n{result.narrative}\n\n"
        "ACTIONS\n" + "\n".join(f"- {a}" for a in result.action_list)
    )

    return client.structured(
        node="judge", system=JUDGE_SYSTEM, user=summary, schema=Judgement, temperature=0.0
    )
