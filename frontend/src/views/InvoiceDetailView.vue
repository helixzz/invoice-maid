<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import { api } from '@/api/client'
import {
  CATEGORY_COLORS,
  CATEGORY_LABELS,
  REIMBURSEMENT_COLORS,
  REIMBURSEMENT_LABELS,
  REIMBURSEMENT_ORDER,
  type Invoice,
  type ReimbursementStatus,
} from '@/types'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const invoice = ref<Invoice | null>(null)
const loading = ref(true)
const error = ref('')
const previewUrl = ref('')
const previewLoading = ref(false)

const isEditing = ref(false)
const editForm = ref<{
  buyer: string
  seller: string
  amount: number
  currency: string
  invoice_date: string
  invoice_type: string
  reimbursement_status: ReimbursementStatus
  tags: string
  notes: string
  item_summary: string
  invoice_no: string
}>({
  buyer: '',
  seller: '',
  amount: 0,
  currency: 'CNY',
  invoice_date: '',
  invoice_type: '',
  reimbursement_status: 'unreimbursed',
  tags: '',
  notes: '',
  item_summary: '',
  invoice_no: '',
})
const isSaving = ref(false)
const isStamping = ref(false)
const actionError = ref('')
const showHistory = ref(false)

const FIELD_LABELS: Record<string, string> = {
  invoice_no: '发票号码',
  buyer: '购买方',
  seller: '销售方',
  amount: '金额',
  currency: '币种',
  invoice_date: '开票日期',
  invoice_type: '发票类型',
  invoice_category: '发票分类',
  reimbursement_status: '报销状态',
  tags: '标签',
  notes: '报销备注',
  item_summary: '项目摘要',
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

const isPdf = computed(() => invoice.value?.source_format?.toLowerCase() === 'pdf')

const activeStatus = computed<ReimbursementStatus>(() => {
  if (isEditing.value) return editForm.value.reimbursement_status
  return invoice.value?.reimbursement_status || 'unreimbursed'
})

const parsedTags = computed(() => {
  const raw = invoice.value?.tags || ''
  return raw
    .split(/[,，]/)
    .map(t => t.trim())
    .filter(Boolean)
})

const confidencePercent = computed(() => {
  const c = invoice.value?.confidence ?? 0
  return Math.max(0, Math.min(100, Math.round(c * 100)))
})

const confidenceTone = computed(() => {
  const c = invoice.value?.confidence ?? 0
  if (c > 0.8) return { text: 'text-mint-700', bar: 'bg-mint-600' }
  if (c > 0.5) return { text: 'text-[#9A631B]', bar: 'bg-[#C78A3B]' }
  return { text: 'text-cinnabar-600', bar: 'bg-cinnabar-600' }
})

const categoryBadgeClass = computed(() => {
  const cat = invoice.value?.invoice_category
  return cat && CATEGORY_COLORS[cat] ? CATEGORY_COLORS[cat] : 'bg-cream-300/70 text-ink-700 border-cream-500'
})

const categoryLabel = computed(() => {
  const cat = invoice.value?.invoice_category
  return (cat && CATEGORY_LABELS[cat]) || invoice.value?.invoice_type || '未分类'
})

const startEdit = () => {
  if (!invoice.value) return
  actionError.value = ''
  editForm.value = {
    buyer: invoice.value.buyer || '',
    seller: invoice.value.seller || '',
    amount: invoice.value.amount,
    currency: invoice.value.currency || 'CNY',
    invoice_date: formatDateInput(invoice.value.invoice_date),
    invoice_type: invoice.value.invoice_type || '',
    reimbursement_status: invoice.value.reimbursement_status || 'unreimbursed',
    tags: invoice.value.tags || '',
    notes: invoice.value.notes || '',
    item_summary: invoice.value.item_summary ?? '',
    invoice_no: invoice.value.invoice_no || '',
  }
  isEditing.value = true
}

const cancelEdit = () => {
  isEditing.value = false
  actionError.value = ''
}

const saveEdit = async () => {
  if (!invoice.value) return
  isSaving.value = true
  actionError.value = ''
  try {
    const updated = await api.updateInvoice(invoice.value.id, {
      ...editForm.value,
      currency: (editForm.value.currency || 'CNY').trim().toUpperCase(),
    })
    invoice.value = updated
    isEditing.value = false
  } catch (err) {
    console.error('Failed to update invoice', err)
    actionError.value = '保存失败，请检查输入后重试。'
  } finally {
    isSaving.value = false
  }
}

const quickStampStatus = async (status: ReimbursementStatus) => {
  if (!invoice.value) return
  if (isEditing.value) {
    editForm.value.reimbursement_status = status
    return
  }
  if (status === (invoice.value.reimbursement_status || 'unreimbursed') || isStamping.value) {
    return
  }
  isStamping.value = true
  actionError.value = ''
  try {
    const updated = await api.updateInvoice(invoice.value.id, { reimbursement_status: status })
    invoice.value = updated
  } catch (err) {
    console.error('Failed to update reimbursement status', err)
    actionError.value = '更新报销状态失败，请重试。'
  } finally {
    isStamping.value = false
  }
}

const formatDateTime = (dateString: string | undefined) => {
  if (!dateString) return '-'
  return new Date(dateString).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const formatDateInput = (dateString: string | undefined) => {
  if (!dateString) return ''
  return dateString.split('T')[0] || ''
}

const formatDate = (dateString: string | undefined) => {
  if (!dateString) return '-'
  return dateString.split('T')[0] || '-'
}

const formatCurrency = (amount: number | undefined, currency?: string) => {
  if (amount === undefined || Number.isNaN(amount)) return '-'
  const symbol = CURRENCY_SYMBOLS[currency || 'CNY'] || ''
  const formatted = amount.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return symbol ? `${symbol} ${formatted}` : formatted
}

const formatConfidence = (conf: number | undefined) => {
  if (conf === undefined) return '-'
  return `${(conf * 100).toFixed(1)}%`
}

const formatFieldLabel = (fieldName: string) => {
  return FIELD_LABELS[fieldName] || fieldName.replace(/_/g, ' ')
}

const revokePreviewUrl = () => {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
}

const loadPreviewBlob = async (id: number) => {
  previewLoading.value = true
  revokePreviewUrl()
  try {
    const response = await fetch(`/api/v1/invoices/${id}/download`, {
      headers: {
        Authorization: `Bearer ${authStore.token}`,
      },
    })
    if (!response.ok) throw new Error('Failed to load document')
    const blob = await response.blob()
    previewUrl.value = URL.createObjectURL(blob)
  } catch (err) {
    console.error('Error fetching document blob', err)
  } finally {
    previewLoading.value = false
  }
}

const fetchInvoice = async () => {
  loading.value = true
  error.value = ''
  const id = Number(route.params.id)
  if (Number.isNaN(id)) {
    error.value = '无效的发票编号'
    loading.value = false
    return
  }

  try {
    invoice.value = await api.getInvoice(id)
    if (invoice.value.source_format?.toLowerCase() === 'pdf') {
      await loadPreviewBlob(id)
    }
  } catch (err) {
    error.value = '加载发票详情失败，请返回重试。'
    console.error(err)
  } finally {
    loading.value = false
  }
}

const goBack = () => {
  router.push('/invoices')
}

const openPreviewInNewTab = () => {
  if (!previewUrl.value) return
  window.open(previewUrl.value, '_blank', 'noopener,noreferrer')
}

const downloadInvoice = async () => {
  if (!invoice.value) return
  try {
    const response = await fetch(`/api/v1/invoices/${invoice.value.id}/download`, {
      headers: {
        Authorization: `Bearer ${authStore.token}`,
      },
    })
    if (!response.ok) throw new Error('Failed to download invoice')

    const ext = api.invoiceExtension(invoice.value.source_format || 'pdf')
    const fallbackFilename = `${invoice.value.buyer || 'buyer'}_${invoice.value.seller || 'seller'}_${invoice.value.invoice_no || 'invoice'}${ext}`
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
  } catch (err) {
    console.error('Failed to download invoice', err)
    actionError.value = '下载原件失败，请稍后重试。'
  }
}

onMounted(() => {
  fetchInvoice()
})

onBeforeUnmount(() => {
  revokePreviewUrl()
})
</script>

<template>
  <AppLayout>
    <div class="space-y-5">
      <!-- Top Breadcrumb & Actions Bar -->
      <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div class="flex flex-wrap items-center gap-2.5 min-w-0">
          <button
            type="button"
            @click="goBack"
            class="btn-cream py-1.5 px-3 text-xs sm:text-sm"
          >
            <svg class="h-4 w-4 text-ink-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
            </svg>
            返回台账
          </button>

          <template v-if="invoice">
            <span class="text-ink-300 select-none">/</span>
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-xs font-medium text-ink-400">No.</span>
              <span
                v-if="!isEditing"
                class="num-mono text-sm sm:text-base font-bold text-ink-900 truncate"
              >
                {{ invoice.invoice_no || '未识别号码' }}
              </span>
              <input
                v-else
                v-model="editForm.invoice_no"
                type="text"
                class="input-warm num-mono py-1 px-2.5 w-48 sm:w-56 font-semibold"
                placeholder="发票号码"
              />
            </div>

            <span class="stamp-badge" :class="categoryBadgeClass">
              <span class="w-1.5 h-1.5 rounded-full bg-current opacity-75"></span>
              {{ categoryLabel }}
            </span>

            <span
              v-if="invoice.is_manually_corrected"
              class="stamp-badge bg-peach-50 text-ink-700 border-peach-200"
            >
              已校对
            </span>
          </template>
        </div>

        <div v-if="invoice" class="flex items-center gap-2.5 flex-shrink-0">
          <template v-if="isEditing">
            <button
              type="button"
              @click="cancelEdit"
              class="btn-cream"
              :disabled="isSaving"
            >
              取消
            </button>
            <button
              type="button"
              @click="saveEdit"
              class="btn-mint"
              :disabled="isSaving"
            >
              {{ isSaving ? '保存中…' : '保存修改' }}
            </button>
          </template>
          <template v-else>
            <button
              type="button"
              @click="startEdit"
              class="btn-cream"
            >
              <svg class="h-4 w-4 text-ink-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
              </svg>
              编辑
            </button>
            <button
              type="button"
              @click="downloadInvoice"
              class="btn-mint"
            >
              <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              下载原件
            </button>
          </template>
        </div>
      </div>

      <!-- Inline Action Error Banner -->
      <div
        v-if="actionError"
        class="rounded-xl bg-cinnabar-50 border border-cinnabar-200 px-4 py-2.5 text-sm text-cinnabar-700 flex items-center justify-between gap-3"
      >
        <span>{{ actionError }}</span>
        <button
          type="button"
          class="text-xs text-cinnabar-600 hover:text-cinnabar-700 font-medium"
          @click="actionError = ''"
        >
          关闭
        </button>
      </div>

      <!-- Loading Skeleton -->
      <div v-if="loading" class="grid grid-cols-1 lg:grid-cols-12 gap-6 animate-pulse">
        <div class="lg:col-span-6 ledger-card h-[540px] bg-cream-300/60"></div>
        <div class="lg:col-span-6 ledger-card h-[540px] bg-cream-300/60"></div>
      </div>

      <!-- Load Error -->
      <div
        v-else-if="error"
        class="ledger-card bg-cinnabar-50 border-cinnabar-200 p-8 text-center"
      >
        <p class="text-sm font-medium text-cinnabar-700">{{ error }}</p>
        <button type="button" @click="goBack" class="btn-cream mt-4">
          返回票据台账
        </button>
      </div>

      <!-- 50/50 Split Reconciliation Workbench -->
      <div v-else-if="invoice" class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <!-- Left Pane: Sticky Original Document Preview -->
        <section class="lg:col-span-6 lg:sticky lg:top-20 self-start ledger-card overflow-hidden">
          <div class="px-4 py-3 bg-cream-300/60 border-b border-cream-400 flex items-center justify-between gap-3">
            <div class="flex items-center gap-2 min-w-0">
              <h2 class="text-sm font-semibold text-ink-900">原件票面预览</h2>
              <span class="num-mono px-2 py-0.5 text-[11px] font-semibold uppercase rounded-md bg-peach-50 text-cinnabar-600 border border-peach-200">
                {{ invoice.source_format || 'FILE' }}
              </span>
              <span
                v-if="invoice.extraction_method"
                class="num-mono px-2 py-0.5 text-[11px] font-medium rounded-md bg-mint-50 text-mint-800 border border-mint-200 uppercase"
              >
                {{ invoice.extraction_method }}
              </span>
            </div>

            <div class="flex items-center gap-1.5 flex-shrink-0">
              <button
                v-if="previewUrl && isPdf"
                type="button"
                @click="openPreviewInNewTab"
                class="btn-cream py-1 px-2.5 text-xs"
              >
                新窗口
              </button>
              <button
                type="button"
                @click="downloadInvoice"
                class="btn-cream py-1 px-2.5 text-xs"
              >
                下载
              </button>
            </div>
          </div>

          <div class="bg-cream-300/35 p-3 sm:p-4">
            <div
              v-if="previewLoading"
              class="h-[520px] lg:h-[calc(100vh-225px)] min-h-[480px] rounded-xl border border-cream-400 bg-cream-50 flex flex-col items-center justify-center text-ink-400 gap-2"
            >
              <svg class="h-6 w-6 animate-spin text-mint-600" fill="none" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity="0.25" />
                <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" stroke-linecap="round" />
              </svg>
              <span class="text-xs">正在加载原件预览…</span>
            </div>

            <iframe
              v-else-if="previewUrl && isPdf"
              :src="previewUrl"
              class="w-full h-[560px] lg:h-[calc(100vh-225px)] min-h-[500px] rounded-xl border border-cream-400 bg-white shadow-paper"
              title="原件票面预览"
            />

            <div
              v-else
              class="h-[360px] lg:h-[480px] rounded-xl border border-dashed border-cream-500 bg-cream-50/90 flex flex-col items-center justify-center text-center px-6 py-10"
            >
              <div class="w-12 h-12 rounded-2xl bg-cream-300/80 border border-cream-400 flex items-center justify-center text-ink-500 mb-3">
                <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.6" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                </svg>
              </div>
              <p class="text-sm font-medium text-ink-700">
                {{ (invoice.source_format || '当前').toUpperCase() }} 格式暂不支持页内预览
              </p>
              <p class="mt-1 text-xs text-ink-400">
                可下载原件到本地使用对应阅读器核对票面信息。
              </p>
              <button
                type="button"
                @click="downloadInvoice"
                class="btn-mint mt-4 text-xs py-1.5 px-3.5"
              >
                下载原件
              </button>
            </div>
          </div>

          <div class="px-4 py-2.5 bg-cream-50/80 border-t border-cream-400 flex flex-wrap items-center justify-between gap-2 text-xs text-ink-400">
            <span class="num-mono">入库时间：{{ formatDate(invoice.created_at) }}</span>
            <span class="num-mono uppercase">
              {{ invoice.source_format || '-' }} · {{ invoice.extraction_method || '-' }}
            </span>
          </div>
        </section>

        <!-- Right Pane: Warm Cream Stationery Receipt Card -->
        <section class="lg:col-span-6 flex flex-col">
          <div class="ledger-card overflow-hidden">
            <!-- Header: Instant Stamp Bar + Amount & Confidence -->
            <div class="px-5 sm:px-6 pt-5 pb-5 bg-cream-100">
              <!-- Quick Reimbursement Stamp Row -->
              <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2.5">
                <div class="flex items-center gap-2">
                  <span class="text-xs font-medium text-ink-500">报销状态</span>
                  <span v-if="isStamping" class="text-[11px] text-mint-700">保存中…</span>
                </div>
                <div class="flex flex-wrap items-center gap-1.5" role="group" aria-label="切换报销状态">
                  <button
                    v-for="st in REIMBURSEMENT_ORDER"
                    :key="st"
                    type="button"
                    :disabled="isStamping"
                    @click="quickStampStatus(st)"
                    class="stamp-badge py-1 px-2.5 transition-all cursor-pointer disabled:opacity-60"
                    :class="
                      activeStatus === st
                        ? [REIMBURSEMENT_COLORS[st], '-rotate-1 shadow-sm ring-1 ring-inset ring-current/15']
                        : 'bg-cream-50 text-ink-500 border-cream-400 hover:bg-cream-200 hover:text-ink-900'
                    "
                  >
                    <svg
                      v-if="activeStatus === st"
                      class="w-3 h-3 flex-shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
                    </svg>
                    <span>{{ REIMBURSEMENT_LABELS[st] }}</span>
                  </button>
                </div>
              </div>

              <!-- Hero Amount + Confidence -->
              <div class="mt-5 flex flex-col sm:flex-row sm:items-end justify-between gap-4">
                <div class="min-w-0">
                  <div v-if="!isEditing" class="flex flex-wrap items-baseline gap-2.5">
                    <span class="num-mono text-3xl sm:text-[34px] font-bold tracking-tight text-ink-900 leading-none">
                      {{ formatCurrency(invoice.amount, invoice.currency) }}
                    </span>
                    <span class="num-mono inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-mint-50 text-mint-800 border border-mint-200">
                      {{ invoice.currency || 'CNY' }}
                    </span>
                  </div>
                  <div v-else class="flex items-center gap-2">
                    <input
                      v-model="editForm.currency"
                      type="text"
                      maxlength="8"
                      class="input-warm num-mono uppercase w-20 text-center font-semibold"
                      placeholder="CNY"
                      aria-label="币种"
                    />
                    <input
                      v-model.number="editForm.amount"
                      type="number"
                      step="0.01"
                      class="input-warm num-mono w-40 text-lg font-bold"
                      aria-label="金额"
                    />
                  </div>
                  <div class="mt-2 text-xs text-ink-500 num-mono">
                    开票日期：{{ formatDate(invoice.invoice_date) }}
                  </div>
                </div>

                <!-- Compact Confidence Progress Box -->
                <div class="sm:w-48 rounded-xl bg-cream-50 border border-cream-400 px-3.5 py-2.5 flex-shrink-0">
                  <div class="flex items-center justify-between text-xs mb-1.5">
                    <span class="text-ink-500">识别置信度</span>
                    <span class="num-mono font-bold" :class="confidenceTone.text">
                      {{ formatConfidence(invoice.confidence) }}
                    </span>
                  </div>
                  <div class="w-full h-1.5 rounded-full bg-cream-300 overflow-hidden">
                    <div
                      class="h-full rounded-full transition-all duration-300"
                      :class="confidenceTone.bar"
                      :style="{ width: `${confidencePercent}%` }"
                    />
                  </div>
                </div>
              </div>
            </div>

            <!-- Receipt Dashed Divider -->
            <div class="px-5 sm:px-6">
              <div class="receipt-dashed"></div>
            </div>

            <!-- Structured Fields Grid -->
            <div class="px-5 sm:px-6 py-5">
              <dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-5 gap-y-4">
                <!-- Seller -->
                <div>
                  <dt class="text-xs font-medium text-ink-400 mb-1">销售方名称</dt>
                  <dd v-if="!isEditing" class="text-sm font-semibold text-ink-900 break-words leading-snug">
                    {{ invoice.seller || '-' }}
                  </dd>
                  <input
                    v-else
                    v-model="editForm.seller"
                    type="text"
                    class="input-warm"
                    placeholder="销售方名称"
                  />
                </div>

                <!-- Buyer -->
                <div>
                  <dt class="text-xs font-medium text-ink-400 mb-1">购买方 / 抬头</dt>
                  <dd v-if="!isEditing" class="text-sm font-semibold text-ink-900 break-words leading-snug">
                    {{ invoice.buyer || '-' }}
                  </dd>
                  <input
                    v-else
                    v-model="editForm.buyer"
                    type="text"
                    class="input-warm"
                    placeholder="购买方名称"
                  />
                </div>

                <!-- Invoice Date -->
                <div>
                  <dt class="text-xs font-medium text-ink-400 mb-1">开票日期</dt>
                  <dd v-if="!isEditing" class="num-mono text-sm font-semibold text-ink-900">
                    {{ formatDate(invoice.invoice_date) }}
                  </dd>
                  <input
                    v-else
                    v-model="editForm.invoice_date"
                    type="date"
                    class="input-warm num-mono"
                  />
                </div>

                <!-- Invoice Type -->
                <div>
                  <dt class="text-xs font-medium text-ink-400 mb-1">发票类型</dt>
                  <dd v-if="!isEditing" class="text-sm font-medium text-ink-900">
                    {{ invoice.invoice_type || '-' }}
                  </dd>
                  <input
                    v-else
                    v-model="editForm.invoice_type"
                    type="text"
                    class="input-warm"
                    placeholder="例如：电子发票（普通发票）"
                  />
                </div>

                <!-- Item Summary -->
                <div class="sm:col-span-2 pt-1">
                  <dt class="text-xs font-medium text-ink-400 mb-1">项目摘要</dt>
                  <dd
                    v-if="!isEditing"
                    class="text-sm font-medium text-ink-900 leading-relaxed break-words"
                  >
                    {{ invoice.item_summary || '-' }}
                  </dd>
                  <textarea
                    v-else
                    v-model="editForm.item_summary"
                    rows="3"
                    class="input-warm leading-relaxed"
                    placeholder="商品或服务明细摘要"
                  />
                </div>

                <!-- Tags -->
                <div class="sm:col-span-2 pt-1">
                  <dt class="text-xs font-medium text-ink-400 mb-1.5">标签</dt>
                  <dd v-if="!isEditing">
                    <div v-if="parsedTags.length > 0" class="flex flex-wrap gap-1.5">
                      <span
                        v-for="tag in parsedTags"
                        :key="tag"
                        class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-cream-300/80 text-ink-700 border border-cream-500/70"
                      >
                        #{{ tag }}
                      </span>
                    </div>
                    <span v-else class="text-sm text-ink-300">-</span>
                  </dd>
                  <input
                    v-else
                    v-model="editForm.tags"
                    type="text"
                    class="input-warm"
                    placeholder="多个标签用逗号分隔，例如：出差, 办公设备"
                  />
                </div>

                <!-- Notes -->
                <div class="sm:col-span-2">
                  <dt class="text-xs font-medium text-ink-400 mb-1.5">报销备注</dt>
                  <dd
                    v-if="!isEditing"
                    class="text-sm text-ink-700 bg-cream-50 border border-cream-400/90 rounded-xl px-3.5 py-3 min-h-[3.25rem] leading-relaxed whitespace-pre-wrap"
                  >
                    {{ invoice.notes || '暂无备注' }}
                  </dd>
                  <textarea
                    v-else
                    v-model="editForm.notes"
                    rows="3"
                    class="input-warm leading-relaxed"
                    placeholder="报销事由或补充说明"
                  />
                </div>
              </dl>

              <!-- Bottom Edit Actions Bar -->
              <div
                v-if="isEditing"
                class="mt-5 pt-4 border-t border-cream-400 flex items-center justify-end gap-2.5"
              >
                <button
                  type="button"
                  @click="cancelEdit"
                  class="btn-cream"
                  :disabled="isSaving"
                >
                  取消
                </button>
                <button
                  type="button"
                  @click="saveEdit"
                  class="btn-mint"
                  :disabled="isSaving"
                >
                  {{ isSaving ? '保存中…' : '保存修改' }}
                </button>
              </div>
            </div>

            <!-- Footer: Metadata & Collapsible Correction History -->
            <div class="border-t border-cream-400 bg-cream-50/75 px-5 sm:px-6 py-4 space-y-3">
              <div class="flex flex-wrap items-center justify-between gap-3 text-xs text-ink-500">
                <div class="flex flex-wrap items-center gap-3">
                  <span class="inline-flex items-center gap-1.5">
                    <span class="text-ink-400">格式</span>
                    <span class="num-mono px-2 py-0.5 rounded bg-cream-300/70 text-ink-700 border border-cream-400 uppercase">
                      {{ invoice.source_format || '-' }}
                    </span>
                  </span>
                  <span class="inline-flex items-center gap-1.5">
                    <span class="text-ink-400">提取方式</span>
                    <span class="num-mono px-2 py-0.5 rounded bg-cream-300/70 text-ink-700 border border-cream-400">
                      {{ invoice.extraction_method || '-' }}
                    </span>
                  </span>
                </div>

                <button
                  v-if="invoice.correction_history && invoice.correction_history.length > 0"
                  type="button"
                  @click="showHistory = !showHistory"
                  class="inline-flex items-center gap-1 font-medium text-ink-700 hover:text-mint-700 transition-colors"
                >
                  <span>修改记录 ({{ invoice.correction_history.length }})</span>
                  <svg
                    class="w-3.5 h-3.5 transition-transform"
                    :class="{ 'rotate-180': showHistory }"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                <span v-else class="text-ink-400">暂无修改记录</span>
              </div>

              <!-- Correction History Timeline -->
              <div
                v-if="showHistory && invoice.correction_history && invoice.correction_history.length > 0"
                class="pt-3 receipt-dashed space-y-3"
              >
                <div
                  v-for="log in invoice.correction_history"
                  :key="log.id"
                  class="border-l-2 border-cream-500 pl-3.5 py-0.5 text-xs"
                >
                  <div class="flex flex-wrap items-center justify-between gap-2 text-ink-500 mb-1">
                    <span class="font-semibold text-ink-900">{{ formatFieldLabel(log.field_name) }}</span>
                    <span class="num-mono text-[11px] text-ink-400">{{ formatDateTime(log.corrected_at) }}</span>
                  </div>
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="line-through text-cinnabar-600 bg-cinnabar-50 border border-cinnabar-200/70 px-2 py-0.5 rounded">
                      {{ log.old_value || '空' }}
                    </span>
                    <svg class="w-3.5 h-3.5 text-ink-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3" />
                    </svg>
                    <span class="text-mint-800 bg-mint-50 border border-mint-200 px-2 py-0.5 rounded font-medium">
                      {{ log.new_value || '空' }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Subtle Receipt Perforated Bottom Edge -->
          <div class="mx-4 receipt-zig opacity-90" aria-hidden="true"></div>
        </section>
      </div>
    </div>
  </AppLayout>
</template>
