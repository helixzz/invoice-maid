export interface Invoice {
  id: number
  invoice_no: string
  buyer: string
  seller: string
  amount: number
  invoice_date: string
  invoice_type: string
  item_summary: string | null
  source_format: string
  extraction_method: string
  confidence: number
  is_manually_corrected: boolean
  correction_history?: CorrectionLog[]
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
  host?: string | null
  port?: number | null
  username: string
  password?: string
}

export interface AccountUpdate {
  name?: string
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

export interface StatsResponse {
  total_invoices: number
  total_amount: number
  invoices_this_month: number
  amount_this_month: number
  active_accounts: number
  last_scan_at: string | null
  last_scan_found: number | null
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
  invoice_no: string | null
  confidence: number | null
  error_detail: string | null
  created_at: string
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
