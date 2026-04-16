<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useInvoicesStore } from '@/stores/invoices'
import { useDebounceFn } from '@vueuse/core'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import AppLayout from '@/components/AppLayout.vue'
import { api } from '@/api/client'

const router = useRouter()
const invoicesStore = useInvoicesStore()

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
    size.value
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

const formatCurrency = (amount: number) => {
  return `¥${amount.toFixed(2)}`
}

const formatDate = (dateString: string) => {
  if (!dateString) return '-'
  return dateString.split('T')[0]
}

const viewInvoice = (id: number) => {
  router.push(`/invoices/${id}`)
}

const downloadInvoice = (id: number) => {
  const url = api.downloadInvoice(id)
  window.open(url, '_blank')
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
    } catch (error) {
      console.error('Delete failed', error)
    } finally {
      deletingInvoiceId.value = null
    }
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

onMounted(() => {
  fetchInvoices()
})
</script>

<template>
  <AppLayout>
    <div class="space-y-6">
      <!-- Header / Actions -->
      <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 bg-white p-4 rounded-xl shadow-sm border border-slate-200">
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

      <!-- Batch Actions -->
      <div v-if="invoicesStore.selectedIds.length > 0" class="flex items-center justify-between bg-blue-50 p-4 rounded-xl border border-blue-100 transition-all duration-300">
        <span class="text-sm text-blue-800 font-medium">
          {{ invoicesStore.selectedIds.length }} selected
        </span>
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
          Download Selected ({{ invoicesStore.selectedIds.length }})
        </button>
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
                <td class="px-6 py-4 whitespace-nowrap"><div class="h-4 bg-slate-200 rounded w-40"></div></td>
                <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                  <div class="h-4 bg-slate-200 rounded w-16 inline-block"></div>
                </td>
              </tr>
              
              <tr v-else-if="invoicesStore.invoices.length === 0">
                <td colspan="9" class="px-6 py-12 text-center text-slate-500">
                  <div class="flex flex-col items-center justify-center">
                    <svg class="w-12 h-12 text-slate-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                    <p class="text-base font-medium">No invoices found</p>
                    <p class="text-sm mt-1">Configure email accounts in Settings to start scanning.</p>
                  </div>
                </td>
              </tr>

              <tr v-else v-for="invoice in invoicesStore.invoices" :key="invoice.id" class="hover:bg-slate-50 transition-colors">
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
                <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-slate-900">{{ formatCurrency(invoice.amount) }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{{ formatDate(invoice.invoice_date) }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-500">
                  <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-slate-100 text-slate-800 border border-slate-200">
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
  </AppLayout>
</template>
