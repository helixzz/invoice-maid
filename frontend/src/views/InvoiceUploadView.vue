<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { AxiosError } from 'axios'
import { api } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'
import type { Invoice } from '@/types'

const router = useRouter()

const MAX_SIZE_BYTES = 22 * 1024 * 1024
const ALLOWED_EXT = /\.(pdf|xml|ofd)$/i
const ALLOWED_MIME = new Set([
  'application/pdf',
  'application/xml',
  'text/xml',
  'application/octet-stream',
  'application/ofd',
  'application/zip',
  'application/x-zip-compressed',
])
const MAX_CONCURRENT_UPLOADS = 20
const MAX_FILES_PER_BATCH = 200

type EntryStatus = 'queued' | 'uploading' | 'done' | 'error' | 'blocked'

interface QueueEntry {
  id: string
  file: File
  status: EntryStatus
  progress: number
  invoice: Invoice | null
  errorTitle: string | null
  errorDetail: string | null
  errorOutcome: string | null
  existingInvoiceId: number | null
  confidence: number | null
}

let nextEntryId = 0
const uploads = ref<QueueEntry[]>([])
const isDragging = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)
const isUploading = ref(false)

const counts = computed(() => {
  const c = { queued: 0, uploading: 0, done: 0, error: 0, blocked: 0 }
  for (const e of uploads.value) c[e.status] += 1
  return c
})

const hasWorkToDo = computed(() => counts.value.queued > 0 || counts.value.error > 0)
const hasAnyEntries = computed(() => uploads.value.length > 0)
const allDone = computed(
  () => hasAnyEntries.value && counts.value.uploading === 0 && counts.value.queued === 0 && counts.value.error === 0
)

function validateFile(f: File): string | null {
  if (f.size === 0) return '文件内容为空'
  if (f.size > MAX_SIZE_BYTES) {
    return `文件过大（上限 22 MB，当前 ${(f.size / 1024 / 1024).toFixed(1)} MB）`
  }
  const looksRight = ALLOWED_EXT.test(f.name) || ALLOWED_MIME.has(f.type)
  if (!looksRight) {
    return `不支持的文件格式：${f.type || '未知'}（${f.name}）`
  }
  return null
}

function formatSize(bytes: number): string {
  const kb = bytes / 1024
  if (kb < 1024) return `${kb.toFixed(0)} KB`
  return `${(kb / 1024).toFixed(2)} MB`
}

function addFiles(fileList: FileList | File[] | null) {
  if (!fileList) return
  const incoming = Array.from(fileList as ArrayLike<File>)
  const roomLeft = MAX_FILES_PER_BATCH - uploads.value.length
  const accepted = incoming.slice(0, Math.max(roomLeft, 0))
  for (const f of accepted) {
    const rejection = validateFile(f)
    const entry: QueueEntry = {
      id: `u${++nextEntryId}`,
      file: f,
      status: rejection ? 'blocked' : 'queued',
      progress: 0,
      invoice: null,
      errorTitle: rejection ? '无法上传此文件' : null,
      errorDetail: rejection,
      errorOutcome: null,
      existingInvoiceId: null,
      confidence: null,
    }
    uploads.value.push(entry)
  }
}

function onFileInputChange(event: Event) {
  const input = event.target as HTMLInputElement
  addFiles(input.files)
  input.value = ''
}

function onDrop(event: DragEvent) {
  isDragging.value = false
  event.preventDefault()
  addFiles(event.dataTransfer?.files ?? null)
}

function removeEntry(id: string) {
  uploads.value = uploads.value.filter(e => e.id !== id)
}

function clearFinished() {
  uploads.value = uploads.value.filter(e => e.status === 'queued' || e.status === 'uploading' || e.status === 'error')
}

function resetEntryForRetry(entry: QueueEntry) {
  entry.status = 'queued'
  entry.progress = 0
  entry.errorTitle = null
  entry.errorDetail = null
  entry.errorOutcome = null
  entry.existingInvoiceId = null
  entry.confidence = null
}

function retryEntry(entry: QueueEntry) {
  if (entry.status !== 'error') return
  resetEntryForRetry(entry)
  if (!isUploading.value) void runQueue()
}

function startOrRetryQueue() {
  if (counts.value.queued === 0 && counts.value.error > 0) {
    for (const entry of uploads.value) {
      if (entry.status === 'error') resetEntryForRetry(entry)
    }
  }
  void runQueue()
}

interface TranslatedError {
  title: string
  detail: string
  outcome: string | null
  existingInvoiceId: number | null
  confidence: number | null
}

function translateError(err: unknown): TranslatedError {
  const fallback: TranslatedError = {
    title: '上传失败',
    detail: '发生异常，请稍后重试',
    outcome: null,
    existingInvoiceId: null,
    confidence: null,
  }
  if (!(err instanceof AxiosError) || !err.response) return fallback
  const { status, data } = err.response
  const raw = (data as Record<string, unknown> | undefined)?.detail
  if (status === 413) {
    return { ...fallback, title: '文件过大', detail: typeof raw === 'string' ? raw : '文件大小超过 25 MB 限制' }
  }
  if (status === 415) {
    return { ...fallback, title: '不支持的文件格式', detail: typeof raw === 'string' ? raw : '仅支持 PDF、XML、OFD 电子发票文件' }
  }
  if (status === 429) {
    return { ...fallback, title: '请求过于频繁', detail: '并发上传达到速率限制，请稍后点击重试' }
  }
  if (status === 401 || status === 403) {
    return { ...fallback, title: '登录状态已失效', detail: '请重新登录后再上传' }
  }
  if (status === 409 && typeof raw === 'object' && raw !== null) {
    const payload = raw as Record<string, unknown>
    return {
      title: '重复发票（已在台账中）',
      detail: typeof payload.detail === 'string' ? payload.detail : '相同发票号码的记录已存在，未重复入账',
      outcome: typeof payload.outcome === 'string' ? payload.outcome : 'duplicate',
      existingInvoiceId: typeof payload.existing_invoice_id === 'number' ? payload.existing_invoice_id : null,
      confidence: null,
    }
  }
  if (status === 422 && typeof raw === 'object' && raw !== null) {
    const payload = raw as Record<string, unknown>
    const outcome = typeof payload.outcome === 'string' ? payload.outcome : null
    const titleMap: Record<string, string> = {
      low_confidence: '识别置信度过低',
      not_vat_invoice: '未识别为有效发票',
      scam_detected: '疑似无效或异常票据',
      parse_failed: '文件解析失败',
    }
    return {
      title: (outcome && titleMap[outcome]) || '发票未通过校验',
      detail: typeof payload.detail === 'string' ? payload.detail : '解析管线未能从此文件提取有效发票字段',
      outcome,
      existingInvoiceId: null,
      confidence: typeof payload.confidence === 'number' ? payload.confidence : null,
    }
  }
  return fallback
}

async function uploadOne(entry: QueueEntry): Promise<void> {
  entry.status = 'uploading'
  entry.progress = 0
  try {
    const invoice = await api.uploadInvoice(entry.file, pct => {
      entry.progress = pct
    })
    entry.status = 'done'
    entry.invoice = invoice
    entry.progress = 100
  } catch (err) {
    const t = translateError(err)
    entry.status = 'error'
    entry.errorTitle = t.title
    entry.errorDetail = t.detail
    entry.errorOutcome = t.outcome
    entry.existingInvoiceId = t.existingInvoiceId
    entry.confidence = t.confidence
  }
}

async function runQueue(): Promise<void> {
  if (isUploading.value) return
  isUploading.value = true
  try {
    const workers = Array.from({ length: MAX_CONCURRENT_UPLOADS }, () => worker())
    await Promise.all(workers)
  } finally {
    isUploading.value = false
  }
}

async function worker(): Promise<void> {
  while (true) {
    const next = uploads.value.find(e => e.status === 'queued')
    if (!next) return
    await uploadOne(next)
  }
}

function viewInvoice(id: number) {
  router.push(`/invoices/${id}`)
}

function isDuplicateError(entry: QueueEntry): boolean {
  return entry.status === 'error' && (entry.errorOutcome === 'duplicate' || entry.existingInvoiceId !== null)
}

function statusBadgeLabel(entry: QueueEntry): string {
  if (entry.status === 'queued') return '等待上传'
  if (entry.status === 'uploading') return `上传中 ${entry.progress}%`
  if (entry.status === 'done') return '已入账'
  if (entry.status === 'blocked') return '已拦截'
  if (isDuplicateError(entry)) return '重复发票'
  return '上传失败'
}

function statusBadgeClass(entry: QueueEntry): string {
  if (entry.status === 'done') return 'bg-mint-50 text-mint-700 border-mint-300'
  if (entry.status === 'uploading') return 'bg-mint-100/80 text-mint-800 border-mint-200'
  if (entry.status === 'queued') return 'bg-cream-300/70 text-ink-500 border-cream-500'
  if (isDuplicateError(entry) || entry.status === 'blocked') {
    return 'bg-[#FDF5E6] text-[#9A631B] border-[#EBD2A3]'
  }
  return 'bg-cinnabar-50 text-cinnabar-600 border-cinnabar-200'
}
</script>

<template>
  <AppLayout>
    <div class="max-w-4xl mx-auto space-y-5">
      <!-- Page Header + Offline QR Receipt Routing Card -->
      <div class="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 class="font-display text-xl sm:text-2xl font-bold text-ink-900">批量上传电子发票</h2>
          <p class="mt-1 text-sm text-ink-500">
            支持 PDF / XML / OFD，单文件 ≤ <span class="num-mono">22 MB</span>，单批最多 <span class="num-mono">{{ MAX_FILES_PER_BATCH }}</span> 份（<span class="num-mono">{{ MAX_CONCURRENT_UPLOADS }}</span> 并发解析）。
          </p>
        </div>
      </div>

      <!-- Compact Divert Banner to /maid for Offline QR Receipts -->
      <router-link
        to="/maid"
        class="flex items-center justify-between gap-3 rounded-2xl bg-peach-50/90 hover:bg-peach-100/75 border border-peach-200 px-4 py-3 transition-colors group"
      >
        <div class="flex items-center gap-3 min-w-0">
          <span class="w-8 h-8 rounded-xl bg-cream-100 border border-peach-200 flex items-center justify-center text-ink-700 flex-shrink-0">
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 4v1m6 11h2m-6 0h-2v4m0-11v3m0 0h.01M12 12h4.01M16 20h4M4 12h4m12 0h.01M5 8h2a1 1 0 001-1V5a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1zm12 0h2a1 1 0 001-1V5a1 1 0 00-1-1h-2a1 1 0 00-1 1v2a1 1 0 001 1zM5 20h2a1 1 0 001-1v-2a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1z" />
            </svg>
          </span>
          <span class="text-sm font-medium text-ink-900 truncate">
            线下消费二维码小票？去米尔工作台拍码开票 →
          </span>
        </div>
        <span class="text-xs font-semibold text-mint-700 group-hover:text-mint-800 flex-shrink-0 hidden sm:inline">
          前往工作台
        </span>
      </router-link>

      <!-- Cream Stationery Dropzone -->
      <div
        class="relative rounded-2xl border-2 border-dashed px-6 py-12 text-center transition-all shadow-paper"
        :class="
          isDragging
            ? 'paper-dots border-mint-500 ring-4 ring-mint-100'
            : 'bg-cream-100 border-cream-500 hover:border-mint-400 hover:bg-cream-50'
        "
        @dragover.prevent="isDragging = true"
        @dragleave.prevent="isDragging = false"
        @drop="onDrop"
      >
        <input
          ref="fileInputRef"
          type="file"
          accept=".pdf,.xml,.ofd,application/pdf,application/xml,text/xml"
          multiple
          class="sr-only"
          @change="onFileInputChange"
        />

        <div
          class="mx-auto w-14 h-14 rounded-2xl flex items-center justify-center border transition-colors"
          :class="
            isDragging
              ? 'bg-mint-100 border-mint-300 text-mint-700'
              : 'bg-mint-50 border-mint-200 text-mint-600'
          "
        >
          <svg class="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.7" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
          </svg>
        </div>

        <p class="mt-4 text-sm sm:text-base font-medium text-ink-900">
          将发票文件拖放到此处，或
          <button
            type="button"
            class="font-semibold text-mint-700 hover:text-mint-800 underline decoration-mint-300 underline-offset-4"
            @click="fileInputRef?.click()"
          >
            选择本地文件
          </button>
        </p>

        <div class="mt-3 flex flex-wrap items-center justify-center gap-2 text-xs text-ink-500">
          <span class="num-mono px-2 py-0.5 rounded-md bg-cream-300/70 text-ink-700 border border-cream-400">PDF</span>
          <span class="num-mono px-2 py-0.5 rounded-md bg-cream-300/70 text-ink-700 border border-cream-400">XML</span>
          <span class="num-mono px-2 py-0.5 rounded-md bg-cream-300/70 text-ink-700 border border-cream-400">OFD</span>
          <span class="text-ink-400">· 单文件 ≤ 22 MB · 单批最多 {{ MAX_FILES_PER_BATCH }} 份</span>
        </div>
      </div>

      <!-- Upload Queue Card -->
      <div v-if="hasAnyEntries" class="ledger-card overflow-hidden">
        <!-- Queue Toolbar -->
        <div class="px-5 py-3.5 bg-cream-300/50 border-b border-cream-400 flex items-center justify-between flex-wrap gap-3">
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-500">
            <span class="font-semibold text-ink-900">
              共 <span class="num-mono">{{ uploads.length }}</span> 份
            </span>
            <span v-if="counts.queued > 0">
              · <span class="num-mono">{{ counts.queued }}</span> 等待
            </span>
            <span v-if="counts.uploading > 0" class="text-mint-700 font-medium">
              · <span class="num-mono">{{ counts.uploading }}</span> 上传中
            </span>
            <span v-if="counts.done > 0" class="text-mint-700 font-medium">
              · <span class="num-mono">{{ counts.done }}</span> 已入账
            </span>
            <span v-if="counts.error > 0" class="text-cinnabar-600 font-medium">
              · <span class="num-mono">{{ counts.error }}</span> 失败
            </span>
            <span v-if="counts.blocked > 0" class="text-[#9A631B] font-medium">
              · <span class="num-mono">{{ counts.blocked }}</span> 已拦截
            </span>
          </div>

          <div class="flex items-center gap-2">
            <button
              v-if="counts.done > 0 || counts.error > 0 || counts.blocked > 0"
              type="button"
              class="btn-cream py-1.5 px-3 text-xs"
              :disabled="isUploading"
              @click="clearFinished"
            >
              清除已完成
            </button>
            <button
              type="button"
              :disabled="isUploading || !hasWorkToDo"
              class="btn-mint py-1.5 px-4 text-xs sm:text-sm"
              @click="startOrRetryQueue"
            >
              <span v-if="isUploading">上传解析中…</span>
              <span v-else-if="counts.error > 0 && counts.queued === 0">重试失败 (<span class="num-mono">{{ counts.error }}</span>)</span>
              <span v-else>开始上传 (<span class="num-mono">{{ counts.queued }}</span>)</span>
            </button>
          </div>
        </div>

        <!-- Queue Entries -->
        <ul class="divide-y divide-cream-400/70">
          <li
            v-for="entry in uploads"
            :key="entry.id"
            class="px-5 py-3.5 hover:bg-cream-50/70 transition-colors"
          >
            <div class="flex items-start gap-3">
              <!-- Status Icon -->
              <div class="flex-shrink-0 mt-0.5">
                <svg
                  v-if="entry.status === 'done'"
                  class="h-5 w-5 text-mint-600"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
                </svg>
                <svg
                  v-else-if="isDuplicateError(entry) || entry.status === 'blocked'"
                  class="h-5 w-5 text-[#C78A3B]"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <svg
                  v-else-if="entry.status === 'error'"
                  class="h-5 w-5 text-cinnabar-600"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <svg
                  v-else-if="entry.status === 'uploading'"
                  class="h-5 w-5 text-mint-600 animate-spin"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3.5" opacity="0.25" />
                  <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" />
                </svg>
                <svg
                  v-else
                  class="h-5 w-5 text-ink-300"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.6" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                </svg>
              </div>

              <!-- Entry Details -->
              <div class="flex-1 min-w-0">
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0 flex-1">
                    <p class="text-sm font-medium text-ink-900 truncate">{{ entry.file.name }}</p>
                    <p class="mt-0.5 text-xs text-ink-400 flex flex-wrap items-center gap-x-2 gap-y-0.5">
                      <span class="num-mono">{{ formatSize(entry.file.size) }}</span>
                      <template v-if="entry.status === 'done' && entry.invoice">
                        <span>·</span>
                        <span class="num-mono text-ink-700">#{{ entry.invoice.invoice_no }}</span>
                        <button
                          type="button"
                          class="font-medium text-mint-700 hover:text-mint-800 underline underline-offset-2"
                          @click="viewInvoice(entry.invoice.id)"
                        >
                          查看详情
                        </button>
                      </template>
                    </p>
                  </div>

                  <div class="flex items-center gap-2 flex-shrink-0">
                    <span class="stamp-badge num-mono" :class="statusBadgeClass(entry)">
                      {{ statusBadgeLabel(entry) }}
                    </span>

                    <button
                      v-if="entry.status === 'error'"
                      type="button"
                      class="text-xs font-medium text-mint-700 hover:text-mint-800 px-1.5 py-0.5 rounded hover:bg-mint-50 transition-colors"
                      @click="retryEntry(entry)"
                    >
                      重试
                    </button>

                    <button
                      v-if="entry.status !== 'uploading'"
                      type="button"
                      class="text-xs text-ink-300 hover:text-ink-700 px-1 py-0.5 rounded transition-colors"
                      :aria-label="`移除 ${entry.file.name}`"
                      @click="removeEntry(entry.id)"
                    >
                      ✕
                    </button>
                  </div>
                </div>

                <!-- Upload Progress Bar -->
                <div v-if="entry.status === 'uploading'" class="mt-2.5">
                  <div class="w-full h-1.5 bg-cream-300 rounded-full overflow-hidden">
                    <div
                      class="h-full bg-mint-500 rounded-full transition-all duration-200"
                      :style="{ width: `${entry.progress}%` }"
                    />
                  </div>
                </div>

                <!-- Error / Duplicate / Blocked Callout -->
                <div
                  v-if="(entry.status === 'error' || entry.status === 'blocked') && entry.errorDetail"
                  class="mt-2.5 rounded-xl border px-3.5 py-2.5"
                  :class="
                    isDuplicateError(entry) || entry.status === 'blocked'
                      ? 'bg-[#FDF5E6] border-[#EBD2A3] text-[#8B5E22]'
                      : 'bg-cinnabar-50 border-cinnabar-200 text-cinnabar-700'
                  "
                >
                  <p class="text-xs font-semibold">{{ entry.errorTitle }}</p>
                  <p class="text-xs opacity-90 mt-0.5">{{ entry.errorDetail }}</p>
                  <p v-if="entry.confidence !== null" class="text-xs num-mono opacity-90 mt-0.5">
                    识别置信度：{{ (entry.confidence * 100).toFixed(0) }}%
                  </p>
                  <button
                    v-if="entry.existingInvoiceId"
                    type="button"
                    class="mt-1.5 inline-flex items-center gap-1 text-xs font-semibold text-mint-800 hover:text-mint-900 underline underline-offset-2"
                    @click="viewInvoice(entry.existingInvoiceId)"
                  >
                    查看已有发票 →
                  </button>
                </div>
              </div>
            </div>
          </li>
        </ul>

        <!-- All Done Receipt Banner -->
        <div
          v-if="allDone && counts.done > 0"
          class="px-5 py-3.5 border-t border-mint-200 bg-mint-50/85 text-sm text-mint-800 flex flex-wrap items-center justify-between gap-2"
        >
          <span>
            已成功归档 <strong class="num-mono">{{ counts.done }}</strong> 张发票，可继续拖入文件。
          </span>
          <router-link
            to="/invoices"
            class="text-xs sm:text-sm font-semibold text-mint-700 hover:text-mint-900 underline underline-offset-2"
          >
            返回票据台账 →
          </router-link>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
