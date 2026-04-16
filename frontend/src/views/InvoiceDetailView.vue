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
const pdfUrl = ref('')

const fetchInvoice = async () => {
  const id = Number(route.params.id)
  if (isNaN(id)) {
    error.value = 'Invalid invoice ID'
    loading.value = false
    return
  }

  try {
    invoice.value = await api.getInvoice(id)
    await loadPdfBlob(id)
  } catch (err: any) {
    error.value = 'Failed to load invoice details'
    console.error(err)
  } finally {
    loading.value = false
  }
}

const loadPdfBlob = async (id: number) => {
  try {
    const response = await fetch(`/api/v1/invoices/${id}/download`, {
      headers: {
        'Authorization': `Bearer ${authStore.token}`
      }
    })
    
    if (!response.ok) throw new Error('Failed to load PDF')
    
    const blob = await response.blob()
    pdfUrl.value = URL.createObjectURL(blob)
  } catch (err) {
    console.error('Error fetching PDF blob', err)
  }
}

const formatCurrency = (amount: number | undefined) => {
  if (amount === undefined) return '-'
  return `¥${amount.toFixed(2)}`
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

const downloadInvoice = () => {
  if (pdfUrl.value) {
    const a = document.createElement('a')
    a.href = pdfUrl.value
    const filename = `${invoice.value?.buyer || 'buyer'}_${invoice.value?.seller || 'seller'}_${invoice.value?.invoice_no || 'invoice'}.pdf`
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
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
        <div class="px-6 py-5 border-b border-slate-200 flex justify-between items-center bg-slate-50">
          <div>
            <h3 class="text-xl font-bold text-slate-900 flex items-center gap-3">
              {{ invoice.invoice_no || 'Unknown Invoice' }}
              <span 
                class="px-2.5 py-0.5 text-xs font-semibold rounded-full border"
                :class="invoice.invoice_type.toLowerCase().includes('增值税专用发票') ? 'bg-indigo-50 text-indigo-700 border-indigo-200' : 'bg-slate-100 text-slate-700 border-slate-200'"
              >
                {{ invoice.invoice_type || 'Unknown Type' }}
              </span>
            </h3>
            <p class="mt-1 text-sm text-slate-500">
              Extracted from email on {{ formatDate(invoice.created_at) }}
            </p>
          </div>
          <button
            @click="downloadInvoice"
            class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-lg shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
          >
            <svg class="-ml-1 mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
            Download Original
          </button>
        </div>
        
        <div class="p-6">
          <!-- Hero Row -->
          <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8 pb-8 border-b border-slate-100">
            <div class="flex flex-col justify-center items-center p-6 bg-slate-50 rounded-xl border border-slate-100">
              <span class="text-sm font-medium text-slate-500 uppercase tracking-wider mb-1">Total Amount</span>
              <span class="text-3xl font-bold text-blue-600">{{ formatCurrency(invoice.amount) }}</span>
            </div>
            <div class="flex flex-col justify-center items-center p-6 bg-slate-50 rounded-xl border border-slate-100">
              <span class="text-sm font-medium text-slate-500 uppercase tracking-wider mb-1">Invoice Date</span>
              <span class="text-2xl font-semibold text-slate-800">{{ formatDate(invoice.invoice_date) }}</span>
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
              <dt class="text-xs font-medium text-slate-500 uppercase tracking-wide">Buyer</dt>
              <dd class="mt-1 text-sm text-slate-900 bg-slate-50 p-3 rounded-lg border border-slate-100 min-h-[3rem]">{{ invoice.buyer || '-' }}</dd>
            </div>
            <div class="sm:col-span-1">
              <dt class="text-xs font-medium text-slate-500 uppercase tracking-wide">Seller</dt>
              <dd class="mt-1 text-sm text-slate-900 bg-slate-50 p-3 rounded-lg border border-slate-100 min-h-[3rem]">{{ invoice.seller || '-' }}</dd>
            </div>
            
            <div class="sm:col-span-2">
              <dt class="text-xs font-medium text-slate-500 uppercase tracking-wide">Item Summary</dt>
              <dd class="mt-1 text-sm text-slate-900 bg-slate-50 p-4 rounded-lg border border-slate-100 min-h-[4rem] leading-relaxed">{{ invoice.item_summary || '-' }}</dd>
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
        </div>

        <div class="px-6 py-5 border-t border-slate-200">
          <h4 class="text-sm font-medium text-slate-900 mb-4 uppercase tracking-wide">Document Preview</h4>
          <div class="bg-slate-100 rounded-lg border border-slate-200 flex items-center justify-center min-h-[600px] overflow-hidden">
            <iframe v-if="pdfUrl" :src="pdfUrl" class="w-full h-[800px]" title="PDF Preview"></iframe>
            <div v-else class="text-slate-400 flex flex-col items-center justify-center p-12">
              <svg class="h-12 w-12 mb-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"></path>
              </svg>
              <span>Preview not available</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
