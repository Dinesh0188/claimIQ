// -- Vocabularies --

export type Classification =
  | "PAYABLE"
  | "LIST_I_OPTIONAL"
  | "LIST_II_ROOM"
  | "LIST_III_PROCEDURE"
  | "LIST_IV_TREATMENT"
  | "UNMAPPED";

export type Bearer = "PATIENT" | "HOSPITAL" | "INSURER" | "UNKNOWN";

export type Head =
  | "ROOM"
  | "NURSING"
  | "PROCEDURE"
  | "CONSULTATION"
  | "INVESTIGATION"
  | "PHARMACY"
  | "IMPLANT"
  | "OTHER";

export type Severity = "BLOCKER" | "WARNING" | "INFO";

export type Verdict = "CLEAN" | "NEEDS_ATTENTION" | "CANNOT_VERIFY";

// -- Input models --

export interface BillLineItem {
  line_no: number;
  description: string;
  head: Head;
  quantity: string;
  unit_rate: string;
  amount: string;
  service_date: string | null;
  extract_confidence: number;
}

export interface RoomStay {
  room_category: string;
  rate_per_day: string;
  days: number;
  is_icu: boolean;
}

export interface PolicyTerms {
  policy_id: string;
  sum_insured: string;
  balance_sum_insured: string;
  room_rent_cap_per_day: string | null;
  icu_cap_per_day: string | null;
  copay_percent: string;
  deductible: string;
  procedure_sublimits: Record<string, string>;
  policy_inception_date?: string | null;
}

export interface ClaimContext {
  claim_type: "cashless" | "reimbursement";
  admission_date: string;
  discharge_date: string;
  primary_diagnosis: string;
  procedure_performed: string | null;
  is_accident?: boolean;
  is_maternity?: boolean;
  involves_implant: boolean;
  is_ped_related?: boolean;
  preauth_approved_amount: string | null;
  documents_attached: string[];
}

export interface ClaimPacket {
  claim_id: string;
  context: ClaimContext;
  policy: PolicyTerms;
  room_stay: RoomStay;
  line_items: BillLineItem[];
}

// -- Output models --

export interface ItemFinding {
  line_no: number;
  description: string;
  head: Head;
  amount: string;
  classification: Classification;
  bearer: Bearer;
  deducted_amount: string;
  reason: string;
  confidence: number;
  cited_chunk_id: string | null;
  source: "keyword" | "retrieval" | "llm";
  rule_id: string;
  severity: Severity;
  field: string;
  observed: string;
  expected: string;
  citation: string;
  source_id: string;
  impact: string | null;
}

export interface PolicyDeduction {
  step: string;
  basis: string;
  amount: string;
  inputs: Record<string, string>;
  formula: string;
}

export interface WaterfallResult {
  profile: string;
  gross_bill: string;
  item_deduction_total: string;
  policy_deductions: PolicyDeduction[];
  projected_settlement: string;
  patient_liability: string;
  hospital_writeoff: string;
}

export interface DocumentGap {
  document_id: string;
  name: string;
  reason: string;
  severity: Severity;
  rule_id?: string;
  citation?: string;
  impact?: string | null;
}

export interface ConsistencyFlag {
  check_id: string;
  message: string;
  severity: Severity;
  rule_id?: string;
  field?: string;
  observed?: string;
  expected?: string;
  impact?: string | null;
}

export interface AuditResult {
  claim_id: string;
  gross_bill: string;
  diagnosis: string;
  procedure: string;
  findings: ItemFinding[];
  profiles: Record<string, WaterfallResult>;
  typical_profile: string;
  document_gaps: DocumentGap[];
  consistency_flags: ConsistencyFlag[];
  narrative: string;
  action_list: string[];
  unmapped_count: number;
  ai_used: boolean;
  errors: string[];
  corpus_version: string;
  verify_passed: boolean;
  verify_problems: string[];
  repair_count: number;
  coverage: string;
  caveats: string[];
  disclosures: string[];
  strategy: string;
  verdict: Verdict;
}

// -- Extraction response --

export interface ExtractionResult {
  line_items: BillLineItem[];
  room_stay: RoomStay | null;
  has_implant: boolean;
  room_category: string | null;
  room_rate_per_day: number | null;
  room_days: number | null;
  low_confidence: number;
  method: string;
  file_kind: string;
  how: string;
  filename: string;
  document_id: string | null;
  document_label: string;
  policy_terms: PolicyTerms | null;
  clinical_context: {
    admission_date?: string;
    discharge_date?: string;
    primary_diagnosis?: string;
    procedure_performed?: string;
  } | null;
}

// -- Health endpoint --

export interface HealthResponse {
  status: string;
  ai_enabled: boolean;
  key_present: boolean;
  provider: string;
  model: string;
  corpus_version: string;
  corpus_chunks: number;
  dense_retrieval: boolean;
  has_vision: boolean;
}

// -- Profiles --

export interface ProfileConfig {
  label: string;
  note: string;
  excluded_heads: string[];
}

// -- Claims list --

export interface ClaimSummary {
  claim_id: string;
  month: string;
  audited_at: string;
  diagnosis: string;
  procedure: string;
  gross_bill: string;
  settlement: string;
  patient_liability: string;
  hospital_writeoff: string;
  room_rent_deduction: string;
  unmapped_count: number;
  doc_gap_count: number;
  corpus_version: string;
  ai_pipeline: boolean;
}

export interface ClaimsListResponse {
  claims: ClaimSummary[];
  total: number;
  limit: number;
  offset: number;
  months: string[];
}

export interface ClaimDetail extends ClaimSummary {
  findings: {
    line_no: number;
    description: string;
    head: string;
    classification: string;
    bearer: string;
    amount: string;
    deducted_amount: string;
    cited_chunk_id: string | null;
  }[];
  document_gaps: {
    document_id: string;
    name: string;
    severity: string;
  }[];
}

// -- Analytics --

export interface PortfolioSummary {
  empty: boolean;
  claims: number;
  ai_claims: number;
  gross: string;
  settlement: string;
  patient: string;
  hospital_writeoff: string;
  room_rent_deduction: string;
  preventable: string;
  avg_deduction_pct: string;
  unmapped_total: number;
  doc_gaps: number;
}

export interface LeakageItem {
  cause: string;
  amount: number;
  bearer: string;
}

export interface TopLeakingItem {
  item: string;
  claims: number;
  total_deducted: number;
}

export interface MissingDoc {
  name: string;
  severity: string;
  claims: number;
}

// -- Rule catalog --

export interface RuleChunk {
  chunk_id: string;
  title: string;
  body: string;
  list_name: string | null;
  source_id: string;
  aliases: string[];
}

export interface RuleCatalog {
  corpus_version: string;
  verified_on: string | null;
  note: string;
  stale_sources: Record<string, string>;
  counts: Record<string, number>;
  total: number;
  chunk_ids: string[];
  rules?: RuleChunk[];
}
