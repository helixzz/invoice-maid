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
const MAX_CONCURRENT_UPLOADS = 3
const MAX_FILES_PER_BATCH = 25

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
  if (f.size === 0) return 'Empty file'
  if (f.size > MAX_SIZE_BYTES) {
    return `File too large (max 22 MB, got ${(f.size / 1024 / 1024).toFixed(1)} MB)`
  }
  const looksRight = ALLOWED_EXT.test(f.name) || ALLOWED_MIME.has(f.type)
  if (!looksRight) {
    return `Unsupported type: ${f.type || 'unknown'} (${f.name})`
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
      errorTitle: rejection ? 'Cannot upload this file' : null,
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

interface TranslatedError {
  title: string
  detail: string
  outcome: string | null
  existingInvoiceId: number | null
  confidence: number | null
}

function translateError(err: unknown): TranslatedError {
  const fallback: TranslatedError = {
    title: 'Upload failed',
    detail: 'Unexpected error; please try again',
    outcome: null,
    existingInvoiceId: null,
    confidence: null,
  }
  if (!(err instanceof AxiosError) || !err.response) return fallback
  const { status, data } = err.response
  const raw = (data as Record<string, unknown> | undefined)?.detail
  if (status === 413) {
    return { ...fallback, title: 'File too large', detail: typeof raw === 'string' ? raw : 'File exceeds 25 MB limit' }
  }
  if (status === 415) {
    return { ...fallback, title: 'Unsupported file type', detail: typeof raw === 'string' ? raw : 'Only PDF, XML, and OFD invoice files are accepted' }
  }
  if (status === 429) {
    return { ...fallback, title: 'Rate limit reached', detail: 'Too many uploads at once; click Retry in a minute' }
  }
  if (status === 401 || status === 403) {
    return { ...fallback, title: 'Authentication required', detail: 'Please log in again' }
  }
  if (status === 409 && typeof raw === 'object' && raw !== null) {
    const payload = raw as Record<string, unknown>
    return {
      title: 'Already on file',
      detail: typeof payload.detail === 'string' ? payload.detail : 'An invoice with the same number is already saved',
      outcome: typeof payload.outcome === 'string' ? payload.outcome : 'duplicate',
      existingInvoiceId: typeof payload.existing_invoice_id === 'number' ? payload.existing_invoice_id : null,
      confidence: null,
    }
  }
  if (status === 422 && typeof raw === 'object' && raw !== null) {
    const payload = raw as Record<string, unknown>
    const outcome = typeof payload.outcome === 'string' ? payload.outcome : null
    const titleMap: Record<string, string> = {
      low_confidence: 'Could not confidently extract this invoice',
      not_vat_invoice: 'Not recognized as a valid invoice',
      scam_detected: 'Rejected as suspicious content',
      parse_failed: 'Could not parse this file',
    }
    return {
      title: (outcome && titleMap[outcome]) || 'Invoice was rejected',
      detail: typeof payload.detail === 'string' ? payload.detail : 'The extraction pipeline could not accept this file',
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
</script>

<template>
  <AppLayout>
    <div class="max-w-4xl mx-auto">
      <div class="mb-6">
        <h1 class="text-2xl font-semibold text-slate-900">Upload invoices</h1>
        <p class="mt-1 text-sm text-slate-600">
          Drop up to {{ MAX_FILES_PER_BATCH }} PDF, XML, or OFD files at a time.
          Each file runs through the same extraction pipeline used for mailbox scans,
          with up to {{ MAX_CONCURRENT_UPLOADS }} processing in parallel.
        </p>
      </div>

      <div
        class="relative rounded-xl border-2 border-dashed bg-white px-6 py-10 text-center transition-colors"
        :class="isDragging ? 'border-blue-400 bg-blue-50' : 'border-slate-300'"
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
        >
        <svg class="mx-auto h-10 w-10 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16V4m0 0L3 8m4-4l4 4m6 4v8m0 0l-4-4m4 4l4-4" />
        </svg>
        <p class="mt-3 text-sm text-slate-700">
          Drag and drop invoice files here, or
          <button
            type="button"
            class="font-semibold text-blue-600 hover:text-blue-700 underline underline-offset-2"
            @click="fileInputRef?.click()"
          >
            browse
          </button>
        </p>
        <p class="mt-1 text-xs text-slate-500">
          PDF, XML, or OFD &middot; up to 22 MB each &middot; max {{ MAX_FILES_PER_BATCH }} per batch
        </p>
      </div>

      <div v-if="hasAnyEntries" class="mt-6 bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div class="px-4 py-3 border-b border-slate-200 flex items-center justify-between flex-wrap gap-2">
          <div class="flex items-center gap-3 text-xs text-slate-600">
            <span class="font-semibold text-slate-800">{{ uploads.length }} file{{ uploads.length === 1 ? '' : 's' }}</span>
            <span v-if="counts.queued > 0">&middot; {{ counts.queued }} queued</span>
            <span v-if="counts.uploading > 0" class="text-blue-600">&middot; {{ counts.uploading }} uploading</span>
            <span v-if="counts.done > 0" class="text-green-600">&middot; {{ counts.done }} saved</span>
            <span v-if="counts.error > 0" class="text-red-600">&middot; {{ counts.error }} failed</span>
            <span v-if="counts.blocked > 0" class="text-amber-600">&middot; {{ counts.blocked }} blocked</span>
          </div>
          <div class="flex items-center gap-2">
            <button
              v-if="counts.done > 0 || counts.error > 0 || counts.blocked > 0"
              type="button"
              class="text-xs text-slate-600 hover:text-slate-800 underline underline-offset-2"
              :disabled="isUploading"
              @click="clearFinished"
            >
              Clear finished
            </button>
            <button
              type="button"
              :disabled="isUploading || !hasWorkToDo"
              class="inline-flex items-center px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
              @click="runQueue"
            >
              <span v-if="isUploading">Uploading…</span>
              <span v-else-if="counts.error > 0 && counts.queued === 0">Retry failed ({{ counts.error }})</span>
              <span v-else>Upload {{ counts.queued }} file{{ counts.queued === 1 ? '' : 's' }}</span>
            </button>
          </div>
        </div>

        <ul class="divide-y divide-slate-100">
          <li
            v-for="entry in uploads"
            :key="entry.id"
            class="px-4 py-3"
          >
            <div class="flex items-start gap-3">
              <div class="flex-shrink-0 mt-1">
                <svg v-if="entry.status === 'done'" class="h-5 w-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
                </svg>
                <svg v-else-if="entry.status === 'error' || entry.status === 'blocked'" class="h-5 w-5 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <svg v-else-if="entry.status === 'uploading'" class="h-5 w-5 text-blue-500 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" opacity="0.25" />
                  <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="4" stroke-linecap="round" />
                </svg>
                <svg v-else class="h-5 w-5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
              </div>
              <div class="flex-1 min-w-0">
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0 flex-1">
                    <p class="text-sm font-medium text-slate-900 truncate">{{ entry.file.name }}</p>
                    <p class="text-xs text-slate-500">
                      {{ formatSize(entry.file.size) }}
                      <span v-if="entry.status === 'done' && entry.invoice">
                        &middot; #{{ entry.invoice.invoice_no }} &middot;
                        <button
                          type="button"
                          class="text-blue-600 hover:text-blue-800 underline underline-offset-2"
                          @click="viewInvoice(entry.invoice.id)"
                        >
                          view
                        </button>
                      </span>
                    </p>
                  </div>
                  <div class="flex items-center gap-2">
                    <button
                      v-if="entry.status === 'error'"
                      type="button"
                      class="text-xs font-medium text-blue-600 hover:text-blue-800"
                      @click="retryEntry(entry)"
                    >
                      Retry
                    </button>
                    <button
                      v-if="entry.status !== 'uploading'"
                      type="button"
                      class="text-xs text-slate-400 hover:text-slate-600"
                      :aria-label="`Remove ${entry.file.name}`"
                      @click="removeEntry(entry.id)"
                    >
                      ✕
                    </button>
                  </div>
                </div>

                <div v-if="entry.status === 'uploading'" class="mt-2">
                  <div class="w-full h-1.5 bg-slate-200 rounded-full overflow-hidden">
                    <div
                      class="h-full bg-blue-500 transition-all duration-200"
                      :style="{ width: `${entry.progress}%` }"
                    />
                  </div>
                </div>

                <div
                  v-if="(entry.status === 'error' || entry.status === 'blocked') && entry.errorDetail"
                  class="mt-2 rounded-md bg-red-50 border border-red-100 px-3 py-2"
                >
                  <p class="text-xs font-semibold text-red-900">{{ entry.errorTitle }}</p>
                  <p class="text-xs text-red-700 mt-0.5">{{ entry.errorDetail }}</p>
                  <p v-if="entry.confidence !== null" class="text-xs text-red-700 mt-0.5">
                    Confidence: {{ (entry.confidence * 100).toFixed(0) }}%
                  </p>
                  <button
                    v-if="entry.existingInvoiceId"
                    type="button"
                    class="mt-1 text-xs font-medium text-red-700 hover:text-red-900 underline underline-offset-2"
                    @click="viewInvoice(entry.existingInvoiceId)"
                  >
                    View existing invoice
                  </button>
                </div>
              </div>
            </div>
          </li>
        </ul>

        <div
          v-if="allDone && counts.done > 0"
          class="px-4 py-3 border-t border-slate-200 bg-green-50 text-sm text-green-900"
        >
          ✓ Successfully saved {{ counts.done }} invoice{{ counts.done === 1 ? '' : 's' }}. You can drop more files above, or
          <router-link to="/invoices" class="font-semibold underline underline-offset-2">
            view your invoice list
          </router-link>.
        </div>
      </div>
    </div>
  </AppLayout>
</template>
