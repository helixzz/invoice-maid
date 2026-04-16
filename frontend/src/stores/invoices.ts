import { defineStore } from 'pinia'
import { api } from '@/api/client'
import type { Invoice } from '@/types'

interface InvoicesState {
  invoices: Invoice[]
  loading: boolean
  total: number
  selectedIds: number[]
}

export const useInvoicesStore = defineStore('invoices', {
  state: (): InvoicesState => ({
    invoices: [],
    loading: false,
    total: 0,
    selectedIds: [],
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
        const response = await api.getInvoices({
          q: query,
          date_from: dateFrom,
          date_to: dateTo,
          page,
          size
        })
        this.invoices = response.items
        this.total = response.total
      } catch (error) {
        console.error('Failed to fetch invoices', error)
        throw error
      } finally {
        this.loading = false
      }
    },
    async deleteInvoice(id: number) {
      try {
        await api.deleteInvoice(id)
        this.invoices = this.invoices.filter(i => i.id !== id)
        this.selectedIds = this.selectedIds.filter(selectedId => selectedId !== id)
        this.total--
      } catch (error) {
        console.error('Failed to delete invoice', error)
        throw error
      }
    },
    toggleSelection(id: number) {
      const index = this.selectedIds.indexOf(id)
      if (index === -1) {
        this.selectedIds.push(id)
      } else {
        this.selectedIds.splice(index, 1)
      }
    },
    selectAll(ids: number[]) {
      this.selectedIds = ids
    },
    clearSelection() {
      this.selectedIds = []
    }
  },
})
