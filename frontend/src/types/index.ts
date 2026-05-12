export type InvoiceCategory =
  | 'vat_invoice'
  | 'overseas_invoice'
  | 'receipt'
  | 'proforma'
  | 'other'

export const CATEGORY_LABELS: Record<InvoiceCategory, string> = {
  vat_invoice: '增值税发票',
  overseas_invoice: '境外票据',
  receipt: '收据',
  proforma: '形式发票',
  other: '其它',
}

export const CATEGORY_COLORS: Record<InvoiceCategory, string> = {
  vat_invoice: 'bg-blue-50 text-blue-700 border-blue-200',
  overseas_invoice: 'bg-violet-50 text-violet-700 border-violet-200',
  receipt: 'bg-amber-50 text-amber-700 border-amber-200',
  proforma: 'bg-yellow-50 text-yellow-800 border-yellow-200',
  other: 'bg-slate-100 text-slate-700 border-slate-200',
}

export const CATEGORY_ORDER: InvoiceCategory[] = [
  'vat_invoice',
  'overseas_invoice',
  'receipt',
  'proforma',
  'other',
]

export interface Invoice {
  id: number
  invoice_no: string
  buyer: string
  seller: string
  amount: number
  invoice_date: string
  invoice_type: string
  invoice_category: InvoiceCategory
  currency: string
  item_summary: string | null
  source_format: string
  extraction_method: string
  confidence: number
  is_manually_corrected: boolean
  correction_history?: CorrectionLog[]
  created_at: string
}

export interface UserInfo {
  id: number
  email: string
  is_active: boolean
  is_admin: boolean
  created_at: string
}

export interface InvoiceListResponse {
  items: Invoice[]
  total: number
  page: number
  size: number
}

export interface EmailAccount {
  id: number
  name: string
  type: string
  outlook_account_type: string
  host: string | null
  port: number | null
  username: string
  is_active: boolean
  last_scan_uid: string | null
  created_at: string
}

export interface AccountCreate {
  name: string
  type: string
  outlook_account_type?: string
  host?: string | null
  port?: number | null
  username: string
  password?: string
}

export interface AccountUpdate {
  name?: string
  outlook_account_type?: string
  host?: string | null
  port?: number | null
  username?: string
  password?: string
  is_active?: boolean
}

export interface ScanLog {
  id: number
  email_account_id: number
  started_at: string
  finished_at: string | null
  emails_scanned: number
  invoices_found: number
  error_message: string | null
}

export interface ScanLogListResponse {
  items: ScanLog[]
  total: number
  page: number
  size: number
}

export interface ConnectionTestResponse {
  ok: boolean
  detail: string | null
}

export interface CategoryCountPoint {
  category: InvoiceCategory
  count: number
}

export interface StatsResponse {
  total_invoices: number
  total_amount: number
  invoices_this_month: number
  amount_this_month: number
  active_accounts: number
  last_scan_at: string | null
  last_scan_found: number | null
  by_category?: CategoryCountPoint[]
}

export interface AISettingsResponse {
  llm_base_url: string
  llm_api_key_masked: string
  llm_model: string
  llm_embed_model: string
  embed_dim: number
  source: 'database' | 'environment'
}

export interface AISettingsUpdate {
  llm_base_url?: string
  llm_api_key?: string
  llm_model?: string
  llm_embed_model?: string
  embed_dim?: number
}

export interface ClassifierSettingsResponse {
  trusted_senders: string
  extra_keywords: string
}

export interface ClassifierSettingsUpdate {
  trusted_senders?: string
  extra_keywords?: string
}

export interface CorrectionLog {
  id: number
  field_name: string
  old_value: string | null
  new_value: string | null
  corrected_at: string
}

export interface ExtractionLog {
  id: number
  email_uid: string
  email_subject: string
  attachment_filename: string | null
  outcome: string
  classification_tier: number | null
  parse_method: string | null
  parse_format: string | null
  download_outcome: string | null
  invoice_no: string | null
  confidence: number | null
  error_detail: string | null
  created_at: string
}

export interface ExtractionSummary {
  scan_log_id: number
  total: number
  outcomes: Record<string, number>
  parse_methods: Record<string, number>
  classification_tiers: Record<string, number>
}

export interface AIConnectionTestResult {
  ok: boolean
  model: string
  detail?: string
  latency_ms?: number
  error_type?: string
  dim?: number
  dim_mismatch?: boolean
}

export interface AIConnectionTestResponse {
  ok: boolean
  chat: AIConnectionTestResult
  embed: AIConnectionTestResult
}

export interface SavedView {
  id: number
  name: string
  filter_json: string
  created_at: string
}

export interface StatsAnalytics {
  monthly_spend: {month: string; total: number; count: number}[]
  top_sellers: {seller: string; total: number; count: number}[]
  by_type: {type: string; count: number}[]
  by_method: {method: string; count: number}[]
  avg_confidence: number
}

export interface OAuthInitiateResponse {
  status: 'authorized' | 'pending'
  verification_uri?: string
  user_code?: string
  expires_at?: string
}

export interface OAuthStatusResponse {
  status: 'none' | 'pending' | 'authorized' | 'expired' | 'error'
  verification_uri?: string
  user_code?: string
  expires_at?: string
  detail?: string
}

export interface AdminUserSummary {
  id: number
  email: string
  is_active: boolean
  is_admin: boolean
  created_at: string
  invoice_count: number
}

export interface AdminUserPatch {
  is_active?: boolean
  is_admin?: boolean
  email?: string
}
