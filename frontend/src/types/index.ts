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
  type?: string
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
