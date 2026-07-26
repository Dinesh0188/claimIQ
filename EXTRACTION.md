# Bill extraction benchmark

Bills are rendered from JSON packets by `scripts/gen_bill_pdf.py`, so ground truth is exact — which is the only reason these numbers mean anything.

Extraction takes the cheaper path when it can: a native PDF's ruled table is parsed directly with pdfplumber, and the vision model is reserved for scans with no text layer. There is no OCR binary in the stack either way.

## Native PDFs — text-layer table parse

| bill | rows found | row recall | amount accuracy | head accuracy | bill total |
|---|---|---|---|---|---|
| billing_error | 37/37 | 100% | 100% | 100% | exact |
| cardiac | 23/23 | 100% | 100% | 100% | exact |
| clean | 8/8 | 100% | 100% | 100% | exact |
| incomplete | 14/14 | 100% | 100% | 100% | exact |

Overall row recall: **100.0%**.
