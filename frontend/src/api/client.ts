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
  StatsResponse
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
  // Auth
  async login(password: string): Promise<{access_token: string}> {
    const formData = new FormData()
    formData.append('username', 'admin') // Assuming fixed user or OAuth style where username might be required but unused if it's just password auth. Actually standard OAuth2 uses username/password.
    // However the store sends { password } JSON initially.
    // According to instructions: login(password: string): Promise<{access_token: string}>
    // I will use JSON for now but might need to adjust based on typical FastAPI OAuth2PasswordRequestForm
    // Assuming backend is JSON based since the original store did `apiClient.post('/auth/login', { password })`
    const res = await apiClient.post('/auth/login', { password })
    return res.data
  },

  // Invoices
  async getInvoices(params?: {q?: string, date_from?: string, date_to?: string, page?: number, size?: number}): Promise<InvoiceListResponse> {
    const res = await apiClient.get('/invoices', { params })
    return res.data
  },
  
  async getInvoice(id: number): Promise<Invoice> {
    const res = await apiClient.get(`/invoices/${id}`)
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

  async getStats(): Promise<StatsResponse> {
    const res = await apiClient.get('/stats')
    return res.data
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

  async testConnection(id: number): Promise<ConnectionTestResponse> {
    const res = await apiClient.post(`/accounts/${id}/test-connection`)
    return res.data
  },

  // Scan
  async triggerScan(): Promise<{status: string}> {
    const res = await apiClient.post('/scan/trigger')
    return res.data
  },

  async getScanLogs(params?: {page?: number, size?: number}): Promise<ScanLogListResponse> {
    const res = await apiClient.get('/scan/logs', { params })
    return res.data
  }
}

export default api
