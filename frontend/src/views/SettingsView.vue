<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import AppLayout from '@/components/AppLayout.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import Toast from '@/components/Toast.vue'
import ScanProgressBar from '@/components/ScanProgressBar.vue'
import { useScanProgress } from '@/composables/useScanProgress'
import { api } from '@/api/client'
import type { 
  EmailAccount, 
  AccountCreate, 
  AccountUpdate, 
  ScanLog, 
  AISettingsResponse, 
  AISettingsUpdate, 
  ClassifierSettingsResponse,
  ClassifierSettingsUpdate,
  ExtractionLog,
  OAuthInitiateResponse,
  OAuthStatusResponse
} from '@/types'

const activeTab = ref('accounts')

// OAuth status map for Outlook accounts
const oauthStatusMap = ref<Record<number, 'authorized' | 'none' | 'pending' | 'expired' | 'error' | 'loading'>>({})

const refreshOAuthStatuses = async (accountList: EmailAccount[]) => {
  const outlookAccounts = accountList.filter(a => a.type === 'outlook')
  for (const acct of outlookAccounts) {
    oauthStatusMap.value[acct.id] = 'loading'
  }
  for (const acct of outlookAccounts) {
    try {
      const status = await api.getOAuthStatus(acct.id)
      oauthStatusMap.value[acct.id] = status.status as 'authorized' | 'none' | 'pending' | 'expired' | 'error'
    } catch {
      oauthStatusMap.value[acct.id] = 'none'
    }
  }
}

// AI Settings
const aiSettings = ref<AISettingsResponse | null>(null)
const aiSettingsForm = ref<AISettingsUpdate>({})
const loadingAISettings = ref(false)
const savingAISettings = ref(false)
const availableModels = ref<string[]>([])
const loadingModels = ref(false)
const modelsFetchFailed = ref(false)

// Classifier Settings
const classifierSettings = ref<ClassifierSettingsResponse | null>(null)
const classifierSettingsForm = ref<ClassifierSettingsUpdate>({})
const loadingClassifierSettings = ref(false)
const savingClassifierSettings = ref(false)

const fetchClassifierSettings = async () => {
  loadingClassifierSettings.value = true
  try {
    const res = await api.getClassifierSettings()
    classifierSettings.value = res
    classifierSettingsForm.value = {
      trusted_senders: res.trusted_senders,
      extra_keywords: res.extra_keywords,
    }
  } catch (error) {
    console.error('Failed to fetch Classifier settings', error)
    toastRef.value?.addToast('Failed to fetch Classifier settings', 'error')
  } finally {
    loadingClassifierSettings.value = false
  }
}

const saveClassifierSettings = async () => {
  savingClassifierSettings.value = true
  try {
    await api.updateClassifierSettings(classifierSettingsForm.value)
    toastRef.value?.addToast('Classifier settings saved successfully', 'success')
    await fetchClassifierSettings()
  } catch (error) {
    console.error('Failed to save Classifier settings', error)
    toastRef.value?.addToast('Failed to save Classifier settings', 'error')
  } finally {
    savingClassifierSettings.value = false
  }
}

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

const {
  progress: scanProgress,
  isActive: scanIsActive,
  statusLine: scanStatusLine,
  connect: connectScanProgress,
  disconnect: disconnectScanProgress
} = useScanProgress()

const scanLogs = ref<ScanLog[]>([])
const loadingLogs = ref(false)
const scanning = ref(false)
const expandedLogId = ref<number | null>(null)
const extractionLogs = ref<Record<number, ExtractionLog[]>>({})
const loadingExtractionLogs = ref<Record<number, boolean>>({})

const toggleLogExpansion = async (logId: number) => {
  if (expandedLogId.value === logId) {
    expandedLogId.value = null
    return
  }
  
  expandedLogId.value = logId
  
  if (!extractionLogs.value[logId]) {
    loadingExtractionLogs.value[logId] = true
    try {
      extractionLogs.value[logId] = await api.getExtractionLogs(logId)
    } catch (error) {
      console.error('Failed to load extraction logs', error)
      toastRef.value?.addToast('Failed to load extraction details', 'error')
    } finally {
      loadingExtractionLogs.value[logId] = false
    }
  }
}

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
  password: '',
  outlook_account_type: 'personal'
}

const accountForm = ref<AccountCreate>({ ...defaultAccountForm })
const savingAccount = ref(false)

const accountNameById = (accountId: number) => {
  return accounts.value.find((account) => account.id === accountId)?.name || `#${accountId}`
}

// OAuth Flow State
const showOAuthModal = ref(false)
const oauthInitiateData = ref<OAuthInitiateResponse | null>(null)
const oauthStatusData = ref<OAuthStatusResponse | null>(null)
const oauthAccountId = ref<number | null>(null)
const oauthPollingInterval = ref<number | null>(null)
const oauthTimeRemaining = ref<number>(0)
const oauthCountdownInterval = ref<number | null>(null)

const stopOAuthPolling = () => {
  if (oauthPollingInterval.value) {
    clearInterval(oauthPollingInterval.value)
    oauthPollingInterval.value = null
  }
  if (oauthCountdownInterval.value) {
    clearInterval(oauthCountdownInterval.value)
    oauthCountdownInterval.value = null
  }
}

const closeOAuthModal = () => {
  showOAuthModal.value = false
  stopOAuthPolling()
}

const updateOAuthCountdown = () => {
  if (oauthInitiateData.value?.expires_at) {
    const expiresAt = new Date(oauthInitiateData.value.expires_at).getTime()
    const now = Date.now()
    oauthTimeRemaining.value = Math.max(0, Math.floor((expiresAt - now) / 1000))
  }
}

const formatTimeRemaining = (seconds: number) => {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

const copyDeviceCode = () => {
  if (oauthInitiateData.value?.user_code) {
    navigator.clipboard.writeText(oauthInitiateData.value.user_code)
    toastRef.value?.addToast('Device code copied to clipboard', 'info')
  }
}

const pollOAuthStatus = async (accountId: number) => {
  try {
    const status = await api.getOAuthStatus(accountId)
    oauthStatusData.value = status
    
    if (status.status === 'authorized') {
      stopOAuthPolling()
      oauthStatusMap.value[accountId] = 'authorized'
      toastRef.value?.addToast('Authorization successful! Outlook account is now connected.', 'success')
      setTimeout(() => {
        closeOAuthModal()
        testConnection(accountId)
      }, 2000)
    } else if (status.status === 'expired' || status.status === 'error') {
      stopOAuthPolling()
      oauthStatusMap.value[accountId] = status.status as 'expired' | 'error'
      const detail = status.detail || (status.status === 'expired' ? 'Device code expired. Please try again.' : 'Authorization failed. Please try again.')
      toastRef.value?.addToast(detail, 'error')
    }
  } catch (error) {
    console.error('Failed to poll OAuth status', error)
  }
}

const startOAuthFlow = async (accountId: number) => {
  try {
    oauthAccountId.value = accountId
    oauthStatusData.value = null
    const res = await api.initiateOAuth(accountId)
    oauthInitiateData.value = res
    
    if (res.status === 'authorized') {
      toastRef.value?.addToast('Account is already authorized', 'success')
    } else if (res.status === 'pending') {
      showOAuthModal.value = true
      updateOAuthCountdown()
      
      oauthCountdownInterval.value = window.setInterval(updateOAuthCountdown, 1000)
      
      // Poll every 3 seconds
      oauthPollingInterval.value = window.setInterval(() => pollOAuthStatus(accountId), 3000)
    }
  } catch (error) {
    console.error('Failed to initiate OAuth', error)
    toastRef.value?.addToast('Failed to start authorization flow', 'error')
  }
}

const fetchAccounts = async () => {
  loadingAccounts.value = true
  try {
    accounts.value = await api.getAccounts()
    refreshOAuthStatuses(accounts.value)
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
    // Connection will be handled by the watcher or connectScanProgress if it's already active
    // We can also let the polling logic kick in. But ideally we just rely on SSE.
    connectScanProgress()
  } catch (error) {
    console.error('Failed to trigger scan', error)
    toastRef.value?.addToast('Failed to trigger scan', 'error')
    scanning.value = false
  }
}

watch(() => scanProgress.value.phase, (newPhase) => {
  if (newPhase === 'done' || newPhase === 'error') {
    scanning.value = false
    fetchLogs()
  } else if (newPhase === 'scanning') {
    scanning.value = true
  }
})

const testConnection = async (accountId: number) => {
  toastRef.value?.addToast('Connection test initiated...', 'info')
  try {
    const response = await api.testConnection(accountId)
    if (response.ok) {
      toastRef.value?.addToast('Connection successful!', 'success')
    } else {
      toastRef.value?.addToast(response.detail || 'Connection failed', 'error')
      if (response.detail?.toLowerCase().includes('outlook authorization required')) {
        startOAuthFlow(accountId)
      }
    }
  } catch (error: any) {
    console.error('Failed to test connection', error)
    const detail = error?.response?.data?.detail
    toastRef.value?.addToast(detail || 'Connection test failed', 'error')
    if (detail?.toLowerCase().includes('outlook authorization required')) {
      startOAuthFlow(accountId)
    }
  }
}

const openAddModal = () => {
  editingAccountId.value = null
  accountForm.value = { ...defaultAccountForm, outlook_account_type: 'personal' }
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
    outlook_account_type: account.outlook_account_type || 'personal',
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
      if (accountForm.value.type === 'outlook') {
        updateData.outlook_account_type = accountForm.value.outlook_account_type
      }
      if (accountForm.value.password) {
        updateData.password = accountForm.value.password
      }
      const updatedAccount = await api.updateAccount(editingAccountId.value, updateData)
      toastRef.value?.addToast('Account updated successfully', 'success')
      
      if (updatedAccount.type === 'outlook') {
        startOAuthFlow(updatedAccount.id)
      }
    } else {
      const createData = { ...accountForm.value }
      if (createData.type !== 'outlook') {
        delete createData.outlook_account_type
      }
      const newAccount = await api.createAccount(createData)
      toastRef.value?.addToast('Account created successfully', 'success')
      
      if (newAccount.type === 'outlook') {
        startOAuthFlow(newAccount.id)
      }
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
  fetchClassifierSettings()
  connectScanProgress()
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
          <button
            @click="activeTab = 'classifier'"
            :class="[activeTab === 'classifier' ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors flex items-center']"
          >
            <svg class="mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"></path></svg>
            分类规则
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
                    <span v-if="account.type === 'outlook' && account.outlook_account_type" class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border"
                      :class="account.outlook_account_type === 'personal' ? 'bg-slate-100 text-slate-700 border-slate-200' : 'bg-blue-50 text-blue-700 border-blue-200'">
                      {{ account.outlook_account_type === 'personal' ? 'Personal' : 'Organizational' }}
                    </span>
                    <span v-if="account.type === 'outlook'" class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border transition-colors"
                      :class="oauthStatusMap[account.id] === 'authorized'
                        ? 'bg-green-50 text-green-700 border-green-200'
                        : oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined
                          ? 'bg-slate-100 text-slate-400 border-slate-200'
                          : 'bg-amber-50 text-amber-700 border-amber-200'">
                      <svg v-if="oauthStatusMap[account.id] === 'authorized'" class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/>
                      </svg>
                      <svg v-else-if="oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined" class="w-3 h-3 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                      </svg>
                      <svg v-else class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
                      </svg>
                      <span v-if="oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined">Checking...</span>
                      <span v-else>{{ oauthStatusMap[account.id] === 'authorized' ? 'Authenticated' : 'Not authenticated' }}</span>
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
                  <button
                    v-if="account.type === 'outlook'"
                    @click="startOAuthFlow(account.id)"
                    class="text-sm transition-colors hidden sm:inline-block px-3 py-1 rounded font-medium border"
                    :class="oauthStatusMap[account.id] === 'authorized'
                      ? 'text-slate-600 hover:text-slate-900 border-slate-300 hover:bg-slate-50'
                      : oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined
                        ? 'text-slate-400 border-slate-200 cursor-wait'
                        : 'text-white bg-blue-600 hover:bg-blue-700 border-blue-600 hover:border-blue-700'"
                    :disabled="oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined"
                  >
                    <span v-if="oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined">Checking...</span>
                    <span v-else-if="oauthStatusMap[account.id] === 'authorized'">Re-authenticate</span>
                    <span v-else>Authenticate</span>
                  </button>
                  <button @click="testConnection(account.id)" class="text-sm text-green-600 hover:text-green-900 transition-colors hidden sm:inline-block border border-green-200 px-3 py-1 rounded hover:bg-green-50 font-medium">Test Connection</button>
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
              <p v-if="scanning" class="mt-2 text-sm font-medium text-blue-600 truncate max-w-lg" :title="scanStatusLine">
                {{ scanStatusLine }}
              </p>
            </div>
            <div class="mt-4 sm:mt-0 flex flex-col sm:items-end">
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

        <ScanProgressBar :progress="scanProgress" v-if="scanProgress.phase !== 'idle'" />

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
                <template v-else v-for="log in scanLogs" :key="log.id">
                  <tr @click="toggleLogExpansion(log.id)" class="hover:bg-slate-50 cursor-pointer transition-colors" :class="{'bg-blue-50/30': expandedLogId === log.id}">
                    <td class="px-6 py-4 whitespace-nowrap text-sm">
                      <div class="flex items-center">
                        <svg class="w-4 h-4 mr-2 text-slate-400 transition-transform" :class="{'rotate-90': expandedLogId === log.id}" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
                        <span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-800 border border-slate-200 shadow-sm truncate max-w-[150px]" :title="accountNameById(log.email_account_id)">
                          {{ accountNameById(log.email_account_id) }}
                        </span>
                      </div>
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
                <!-- Extraction Logs Detail Row -->
                <tr v-if="expandedLogId === log.id" :key="`detail-${log.id}`" class="bg-slate-50 border-t border-slate-100">
                  <td colspan="6" class="px-6 py-4">
                    <div v-if="loadingExtractionLogs[log.id]" class="text-sm text-slate-500 py-2 animate-pulse">
                      Loading extraction details...
                    </div>
                    <div v-else-if="!extractionLogs[log.id] || extractionLogs[log.id].length === 0" class="text-sm text-slate-500 py-2">
                      No emails processed in this scan.
                    </div>
                    <div v-else class="space-y-3">
                      <div class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Extraction Details</div>
                      <div v-for="ext in extractionLogs[log.id]" :key="ext.id" class="bg-white border border-slate-200 rounded-lg p-3 text-sm flex flex-col gap-2 shadow-sm">
                        <div class="flex items-center justify-between gap-4">
                          <div class="flex-1 font-medium text-slate-800 truncate" :title="ext.email_subject">{{ ext.email_subject || 'No Subject' }}</div>
                          <div class="flex items-center gap-2">
                            <span 
                              class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border"
                              :class="{
                                'bg-green-50 text-green-700 border-green-200': ext.outcome === 'success',
                                'bg-slate-100 text-slate-700 border-slate-200': ext.outcome === 'skipped' || ext.outcome === 'no_invoice',
                                'bg-red-50 text-red-700 border-red-200': ext.outcome === 'error' || ext.outcome === 'failed'
                              }"
                            >
                              {{ ext.outcome }}
                            </span>
                            <span v-if="ext.confidence" class="text-xs text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200" title="Confidence Score">
                              {{ (ext.confidence * 100).toFixed(0) }}%
                            </span>
                          </div>
                        </div>
                        
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-slate-600 mt-1">
                          <div v-if="ext.attachment_filename" class="flex items-center gap-1 truncate" :title="ext.attachment_filename">
                            <svg class="w-3.5 h-3.5 text-slate-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path></svg>
                            {{ ext.attachment_filename }}
                          </div>
                          <div v-if="ext.invoice_no" class="flex items-center gap-1">
                            <svg class="w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                            {{ ext.invoice_no }}
                          </div>
                        </div>
                        
                        <div v-if="ext.error_detail" class="mt-1 text-xs text-red-600 bg-red-50 p-2 rounded border border-red-100">
                          {{ ext.error_detail }}
                        </div>
                      </div>
                    </div>
                  </td>
                </tr>
                </template>
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

      <!-- Classifier Settings Tab -->
      <div v-if="activeTab === 'classifier'" class="space-y-6">
        <div class="sm:flex sm:items-center sm:justify-between bg-white p-4 rounded-xl shadow-sm border border-slate-200">
          <div>
            <h3 class="text-lg leading-6 font-medium text-slate-900">Email Classification Rules</h3>
            <p class="mt-1 max-w-2xl text-sm text-slate-500">Configure how emails are identified as containing invoices.</p>
          </div>
        </div>

        <div class="bg-white shadow-sm sm:rounded-xl border border-slate-200 p-6">
          <div v-if="loadingClassifierSettings" class="p-6 text-center text-slate-500 animate-pulse">
            Loading settings...
          </div>
          <form v-else @submit.prevent="saveClassifierSettings" class="space-y-8">
            <div>
              <label class="block text-sm font-medium text-slate-700 mb-1">
                Trusted Sender Domains / Addresses
              </label>
              <p class="text-sm text-slate-500 mb-3">Emails from these senders are immediately classified as invoice-related without LLM. One per line. Example: tax.gov.cn, einvoice@company.com</p>
              <textarea v-model="classifierSettingsForm.trusted_senders" rows="5" class="shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border" placeholder="tax.gov.cn&#10;einvoice@company.com"></textarea>
            </div>

            <div>
              <label class="block text-sm font-medium text-slate-700 mb-1">
                Extra Invoice Keywords
              </label>
              <p class="text-sm text-slate-500 mb-3">Additional keywords (beyond the built-in list) that identify invoice emails. One per line. Example: 财务, billing</p>
              <textarea v-model="classifierSettingsForm.extra_keywords" rows="4" class="shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border" placeholder="财务&#10;billing"></textarea>
            </div>

            <div class="bg-slate-50 rounded-lg p-4 border border-slate-200">
              <h4 class="text-sm font-medium text-slate-700 mb-2">Built-in keywords (always active)</h4>
              <div class="flex flex-wrap gap-2">
                <span v-for="keyword in ['发票', 'invoice', '开票', '报销', '税务', 'fapiao', 'receipt', 'vat', '增值税', '电子发票', '数电票']" :key="keyword" class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-200 text-slate-800">
                  {{ keyword }}
                </span>
              </div>
            </div>

            <div class="pt-5 border-t border-slate-200 flex justify-end">
              <button
                type="submit"
                :disabled="savingClassifierSettings"
                class="inline-flex items-center justify-center py-2 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 transition-colors"
              >
                <svg v-if="savingClassifierSettings" class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                {{ savingClassifierSettings ? 'Saving...' : 'Save' }}
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
                      <label class="block text-sm font-medium text-slate-700">
                        {{ accountForm.type === 'outlook' ? 'Microsoft Account Email' : 'Username / Email' }}
                      </label>
                      <input type="text" v-model="accountForm.username" required class="mt-1 focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border" :placeholder="accountForm.type === 'outlook' ? 'you@outlook.com or you@live.cn' : 'user@example.com'">
                      <p v-if="accountForm.type === 'outlook'" class="mt-1 text-xs text-slate-500">Enter your Microsoft account email address. OAuth2 authentication happens after saving.</p>
                    </div>

                    <div v-if="accountForm.type === 'outlook'" class="mt-4">
                      <label class="block text-sm font-medium text-slate-700 mb-2">Microsoft Account Type</label>
                      <div class="space-y-3">
                        <div class="flex items-start">
                          <div class="flex items-center h-5">
                            <input id="type-personal" v-model="accountForm.outlook_account_type" type="radio" value="personal" class="focus:ring-blue-500 h-4 w-4 text-blue-600 border-slate-300">
                          </div>
                          <div class="ml-3 text-sm">
                            <label for="type-personal" class="font-medium text-slate-700">Personal</label>
                            <p class="text-slate-500">For @outlook.com, @live.com, @live.cn, @hotmail.com, @msn.com</p>
                          </div>
                        </div>
                        <div class="flex items-start">
                          <div class="flex items-center h-5">
                            <input id="type-org" v-model="accountForm.outlook_account_type" type="radio" value="organizational" class="focus:ring-blue-500 h-4 w-4 text-blue-600 border-slate-300">
                          </div>
                          <div class="ml-3 text-sm">
                            <label for="type-org" class="font-medium text-slate-700">Organizational</label>
                            <p class="text-slate-500">For work or school accounts with a custom domain</p>
                            <p v-if="accountForm.outlook_account_type === 'organizational'" class="text-xs text-slate-500 mt-1">For Azure AD / Entra ID work accounts. May require Azure App Registration for some organizations.</p>
                          </div>
                        </div>
                      </div>
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

    <!-- OAuth Flow Modal -->
    <div v-if="showOAuthModal" class="fixed inset-0 z-10 overflow-y-auto" aria-labelledby="oauth-modal-title" role="dialog" aria-modal="true">
      <div class="flex items-end justify-center min-h-screen pt-4 px-4 pb-20 text-center sm:block sm:p-0">
        <div class="fixed inset-0 bg-slate-500 bg-opacity-75 transition-opacity" aria-hidden="true" @click="closeOAuthModal"></div>
        <span class="hidden sm:inline-block sm:align-middle sm:h-screen" aria-hidden="true">&#8203;</span>
        <div class="inline-block align-bottom bg-white rounded-xl text-left overflow-hidden shadow-xl transform transition-all sm:my-8 sm:align-middle sm:max-w-xl sm:w-full">
          <div class="bg-white px-4 pt-5 pb-4 sm:p-6 sm:pb-4">
            <div class="sm:flex sm:items-start">
              <div class="mx-auto flex-shrink-0 flex items-center justify-center h-12 w-12 rounded-full bg-blue-100 sm:mx-0 sm:h-10 sm:w-10">
                <svg class="h-6 w-6 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
              </div>
              <div class="mt-3 text-center sm:mt-0 sm:ml-4 sm:text-left w-full">
                <h3 class="text-lg leading-6 font-medium text-slate-900" id="oauth-modal-title">
                  Outlook Authorization Required
                </h3>
                <div class="mt-4 space-y-4">
                  <p class="text-sm text-slate-500">
                    Open the following URL in your browser and enter the code below:
                  </p>
                  
                  <div v-if="oauthInitiateData?.verification_uri" class="mt-2 text-center">
                    <a :href="oauthInitiateData.verification_uri" target="_blank" rel="noopener noreferrer" class="text-blue-600 hover:text-blue-800 font-medium hover:underline flex items-center justify-center gap-1">
                      {{ oauthInitiateData.verification_uri }}
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                    </a>
                  </div>
                  
                  <div class="bg-slate-50 p-4 rounded-lg border border-slate-200 mt-4 text-center">
                    <div class="text-3xl font-mono tracking-widest text-slate-800 font-bold select-all flex items-center justify-center gap-3">
                      {{ oauthInitiateData?.user_code }}
                      <button @click="copyDeviceCode" type="button" class="text-slate-400 hover:text-blue-600 transition-colors" title="Copy to clipboard">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
                      </button>
                    </div>
                  </div>
                  
                  <div v-if="oauthStatusData?.status === 'authorized'" class="mt-4 p-3 bg-green-50 text-green-700 rounded-md border border-green-100 flex items-center">
                    <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                    ✓ Authorization successful!
                  </div>
                  <div v-else-if="oauthStatusData?.status === 'error' || oauthStatusData?.status === 'expired' || oauthTimeRemaining <= 0" class="mt-4 p-3 bg-red-50 text-red-700 rounded-md border border-red-100 flex items-center justify-between">
                    <div class="flex items-center">
                      <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                      {{ oauthStatusData?.status === 'expired' || oauthTimeRemaining <= 0 ? 'Code expired.' : (oauthStatusData?.detail || 'Authorization failed.') }}
                    </div>
                    <button @click="startOAuthFlow(oauthAccountId!)" type="button" class="text-sm font-medium underline text-red-700 hover:text-red-900">Try Again</button>
                  </div>
                  <div v-else class="mt-4 flex flex-col items-center space-y-2">
                    <div class="flex items-center text-sm text-slate-500">
                      <svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-blue-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                      </svg>
                      Waiting for authorization...
                    </div>
                    <div class="text-xs font-medium" :class="oauthTimeRemaining < 60 ? 'text-orange-600' : 'text-slate-400'">
                      Code expires in: {{ formatTimeRemaining(oauthTimeRemaining) }}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div class="bg-slate-50 px-4 py-3 sm:px-6 sm:flex sm:flex-row-reverse">
            <button type="button" @click="closeOAuthModal" class="mt-3 w-full inline-flex justify-center rounded-md border border-slate-300 shadow-sm px-4 py-2 bg-white text-base font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 sm:mt-0 sm:ml-3 sm:w-auto sm:text-sm transition-colors">
              {{ oauthStatusData?.status === 'authorized' ? 'Close' : 'Cancel' }}
            </button>
          </div>
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
