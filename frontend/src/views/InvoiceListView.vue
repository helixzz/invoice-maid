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
  REIMBURSEMENT_COLORS,
  REIMBURSEMENT_LABELS,
  REIMBURSEMENT_ORDER,
  type InvoiceCategory,
  type ReimbursementStatus,
  type StatsResponse,
  type SavedView,
} from '@/types'

const router = useRouter()
const route = useRoute()
const invoicesStore = useInvoicesStore()
const authStore = useAuthStore()

const CATEGORY_VALUES = new Set<InvoiceCategory>(CATEGORY_ORDER)
const REIMBURSEMENT_VALUES = new Set<ReimbursementStatus>(REIMBURSEMENT_ORDER)

const categoryOptions = CATEGORY_ORDER.map(value => ({
  value,
  label: CATEGORY_LABELS[value],
}))

const reimbursementOptions = REIMBURSEMENT_ORDER.map(value => ({
  value,
  label: REIMBURSEMENT_LABELS[value],
}))

function toReimbursementList(raw: unknown): ReimbursementStatus[] {
  const list = Array.isArray(raw) ? raw : raw ? [raw] : []
  const result: ReimbursementStatus[] = []
  for (const entry of list) {
    if (typeof entry === 'string' && REIMBURSEMENT_VALUES.has(entry as ReimbursementStatus)) {
      result.push(entry as ReimbursementStatus)
    }
  }
  return result
}

function toCategoryList(raw: unknown): InvoiceCategory[] {
  const list = Array.isArray(raw) ? raw : raw ? [raw] : []
  const result: InvoiceCategory[] = []
  for (const entry of list) {
    if (typeof entry === 'string' && CATEGORY_VALUES.has(entry as InvoiceCategory)) {
      result.push(entry as InvoiceCategory)
    }
  }
  return result
}

function parseCategoriesFromQuery(): InvoiceCategory[] {
  return toCategoryList(route.query.category)
}

interface ViewFilters {
  q: string
  dateFrom: string
  dateTo: string
  categories: InvoiceCategory[]
  reimbursementStatuses: ReimbursementStatus[]
}

// Views saved before categories existed have no `category` key at all;
// toCategoryList turns that (and any stale value) into an empty filter.
function parseViewFilters(view: SavedView): ViewFilters {
  const filters = JSON.parse(view.filter_json)
  return {
    q: filters.q || '',
    dateFrom: filters.date_from || '',
    dateTo: filters.date_to || '',
    categories: toCategoryList(filters.category),
    reimbursementStatuses: toReimbursementList(filters.reimbursement_status),
  }
}

// Order-insensitive: the same categories picked in a different order are the
// same filter, so they must not count as a divergence.
function filterSignature(filters: ViewFilters): string {
  return JSON.stringify([
    filters.q,
    filters.dateFrom,
    filters.dateTo,
    [...filters.categories].sort(),
    [...filters.reimbursementStatuses].sort(),
  ])
}

const selectedCategories = ref<InvoiceCategory[]>(parseCategoriesFromQuery())
const selectedReimbursementStatuses = ref<ReimbursementStatus[]>([])

const stats = ref<StatsResponse | null>(null)
const loadingStats = ref(true)
const showDetailedStats = ref(false)

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

const clearAllFilters = () => {
  activeViewId.value = null
  searchQuery.value = ''
  dateFrom.value = ''
  dateTo.value = ''
  selectedCategories.value = []
  selectedReimbursementStatuses.value = []
}

const applySavedView = (viewId: number | '') => {
  if (!viewId) {
    clearAllFilters()
    return
  }

  const view = savedViews.value.find(v => v.id === viewId)
  if (view) {
    try {
      const filters = parseViewFilters(view)
      searchQuery.value = filters.q
      dateFrom.value = filters.dateFrom
      dateTo.value = filters.dateTo
      selectedCategories.value = filters.categories
      selectedReimbursementStatuses.value = filters.reimbursementStatuses
      activeViewId.value = view.id
    } catch (e) {
      console.error('Failed to parse saved view filters', e)
    }
  }
}

// Applying a view sets every filter from parseViewFilters, so a freshly
// applied view always matches its own signature and survives this check;
// only a later manual edit diverges and falls back to All Invoices.
const releaseActiveViewIfDiverged = () => {
  const view = savedViews.value.find(v => v.id === activeViewId.value)
  if (!view) return
  try {
    const current: ViewFilters = {
      q: searchQuery.value,
      dateFrom: dateFrom.value,
      dateTo: dateTo.value,
      categories: selectedCategories.value,
      reimbursementStatuses: selectedReimbursementStatuses.value,
    }
    if (filterSignature(current) !== filterSignature(parseViewFilters(view))) {
      activeViewId.value = null
    }
  } catch (e) {
    console.error('Failed to parse saved view filters', e)
  }
}

const saveCurrentView = async () => {
  if (!newViewName.value.trim()) return

  isSavingView.value = true
  const filterJson = JSON.stringify({
    q: searchQuery.value,
    date_from: dateFrom.value,
    date_to: dateTo.value,
    category: [...selectedCategories.value],
    reimbursement_status: [...selectedReimbursementStatuses.value],
  })

  try {
    const view = await api.createSavedView(newViewName.value.trim(), filterJson)
    savedViews.value.push(view)
    activeViewId.value = view.id
    showSaveViewModal.value = false
    newViewName.value = ''
  } catch (error) {
    console.error('Failed to save view', error)
    alert('保存视图失败')
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
      reimbursement_status: selectedReimbursementStatuses.value.length > 0 ? [...selectedReimbursementStatuses.value] : undefined,
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
    alert('导出 CSV 失败')
  } finally {
    isExporting.value = false
  }
}

const exportXLSX = async () => {
  isExporting.value = true
  try {
    const blob = await api.exportInvoicesXLSX({
      q: searchQuery.value || undefined,
      date_from: dateFrom.value || undefined,
      date_to: dateTo.value || undefined,
      category: selectedCategories.value.length > 0 ? [...selectedCategories.value] : undefined,
      reimbursement_status: selectedReimbursementStatuses.value.length > 0 ? [...selectedReimbursementStatuses.value] : undefined,
    })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `invoices_reimbursement_${new Date().toISOString().split('T')[0]}.xlsx`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
  } catch (error) {
    console.error('Failed to export Excel', error)
    alert('导出 Excel 失败')
  } finally {
    isExporting.value = false
  }
}

const updatingReimbursement = ref(false)
const batchSetReimbursement = async (status: ReimbursementStatus) => {
  if (invoicesStore.selectedIds.length === 0) return
  updatingReimbursement.value = true
  try {
    await api.batchUpdateReimbursement(invoicesStore.selectedIds, { reimbursement_status: status })
    await fetchInvoices()
    invoicesStore.clearSelection()
  } catch (error) {
    console.error('Failed to update reimbursement status', error)
    alert('批量更新报销状态失败')
  } finally {
    updatingReimbursement.value = false
  }
}

const downloadingReimbursementPack = ref(false)
const downloadReimbursementPack = async () => {
  if (invoicesStore.selectedIds.length === 0) return
  downloadingReimbursementPack.value = true
  try {
    const blob = await api.downloadReimbursementPack(invoicesStore.selectedIds)
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `reimbursement_pack_${new Date().toISOString().split('T')[0]}.zip`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
  } catch (error) {
    console.error('Failed to download reimbursement pack', error)
    alert('导出报销包失败')
  } finally {
    downloadingReimbursementPack.value = false
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
    [...selectedReimbursementStatuses.value],
  )
}

const debouncedSearch = useDebounceFn(() => {
  page.value = 1
  fetchInvoices()
}, 300)

watch(searchQuery, () => {
  releaseActiveViewIfDiverged()
  debouncedSearch()
})

watch([dateFrom, dateTo, size], () => {
  releaseActiveViewIfDiverged()
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
  releaseActiveViewIfDiverged()
  invoicesStore.setCategories(next)
  syncCategoryQuery(next)
  page.value = 1
  fetchInvoices()
}, { deep: true })

watch(selectedReimbursementStatuses, (next, prev) => {
  if (prev && next.length === prev.length && next.every((v, i) => v === prev[i])) {
    return
  }
  releaseActiveViewIfDiverged()
  invoicesStore.setReimbursementStatuses(next)
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

const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$',
  CNY: '¥',
  EUR: '€',
  GBP: '£',
  JPY: '¥',
  HKD: 'HK$',
  SGD: 'S$',
  CAD: 'C$',
  AUD: 'A$',
}

const CURRENCY_NAMES: Record<string, string> = {
  CNY: '人民币账本汇总',
  USD: '美元账本汇总',
  EUR: '欧元账本汇总',
  GBP: '英镑账本汇总',
  JPY: '日元账本汇总',
  HKD: '港币账本汇总',
  SGD: '新加坡元账本汇总',
  CAD: '加元账本汇总',
  AUD: '澳元账本汇总',
}

const currencyTitle = (currency: string) => CURRENCY_NAMES[currency] || `${currency} 账本汇总`

const currencyBadgeClass = (currency: string) => {
  if (currency === 'CNY') return 'bg-mint-50 text-mint-800 border-mint-200'
  if (currency === 'USD') return 'bg-[#F3EFFA] text-[#5B4583] border-[#DDD3F0]'
  if (currency === 'EUR' || currency === 'GBP') return 'bg-[#FDF5E6] text-[#8B5E22] border-[#EEDAB6]'
  return 'bg-cream-300/70 text-ink-700 border-cream-500'
}

const formatCurrency = (amount: number, currency?: string) => {
  const code = currency || 'CNY'
  const symbol = CURRENCY_SYMBOLS[code] || code
  const formatted = amount.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return `${symbol} ${formatted}`
}

const formatDate = (dateString: string) => {
  if (!dateString) return '—'
  return dateString.split('T')[0]
}

const copiedInvoiceNo = ref<string | null>(null)
const copyInvoiceNo = async (invoiceNo: string) => {
  if (!invoiceNo) return
  try {
    await navigator.clipboard.writeText(invoiceNo)
    copiedInvoiceNo.value = invoiceNo
    window.setTimeout(() => {
      if (copiedInvoiceNo.value === invoiceNo) {
        copiedInvoiceNo.value = null
      }
    }, 1500)
  } catch {
    // Clipboard may be unavailable in non-secure contexts
  }
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

const hasActiveFilters = computed(
  () =>
    Boolean(searchQuery.value || dateFrom.value || dateTo.value) ||
    selectedCategories.value.length > 0 ||
    selectedReimbursementStatuses.value.length > 0,
)

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

const toggleCategoryBucket = (category: InvoiceCategory) => {
  const next = new Set(selectedCategories.value)
  if (next.has(category)) {
    next.delete(category)
  } else {
    next.add(category)
  }
  selectedCategories.value = [...next]
}

const monthlySpendPivot = computed(() => {
  const result: { month: string; [currency: string]: string | number }[] = []
  const perCurrency = stats.value?.monthly_spend_by_currency ?? []
  if (!perCurrency.length) return []
  const allMonths = new Set<string>()
  for (const gb of perCurrency) {
    for (const pt of gb.monthly_spend) {
      allMonths.add(pt.month)
    }
  }
  for (const month of [...allMonths].sort()) {
    const row = { month } as { month: string; [currency: string]: string | number }
    for (const gb of perCurrency) {
      const pt = gb.monthly_spend.find((p: { month: string }) => p.month === month)
      row[gb.currency] = pt ? pt.total : 0
    }
    result.push(row)
  }
  return result
})

const hasDetailedStats = computed(() => {
  if (!stats.value) return false
  return (
    stats.value.total_invoices > 0 ||
    Boolean(stats.value.by_category?.length) ||
    Boolean(stats.value.monthly_spend_by_currency?.length)
  )
})

const selectedCurrencyTotals = computed(() => {
  if (invoicesStore.selectedIds.length === 0) return []
  const selectedSet = new Set(invoicesStore.selectedIds)
  const totals = new Map<string, number>()
  for (const inv of invoicesStore.invoices) {
    if (!selectedSet.has(inv.id)) continue
    const code = inv.currency || 'CNY'
    totals.set(code, (totals.get(code) || 0) + Number(inv.amount || 0))
  }
  return [...totals.entries()].map(([currency, total]) => ({ currency, total }))
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
    <div class="space-y-4" :class="invoicesStore.selectedIds.length > 0 ? 'pb-24' : ''">
      <!-- Top Compact Multi-Currency Ledger Ribbon -->
      <section v-if="!loadingStats && stats" class="space-y-3">
        <div
          class="grid grid-cols-1 md:grid-cols-2 gap-4"
          :class="(stats.by_currency?.length || 0) >= 2 ? 'xl:grid-cols-3' : 'xl:grid-cols-2'"
        >
          <!-- Real Multi-Currency Summary Cards -->
          <div
            v-for="cb in (stats.by_currency || [])"
            :key="cb.currency"
            class="ledger-card px-5 py-4 flex flex-col justify-between"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="text-xs font-medium text-ink-500">{{ currencyTitle(cb.currency) }}</span>
              <span
                class="inline-flex items-center px-2 py-0.5 rounded-full border text-[11px] font-semibold num-mono"
                :class="currencyBadgeClass(cb.currency)"
              >
                {{ cb.currency }} · {{ cb.count }} 张
              </span>
            </div>
            <div class="mt-2.5 text-2xl sm:text-[26px] font-bold tracking-tight text-ink-900 num-mono leading-tight">
              {{ formatCurrency(cb.total, cb.currency) }}
            </div>
            <div class="mt-3 pt-2.5 receipt-dashed flex items-center justify-between text-xs text-ink-400">
              <span>原币种精准记账</span>
              <span class="num-mono text-ink-500">
                占比 {{ stats.total_invoices ? Math.round((cb.count / stats.total_invoices) * 100) : 0 }}%
              </span>
            </div>
          </div>

          <!-- Total & This Month & Sync Overview Card -->
          <div class="ledger-card px-5 py-4 bg-gradient-to-br from-cream-100 via-cream-100 to-peach-50/70 flex flex-col justify-between">
            <div class="flex items-center justify-between gap-2">
              <span class="text-xs font-medium text-ink-500">账本概览</span>
              <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-cream-500 bg-cream-50 text-[11px] font-medium text-ink-700 num-mono">
                <span class="w-1.5 h-1.5 rounded-full" :class="stats.active_accounts > 0 ? 'bg-mint-500' : 'bg-ink-300'"></span>
                邮箱 {{ stats.active_accounts }} 个
              </span>
            </div>

            <div class="mt-2.5 flex items-baseline gap-6">
              <div>
                <div class="text-2xl sm:text-[26px] font-bold text-ink-900 num-mono leading-tight">
                  {{ stats.total_invoices }}
                  <span class="text-xs font-normal text-ink-500 ml-0.5">张总票据</span>
                </div>
              </div>
              <div class="border-l border-cream-400 pl-4">
                <div class="text-lg font-bold text-mint-700 num-mono leading-tight">
                  +{{ stats.invoices_this_month }}
                  <span class="text-xs font-normal text-ink-500 ml-0.5">本月新增</span>
                </div>
              </div>
            </div>

            <div class="mt-3 pt-2.5 receipt-dashed flex items-center justify-between gap-2 text-xs">
              <span class="text-ink-400 num-mono truncate">
                {{ stats.last_scan_at ? `上次扫描 ${formatDate(stats.last_scan_at)}` : '尚未执行邮箱扫描' }}
              </span>
              <button
                v-if="hasDetailedStats"
                type="button"
                @click="showDetailedStats = !showDetailedStats"
                class="inline-flex items-center gap-1 font-medium text-mint-700 hover:text-mint-900 whitespace-nowrap transition-colors"
                :aria-expanded="showDetailedStats"
              >
                <span>{{ showDetailedStats ? '收起月度与分类统计' : '展开月度与分类统计' }}</span>
                <svg
                  class="w-3.5 h-3.5 transition-transform duration-200"
                  :class="showDetailedStats ? 'rotate-180' : ''"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
            </div>
          </div>
        </div>

        <!-- Collapsible Monthly & Category Stats Drawer (Collapsed by default) -->
        <div
          v-show="showDetailedStats && hasDetailedStats"
          class="space-y-3"
        >
          <div
            v-if="stats.total_invoices > 0 || (stats.by_category && stats.by_category.length > 0)"
            class="ledger-card p-4"
            data-test-id="stats-by-category"
          >
            <div class="flex items-center justify-between mb-3">
              <span class="text-xs font-semibold text-ink-500">按分类统计</span>
              <span class="text-[11px] text-ink-400">点击分类可快速筛选</span>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5">
              <button
                v-for="bucket in categoryBreakdown"
                :key="bucket.category"
                type="button"
                @click="toggleCategoryBucket(bucket.category)"
                class="flex items-center justify-between gap-2 px-3 py-2 rounded-xl border transition-colors text-left"
                :class="selectedCategories.includes(bucket.category)
                  ? 'border-mint-500 bg-mint-50/70'
                  : 'border-cream-400 bg-cream-50 hover:bg-cream-200/70'"
                :data-test-id="`stats-category-${bucket.category}`"
              >
                <CategoryBadge :category="bucket.category" />
                <span
                  class="text-sm font-semibold num-mono"
                  :class="bucket.count === 0 ? 'text-ink-300' : 'text-ink-900'"
                >
                  {{ bucket.count === 0 ? '— (0)' : bucket.count }}
                </span>
              </button>
            </div>
          </div>

          <div
            v-if="stats.monthly_spend_by_currency?.length"
            class="ledger-card p-4"
          >
            <p class="text-xs font-semibold text-ink-500 mb-3">月度分币种明细</p>
            <div class="overflow-x-auto">
              <table class="min-w-full divide-y divide-cream-400 text-sm">
                <thead>
                  <tr class="bg-cream-300/60 text-left text-xs font-semibold text-ink-500">
                    <th class="px-3 py-2 rounded-l-lg">月份</th>
                    <th
                      v-for="(gb, idx) in stats.monthly_spend_by_currency"
                      :key="gb.currency"
                      class="px-3 py-2 text-right num-mono"
                      :class="idx === stats.monthly_spend_by_currency.length - 1 ? 'rounded-r-lg' : ''"
                    >
                      {{ gb.currency }}
                    </th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-cream-400/70">
                  <tr v-for="mp in monthlySpendPivot" :key="mp.month" class="hover:bg-cream-200/60 transition-colors">
                    <td class="px-3 py-2 font-medium text-ink-700 num-mono">{{ mp.month }}</td>
                    <td
                      v-for="gb in stats.monthly_spend_by_currency"
                      :key="gb.currency"
                      class="px-3 py-2 text-right num-mono text-ink-900"
                    >
                      <span v-if="mp[gb.currency]">{{ formatCurrency(Number(mp[gb.currency]), gb.currency) }}</span>
                      <span v-else class="text-ink-300">—</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <!-- Empty State: No email accounts configured -->
      <div
        v-if="!loadingStats && stats?.total_invoices === 0 && stats?.active_accounts === 0"
        class="ledger-card paper-dots p-10 text-center"
      >
        <div class="mx-auto w-14 h-14 bg-cream-100 rounded-2xl border border-cream-400 flex items-center justify-center shadow-paper mb-4">
          <svg class="w-7 h-7 text-mint-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.6" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </div>
        <h2 class="font-display text-xl font-bold text-ink-900 mb-2">尚未配置收票邮箱</h2>
        <p class="text-sm text-ink-500 max-w-md mx-auto mb-6">连接邮箱账号后将自动定时扫描并解析电子发票，也可直接手动上传归档。</p>
        <div class="flex items-center justify-center gap-3">
          <button @click="router.push('/settings')" class="btn-mint">
            前往系统设置
          </button>
          <button @click="router.push('/upload')" class="btn-cream">
            上传票据
          </button>
        </div>
      </div>

      <!-- Empty State: Accounts configured but no invoices yet -->
      <div
        v-else-if="!loadingStats && stats?.total_invoices === 0 && stats?.active_accounts > 0"
        class="ledger-card paper-dots p-10 text-center"
      >
        <div class="mx-auto w-14 h-14 bg-cream-100 rounded-2xl border border-cream-400 flex items-center justify-center shadow-paper mb-4">
          <svg class="w-7 h-7 text-ink-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.6" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
        </div>
        <h2 class="font-display text-xl font-bold text-ink-900 mb-2">暂无发票记录</h2>
        <p class="text-sm text-ink-500 max-w-md mx-auto mb-6">已配置邮箱账号，可前往立即触发一次扫描，或直接上传本地电子发票。</p>
        <div class="flex items-center justify-center gap-3">
          <button @click="router.push('/settings')" class="btn-mint">
            管理扫描
          </button>
          <button @click="router.push('/upload')" class="btn-cream">
            上传票据
          </button>
        </div>
      </div>

      <!-- Main Ledger Workspace: Saved Views Tabs + Filter Bar + Table -->
      <template v-else>
        <div class="ledger-card overflow-hidden">
          <!-- Row 1: Stationery Index Sticker Saved Views + Export Actions -->
          <div class="px-4 py-3 bg-cream-200/60 border-b border-cream-400 flex flex-col lg:flex-row lg:items-center justify-between gap-3">
            <!-- Saved Views Stickers -->
            <div class="flex flex-wrap items-center gap-2 min-w-0">
              <button
                type="button"
                @click="applySavedView('')"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium border transition-all"
                :class="!activeViewId
                  ? 'bg-cream-100 text-ink-900 border-cream-500 shadow-paper font-semibold'
                  : 'bg-cream-300/50 text-ink-500 border-transparent hover:bg-cream-100/80 hover:text-ink-900'"
              >
                <span>全部票据</span>
                <span
                  v-if="stats"
                  class="px-1.5 py-0.5 rounded-full text-[11px] num-mono"
                  :class="!activeViewId ? 'bg-mint-100 text-mint-800' : 'bg-cream-400/70 text-ink-500'"
                >
                  {{ stats.total_invoices }}
                </span>
              </button>

              <div
                v-for="view in savedViews"
                :key="view.id"
                class="inline-flex items-center rounded-xl border transition-all"
                :class="activeViewId === view.id
                  ? 'bg-mint-50 text-mint-800 border-mint-300 shadow-paper font-semibold'
                  : 'bg-cream-100/80 text-ink-700 border-cream-400 hover:bg-cream-100 hover:border-cream-500'"
              >
                <button
                  type="button"
                  @click="applySavedView(view.id)"
                  class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs"
                >
                  <span
                    class="w-1.5 h-1.5 rounded-full"
                    :class="activeViewId === view.id ? 'bg-mint-600' : 'bg-ink-300'"
                  ></span>
                  <span>{{ view.name }}</span>
                </button>
                <button
                  v-if="activeViewId === view.id"
                  type="button"
                  @click.stop="deleteSavedView(view.id)"
                  class="pr-2 pl-0.5 py-1.5 text-mint-700/70 hover:text-cinnabar-600 transition-colors"
                  title="删除当前视图"
                  aria-label="删除当前视图"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <!-- Preserved select for E2E & keyboard compatibility -->
              <select
                data-test-id="saved-view-select"
                :value="activeViewId || ''"
                @change="e => applySavedView(Number((e.target as HTMLSelectElement).value) || '')"
                aria-label="切换已保存视图"
                class="sr-only"
              >
                <option value="">全部票据</option>
                <option v-for="view in savedViews" :key="view.id" :value="view.id">{{ view.name }}</option>
              </select>

              <button
                v-if="hasActiveFilters"
                type="button"
                @click="showSaveViewModal = true"
                class="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl text-xs font-medium border border-dashed border-cream-500 text-ink-700 bg-cream-50/70 hover:bg-cream-100 hover:border-mint-500 hover:text-mint-800 transition-colors whitespace-nowrap"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
                </svg>
                保存视图
              </button>

              <span v-if="loadingViews" class="text-[11px] text-ink-400">加载视图中…</span>
            </div>

            <!-- Export Actions -->
            <div class="flex items-center justify-end gap-2 flex-shrink-0">
              <button
                type="button"
                @click="exportXLSX"
                :disabled="isExporting"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium border border-mint-300 text-mint-800 bg-mint-50 hover:bg-mint-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                导出 Excel
              </button>
              <button
                type="button"
                @click="exportCSV"
                :disabled="isExporting"
                class="btn-cream py-1.5 px-3 text-xs"
              >
                <svg v-if="isExporting" class="animate-spin h-3.5 w-3.5 text-ink-500" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                <svg v-else class="w-3.5 h-3.5 text-ink-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                {{ isExporting ? '导出中…' : '导出 CSV' }}
              </button>
            </div>
          </div>

          <!-- Row 2: Search + Date Range + Category & Reimbursement Chips -->
          <div class="p-4 space-y-3">
            <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
              <div class="relative flex-1 max-w-md">
                <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <svg class="h-4 w-4 text-ink-400" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd" />
                  </svg>
                </div>
                <input
                  type="text"
                  v-model="searchQuery"
                  class="input-warm pl-9 pr-8 py-1.5"
                  placeholder="搜索销售方、购买方、发票号码、项目摘要…"
                />
                <button
                  v-if="searchQuery"
                  type="button"
                  @click="searchQuery = ''"
                  class="absolute inset-y-0 right-0 pr-2.5 flex items-center text-ink-400 hover:text-ink-700"
                  title="清空搜索"
                >
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <div class="flex flex-wrap items-center gap-2">
                <div class="flex items-center gap-1.5">
                  <input
                    type="date"
                    v-model="dateFrom"
                    aria-label="开始日期"
                    class="input-warm w-auto py-1.5 px-2.5 text-xs num-mono"
                  />
                  <span class="text-ink-400 text-xs">~</span>
                  <input
                    type="date"
                    v-model="dateTo"
                    aria-label="结束日期"
                    class="input-warm w-auto py-1.5 px-2.5 text-xs num-mono"
                  />
                  <button
                    v-if="dateFrom || dateTo"
                    type="button"
                    @click="clearDates"
                    class="p-1.5 text-ink-400 hover:text-ink-700 rounded-lg hover:bg-cream-300/60 transition-colors"
                    title="清除日期"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>

                <button
                  v-if="hasActiveFilters"
                  type="button"
                  @click="clearAllFilters"
                  class="text-xs font-medium text-ink-500 hover:text-ink-900 px-2 py-1 rounded-lg hover:bg-cream-300/60 transition-colors"
                >
                  重置筛选
                </button>
              </div>
            </div>

            <div class="pt-2.5 border-t border-cream-400/70 space-y-2" data-test-id="category-filter">
              <MultiSelectChips
                v-model="selectedCategories"
                :options="categoryOptions"
                label="分类"
              />
              <MultiSelectChips
                v-model="selectedReimbursementStatuses"
                :options="reimbursementOptions"
                label="报销状态"
              />
            </div>
          </div>
        </div>

        <!-- Ledger Table -->
        <div class="ledger-card overflow-hidden">
          <div class="overflow-x-auto">
            <table class="min-w-full divide-y divide-cream-400">
              <thead class="bg-cream-300/70">
                <tr>
                  <th scope="col" class="w-11 pl-5 pr-2 py-3 text-left">
                    <input
                      type="checkbox"
                      :checked="isAllSelected"
                      :indeterminate="isIndeterminate"
                      @change="toggleAll"
                      aria-label="全选当前页"
                      class="h-4 w-4 rounded border-cream-500 text-mint-600 accent-mint-600 focus:ring-mint-500 cursor-pointer"
                    />
                  </th>
                  <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-ink-500 tracking-wide">
                    销售方 &amp; 项目摘要
                  </th>
                  <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-ink-500 tracking-wide">
                    购买方 / 抬头
                  </th>
                  <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-ink-500 tracking-wide">
                    发票号码
                  </th>
                  <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-ink-500 tracking-wide">
                    开票日期
                  </th>
                  <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-ink-500 tracking-wide">
                    分类
                  </th>
                  <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-ink-500 tracking-wide">
                    报销状态
                  </th>
                  <th scope="col" class="px-4 py-3 text-right text-xs font-semibold text-ink-500 tracking-wide">
                    金额
                  </th>
                  <th scope="col" class="pl-3 pr-5 py-3 text-right text-xs font-semibold text-ink-500 tracking-wide">
                    操作
                  </th>
                </tr>
              </thead>

              <tbody class="bg-cream-100 divide-y divide-cream-400/80">
                <!-- Loading Skeleton -->
                <tr v-if="invoicesStore.loading" v-for="i in 5" :key="i" class="animate-pulse">
                  <td class="pl-5 pr-2 py-4"><div class="h-4 w-4 bg-cream-300 rounded"></div></td>
                  <td class="px-4 py-4">
                    <div class="h-4 w-44 bg-cream-300 rounded mb-1.5"></div>
                    <div class="h-3 w-32 bg-cream-300/70 rounded"></div>
                  </td>
                  <td class="px-4 py-4"><div class="h-4 w-28 bg-cream-300 rounded"></div></td>
                  <td class="px-4 py-4"><div class="h-4 w-32 bg-cream-300 rounded"></div></td>
                  <td class="px-4 py-4"><div class="h-4 w-20 bg-cream-300 rounded"></div></td>
                  <td class="px-4 py-4"><div class="h-5 w-16 bg-cream-300 rounded-full"></div></td>
                  <td class="px-4 py-4"><div class="h-5 w-14 bg-cream-300 rounded-md"></div></td>
                  <td class="px-4 py-4 text-right"><div class="h-4 w-24 bg-cream-300 rounded ml-auto"></div></td>
                  <td class="pl-3 pr-5 py-4 text-right"><div class="h-4 w-20 bg-cream-300 rounded ml-auto"></div></td>
                </tr>

                <!-- No Matches -->
                <tr v-else-if="invoicesStore.invoices.length === 0">
                  <td colspan="9" class="px-6 py-16 text-center">
                    <div class="flex flex-col items-center justify-center">
                      <div class="w-12 h-12 rounded-2xl bg-cream-200 border border-cream-400 flex items-center justify-center mb-3">
                        <svg class="w-6 h-6 text-ink-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.6" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </div>
                      <p class="text-sm font-semibold text-ink-900">没有找到符合条件的票据</p>
                      <p class="text-xs text-ink-500 mt-1 mb-4">可调整搜索关键词、日期区间或筛选条件。</p>
                      <button
                        v-if="hasActiveFilters"
                        type="button"
                        @click="clearAllFilters"
                        class="btn-cream text-xs py-1.5"
                      >
                        清空全部筛选
                      </button>
                    </div>
                  </td>
                </tr>

                <!-- Invoice Rows -->
                <tr
                  v-else
                  v-for="invoice in invoicesStore.invoices"
                  :key="invoice.id"
                  class="transition-colors"
                  :class="invoicesStore.selectedIds.includes(invoice.id)
                    ? 'bg-mint-50/55 hover:bg-mint-50/85'
                    : 'hover:bg-cream-200/70'"
                  :data-test-id="`invoice-row-${invoice.id}`"
                >
                  <td class="pl-5 pr-2 py-3.5 whitespace-nowrap">
                    <input
                      type="checkbox"
                      :checked="invoicesStore.selectedIds.includes(invoice.id)"
                      @change="invoicesStore.toggleSelection(invoice.id)"
                      :aria-label="`选择发票 ${invoice.invoice_no}`"
                      class="h-4 w-4 rounded border-cream-500 text-mint-600 accent-mint-600 focus:ring-mint-500 cursor-pointer"
                    />
                  </td>

                  <!-- Seller + Type + Item Summary -->
                  <td class="px-4 py-3.5 max-w-[280px]">
                    <div class="flex items-center gap-1.5 min-w-0">
                      <button
                        type="button"
                        @click="viewInvoice(invoice.id)"
                        class="text-sm font-semibold text-ink-900 hover:text-mint-700 truncate text-left transition-colors"
                        :title="invoice.seller || '—'"
                      >
                        {{ invoice.seller || '—' }}
                      </button>
                      <span
                        v-if="invoice.invoice_type"
                        class="flex-shrink-0 px-1.5 py-0.5 text-[10px] leading-tight font-medium rounded border"
                        :class="invoice.invoice_type.toLowerCase().includes('专用')
                          ? 'bg-mint-50 text-mint-800 border-mint-200'
                          : 'bg-cream-300/70 text-ink-500 border-cream-400'"
                      >
                        {{ invoice.invoice_type }}
                      </span>
                    </div>
                    <div
                      class="text-xs text-ink-500 truncate mt-0.5"
                      :title="invoice.item_summary || ''"
                    >
                      {{ invoice.item_summary || '—' }}
                    </div>
                  </td>

                  <!-- Buyer -->
                  <td class="px-4 py-3.5 text-sm text-ink-700 max-w-[170px] truncate" :title="invoice.buyer || ''">
                    {{ invoice.buyer || '—' }}
                  </td>

                  <!-- Invoice No (Mono + Click Copy) -->
                  <td class="px-4 py-3.5 whitespace-nowrap">
                    <div class="inline-flex items-center gap-1 group">
                      <span class="text-xs text-ink-700 num-mono" :title="invoice.invoice_no">
                        {{ invoice.invoice_no || '—' }}
                      </span>
                      <button
                        v-if="invoice.invoice_no"
                        type="button"
                        @click.stop="copyInvoiceNo(invoice.invoice_no)"
                        class="opacity-0 group-hover:opacity-100 focus:opacity-100 p-0.5 rounded text-ink-400 hover:text-mint-700 transition-opacity"
                        :title="copiedInvoiceNo === invoice.invoice_no ? '已复制' : '复制发票号码'"
                      >
                        <svg v-if="copiedInvoiceNo === invoice.invoice_no" class="w-3.5 h-3.5 text-mint-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
                        </svg>
                        <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                        </svg>
                      </button>
                    </div>
                  </td>

                  <!-- Date -->
                  <td class="px-4 py-3.5 whitespace-nowrap text-xs text-ink-500 num-mono">
                    {{ formatDate(invoice.invoice_date) }}
                  </td>

                  <!-- Category -->
                  <td class="px-4 py-3.5 whitespace-nowrap">
                    <CategoryBadge :category="invoice.invoice_category" />
                  </td>

                  <!-- Reimbursement Stamp -->
                  <td class="px-4 py-3.5 whitespace-nowrap">
                    <span
                      class="stamp-badge"
                      :class="REIMBURSEMENT_COLORS[invoice.reimbursement_status || 'unreimbursed']"
                      :title="invoice.tags || invoice.notes || ''"
                    >
                      {{ REIMBURSEMENT_LABELS[invoice.reimbursement_status || 'unreimbursed'] }}
                    </span>
                  </td>

                  <!-- Amount (Strict Right-Aligned Mono + Real Currency Code) -->
                  <td class="px-4 py-3.5 whitespace-nowrap text-right">
                    <span class="text-[11px] font-medium text-ink-400 num-mono mr-1.5">
                      {{ invoice.currency || 'CNY' }}
                    </span>
                    <span class="text-sm font-semibold text-ink-900 num-mono">
                      {{ formatCurrency(invoice.amount, invoice.currency) }}
                    </span>
                  </td>

                  <!-- Row Actions -->
                  <td class="pl-3 pr-5 py-3.5 whitespace-nowrap text-right text-xs font-medium">
                    <div class="inline-flex items-center gap-1">
                      <button
                        type="button"
                        @click="viewInvoice(invoice.id)"
                        class="px-2 py-1 rounded-lg text-mint-700 hover:text-mint-900 hover:bg-mint-50 transition-colors"
                      >
                        详情
                      </button>
                      <button
                        type="button"
                        @click="downloadInvoice(invoice.id)"
                        class="px-2 py-1 rounded-lg text-ink-700 hover:text-ink-900 hover:bg-cream-300/70 transition-colors"
                      >
                        下载
                      </button>
                      <button
                        type="button"
                        @click="confirmDelete(invoice.id)"
                        class="px-2 py-1 rounded-lg text-cinnabar-600 hover:text-cinnabar-700 hover:bg-cinnabar-50 transition-colors"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Pagination -->
          <div class="bg-cream-100 px-5 py-3.5 flex flex-col sm:flex-row items-center justify-between gap-3 border-t border-cream-400">
            <div class="flex items-center gap-3 text-xs text-ink-500">
              <span>
                显示第
                <span class="font-semibold text-ink-900 num-mono">
                  {{ (page - 1) * size + (invoicesStore.invoices.length > 0 ? 1 : 0) }}–{{ Math.min(page * size, invoicesStore.total) }}
                </span>
                条，共
                <span class="font-semibold text-ink-900 num-mono">{{ invoicesStore.total }}</span>
                条
              </span>
              <select
                v-model="size"
                aria-label="每页显示数量"
                class="input-warm w-auto py-1 pl-2.5 pr-7 text-xs num-mono"
              >
                <option :value="20">20 条/页</option>
                <option :value="50">50 条/页</option>
                <option :value="100">100 条/页</option>
              </select>
            </div>

            <nav class="inline-flex items-center gap-1.5" aria-label="分页导航">
              <button
                type="button"
                @click="handlePageChange(page - 1)"
                :disabled="page === 1"
                class="btn-cream py-1.5 px-3 text-xs"
              >
                上一页
              </button>
              <span class="px-3 py-1.5 rounded-xl bg-cream-200 border border-cream-400 text-xs font-medium text-ink-700 num-mono">
                {{ page }} / {{ totalPages || 1 }}
              </span>
              <button
                type="button"
                @click="handlePageChange(page + 1)"
                :disabled="page >= totalPages"
                class="btn-cream py-1.5 px-3 text-xs"
              >
                下一页
              </button>
            </nav>
          </div>
        </div>
      </template>
    </div>

    <!-- Bottom Deep Tea-Mint Floating Batch Action Dock -->
    <Transition
      enter-active-class="transition duration-200 ease-out"
      enter-from-class="opacity-0 translate-y-3"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition duration-150 ease-in"
      leave-from-class="opacity-100 translate-y-0"
      leave-to-class="opacity-0 translate-y-3"
    >
      <div
        v-if="invoicesStore.selectedIds.length > 0"
        class="bg-mint-900 text-white shadow-dock rounded-2xl fixed bottom-16 lg:bottom-6 left-1/2 -translate-x-1/2 z-40 max-w-[calc(100vw-1.5rem)] border border-mint-700/70 px-3.5 py-2.5"
      >
        <div class="flex flex-wrap items-center justify-center gap-2">
          <div class="inline-flex items-center gap-2 px-2.5 py-1 rounded-xl bg-mint-800/90 border border-mint-700/80 text-xs">
            <span class="font-semibold num-mono">已选 {{ invoicesStore.selectedIds.length }} 张</span>
            <span
              v-if="selectedCurrencyTotals.length > 0"
              class="hidden sm:inline text-mint-200 num-mono border-l border-mint-700 pl-2"
            >
              {{ selectedCurrencyTotals.map(item => formatCurrency(item.total, item.currency)).join(' + ') }}
            </span>
          </div>

          <div class="flex flex-wrap items-center justify-center gap-1.5">
            <button
              type="button"
              @click="batchSetReimbursement('reimbursed')"
              :disabled="updatingReimbursement"
              class="inline-flex items-center gap-1 px-3 py-1.5 rounded-xl text-xs font-semibold bg-mint-200 text-mint-900 hover:bg-mint-100 transition-colors disabled:opacity-50"
            >
              标为已报销
            </button>
            <button
              type="button"
              @click="batchSetReimbursement('pending')"
              :disabled="updatingReimbursement"
              class="inline-flex items-center gap-1 px-3 py-1.5 rounded-xl text-xs font-medium bg-mint-800 hover:bg-mint-700 text-cream-100 border border-mint-700 transition-colors disabled:opacity-50"
            >
              标为报销中
            </button>
            <button
              type="button"
              @click="downloadReimbursementPack"
              :disabled="downloadingReimbursementPack"
              class="inline-flex items-center gap-1 px-3 py-1.5 rounded-xl text-xs font-medium bg-mint-600 hover:bg-mint-500 text-white transition-colors disabled:opacity-50"
            >
              {{ downloadingReimbursementPack ? '打包中…' : '导出报销包 ZIP' }}
            </button>
            <button
              type="button"
              @click="batchDownload"
              :disabled="downloadingBatch"
              class="inline-flex items-center gap-1 px-3 py-1.5 rounded-xl text-xs font-medium bg-mint-800 hover:bg-mint-700 text-cream-100 border border-mint-700 transition-colors disabled:opacity-50"
            >
              {{ downloadingBatch ? '下载中…' : '批量下载' }}
            </button>
            <button
              type="button"
              @click="confirmBatchDelete"
              :disabled="deletingBatch"
              class="inline-flex items-center gap-1 px-3 py-1.5 rounded-xl text-xs font-medium bg-cinnabar-600 hover:bg-cinnabar-700 text-white transition-colors disabled:opacity-50"
            >
              {{ deletingBatch ? '删除中…' : '批量删除' }}
            </button>
            <button
              type="button"
              @click="invoicesStore.clearSelection"
              class="px-2 py-1.5 rounded-xl text-xs text-mint-200 hover:text-white hover:bg-mint-800/80 transition-colors"
            >
              取消选择
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <ConfirmDialog
      ref="confirmDialog"
      title="删除发票"
      message="确定删除这张发票吗？此操作无法撤销。"
      confirmText="删除"
      @confirm="executeDelete"
    />
    <ConfirmDialog
      ref="confirmBatchDeleteDialog"
      title="批量删除发票"
      message="确定删除已选中的发票吗？此操作无法撤销。"
      confirmText="全部删除"
      @confirm="executeBatchDelete"
    />

    <!-- Save View Modal -->
    <div
      v-if="showSaveViewModal"
      class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-ink-900/35 backdrop-blur-[2px]"
      aria-labelledby="modal-title"
      role="dialog"
      aria-modal="true"
      @click.self="showSaveViewModal = false"
    >
      <div class="ledger-card max-w-md w-full p-6 shadow-paper-lg">
        <form @submit.prevent="saveCurrentView" class="space-y-4">
          <h3 class="font-display text-lg font-bold text-ink-900" id="modal-title">
            保存筛选视图
          </h3>

          <div>
            <label class="block text-xs font-medium text-ink-500 mb-1.5">视图名称</label>
            <input
              type="text"
              v-model="newViewName"
              required
              class="input-warm"
              placeholder="例如：本月未报销、境外美元账单"
            />
          </div>

          <div class="bg-cream-200/80 border border-cream-400 rounded-xl p-3 text-xs text-ink-700">
            <p class="font-semibold text-ink-900 mb-1.5">当前筛选条件</p>
            <ul class="space-y-1">
              <li v-if="searchQuery">关键词：「{{ searchQuery }}」</li>
              <li v-if="dateFrom || dateTo" class="num-mono">
                日期：{{ dateFrom || '不限' }} ~ {{ dateTo || '不限' }}
              </li>
              <li v-if="selectedCategories.length">
                分类：{{ selectedCategories.map(c => CATEGORY_LABELS[c]).join('、') }}
              </li>
              <li v-if="selectedReimbursementStatuses.length">
                报销状态：{{ selectedReimbursementStatuses.map(s => REIMBURSEMENT_LABELS[s]).join('、') }}
              </li>
            </ul>
          </div>

          <div class="flex justify-end gap-2 pt-1">
            <button type="button" @click="showSaveViewModal = false" class="btn-cream">
              取消
            </button>
            <button
              type="submit"
              :disabled="isSavingView || !newViewName.trim()"
              class="btn-mint"
            >
              {{ isSavingView ? '保存中…' : '保存视图' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </AppLayout>
</template>
