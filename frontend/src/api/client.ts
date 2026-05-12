import axios from 'axios'
import { useAuthStore } from '@/stores/auth'
import type { 
  Invoice, 
  InvoiceListResponse, 
  EmailAccount, 
  AccountCreate, 
  AccountUpdate, 
  ScanLog,
  ScanLogListResponse,
  ConnectionTestResponse,
  StatsResponse,
  AISettingsResponse,
  AISettingsUpdate,
  ClassifierSettingsResponse,
  ClassifierSettingsUpdate,
  ExtractionLog,
  ExtractionSummary,
  AIConnectionTestResponse,
  SavedView,
  StatsAnalytics,
  OAuthInitiateResponse,
  OAuthStatusResponse,
  UserInfo,
  AdminUserSummary,
  AdminUserPatch,
} from '@/types'

export const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
})

apiClient.interceptors.request.use(
  (config) => {
    const authStore = useAuthStore()
    if (authStore.token && config.headers) {
      config.headers.Authorization = `Bearer ${authStore.token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

apiClient.interceptors.response.use(
  (response) => {
    return response
  },
  (error) => {
    if (error.response && error.response.status === 401) {
      const authStore = useAuthStore()
      authStore.logout()
    }
    return Promise.reject(error)
  }
)

export const api = {
  // Helpers
  extractFilename(response: Response, fallback: string): string {
    const disposition = response.headers.get('Content-Disposition')
    if (disposition) {
      const match = disposition.match(/filename\*?=(?:UTF-8''|"?)([^";]+)/i)
      if (match) return decodeURIComponent(match[1].replace(/"/g, ''))
    }
    return fallback
  },

  invoiceExtension(sourceFormat: string): string {
    const map: Record<string, string> = { pdf: '.pdf', xml: '.xml', ofd: '.ofd' }
    return map[sourceFormat.toLowerCase()] || '.pdf'
  },

  // Auth
  async login(email: string, password: string): Promise<{access_token: string}> {
    const res = await apiClient.post('/auth/login', { email, password })
    return res.data
  },

  async register(email: string, password: string, passwordConfirm: string): Promise<{access_token: string}> {
    const res = await apiClient.post('/auth/register', {
      email,
      password,
      password_confirm: passwordConfirm,
    })
    return res.data
  },

  async me(): Promise<UserInfo> {
    const res = await apiClient.get('/auth/me')
    return res.data
  },

  async changePassword(
    currentPassword: string,
    newPassword: string,
    newPasswordConfirm: string,
  ): Promise<void> {
    await apiClient.put('/auth/me/password', {
      current_password: currentPassword,
      new_password: newPassword,
      new_password_confirm: newPasswordConfirm,
    })
  },

  // Invoices
  async getInvoices(params?: {q?: string, date_from?: string, date_to?: string, category?: string[], page?: number, size?: number}): Promise<InvoiceListResponse> {
    const res = await apiClient.get('/invoices', {
      params,
      paramsSerializer: { indexes: null },
    })
    return res.data
  },
  
  async getInvoice(id: number): Promise<Invoice> {
    const res = await apiClient.get(`/invoices/${id}`)
    return res.data
  },

  async updateInvoice(id: number, data: Partial<Invoice>): Promise<Invoice> {
    const res = await apiClient.put(`/invoices/${id}`, data)
    return res.data
  },

  async getSimilarInvoices(id: number): Promise<Invoice[]> {
    const res = await apiClient.get(`/invoices/${id}/similar`)
    return res.data
  },

  async deleteInvoice(id: number): Promise<void> {
    await apiClient.delete(`/invoices/${id}`)
  },

  async searchInvoices(query: string): Promise<InvoiceListResponse> {
    const res = await apiClient.post('/invoices/search', { query })
    return res.data
  },

  downloadInvoice(id: number): string {
    return `/api/v1/invoices/${id}/download`
  },

  async batchDownload(ids: number[]): Promise<Blob> {
    const res = await apiClient.post('/invoices/batch-download', { ids }, { responseType: 'blob' })
    return res.data
  },

  async batchDelete(ids: number[]): Promise<void> {
    await apiClient.post('/invoices/batch-delete', { ids })
  },

  async uploadInvoice(
    file: File,
    onProgress?: (percent: number) => void,
  ): Promise<Invoice> {
    const form = new FormData()
    form.append('file', file)
    const res = await apiClient.post<Invoice>('/invoices/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (evt) => {
        if (onProgress && evt.total) {
          onProgress(Math.round((evt.loaded / evt.total) * 100))
        }
      },
    })
    return res.data
  },

  async getStats(): Promise<StatsResponse> {
    const res = await apiClient.get('/stats')
    return res.data
  },

  async getAnalytics(): Promise<StatsAnalytics> {
    const res = await apiClient.get('/stats/analytics')
    return res.data
  },

  exportInvoicesCSV(params?: {q?: string, date_from?: string, date_to?: string, category?: string[]}): Promise<Blob> {
    return apiClient.get('/invoices/export', {
      params,
      paramsSerializer: { indexes: null },
      responseType: 'blob',
    }).then(res => res.data)
  },

  async getSavedViews(): Promise<SavedView[]> {
    const res = await apiClient.get('/invoices/views')
    return res.data
  },

  async createSavedView(name: string, filterJson: string): Promise<SavedView> {
    const res = await apiClient.post('/invoices/views', { name, filter_json: filterJson })
    return res.data
  },

  async deleteSavedView(id: number): Promise<void> {
    await apiClient.delete(`/invoices/views/${id}`)
  },

  // Accounts
  async getAccounts(): Promise<EmailAccount[]> {
    const res = await apiClient.get('/accounts')
    return res.data
  },

  async createAccount(data: AccountCreate): Promise<EmailAccount> {
    const res = await apiClient.post('/accounts', data)
    return res.data
  },

  async updateAccount(id: number, data: AccountUpdate): Promise<EmailAccount> {
    const res = await apiClient.put(`/accounts/${id}`, data)
    return res.data
  },

  async deleteAccount(id: number): Promise<void> {
    await apiClient.delete(`/accounts/${id}`)
  },

  async setCursorStorageState(accountId: number, storageState: string): Promise<{status: string}> {
    const res = await apiClient.post(`/accounts/${accountId}/cursor-auth`, {
      playwright_storage_state: storageState,
    })
    return res.data
  },

  async testConnection(id: number): Promise<ConnectionTestResponse> {
    const res = await apiClient.post(`/accounts/${id}/test-connection`)
    return res.data
  },

  async initiateOAuth(id: number): Promise<OAuthInitiateResponse> {
    const res = await apiClient.post(`/accounts/${id}/oauth/initiate`)
    return res.data
  },

  async getOAuthStatus(id: number): Promise<OAuthStatusResponse> {
    const res = await apiClient.get(`/accounts/${id}/oauth/status`)
    return res.data
  },

  // Scan
  async triggerScan(options?: {full?: boolean, unread_only?: boolean, since?: string | null, email_account_id?: number | null}): Promise<{status: string}> {
    const body = {
      full: options?.full ?? false,
      unread_only: options?.unread_only ?? false,
      since: options?.since ?? null,
      email_account_id: options?.email_account_id ?? null,
    }
    const res = await apiClient.post('/scan/trigger', body)
    return res.data
  },

  async getScanLogs(params?: {page?: number, size?: number}): Promise<ScanLogListResponse> {
    const res = await apiClient.get('/scan/logs', { params })
    return res.data
  },

  async getExtractionLogs(scanLogId: number): Promise<ExtractionLog[]> {
    const res = await apiClient.get(`/scan/logs/${scanLogId}/extractions`)
    return res.data.items ?? []
  },

  async getScanLogSummary(scanLogId: number): Promise<ExtractionSummary> {
    const res = await apiClient.get(`/scan/logs/${scanLogId}/summary`)
    return res.data
  },

  async getAISettings(): Promise<AISettingsResponse> {
    const res = await apiClient.get('/settings/ai')
    return res.data
  },

  async updateAISettings(data: AISettingsUpdate): Promise<void> {
    await apiClient.put('/settings/ai', data)
  },

  async getAIModels(): Promise<{ models: string[] }> {
    const res = await apiClient.get('/settings/ai/models')
    return res.data
  },

  async testAIConnection(): Promise<AIConnectionTestResponse> {
    const res = await apiClient.post('/settings/ai/test-connection')
    return res.data
  },

  async getClassifierSettings(): Promise<ClassifierSettingsResponse> {
    const res = await apiClient.get('/settings/classifier')
    return res.data
  },

  async updateClassifierSettings(data: ClassifierSettingsUpdate): Promise<void> {
    await apiClient.put('/settings/classifier', data)
  },

  // Admin
  async adminListUsers(): Promise<AdminUserSummary[]> {
    const res = await apiClient.get('/admin/users')
    return res.data
  },

  async adminUpdateUser(id: number, patch: AdminUserPatch): Promise<AdminUserSummary> {
    const res = await apiClient.put(`/admin/users/${id}`, patch)
    return res.data
  },

  async adminDeleteUser(id: number): Promise<void> {
    await apiClient.delete(`/admin/users/${id}`)
  },
}

export default api
