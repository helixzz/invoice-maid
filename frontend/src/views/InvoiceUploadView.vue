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

type UploadStatus = 'idle' | 'uploading' | 'done' | 'error'

interface UploadError {
  title: string
  detail: string
  existingInvoiceId?: number
  confidence?: number
  outcome?: string
}

const selectedFile = ref<File | null>(null)
const progress = ref(0)
const status = ref<UploadStatus>('idle')
const error = ref<UploadError | null>(null)
const savedInvoice = ref<Invoice | null>(null)
const isDragging = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)

const formattedSize = computed(() => {
  if (!selectedFile.value) return ''
  const kb = selectedFile.value.size / 1024
  if (kb < 1024) return `${kb.toFixed(0)} KB`
  return `${(kb / 1024).toFixed(2)} MB`
})

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

function setFile(f: File | null) {
  selectedFile.value = f
  savedInvoice.value = null
  error.value = null
  progress.value = 0
  status.value = 'idle'
  if (f) {
    const msg = validateFile(f)
    if (msg) {
      error.value = { title: 'Cannot upload this file', detail: msg }
      status.value = 'error'
    }
  }
}

function onFileInputChange(event: Event) {
  const input = event.target as HTMLInputElement
  setFile(input.files?.[0] ?? null)
}

function onDrop(event: DragEvent) {
  isDragging.value = false
  event.preventDefault()
  const f = event.dataTransfer?.files[0]
  if (f) setFile(f)
}

function resetAndSelectAnother() {
  setFile(null)
  if (fileInputRef.value) fileInputRef.value.value = ''
}

function translateError(err: unknown): UploadError {
  if (err instanceof AxiosError && err.response) {
    const status_ = err.response.status
    const raw = err.response.data?.detail
    if (status_ === 413) {
      return { title: 'File too large', detail: typeof raw === 'string' ? raw : 'File exceeds 25 MB limit' }
    }
    if (status_ === 415) {
      return { title: 'Unsupported file type', detail: typeof raw === 'string' ? raw : 'Only PDF, XML, and OFD invoice files are accepted' }
    }
    if (status_ === 422 && typeof raw === 'object' && raw !== null) {
      const outcome = raw.outcome as string | undefined
      if (outcome === 'low_confidence') {
        return {
          title: 'Could not confidently extract this invoice',
          detail: raw.detail ?? 'Extraction confidence was too low',
          confidence: raw.confidence,
          outcome,
        }
      }
      if (outcome === 'not_vat_invoice' || outcome === 'scam_detected') {
        return {
          title: 'Not recognized as a valid invoice',
          detail: raw.detail ?? 'The file does not appear to be a valid VAT invoice',
          outcome,
        }
      }
      if (outcome === 'parse_failed') {
        return {
          title: 'Could not parse this file',
          detail: raw.detail ?? 'The file is corrupt or in an unexpected format',
          outcome,
        }
      }
    }
    if (status_ === 409 && typeof raw === 'object' && raw !== null) {
      return {
        title: 'This invoice already exists',
        detail: raw.detail ?? 'An invoice with the same number is already on file',
        existingInvoiceId: raw.existing_invoice_id,
        outcome: raw.outcome,
      }
    }
    if (status_ === 429) {
      return { title: 'Too many uploads', detail: 'Please wait a minute and try again' }
    }
    if (status_ === 401 || status_ === 403) {
      return { title: 'Authentication required', detail: 'Please log in again' }
    }
  }
  return { title: 'Upload failed', detail: 'Unexpected error; please try again' }
}

async function upload() {
  if (!selectedFile.value) return
  const preFlight = validateFile(selectedFile.value)
  if (preFlight) {
    error.value = { title: 'Cannot upload this file', detail: preFlight }
    status.value = 'error'
    return
  }
  status.value = 'uploading'
  progress.value = 0
  error.value = null
  try {
    const invoice = await api.uploadInvoice(selectedFile.value, (pct) => {
      progress.value = pct
    })
    savedInvoice.value = invoice
    status.value = 'done'
  } catch (err) {
    error.value = translateError(err)
    status.value = 'error'
  }
}

function viewInvoice(id: number) {
  router.push(`/invoices/${id}`)
}
</script>

<template>
  <AppLayout>
    <div class="max-w-3xl mx-auto">
      <div class="mb-6">
        <h1 class="text-2xl font-semibold text-slate-900">Upload invoice</h1>
        <p class="mt-1 text-sm text-slate-600">
          Upload a single PDF, XML, or OFD invoice. The same classifier
          and extraction pipeline used for mailbox scans will parse it and
          save the structured data.
        </p>
      </div>

      <div
        class="relative rounded-xl border-2 border-dashed bg-white px-8 py-12 text-center transition-colors"
        :class="isDragging ? 'border-blue-400 bg-blue-50' : 'border-slate-300'"
        @dragover.prevent="isDragging = true"
        @dragleave.prevent="isDragging = false"
        @drop="onDrop"
      >
        <input
          ref="fileInputRef"
          type="file"
          accept=".pdf,.xml,.ofd,application/pdf,application/xml,text/xml"
          class="sr-only"
          @change="onFileInputChange"
        >

        <div v-if="!selectedFile">
          <svg class="mx-auto h-12 w-12 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16V4m0 0L3 8m4-4l4 4m6 4v8m0 0l-4-4m4 4l4-4" />
          </svg>
          <p class="mt-3 text-sm text-slate-700">
            Drag and drop an invoice file here, or
            <button
              type="button"
              class="font-semibold text-blue-600 hover:text-blue-700 underline underline-offset-2"
              @click="fileInputRef?.click()"
            >
              browse
            </button>
          </p>
          <p class="mt-1 text-xs text-slate-500">
            PDF, XML, or OFD &middot; up to 22 MB
          </p>
        </div>

        <div v-else class="text-left space-y-4">
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0 flex-1">
              <p class="text-sm font-medium text-slate-900 truncate">
                {{ selectedFile.name }}
              </p>
              <p class="text-xs text-slate-500">
                {{ formattedSize }} &middot; {{ selectedFile.type || 'unknown type' }}
              </p>
            </div>
            <button
              v-if="status !== 'uploading'"
              type="button"
              class="text-sm text-slate-500 hover:text-slate-700"
              @click="resetAndSelectAnother"
            >
              Change
            </button>
          </div>

          <div v-if="status === 'uploading'">
            <div class="w-full h-2 bg-slate-200 rounded-full overflow-hidden">
              <div
                class="h-full bg-blue-500 transition-all duration-200"
                :style="{ width: `${progress}%` }"
              />
            </div>
            <p class="mt-2 text-xs text-slate-500 text-right">
              Uploading… {{ progress }}%
            </p>
          </div>

          <div v-if="status === 'idle' || (status === 'error' && error?.title === 'Cannot upload this file')">
            <button
              type="button"
              :disabled="error !== null"
              class="w-full inline-flex justify-center px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
              @click="upload"
            >
              Upload and extract
            </button>
          </div>
        </div>
      </div>

      <div
        v-if="status === 'done' && savedInvoice"
        class="mt-6 rounded-xl border border-green-200 bg-green-50 p-5"
      >
        <div class="flex items-start gap-3">
          <svg class="h-6 w-6 flex-shrink-0 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <div class="flex-1 min-w-0">
            <h3 class="text-sm font-semibold text-green-900">
              Invoice {{ savedInvoice.invoice_no }} saved
            </h3>
            <dl class="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-700">
              <div>
                <dt class="font-medium text-slate-500">Buyer</dt>
                <dd>{{ savedInvoice.buyer }}</dd>
              </div>
              <div>
                <dt class="font-medium text-slate-500">Seller</dt>
                <dd>{{ savedInvoice.seller }}</dd>
              </div>
              <div>
                <dt class="font-medium text-slate-500">Amount</dt>
                <dd>¥{{ savedInvoice.amount }}</dd>
              </div>
              <div>
                <dt class="font-medium text-slate-500">Confidence</dt>
                <dd>{{ (savedInvoice.confidence * 100).toFixed(0) }}%</dd>
              </div>
            </dl>
            <div class="mt-3 flex gap-2">
              <button
                type="button"
                class="text-xs font-medium text-blue-700 hover:text-blue-900 underline underline-offset-2"
                @click="viewInvoice(savedInvoice.id)"
              >
                View detail
              </button>
              <button
                type="button"
                class="text-xs font-medium text-slate-700 hover:text-slate-900 underline underline-offset-2"
                @click="resetAndSelectAnother"
              >
                Upload another
              </button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="status === 'error' && error"
        class="mt-6 rounded-xl border border-red-200 bg-red-50 p-5"
      >
        <div class="flex items-start gap-3">
          <svg class="h-6 w-6 flex-shrink-0 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <div class="flex-1 min-w-0">
            <h3 class="text-sm font-semibold text-red-900">
              {{ error.title }}
            </h3>
            <p class="mt-1 text-xs text-red-700">
              {{ error.detail }}
            </p>
            <div v-if="error.confidence !== undefined" class="mt-2 text-xs text-red-700">
              Confidence: {{ (error.confidence * 100).toFixed(0) }}%
            </div>
            <div v-if="error.existingInvoiceId" class="mt-3">
              <button
                type="button"
                class="text-xs font-medium text-red-700 hover:text-red-900 underline underline-offset-2"
                @click="viewInvoice(error.existingInvoiceId)"
              >
                View existing invoice
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
