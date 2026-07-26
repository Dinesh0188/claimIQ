# Evaluation

Full agent over 4 canonical scenarios. Regenerate with `python scripts/run_eval.py`.

| scenario | invariant | citations | verifier | repairs | grounded | useful | latency | tokens |
|---|---|---|---|---|---|---|---|---|
| billing_error | PASS | 100% | pass | 0 | 4/5 | 5/5 | 9,427 ms | 3,902 |
| cardiac | PASS | 100% | pass | 0 | 4/5 | 5/5 | 14,342 ms | 3,180 |
| clean | PASS | 100% | pass | 0 | 5/5 | 2/5 | 24,883 ms | 0 (cached) |
| incomplete | PASS | 100% | pass | 0 | 5/5 | 5/5 | 7,084 ms | 2,615 |

## Aggregate

- Deterministic invariant held on **4/4** scenarios
- Mean citation coverage on non-payable determinations: **100.0%**
- Verifier passed on **4/4**, mean **0.0** repair(s)
- Mean groundedness **4.5/5**, usefulness **4.2/5** (over 4/4 scenarios the judge answered)
- Mean latency **13,934 ms**, mean **2,424** tokens per claim. Rows showing 0 tokens were served from the content-hash disk cache, so their latency reflects the cache and not the model.

## What these numbers are and are not

`invariant` and `citations` are deterministic checks — they either hold or they do not, and the verifier blocks output when they fail.

`grounded` and `useful` come from an LLM judge. Treat them as directional: they are good at catching a regression between prompt versions and bad at establishing an absolute quality level, not least because the judge shares a family with the model being judged. They gate nothing.

## Claims the judge flagged as unsupported

- These hospital‑absorbed deductions repeat on every claim and represent the most important finding for the billing desk.
- These repeat on every claim and represent the most actionable loss for the hospital.
- Addressing the missing documents and correcting the repeat hospital‑borne charges will reduce the hospital’s loss on future claims.
- Audit the billing template and remove all HOSPITAL‑borne items (room supplies, OT consumables, treatment‑level charges) that are currently being absorbed on every claim
