<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useInvoicesStore } from '@/stores/invoices'
import { useDebounceFn } from '@vueuse/core'
import { useAuthStore } from '@/stores/auth'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import AppLayout from '@/components/AppLayout.vue'
import CategoryBadge from '@/components/CategoryBadge.vue'
import MultiSelectChips from '@/components/MultiSelectChips.vue'
import { api } from '@/api/client'
import {
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  type InvoiceCategory,
  type StatsResponse,
  type SavedView,
} from '@/types'

const router = useRouter()
const route = useRoute()
const invoicesStore = useInvoicesStore()
const authStore = useAuthStore()

const CATEGORY_VALUES = new Set<InvoiceCategory>(CATEGORY_ORDER)

const categoryOptions = CATEGORY_ORDER.map(value => ({
  value,
  label: CATEGORY_LABELS[value],
}))

function parseCategoriesFromQuery(): InvoiceCategory[] {
  const raw = route.query.category
  const list = Array.isArray(raw) ? raw : raw ? [raw] : []
  const result: InvoiceCategory[] = []
  for (const entry of list) {
    if (typeof entry === 'string' && CATEGORY_VALUES.has(entry as InvoiceCategory)) {
      result.push(entry as InvoiceCategory)
    }
  }
  return result
}

const selectedCategories = ref<InvoiceCategory[]>(parseCategoriesFromQuery())

const stats = ref<StatsResponse | null>(null)
const loadingStats = ref(true)

const savedViews = ref<SavedView[]>([])
const loadingViews = ref(false)
const activeViewId = ref<number | null>(null)
const showSaveViewModal = ref(false)
const newViewName = ref('')
const isSavingView = ref(false)

const fetchSavedViews = async () => {
  loadingViews.value = true
  try {
    savedViews.value = await api.getSavedViews()
  } catch (error) {
    console.error('Failed to load saved views', error)
  } finally {
    loadingViews.value = false
  }
}

const applySavedView = (viewId: number | '') => {
  if (!viewId) {
    activeViewId.value = null
    searchQuery.value = ''
    dateFrom.value = ''
    dateTo.value = ''
    return
  }

  const view = savedViews.value.find(v => v.id === viewId)
  if (view) {
    try {
      const filters = JSON.parse(view.filter_json)
      searchQuery.value = filters.q || ''
      dateFrom.value = filters.date_from || ''
      dateTo.value = filters.date_to || ''
      activeViewId.value = view.id
    } catch (e) {
      console.error('Failed to parse saved view filters', e)
    }
  }
}

const saveCurrentView = async () => {
  if (!newViewName.value.trim()) return
  
  isSavingView.value = true
  const filterJson = JSON.stringify({
    q: searchQuery.value,
    date_from: dateFrom.value,
    date_to: dateTo.value
  })

  try {
    const view = await api.createSavedView(newViewName.value, filterJson)
    savedViews.value.push(view)
    activeViewId.value = view.id
    showSaveViewModal.value = false
    newViewName.value = ''
  } catch (error) {
    console.error('Failed to save view', error)
    alert('Failed to save view')
  } finally {
    isSavingView.value = false
  }
}

const deleteSavedView = async (id: number) => {
  try {
    await api.deleteSavedView(id)
    savedViews.value = savedViews.value.filter(v => v.id !== id)
    if (activeViewId.value === id) {
      activeViewId.value = null
    }
  } catch (error) {
    console.error('Failed to delete view', error)
  }
}

const isExporting = ref(false)
const exportCSV = async () => {
  isExporting.value = true
  try {
    const blob = await api.exportInvoicesCSV({
      q: searchQuery.value || undefined,
      date_from: dateFrom.value || undefined,
      date_to: dateTo.value || undefined,
      category: selectedCategories.value.length > 0 ? [...selectedCategories.value] : undefined,
    })
    
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `invoices_export_${new Date().toISOString().split('T')[0]}.csv`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
  } catch (error) {
    console.error('Failed to export CSV', error)
    alert('Failed to export CSV')
  } finally {
    isExporting.value = false
  }
}

const fetchStats = async () => {
  try {
    stats.value = await api.getStats()
  } catch (error) {
    console.error('Failed to load stats', error)
  } finally {
    loadingStats.value = false
  }
}

const searchQuery = ref('')
const dateFrom = ref('')
const dateTo = ref('')
const page = ref(1)
const size = ref(20)

const confirmDialog = ref<InstanceType<typeof ConfirmDialog> | null>(null)
const deletingInvoiceId = ref<number | null>(null)

const fetchInvoices = async () => {
  await invoicesStore.fetchInvoices(
    searchQuery.value,
    dateFrom.value || undefined,
    dateTo.value || undefined,
    page.value,
    size.value,
    [...selectedCategories.value],
  )
}

const debouncedSearch = useDebounceFn(() => {
  page.value = 1
  fetchInvoices()
}, 300)

watch(searchQuery, debouncedSearch)

watch([dateFrom, dateTo, size], () => {
  page.value = 1
  fetchInvoices()
})

function syncCategoryQuery(next: InvoiceCategory[]) {
  const query = { ...route.query }
  if (next.length > 0) {
    query.category = next
  } else {
    delete query.category
  }
  router.replace({ query })
}

watch(selectedCategories, (next, prev) => {
  if (prev && next.length === prev.length && next.every((v, i) => v === prev[i])) {
    return
  }
  invoicesStore.setCategories(next)
  syncCategoryQuery(next)
  page.value = 1
  fetchInvoices()
}, { deep: true })

watch(() => route.query.category, () => {
  const next = parseCategoriesFromQuery()
  if (
    next.length === selectedCategories.value.length &&
    next.every((v, i) => v === selectedCategories.value[i])
  ) {
    return
  }
  selectedCategories.value = next
})

const handlePageChange = (newPage: number) => {
  if (newPage < 1 || newPage > totalPages.value) return
  page.value = newPage
  fetchInvoices()
}

const totalPages = computed(() => Math.ceil(invoicesStore.total / size.value))

const isAllSelected = computed(() => {
  return invoicesStore.invoices.length > 0 && invoicesStore.selectedIds.length === invoicesStore.invoices.length
})

const isIndeterminate = computed(() => {
  return invoicesStore.selectedIds.length > 0 && invoicesStore.selectedIds.length < invoicesStore.invoices.length
})

const toggleAll = () => {
  if (isAllSelected.value) {
    invoicesStore.clearSelection()
  } else {
    invoicesStore.selectAll(invoicesStore.invoices.map(i => i.id))
  }
}

const CURRENCY_SYMBOLS: Record<string, string> = { USD: '$', CNY: '¥', EUR: '€', GBP: '£', JPY: '¥' }

const formatCurrency = (amount: number, currency?: string) => {
  const symbol = CURRENCY_SYMBOLS[currency || 'CNY'] || currency || ''
  return `${symbol}${amount.toFixed(2)}`
}

const formatDate = (dateString: string) => {
  if (!dateString) return '-'
  return dateString.split('T')[0]
}

const viewInvoice = (id: number) => {
  router.push(`/invoices/${id}`)
}

const downloadInvoice = async (id: number) => {
  try {
    const response = await fetch(`/api/v1/invoices/${id}/download`, {
      headers: {
        'Authorization': `Bearer ${authStore.token}`
      }
    })
    
    if (!response.ok) throw new Error('Failed to download invoice')
    
    const invoice = invoicesStore.invoices.find(i => i.id === id)
    const ext = invoice ? api.invoiceExtension(invoice.source_format) : '.pdf'
    const fallbackFilename = invoice 
      ? `${invoice.buyer || 'buyer'}_${invoice.seller || 'seller'}_${invoice.invoice_no || 'invoice'}${ext}` 
      : `invoice_${id}${ext}`
    
    const filename = api.extractFilename(response, fallbackFilename)
    
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    
    a.download = filename
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
  } catch (error) {
    console.error('Failed to download invoice', error)
  }
}

const confirmDelete = (id: number) => {
  deletingInvoiceId.value = id
  confirmDialog.value?.open()
}

const executeDelete = async () => {
  if (deletingInvoiceId.value) {
    try {
      await invoicesStore.deleteInvoice(deletingInvoiceId.value)
      if (invoicesStore.invoices.length === 0 && page.value > 1) {
        page.value--
        fetchInvoices()
      }
      fetchStats()
    } catch (error) {
      console.error('Delete failed', error)
    } finally {
      deletingInvoiceId.value = null
    }
  }
}

const confirmBatchDeleteDialog = ref<InstanceType<typeof ConfirmDialog> | null>(null)
const deletingBatch = ref(false)

const confirmBatchDelete = () => {
  if (invoicesStore.selectedIds.length === 0) return
  confirmBatchDeleteDialog.value?.open()
}

const executeBatchDelete = async () => {
  if (invoicesStore.selectedIds.length === 0) return
  
  deletingBatch.value = true
  try {
    await api.batchDelete(invoicesStore.selectedIds)
    invoicesStore.clearSelection()
    fetchInvoices()
    fetchStats()
  } catch (error) {
    console.error('Batch delete failed', error)
  } finally {
    deletingBatch.value = false
  }
}

const downloadingBatch = ref(false)
const batchDownload = async () => {
  if (invoicesStore.selectedIds.length === 0) return
  
  downloadingBatch.value = true
  try {
    const blob = await api.batchDownload(invoicesStore.selectedIds)
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `invoices_batch_${new Date().toISOString().split('T')[0]}.zip`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
    invoicesStore.clearSelection()
  } catch (error) {
    console.error('Batch download failed', error)
  } finally {
    downloadingBatch.value = false
  }
}

const clearDates = () => {
  dateFrom.value = ''
  dateTo.value = ''
}

const categoryBreakdown = computed<{ category: InvoiceCategory; count: number }[]>(() => {
  const counts: Record<InvoiceCategory, number> = {
    vat_invoice: 0,
    overseas_invoice: 0,
    receipt: 0,
    proforma: 0,
    other: 0,
  }
  for (const point of stats.value?.by_category ?? []) {
    if (CATEGORY_VALUES.has(point.category)) {
      counts[point.category] = point.count
    }
  }
  return CATEGORY_ORDER.map(category => ({ category, count: counts[category] }))
})

onMounted(() => {
  invoicesStore.setCategories([...selectedCategories.value])
  fetchStats()
  fetchInvoices()
  fetchSavedViews()
})
</script>

<template>
  <AppLayout>
    <div class="space-y-6">
      <div v-if="!loadingStats && stats" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
          <p class="text-sm font-medium text-slate-500">Total Invoices</p>
          <p class="mt-1 text-2xl font-semibold text-slate-900">{{ stats.total_invoices }}</p>
        </div>
        <div
          v-for="cb in (stats.by_currency || [])"
          :key="cb.currency"
          class="bg-white p-4 rounded-xl shadow-sm border border-slate-200"
        >
          <p class="text-sm font-medium text-slate-500">{{ cb.currency === 'CNY' ? '¥ CNY' : cb.currency === 'USD' ? '$ USD' : cb.currency }} Total</p>
          <p class="mt-1 text-2xl font-semibold text-slate-900">{{ formatCurrency(cb.total, cb.currency) }}</p>
          <p class="text-xs text-slate-400 mt-1">{{ cb.count }} invoices</p>
        </div>
        <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
          <p class="text-sm font-medium text-slate-500">This Month</p>
          <p class="mt-1 text-2xl font-semibold text-slate-900">{{ stats.invoices_this_month }}</p>
          <p class="text-xs text-slate-400 mt-1">new invoices</p>
        </div>
        <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200">
          <p class="text-sm font-medium text-slate-500">Active Accounts</p>
          <p class="mt-1 text-2xl font-semibold text-slate-900">{{ stats.active_accounts }}</p>
          <p class="text-xs text-slate-400 mt-1" v-if="stats.last_scan_at">Last scan: {{ formatDate(stats.last_scan_at) }}</p>
        </div>
      </div>

      <div
        v-if="!loadingStats && stats && (stats.total_invoices > 0 || (stats.by_category && stats.by_category.length > 0))"
        class="bg-white p-4 rounded-xl shadow-sm border border-slate-200"
        data-test-id="stats-by-category"
      >
        <p class="text-sm font-medium text-slate-500 mb-3">By Category</p>
        <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <div
            v-for="bucket in categoryBreakdown"
            :key="bucket.category"
            class="flex items-center justify-between gap-2 px-3 py-2 rounded-lg border border-slate-200 bg-slate-50"
            :data-test-id="`stats-category-${bucket.category}`"
          >
            <CategoryBadge :category="bucket.category" />
            <span
              class="text-sm font-semibold"
              :class="bucket.count === 0 ? 'text-slate-400' : 'text-slate-900'"
            >
              {{ bucket.count === 0 ? '— (0)' : bucket.count }}
            </span>
          </div>
        </div>
      </div>

      <div v-if="!loadingStats && stats?.total_invoices === 0 && stats?.active_accounts === 0" class="bg-gradient-to-br from-blue-50 to-indigo-50 rounded-xl p-8 border border-blue-100 shadow-sm text-center">
        <div class="mx-auto w-16 h-16 bg-white rounded-full flex items-center justify-center shadow-sm mb-4">
          <svg class="w-8 h-8 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
        </div>
        <h2 class="text-2xl font-bold text-slate-900 mb-2">Welcome to Invoice Maid</h2>
        <p class="text-slate-600 max-w-md mx-auto mb-6">To get started, configure an email account. Invoice Maid will automatically scan it for invoices and extract the data.</p>
        <button @click="router.push('/settings')" class="inline-flex items-center px-6 py-3 border border-transparent text-base font-medium rounded-lg shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors">
          Go to Settings
          <svg class="ml-2 -mr-1 w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>
        </button>
      </div>

      <div v-else-if="!loadingStats && stats?.total_invoices === 0 && stats?.active_accounts > 0" class="bg-white rounded-xl p-8 border border-slate-200 shadow-sm text-center">
        <div class="mx-auto w-16 h-16 bg-slate-50 rounded-full flex items-center justify-center border border-slate-100 mb-4">
          <svg class="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
        </div>
        <h2 class="text-xl font-semibold text-slate-900 mb-2">No Invoices Found Yet</h2>
        <p class="text-slate-500 max-w-md mx-auto mb-6">Accounts are configured, but no invoices have been extracted. You can wait for the next scheduled scan or trigger one manually.</p>
        <div class="flex items-center justify-center gap-4">
          <button @click="router.push('/settings')" class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-lg shadow-sm text-white bg-blue-600 hover:bg-blue-700 transition-colors">
            Manage Scans
          </button>
        </div>
      </div>

      <div v-else class="flex flex-col gap-4 bg-white p-4 rounded-xl shadow-sm border border-slate-200">
        <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 w-full">
          <div class="flex items-center gap-3 w-full sm:w-auto">
            <select
              :value="activeViewId || ''"
              @change="e => applySavedView(Number((e.target as HTMLSelectElement).value) || '')"
              class="block w-full sm:w-48 pl-3 pr-8 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 bg-white transition-colors"
            >
              <option value="">All Invoices</option>
              <option v-for="view in savedViews" :key="view.id" :value="view.id">{{ view.name }}</option>
            </select>
            <button
              v-if="searchQuery || dateFrom || dateTo"
              @click="showSaveViewModal = true"
              class="text-sm font-medium text-blue-600 hover:text-blue-800 whitespace-nowrap"
            >
              Save View
            </button>
            <button
              v-if="activeViewId"
              @click="deleteSavedView(activeViewId)"
              class="p-2 text-slate-400 hover:text-red-600 transition-colors rounded-full hover:bg-red-50"
              title="Delete View"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
            </button>
          </div>

          <div class="flex items-center justify-end w-full sm:w-auto">
            <button
              @click="exportCSV"
              :disabled="isExporting"
              class="inline-flex items-center px-4 py-2 border border-slate-300 shadow-sm text-sm font-medium rounded-lg text-slate-700 bg-white hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 transition-colors"
            >
              <svg v-if="isExporting" class="animate-spin -ml-1 mr-2 h-4 w-4 text-slate-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
              <svg v-else class="-ml-1 mr-2 h-4 w-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
              {{ isExporting ? 'Exporting...' : 'Export CSV' }}
            </button>
          </div>
        </div>

        <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
          <div class="relative w-full sm:w-96">
            <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
              <svg class="h-5 w-5 text-slate-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd" />
              </svg>
            </div>
            <input
              type="text"
              v-model="searchQuery"
              class="block w-full pl-10 pr-3 py-2 border border-slate-300 rounded-lg leading-5 bg-white placeholder-slate-500 focus:outline-none focus:placeholder-slate-400 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm transition-colors"
              placeholder="Search invoices..."
            />
          </div>

          <div class="flex items-center gap-2 w-full sm:w-auto">
            <input
              type="date"
              v-model="dateFrom"
              class="block w-full sm:w-auto pl-3 pr-3 py-2 border border-slate-300 rounded-lg leading-5 bg-white text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm transition-colors"
            />
            <span class="text-slate-500">-</span>
            <input
              type="date"
              v-model="dateTo"
              class="block w-full sm:w-auto pl-3 pr-3 py-2 border border-slate-300 rounded-lg leading-5 bg-white text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm transition-colors"
            />
            <button
              v-if="dateFrom || dateTo"
              @click="clearDates"
              class="p-2 text-slate-400 hover:text-slate-600 transition-colors rounded-full hover:bg-slate-100"
              title="Clear dates"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
            </button>
          </div>
        </div>

        <div class="pt-2 border-t border-slate-100" data-test-id="category-filter">
          <MultiSelectChips
            v-model="selectedCategories"
            :options="categoryOptions"
            label="Category"
          />
        </div>
      </div>

      <!-- Batch Actions -->
      <div v-if="invoicesStore.selectedIds.length > 0" class="flex items-center justify-between bg-blue-50 p-4 rounded-xl border border-blue-100 transition-all duration-300">
        <div class="flex items-center gap-4">
          <span class="text-sm text-blue-800 font-medium">
            {{ invoicesStore.selectedIds.length }} selected
          </span>
          <button @click="invoicesStore.clearSelection" class="text-sm text-blue-600 hover:text-blue-800 hover:underline transition-colors">
            Clear selection
          </button>
        </div>
        <div class="flex items-center gap-3">
          <button
            @click="confirmBatchDelete"
            :disabled="deletingBatch"
            class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-lg shadow-sm text-white bg-red-600 hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <svg v-if="deletingBatch" class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <svg v-else class="-ml-1 mr-2 h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
            Delete
          </button>
          <button
            @click="batchDownload"
            :disabled="downloadingBatch"
            class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-lg shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <svg v-if="downloadingBatch" class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <svg v-else class="-ml-1 mr-2 h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
            Download
          </button>
        </div>
      </div>

      <!-- Table -->
      <div class="bg-white shadow-sm rounded-xl border border-slate-200 overflow-hidden">
        <div class="overflow-x-auto">
          <table class="min-w-full divide-y divide-slate-200">
            <thead class="bg-slate-50">
              <tr>
                <th scope="col" class="px-6 py-3 text-left">
                  <input
                    type="checkbox"
                    :checked="isAllSelected"
                    :indeterminate="isIndeterminate"
                    @change="toggleAll"
                    class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-slate-300 rounded transition-colors"
                  />
                </th>
                <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Invoice No</th>
                <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Buyer</th>
                <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Seller</th>
                <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Amount</th>
                <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Date</th>
                <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Category</th>
                <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Type</th>
                <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Summary</th>
                <th scope="col" class="relative px-6 py-3">
                  <span class="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody class="bg-white divide-y divide-slate-200">
              <tr v-if="invoicesStore.loading" v-for="i in 5" :key="i" class="animate-pulse">
                <td class="px-6 py-4 whitespace-nowrap"><div class="h-4 bg-slate-200 rounded w-4"></div></td>
                <td class="px-6 py-4 whitespace-nowrap"><div class="h-4 bg-slate-200 rounded w-24"></div></td>
                <td class="px-6 py-4 whitespace-nowrap"><div class="h-4 bg-slate-200 rounded w-32"></div></td>
                <td class="px-6 py-4 whitespace-nowrap"><div class="h-4 bg-slate-200 rounded w-32"></div></td>
                <td class="px-6 py-4 whitespace-nowrap"><div class="h-4 bg-slate-200 rounded w-16"></div></td>
                <td class="px-6 py-4 whitespace-nowrap"><div class="h-4 bg-slate-200 rounded w-24"></div></td>
                <td class="px-6 py-4 whitespace-nowrap"><div class="h-4 bg-slate-200 rounded w-20"></div></td>
                <td class="px-6 py-4 whitespace-nowrap"><div class="h-4 bg-slate-200 rounded w-20"></div></td>
                <td class="px-6 py-4 whitespace-nowrap"><div class="h-4 bg-slate-200 rounded w-40"></div></td>
                <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                  <div class="h-4 bg-slate-200 rounded w-16 inline-block"></div>
                </td>
              </tr>
              
              <tr v-else-if="invoicesStore.invoices.length === 0">
                <td colspan="10" class="px-6 py-16 text-center text-slate-500">
                  <div class="flex flex-col items-center justify-center">
                    <svg class="w-12 h-12 text-slate-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                    <p class="text-base font-medium text-slate-900">No invoices match your search</p>
                    <p class="text-sm mt-1 text-slate-500 max-w-sm mb-4">Try adjusting your search terms or clearing the date filters to see more results.</p>
                    <button
                      v-if="searchQuery || dateFrom || dateTo"
                      @click="searchQuery = ''; clearDates()"
                      class="inline-flex items-center px-4 py-2 border border-slate-300 shadow-sm text-sm font-medium rounded-lg text-slate-700 bg-white hover:bg-slate-50 transition-colors"
                    >
                      Clear Filters
                    </button>
                  </div>
                </td>
              </tr>

              <tr v-else v-for="invoice in invoicesStore.invoices" :key="invoice.id" class="hover:bg-slate-50 transition-colors" :data-test-id="`invoice-row-${invoice.id}`">
                <td class="px-6 py-4 whitespace-nowrap">
                  <input
                    type="checkbox"
                    :checked="invoicesStore.selectedIds.includes(invoice.id)"
                    @change="invoicesStore.toggleSelection(invoice.id)"
                    class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-slate-300 rounded transition-colors"
                  />
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-slate-900">{{ invoice.invoice_no }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{{ invoice.buyer }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{{ invoice.seller }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-slate-900">{{ formatCurrency(invoice.amount, invoice.currency) }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{{ formatDate(invoice.invoice_date) }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm">
                  <CategoryBadge :category="invoice.invoice_category" />
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-500">
                  <span
                    class="px-2.5 py-0.5 inline-flex text-xs leading-5 font-semibold rounded-full border"
                    :class="invoice.invoice_type.toLowerCase().includes('增值税专用发票') ? 'bg-indigo-50 text-indigo-700 border-indigo-200' : 'bg-slate-100 text-slate-700 border-slate-200'"
                  >
                    {{ invoice.invoice_type }}
                  </span>
                </td>
                <td class="px-6 py-4 text-sm text-slate-500 max-w-xs truncate" :title="invoice.item_summary || ''">{{ invoice.item_summary || '-' }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium space-x-3">
                  <button @click="viewInvoice(invoice.id)" class="text-blue-600 hover:text-blue-900 transition-colors">View</button>
                  <button @click="downloadInvoice(invoice.id)" class="text-green-600 hover:text-green-900 transition-colors">Download</button>
                  <button @click="confirmDelete(invoice.id)" class="text-red-600 hover:text-red-900 transition-colors">Delete</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Pagination -->
        <div class="bg-white px-4 py-3 flex items-center justify-between border-t border-slate-200 sm:px-6">
          <div class="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
            <div class="flex items-center gap-4">
              <p class="text-sm text-slate-700">
                Showing
                <span class="font-medium">{{ (page - 1) * size + (invoicesStore.invoices.length > 0 ? 1 : 0) }}</span>
                to
                <span class="font-medium">{{ Math.min(page * size, invoicesStore.total) }}</span>
                of
                <span class="font-medium">{{ invoicesStore.total }}</span>
                results
              </p>
              <select v-model="size" class="block w-20 pl-3 pr-8 py-1.5 text-sm border-slate-300 focus:outline-none focus:ring-blue-500 focus:border-blue-500 rounded-md transition-colors">
                <option :value="20">20</option>
                <option :value="50">50</option>
                <option :value="100">100</option>
              </select>
            </div>
            <div>
              <nav class="relative z-0 inline-flex rounded-md shadow-sm -space-x-px" aria-label="Pagination">
                <button
                  @click="handlePageChange(page - 1)"
                  :disabled="page === 1"
                  class="relative inline-flex items-center px-2 py-2 rounded-l-md border border-slate-300 bg-white text-sm font-medium text-slate-500 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <span class="sr-only">Previous</span>
                  <svg class="h-5 w-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path fill-rule="evenodd" d="M12.707 5.293a1 1 0 010 1.414L9.414 10l3.293 3.293a1 1 0 01-1.414 1.414l-4-4a1 1 0 010-1.414l4-4a1 1 0 011.414 0z" clip-rule="evenodd" />
                  </svg>
                </button>
                <span class="relative inline-flex items-center px-4 py-2 border border-slate-300 bg-white text-sm font-medium text-slate-700">
                  Page {{ page }} of {{ totalPages || 1 }}
                </span>
                <button
                  @click="handlePageChange(page + 1)"
                  :disabled="page >= totalPages"
                  class="relative inline-flex items-center px-2 py-2 rounded-r-md border border-slate-300 bg-white text-sm font-medium text-slate-500 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <span class="sr-only">Next</span>
                  <svg class="h-5 w-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path fill-rule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clip-rule="evenodd" />
                  </svg>
                </button>
              </nav>
            </div>
          </div>
        </div>
      </div>
    </div>

    <ConfirmDialog
      ref="confirmDialog"
      title="Delete Invoice"
      message="Are you sure you want to delete this invoice? This action cannot be undone."
      confirmText="Delete"
      @confirm="executeDelete"
    />
    <ConfirmDialog
      ref="confirmBatchDeleteDialog"
      title="Delete Selected Invoices"
      message="Are you sure you want to delete the selected invoices? This action cannot be undone."
      confirmText="Delete All"
      @confirm="executeBatchDelete"
    />

    <!-- Save View Modal -->
    <div v-if="showSaveViewModal" class="fixed inset-0 z-10 overflow-y-auto" aria-labelledby="modal-title" role="dialog" aria-modal="true">
      <div class="flex items-end justify-center min-h-screen pt-4 px-4 pb-20 text-center sm:block sm:p-0">
        <div class="fixed inset-0 bg-slate-500 bg-opacity-75 transition-opacity" aria-hidden="true" @click="showSaveViewModal = false"></div>
        <span class="hidden sm:inline-block sm:align-middle sm:h-screen" aria-hidden="true">&#8203;</span>
        <div class="inline-block align-bottom bg-white rounded-xl text-left overflow-hidden shadow-xl transform transition-all sm:my-8 sm:align-middle sm:max-w-lg sm:w-full">
          <form @submit.prevent="saveCurrentView">
            <div class="bg-white px-4 pt-5 pb-4 sm:p-6 sm:pb-4">
              <div class="sm:flex sm:items-start">
                <div class="mt-3 text-center sm:mt-0 sm:text-left w-full">
                  <h3 class="text-lg leading-6 font-medium text-slate-900" id="modal-title">
                    Save Current View
                  </h3>
                  <div class="mt-4">
                    <label class="block text-sm font-medium text-slate-700 mb-1">View Name</label>
                    <input type="text" v-model="newViewName" required class="focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border" placeholder="e.g. Q3 Expenses">
                  </div>
                  <div class="mt-4 bg-slate-50 p-3 rounded text-sm text-slate-600">
                    <p class="font-medium mb-1">Saved Filters:</p>
                    <ul class="list-disc pl-5 space-y-1">
                      <li v-if="searchQuery">Search: "{{ searchQuery }}"</li>
                      <li v-if="dateFrom || dateTo">Date: {{ dateFrom || 'Any' }} to {{ dateTo || 'Any' }}</li>
                    </ul>
                  </div>
                </div>
              </div>
            </div>
            <div class="bg-slate-50 px-4 py-3 sm:px-6 sm:flex sm:flex-row-reverse">
              <button type="submit" :disabled="isSavingView || !newViewName.trim()" class="w-full inline-flex justify-center rounded-md border border-transparent shadow-sm px-4 py-2 bg-blue-600 text-base font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-50 transition-colors">
                {{ isSavingView ? 'Saving...' : 'Save View' }}
              </button>
              <button type="button" @click="showSaveViewModal = false" class="mt-3 w-full inline-flex justify-center rounded-md border border-slate-300 shadow-sm px-4 py-2 bg-white text-base font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 sm:mt-0 sm:ml-3 sm:w-auto sm:text-sm transition-colors">
                Cancel
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
