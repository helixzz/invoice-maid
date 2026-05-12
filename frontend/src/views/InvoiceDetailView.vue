<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import { api } from '@/api/client'
import type { Invoice } from '@/types'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const invoice = ref<Invoice | null>(null)
const loading = ref(true)
const error = ref('')
const previewUrl = ref('')

const isEditing = ref(false)
const editForm = ref<Partial<Invoice>>({})
const isSaving = ref(false)
const showHistory = ref(false)

const startEdit = () => {
  if (!invoice.value) return
  editForm.value = {
    buyer: invoice.value.buyer,
    seller: invoice.value.seller,
    amount: invoice.value.amount,
    invoice_date: invoice.value.invoice_date,
    invoice_type: invoice.value.invoice_type,
    item_summary: invoice.value.item_summary,
    invoice_no: invoice.value.invoice_no
  }
  isEditing.value = true
}

const cancelEdit = () => {
  isEditing.value = false
  editForm.value = {}
}

const saveEdit = async () => {
  if (!invoice.value) return
  isSaving.value = true
  try {
    const updated = await api.updateInvoice(invoice.value.id, editForm.value)
    invoice.value = updated
    isEditing.value = false
  } catch (err) {
    console.error('Failed to update invoice', err)
    alert('Failed to update invoice')
  } finally {
    isSaving.value = false
  }
}

const formatDateTime = (dateString: string | undefined) => {
  if (!dateString) return '-'
  return new Date(dateString).toLocaleString()
}

const fetchInvoice = async () => {
  loading.value = true
  const id = Number(route.params.id)
  if (isNaN(id)) {
    error.value = 'Invalid invoice ID'
    loading.value = false
    return
  }

  try {
    invoice.value = await api.getInvoice(id)
    if (invoice.value.source_format?.toLowerCase() === 'pdf') {
      await loadPreviewBlob(id)
    }
  } catch (err: any) {
    error.value = 'Failed to load invoice details'
    console.error(err)
  } finally {
    loading.value = false
  }
}

const loadPreviewBlob = async (id: number) => {
  try {
    const response = await fetch(`/api/v1/invoices/${id}/download`, {
      headers: {
        'Authorization': `Bearer ${authStore.token}`
      }
    })
    
    if (!response.ok) throw new Error('Failed to load document')
    
    const blob = await response.blob()
    previewUrl.value = URL.createObjectURL(blob)
  } catch (err) {
    console.error('Error fetching document blob', err)
  }
}

const formatCurrency = (amount: number | undefined, category?: string) => {
  if (amount === undefined) return '-'
  const symbol = category === 'overseas_invoice' ? '$' : '¥'
  return `${symbol}${amount.toFixed(2)}`
}

const formatDate = (dateString: string | undefined) => {
  if (!dateString) return '-'
  return dateString.split('T')[0]
}

const formatConfidence = (conf: number | undefined) => {
  if (conf === undefined) return '-'
  return `${(conf * 100).toFixed(1)}%`
}

const goBack = () => {
  router.push('/invoices')
}

const downloadInvoice = async () => {
  if (!invoice.value) return
  
  try {
    const response = await fetch(`/api/v1/invoices/${invoice.value.id}/download`, {
      headers: {
        'Authorization': `Bearer ${authStore.token}`
      }
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
  } catch (error) {
    console.error('Failed to download invoice', error)
  }
}

onMounted(() => {
  fetchInvoice()
})
</script>

<template>
  <AppLayout>
    <div class="space-y-6">
      <button @click="goBack" class="inline-flex items-center text-sm font-medium text-slate-500 hover:text-slate-700 transition-colors group">
        <svg class="mr-2 h-5 w-5 text-slate-400 group-hover:text-slate-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path fill-rule="evenodd" d="M9.707 16.707a1 1 0 01-1.414 0l-6-6a1 1 0 010-1.414l6-6a1 1 0 011.414 1.414L5.414 9H17a1 1 0 110 2H5.414l4.293 4.293a1 1 0 010 1.414z" clip-rule="evenodd" />
        </svg>
        Back to Invoices
      </button>

      <div v-if="loading" class="animate-pulse flex space-x-4">
        <div class="flex-1 space-y-6 py-1">
          <div class="h-64 bg-slate-200 rounded"></div>
        </div>
      </div>

      <div v-else-if="error" class="bg-red-50 border border-red-200 rounded-xl p-6 text-center text-red-600">
        {{ error }}
      </div>

      <div v-else-if="invoice" class="bg-white shadow-sm border border-slate-200 rounded-xl overflow-hidden">
        <div class="px-6 py-5 border-b border-slate-200 flex flex-col sm:flex-row sm:justify-between sm:items-center bg-slate-50 gap-4">
          <div>
            <div class="flex items-center gap-3 mb-1">
              <h3 v-if="!isEditing" class="text-xl font-bold text-slate-900 flex items-center gap-3">
                {{ invoice.invoice_no || 'Unknown Invoice' }}
                <span 
                  class="px-2.5 py-0.5 text-xs font-semibold rounded-full border"
                  :class="invoice.invoice_type.toLowerCase().includes('增值税专用发票') ? 'bg-indigo-50 text-indigo-700 border-indigo-200' : 'bg-slate-100 text-slate-700 border-slate-200'"
                >
                  {{ invoice.invoice_type || 'Unknown Type' }}
                </span>
                <span v-if="invoice.is_manually_corrected" class="px-2.5 py-0.5 text-xs font-semibold rounded-full border bg-amber-50 text-amber-700 border-amber-200" title="Manually Corrected">
                  Corrected
                </span>
              </h3>
              <input v-else v-model="editForm.invoice_no" type="text" class="text-xl font-bold text-slate-900 border border-slate-300 rounded px-2 py-1 focus:ring-blue-500 focus:border-blue-500 w-48" placeholder="Invoice No">
            </div>
            <p class="text-sm text-slate-500">
              Extracted from email on {{ formatDate(invoice.created_at) }}
            </p>
          </div>
          <div class="flex items-center gap-3">
            <template v-if="isEditing">
              <button
                @click="cancelEdit"
                class="inline-flex items-center px-4 py-2 border border-slate-300 shadow-sm text-sm font-medium rounded-lg text-slate-700 bg-white hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
                :disabled="isSaving"
              >
                Cancel
              </button>
              <button
                @click="saveEdit"
                class="inline-flex items-center px-4 py-2 border border-transparent shadow-sm text-sm font-medium rounded-lg text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
                :disabled="isSaving"
              >
                {{ isSaving ? 'Saving...' : 'Save Changes' }}
              </button>
            </template>
            <template v-else>
              <button
                @click="startEdit"
                class="inline-flex items-center px-4 py-2 border border-slate-300 shadow-sm text-sm font-medium rounded-lg text-slate-700 bg-white hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
              >
                <svg class="-ml-1 mr-2 h-5 w-5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"></path></svg>
                Edit Details
              </button>
              <button
                @click="downloadInvoice"
                class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-lg shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
              >
                <svg class="-ml-1 mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
                Download Original
              </button>
            </template>
          </div>
        </div>
        
        <div class="p-6">
          <!-- Hero Row -->
          <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8 pb-8 border-b border-slate-100">
            <div class="flex flex-col justify-center items-center p-6 bg-slate-50 rounded-xl border border-slate-100">
              <span class="text-sm font-medium text-slate-500 uppercase tracking-wider mb-1">Total Amount</span>
              <span v-if="!isEditing" class="text-3xl font-bold text-blue-600">{{ formatCurrency(invoice.amount, invoice.invoice_category) }}</span>
              <input v-else v-model.number="editForm.amount" type="number" step="0.01" class="text-2xl font-bold text-blue-600 border border-slate-300 rounded px-2 py-1 text-center w-32 focus:ring-blue-500 focus:border-blue-500">
            </div>
            <div class="flex flex-col justify-center items-center p-6 bg-slate-50 rounded-xl border border-slate-100">
              <span class="text-sm font-medium text-slate-500 uppercase tracking-wider mb-1">Invoice Date</span>
              <span v-if="!isEditing" class="text-2xl font-semibold text-slate-800">{{ formatDate(invoice.invoice_date) }}</span>
              <input v-else v-model="editForm.invoice_date" type="date" class="text-xl font-semibold text-slate-800 border border-slate-300 rounded px-2 py-1 focus:ring-blue-500 focus:border-blue-500">
            </div>
            <div class="flex flex-col justify-center items-center p-6 bg-slate-50 rounded-xl border border-slate-100">
              <span class="text-sm font-medium text-slate-500 uppercase tracking-wider mb-2">AI Confidence</span>
              <div class="w-full max-w-[12rem]">
                <div class="flex justify-between text-sm mb-1">
                  <span :class="{'text-green-600 font-semibold': invoice.confidence > 0.8, 'text-yellow-600 font-semibold': invoice.confidence <= 0.8 && invoice.confidence > 0.5, 'text-red-600 font-semibold': invoice.confidence <= 0.5}">
                    {{ formatConfidence(invoice.confidence) }}
                  </span>
                  <span class="text-slate-400">100%</span>
                </div>
                <div class="w-full bg-slate-200 rounded-full h-2.5">
                  <div 
                    class="h-2.5 rounded-full" 
                    :class="{'bg-green-500': invoice.confidence > 0.8, 'bg-yellow-500': invoice.confidence <= 0.8 && invoice.confidence > 0.5, 'bg-red-500': invoice.confidence <= 0.5}"
                    :style="`width: ${(invoice.confidence * 100).toFixed(0)}%`"
                  ></div>
                </div>
              </div>
            </div>
          </div>

          <!-- Metadata -->
          <dl class="grid grid-cols-1 gap-x-6 gap-y-8 md:grid-cols-2">
            <div class="sm:col-span-1">
              <dt class="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Buyer</dt>
              <dd v-if="!isEditing" class="text-sm text-slate-900 bg-slate-50 p-3 rounded-lg border border-slate-100 min-h-[3rem]">{{ invoice.buyer || '-' }}</dd>
              <input v-else v-model="editForm.buyer" type="text" class="text-sm text-slate-900 border border-slate-300 rounded-lg p-3 w-full focus:ring-blue-500 focus:border-blue-500">
            </div>
            <div class="sm:col-span-1">
              <dt class="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Seller</dt>
              <dd v-if="!isEditing" class="text-sm text-slate-900 bg-slate-50 p-3 rounded-lg border border-slate-100 min-h-[3rem]">{{ invoice.seller || '-' }}</dd>
              <input v-else v-model="editForm.seller" type="text" class="text-sm text-slate-900 border border-slate-300 rounded-lg p-3 w-full focus:ring-blue-500 focus:border-blue-500">
            </div>
            <div class="sm:col-span-1" v-if="isEditing">
              <dt class="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Invoice Type</dt>
              <input v-model="editForm.invoice_type" type="text" class="text-sm text-slate-900 border border-slate-300 rounded-lg p-3 w-full focus:ring-blue-500 focus:border-blue-500">
            </div>
            
            <div :class="['sm:col-span-2', isEditing ? 'mt-2' : '']">
              <dt class="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Item Summary</dt>
              <dd v-if="!isEditing" class="text-sm text-slate-900 bg-slate-50 p-4 rounded-lg border border-slate-100 min-h-[4rem] leading-relaxed">{{ invoice.item_summary || '-' }}</dd>
              <textarea v-else v-model="editForm.item_summary" rows="3" class="text-sm text-slate-900 border border-slate-300 rounded-lg p-4 w-full focus:ring-blue-500 focus:border-blue-500"></textarea>
            </div>
            
            <div class="sm:col-span-2 flex flex-wrap gap-4 pt-4 border-t border-slate-100 mt-2">
              <div class="flex items-center gap-2">
                <span class="text-xs font-medium text-slate-500 uppercase tracking-wide">Format:</span>
                <span class="px-2.5 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-800 border border-slate-200">{{ invoice.source_format || '-' }}</span>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-xs font-medium text-slate-500 uppercase tracking-wide">Method:</span>
                <span class="px-2.5 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-800 border border-slate-200 capitalize">{{ invoice.extraction_method || '-' }}</span>
              </div>
            </div>
          </dl>

          <!-- Correction History -->
          <div v-if="invoice.correction_history && invoice.correction_history.length > 0" class="mt-8 border-t border-slate-100 pt-6">
            <button @click="showHistory = !showHistory" class="flex items-center text-sm font-medium text-slate-600 hover:text-slate-900 transition-colors">
              <svg class="mr-2 h-5 w-5 transition-transform" :class="{ 'rotate-90': showHistory }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
              Correction History ({{ invoice.correction_history.length }})
            </button>
            <div v-if="showHistory" class="mt-4 pl-7 space-y-4">
              <div v-for="log in invoice.correction_history" :key="log.id" class="text-sm border-l-2 border-slate-200 pl-4 py-1">
                <p class="text-slate-500 mb-1">
                  <span class="font-medium text-slate-700 capitalize">{{ log.field_name.replace('_', ' ') }}</span> changed on {{ formatDateTime(log.corrected_at) }}
                </p>
                <div class="flex items-center gap-3">
                  <span class="line-through text-red-400 bg-red-50 px-2 py-0.5 rounded">{{ log.old_value || 'empty' }}</span>
                  <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>
                  <span class="text-green-600 bg-green-50 px-2 py-0.5 rounded">{{ log.new_value || 'empty' }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="px-6 py-5 border-t border-slate-200">
          <h4 class="text-sm font-medium text-slate-900 mb-4 uppercase tracking-wide">Document Preview</h4>
          <div class="bg-slate-100 rounded-lg border border-slate-200 flex items-center justify-center min-h-[600px] overflow-hidden">
            <iframe v-if="previewUrl && invoice?.source_format?.toLowerCase() === 'pdf'" :src="previewUrl" class="w-full h-[800px]" title="Document Preview"></iframe>
            <div v-else class="text-slate-400 flex flex-col items-center justify-center p-12">
              <svg class="h-12 w-12 mb-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"></path>
              </svg>
              <span>Preview not available for {{ invoice?.source_format || 'unknown' }} files. Click Download to save.</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
