# Classification benchmark

Corpus `unverified-97f8583129` — 87 hand-labelled line items written to imitate real hospital bill printing.

| strategy | accuracy | non-payable recall | false deduction rate | citation rate |
|---|---|---|---|---|
| head_only | 51.7% | 30.5% | 3.6% | 0% |
| keyword | 82.8% | 74.6% | 0.0% | 100% |
| retrieval | 40.2% | 11.9% | 0.0% | 100% |
| deterministic | 81.6% | 74.6% | 0.0% | 100% |

### Read `accuracy` against the `head_only` control

`head_only` ignores the item description completely and guesses from the billing head alone. It scores as high as it does because of how this labelled set is built: all 59 non-payable items carry `head=OTHER` and 27 of 28 payable ones do not, so the head column nearly determines the payable/non-payable split by itself. Overall accuracy is therefore inflated for every strategy, and a headline of "100% accuracy" would be close to meaningless on its own.

**`non-payable recall` is the honest number.** It measures picking the correct list among four — I, II, III or IV — which the head cannot indicate at all. That column is where retrieval and the model do real work, and where the gap between strategies is genuine.

**false deduction rate** is the error that matters most in production: a genuinely payable medical charge wrongly marked non-payable. Missing a non-payable item costs the hospital a recovery opportunity; wrongly disallowing a real charge produces a bill the patient should never have been shown.

`retrieval` uses hybrid search with a cosine confidence gate and no LLM — this is what runs when `AI_ENABLED=false`. `llm` hands the retrieved candidates to the model, which must cite the catalog entry it decided from; uncited non-payable verdicts are rejected and downgraded to UNMAPPED.

Best strategy on this set: **keyword** at 82.8% accuracy.
