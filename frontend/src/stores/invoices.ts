import { defineStore } from 'pinia'
import apiClient from '@/api/client'

export interface Invoice {
  id: string
  buyer_name: string
  seller_name: string
  total_amount: number
  item_description: string
  invoice_type: string
  invoice_number: string
  invoice_date: string
  file_url: string
}

interface InvoicesState {
  invoices: Invoice[]
  loading: boolean
  total: number
}

export const useInvoicesStore = defineStore('invoices', {
  state: (): InvoicesState => ({
    invoices: [],
    loading: false,
    total: 0,
  }),
  actions: {
    async fetchInvoices(
      query?: string,
      dateFrom?: string,
      dateTo?: string,
      page: number = 1,
      size: number = 20
    ) {
      this.loading = true
      try {
        const params = new URLSearchParams()
        if (query) params.append('query', query)
        if (dateFrom) params.append('date_from', dateFrom)
        if (dateTo) params.append('date_to', dateTo)
        params.append('page', page.toString())
        params.append('size', size.toString())

        const response = await apiClient.get(`/invoices?${params.toString()}`)
        this.invoices = response.data.items
        this.total = response.data.total
      } catch (error) {
        console.error('Failed to fetch invoices', error)
        throw error
      } finally {
        this.loading = false
      }
    },
  },
})
