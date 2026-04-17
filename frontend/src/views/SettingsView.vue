<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppLayout from '@/components/AppLayout.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import Toast from '@/components/Toast.vue'
import { api } from '@/api/client'
import type { EmailAccount, AccountCreate, AccountUpdate, ScanLog, AISettingsResponse, AISettingsUpdate } from '@/types'

const activeTab = ref('accounts')

// AI Settings
const aiSettings = ref<AISettingsResponse | null>(null)
const aiSettingsForm = ref<AISettingsUpdate>({})
const loadingAISettings = ref(false)
const savingAISettings = ref(false)
const availableModels = ref<string[]>([])
const loadingModels = ref(false)
const modelsFetchFailed = ref(false)

const fetchAISettings = async () => {
  loadingAISettings.value = true
  try {
    const res = await api.getAISettings()
    aiSettings.value = res
    aiSettingsForm.value = {
      llm_base_url: res.llm_base_url,
      llm_model: res.llm_model,
      llm_embed_model: res.llm_embed_model,
      embed_dim: res.embed_dim,
      // intentionally not setting api key here unless they type it
    }
  } catch (error) {
    console.error('Failed to fetch AI settings', error)
    toastRef.value?.addToast('Failed to fetch AI settings', 'error')
  } finally {
    loadingAISettings.value = false
  }
}

const fetchAIModels = async () => {
  loadingModels.value = true
  modelsFetchFailed.value = false
  try {
    const res = await api.getAIModels()
    availableModels.value = res.models
    toastRef.value?.addToast(`Found ${res.models.length} models`, 'success')
  } catch (error) {
    console.error('Failed to fetch AI models', error)
    modelsFetchFailed.value = true
    toastRef.value?.addToast('Failed to fetch models. Using text input.', 'error')
  } finally {
    loadingModels.value = false
  }
}

const saveAISettings = async () => {
  savingAISettings.value = true
  try {
    await api.updateAISettings(aiSettingsForm.value)
    toastRef.value?.addToast('AI settings saved successfully', 'success')
    await fetchAISettings()
    aiSettingsForm.value.llm_api_key = '' // Clear input after save
  } catch (error) {
    console.error('Failed to save AI settings', error)
    toastRef.value?.addToast('Failed to save AI settings', 'error')
  } finally {
    savingAISettings.value = false
  }
}

const accounts = ref<EmailAccount[]>([])
const loadingAccounts = ref(false)

const scanLogs = ref<ScanLog[]>([])
const loadingLogs = ref(false)
const scanning = ref(false)

const toastRef = ref<InstanceType<typeof Toast> | null>(null)
const confirmDialog = ref<InstanceType<typeof ConfirmDialog> | null>(null)
const deletingAccountId = ref<number | null>(null)

const showAccountModal = ref(false)
const editingAccountId = ref<number | null>(null)

const defaultAccountForm: AccountCreate = {
  name: '',
  type: 'imap',
  host: '',
  port: 993,
  username: '',
  password: ''
}

const accountForm = ref<AccountCreate>({ ...defaultAccountForm })
const savingAccount = ref(false)

const accountNameById = (accountId: number) => {
  return accounts.value.find((account) => account.id === accountId)?.name || `#${accountId}`
}

const fetchAccounts = async () => {
  loadingAccounts.value = true
  try {
    accounts.value = await api.getAccounts()
  } catch (error) {
    console.error('Failed to fetch accounts', error)
    toastRef.value?.addToast('Failed to fetch accounts', 'error')
  } finally {
    loadingAccounts.value = false
  }
}

const fetchLogs = async () => {
  loadingLogs.value = true
  try {
    const res = await api.getScanLogs()
    scanLogs.value = res.items
  } catch (error) {
    console.error('Failed to fetch scan logs', error)
    toastRef.value?.addToast('Failed to fetch scan logs', 'error')
  } finally {
    loadingLogs.value = false
  }
}

const triggerScan = async () => {
  scanning.value = true
  try {
    const res = await api.triggerScan()
    toastRef.value?.addToast(`Scan triggered: ${res.status}`, 'success')
    setTimeout(fetchLogs, 2000) // refresh logs after a delay
  } catch (error) {
    console.error('Failed to trigger scan', error)
    toastRef.value?.addToast('Failed to trigger scan', 'error')
  } finally {
    scanning.value = false
  }
}

const testConnection = async (accountId: number) => {
  toastRef.value?.addToast('Connection test initiated...', 'info')
  try {
    const response = await api.testConnection(accountId)
    if (response.ok) {
      toastRef.value?.addToast('Connection successful!', 'success')
    } else {
      toastRef.value?.addToast(response.detail || 'Connection failed', 'error')
    }
  } catch (error: any) {
    console.error('Failed to test connection', error)
    toastRef.value?.addToast(error?.response?.data?.detail || 'Connection test failed', 'error')
  }
}

const openAddModal = () => {
  editingAccountId.value = null
  accountForm.value = { ...defaultAccountForm }
  showAccountModal.value = true
}

const openEditModal = (account: EmailAccount) => {
  editingAccountId.value = account.id
  accountForm.value = {
    name: account.name,
    type: account.type,
    host: account.host,
    port: account.port,
    username: account.username,
    password: '' // Don't prefill password
  }
  showAccountModal.value = true
}

const saveAccount = async () => {
  savingAccount.value = true
  try {
    if (editingAccountId.value) {
      const updateData: AccountUpdate = {
        name: accountForm.value.name,
        host: accountForm.value.host,
        port: accountForm.value.port,
        username: accountForm.value.username,
      }
      if (accountForm.value.password) {
        updateData.password = accountForm.value.password
      }
      await api.updateAccount(editingAccountId.value, updateData)
      toastRef.value?.addToast('Account updated successfully', 'success')
    } else {
      await api.createAccount(accountForm.value)
      toastRef.value?.addToast('Account created successfully', 'success')
    }
    showAccountModal.value = false
    fetchAccounts()
  } catch (error) {
    console.error('Failed to save account', error)
    toastRef.value?.addToast('Failed to save account', 'error')
  } finally {
    savingAccount.value = false
  }
}

const confirmDeleteAccount = (id: number) => {
  deletingAccountId.value = id
  confirmDialog.value?.open()
}

const executeDeleteAccount = async () => {
  if (deletingAccountId.value) {
    try {
      await api.deleteAccount(deletingAccountId.value)
      toastRef.value?.addToast('Account deleted successfully', 'success')
      fetchAccounts()
    } catch (error) {
      console.error('Failed to delete account', error)
      toastRef.value?.addToast('Failed to delete account', 'error')
    } finally {
      deletingAccountId.value = null
    }
  }
}

const formatDate = (dateStr: string | null) => {
  if (!dateStr) return 'Never'
  return new Date(dateStr).toLocaleString()
}

onMounted(() => {
  fetchAccounts()
  fetchLogs()
  fetchAISettings()
})
</script>

<template>
  <AppLayout>
    <div class="space-y-6">
      <div class="border-b border-slate-200">
        <nav class="-mb-px flex space-x-8" aria-label="Tabs">
          <button
            @click="activeTab = 'accounts'"
            :class="[activeTab === 'accounts' ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors flex items-center']"
          >
            <svg class="mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
            Email Accounts
          </button>
          <button
            @click="activeTab = 'scan'"
            :class="[activeTab === 'scan' ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors flex items-center']"
          >
            <svg class="mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
            Scan Operations
          </button>
          <button
            @click="activeTab = 'ai'"
            :class="[activeTab === 'ai' ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors flex items-center']"
          >
            <svg class="mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
            AI 模型
          </button>
        </nav>
      </div>

      <!-- Accounts Tab -->
      <div v-if="activeTab === 'accounts'" class="space-y-6">
        <div class="sm:flex sm:items-center sm:justify-between bg-white p-4 rounded-xl shadow-sm border border-slate-200">
          <div>
            <h3 class="text-lg leading-6 font-medium text-slate-900">Configured Accounts</h3>
            <p class="mt-1 max-w-2xl text-sm text-slate-500">Manage email accounts used for invoice scanning.</p>
          </div>
          <div class="mt-4 sm:mt-0">
            <button
              @click="openAddModal"
              class="inline-flex items-center px-4 py-2 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
            >
              <svg class="-ml-1 mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path></svg>
              Add Account
            </button>
          </div>
        </div>

        <div class="bg-white shadow-sm overflow-hidden sm:rounded-xl border border-slate-200">
          <ul role="list" class="divide-y divide-slate-200">
            <li v-if="loadingAccounts" class="p-6 text-center text-slate-500 animate-pulse">
              Loading accounts...
            </li>
            <li v-else-if="accounts.length === 0" class="p-12 text-center">
               <svg class="mx-auto h-16 w-16 text-blue-400 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
               <h3 class="text-xl font-bold text-slate-900">Let's set up your first account</h3>
               <p class="mt-2 text-slate-500 max-w-md mx-auto mb-6">Invoice Maid scans your email inboxes to automatically find and extract invoices. Add an IMAP, POP3, Outlook, or QQ account to begin.</p>
               <button
                 @click="openAddModal"
                 class="inline-flex items-center px-6 py-3 border border-transparent text-base font-medium rounded-lg shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
               >
                 <svg class="-ml-1 mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path></svg>
                 Add Email Account
               </button>
            </li>
            <li v-else v-for="account in accounts" :key="account.id" class="p-6 hover:bg-slate-50 transition-colors">
              <div class="flex items-center justify-between">
                <div class="flex flex-col">
                  <div class="flex items-center space-x-3 mb-1">
                    <span class="text-sm font-medium text-blue-600 truncate">{{ account.name }}</span>
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium" :class="account.is_active ? 'bg-green-100 text-green-800' : 'bg-slate-100 text-slate-800'">
                      {{ account.is_active ? 'Active' : 'Inactive' }}
                    </span>
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-800 border border-slate-200">
                      {{ account.type }}
                    </span>
                  </div>
                  <div class="text-sm text-slate-500">
                    {{ account.username }}
                  </div>
                  <div class="text-xs text-slate-400 mt-2">
                    Last scan UID: {{ account.last_scan_uid || 'None' }}
                  </div>
                </div>
                <div class="flex items-center space-x-4">
                  <button @click="testConnection(account.id)" class="text-sm text-slate-500 hover:text-slate-700 transition-colors hidden sm:inline-block border border-slate-200 px-3 py-1 rounded hover:bg-slate-50">Test Connection</button>
                  <button @click="openEditModal(account)" class="text-sm text-blue-600 hover:text-blue-900 transition-colors font-medium">Edit</button>
                  <button @click="confirmDeleteAccount(account.id)" class="text-sm text-red-600 hover:text-red-900 transition-colors font-medium">Delete</button>
                </div>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <!-- Scan Management Tab -->
      <div v-if="activeTab === 'scan'" class="space-y-6">
        <div class="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <div class="sm:flex sm:items-center sm:justify-between">
            <div>
              <h3 class="text-lg leading-6 font-medium text-slate-900">Manual Scan</h3>
              <p class="mt-1 text-sm text-slate-500">Trigger an immediate scan across all active email accounts.</p>
            </div>
            <div class="mt-4 sm:mt-0">
              <button
                @click="triggerScan"
                :disabled="scanning"
                class="inline-flex items-center px-6 py-3 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <svg v-if="scanning" class="animate-spin -ml-1 mr-2 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <svg v-else class="-ml-1 mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                {{ scanning ? 'Scanning...' : 'Scan Now' }}
              </button>
            </div>
          </div>
        </div>

        <div class="bg-white shadow-sm overflow-hidden sm:rounded-xl border border-slate-200">
          <div class="px-6 py-5 border-b border-slate-200 bg-slate-50">
            <h3 class="text-lg leading-6 font-medium text-slate-900">Recent Scans</h3>
          </div>
          <div class="overflow-x-auto">
            <table class="min-w-full divide-y divide-slate-200">
              <thead class="bg-slate-50">
                <tr>
                  <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Account ID</th>
                  <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Started</th>
                  <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Finished</th>
                  <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Emails</th>
                  <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Invoices</th>
                  <th scope="col" class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                </tr>
              </thead>
              <tbody class="bg-white divide-y divide-slate-200">
                <tr v-if="loadingLogs" class="animate-pulse">
                  <td colspan="6" class="px-6 py-4 text-center text-sm text-slate-500">Loading logs...</td>
                </tr>
                <tr v-else-if="scanLogs.length === 0">
                  <td colspan="6" class="px-6 py-12 text-center text-slate-500">No scan logs available</td>
                </tr>
                <tr v-else v-for="log in scanLogs" :key="log.id" class="hover:bg-slate-50">
                  <td class="px-6 py-4 whitespace-nowrap text-sm">
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-800 border border-slate-200 shadow-sm truncate max-w-[150px]" :title="accountNameById(log.email_account_id)">
                      {{ accountNameById(log.email_account_id) }}
                    </span>
                  </td>
                  <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{{ formatDate(log.started_at) }}</td>
                  <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{{ formatDate(log.finished_at) }}</td>
                  <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-900">{{ log.emails_scanned }}</td>
                  <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-blue-600">{{ log.invoices_found }}</td>
                  <td class="px-6 py-4 text-sm text-slate-500">
                    <span v-if="log.error_message" class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800" :title="log.error_message">
                      Error
                    </span>
                    <span v-else-if="log.finished_at" class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                      Success
                    </span>
                    <span v-else class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800">
                      Running
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <div v-if="activeTab === 'ai'" class="space-y-6">
        <div class="sm:flex sm:items-center sm:justify-between bg-white p-4 rounded-xl shadow-sm border border-slate-200">
          <div>
            <h3 class="text-lg leading-6 font-medium text-slate-900">AI Model Configuration</h3>
            <p class="mt-1 max-w-2xl text-sm text-slate-500">Configure LLM models used for extracting invoice data.</p>
          </div>
        </div>

        <div class="bg-white shadow-sm sm:rounded-xl border border-slate-200 p-6">
          <div v-if="loadingAISettings" class="p-6 text-center text-slate-500 animate-pulse">
            Loading settings...
          </div>
          <form v-else @submit.prevent="saveAISettings" class="space-y-6">
            <div class="grid grid-cols-1 gap-y-6 gap-x-4 sm:grid-cols-2">
              <div class="sm:col-span-2">
                <label class="flex items-center justify-between text-sm font-medium text-slate-700 mb-1">
                  <span>API Base URL</span>
                  <span v-if="aiSettings?.source === 'database'" class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">Database</span>
                  <span v-else class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-800">Environment</span>
                </label>
                <input type="text" v-model="aiSettingsForm.llm_base_url" placeholder="https://api.openai.com/v1" class="shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border">
              </div>

              <div class="sm:col-span-2">
                <label class="flex items-center justify-between text-sm font-medium text-slate-700 mb-1">
                  <span>API Key</span>
                  <span v-if="aiSettings?.source === 'database'" class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">Database</span>
                  <span v-else class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-800">Environment</span>
                </label>
                <input type="password" v-model="aiSettingsForm.llm_api_key" :placeholder="aiSettings?.llm_api_key_masked" class="shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border">
                <p class="mt-1 text-xs text-slate-500">Leave blank to keep existing key.</p>
              </div>

              <div>
                <label class="flex items-center justify-between text-sm font-medium text-slate-700 mb-1">
                  <span>Chat Model</span>
                  <span v-if="aiSettings?.source === 'database'" class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">Database</span>
                  <span v-else class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-800">Environment</span>
                </label>
                <div class="flex space-x-2">
                  <select v-if="availableModels.length > 0 && !modelsFetchFailed" v-model="aiSettingsForm.llm_model" class="block w-full py-2 px-3 border border-slate-300 bg-white rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
                    <option v-for="model in availableModels" :key="model" :value="model">{{ model }}</option>
                  </select>
                  <input v-else type="text" v-model="aiSettingsForm.llm_model" class="shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border">
                  <button type="button" @click="fetchAIModels" :disabled="loadingModels" class="inline-flex items-center px-3 py-2 border border-slate-300 shadow-sm text-sm leading-4 font-medium rounded-md text-slate-700 bg-white hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 transition-colors" title="Refresh Models">
                    <svg v-if="loadingModels" class="animate-spin h-4 w-4 text-slate-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    <svg v-else class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                  </button>
                </div>
              </div>

              <div>
                <label class="flex items-center justify-between text-sm font-medium text-slate-700 mb-1">
                  <span>Embedding Model</span>
                  <span v-if="aiSettings?.source === 'database'" class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">Database</span>
                  <span v-else class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-800">Environment</span>
                </label>
                <div class="flex space-x-2">
                  <select v-if="availableModels.length > 0 && !modelsFetchFailed" v-model="aiSettingsForm.llm_embed_model" class="block w-full py-2 px-3 border border-slate-300 bg-white rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
                    <option v-for="model in availableModels" :key="model" :value="model">{{ model }}</option>
                  </select>
                  <input v-else type="text" v-model="aiSettingsForm.llm_embed_model" class="shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border">
                  <button type="button" @click="fetchAIModels" :disabled="loadingModels" class="inline-flex items-center px-3 py-2 border border-slate-300 shadow-sm text-sm leading-4 font-medium rounded-md text-slate-700 bg-white hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 transition-colors" title="Refresh Models">
                    <svg v-if="loadingModels" class="animate-spin h-4 w-4 text-slate-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    <svg v-else class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                  </button>
                </div>
              </div>

              <div>
                <label class="flex items-center justify-between text-sm font-medium text-slate-700 mb-1">
                  <span>Embedding Dimensions</span>
                  <span v-if="aiSettings?.source === 'database'" class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">Database</span>
                  <span v-else class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-800">Environment</span>
                </label>
                <input type="number" v-model="aiSettingsForm.embed_dim" placeholder="1536" class="shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border">
              </div>
            </div>

            <div class="pt-5 border-t border-slate-200 flex justify-between items-center">
              <button
                type="button"
                @click="fetchAIModels"
                :disabled="loadingModels"
                class="inline-flex items-center justify-center py-2 px-4 border border-slate-300 shadow-sm text-sm font-medium rounded-md text-slate-700 bg-white hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 transition-colors"
              >
                {{ loadingModels ? 'Testing...' : 'Test Connection' }}
              </button>
              
              <button
                type="submit"
                :disabled="savingAISettings"
                class="inline-flex items-center justify-center py-2 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 transition-colors"
              >
                <svg v-if="savingAISettings" class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                {{ savingAISettings ? 'Saving...' : 'Save Settings' }}
              </button>
            </div>
          </form>
        </div>
      </div>

    </div>

    <!-- Account Form Modal -->
    <div v-if="showAccountModal" class="fixed inset-0 z-10 overflow-y-auto" aria-labelledby="modal-title" role="dialog" aria-modal="true">
      <div class="flex items-end justify-center min-h-screen pt-4 px-4 pb-20 text-center sm:block sm:p-0">
        <div class="fixed inset-0 bg-slate-500 bg-opacity-75 transition-opacity" aria-hidden="true" @click="showAccountModal = false"></div>
        <span class="hidden sm:inline-block sm:align-middle sm:h-screen" aria-hidden="true">&#8203;</span>
        <div class="inline-block align-bottom bg-white rounded-xl text-left overflow-hidden shadow-xl transform transition-all sm:my-8 sm:align-middle sm:max-w-lg sm:w-full">
          <form @submit.prevent="saveAccount">
            <div class="bg-white px-4 pt-5 pb-4 sm:p-6 sm:pb-4">
              <div class="sm:flex sm:items-start">
                <div class="mt-3 text-center sm:mt-0 sm:ml-4 sm:text-left w-full">
                  <h3 class="text-lg leading-6 font-medium text-slate-900" id="modal-title">
                    {{ editingAccountId ? 'Edit Account' : 'Add Email Account' }}
                  </h3>
                  <div class="mt-6 space-y-4">
                    <div>
                      <label class="block text-sm font-medium text-slate-700">Account Name</label>
                      <input type="text" v-model="accountForm.name" required class="mt-1 focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border" placeholder="e.g. Work Email">
                    </div>
                    
                    <div>
                      <label class="block text-sm font-medium text-slate-700">Protocol Type</label>
                      <select v-model="accountForm.type" class="mt-1 block w-full py-2 px-3 border border-slate-300 bg-white rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
                        <option value="imap">IMAP</option>
                        <option value="pop3">POP3</option>
                        <option value="outlook">Outlook (OAuth)</option>
                        <option value="qq">QQ Mail</option>
                      </select>
                    </div>

                    <template v-if="accountForm.type !== 'outlook'">
                      <div class="grid grid-cols-3 gap-4">
                        <div class="col-span-2">
                          <label class="block text-sm font-medium text-slate-700">Host</label>
                          <input type="text" v-model="accountForm.host" :required="accountForm.type !== 'outlook'" class="mt-1 focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border" placeholder="imap.example.com">
                        </div>
                        <div class="col-span-1">
                          <label class="block text-sm font-medium text-slate-700">Port</label>
                          <input type="number" v-model="accountForm.port" :required="accountForm.type !== 'outlook'" class="mt-1 focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border" placeholder="993">
                        </div>
                      </div>
                    </template>

                    <div v-if="accountForm.type === 'outlook'" class="bg-blue-50 p-4 rounded-md border border-blue-100 text-sm text-blue-700">
                      Outlook accounts require OAuth2 authentication. You will be prompted to authenticate when saving.
                    </div>

                    <div>
                      <label class="block text-sm font-medium text-slate-700">Username / Email</label>
                      <input type="email" v-model="accountForm.username" required class="mt-1 focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border" placeholder="user@example.com">
                    </div>

                    <div v-if="accountForm.type !== 'outlook'">
                      <label class="block text-sm font-medium text-slate-700">
                        Password / App Password
                        <span v-if="editingAccountId" class="text-slate-400 font-normal ml-1">(Leave blank to keep existing)</span>
                      </label>
                      <input type="password" v-model="accountForm.password" :required="!editingAccountId && accountForm.type !== 'outlook'" class="mt-1 focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border">
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div class="bg-slate-50 px-4 py-3 sm:px-6 sm:flex sm:flex-row-reverse">
              <button type="submit" :disabled="savingAccount" class="w-full inline-flex justify-center rounded-md border border-transparent shadow-sm px-4 py-2 bg-blue-600 text-base font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-50 transition-colors">
                {{ savingAccount ? 'Saving...' : 'Save Account' }}
              </button>
              <button type="button" @click="showAccountModal = false" class="mt-3 w-full inline-flex justify-center rounded-md border border-slate-300 shadow-sm px-4 py-2 bg-white text-base font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 sm:mt-0 sm:ml-3 sm:w-auto sm:text-sm transition-colors">
                Cancel
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <ConfirmDialog
      ref="confirmDialog"
      title="Delete Account"
      message="Are you sure you want to delete this email account? This will stop future scans for this account but won't delete previously extracted invoices."
      confirmText="Delete"
      @confirm="executeDeleteAccount"
    />
    <Toast ref="toastRef" />
  </AppLayout>
</template>
