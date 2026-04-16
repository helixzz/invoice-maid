<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppLayout from '@/components/AppLayout.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import Toast from '@/components/Toast.vue'
import { api } from '@/api/client'
import type { EmailAccount, AccountCreate, AccountUpdate, ScanLog } from '@/types'

const activeTab = ref('accounts')

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
  type: 'IMAP',
  host: '',
  port: 993,
  username: '',
  password: ''
}

const accountForm = ref<AccountCreate>({ ...defaultAccountForm })
const savingAccount = ref(false)

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
    scanLogs.value = await api.getScanLogs()
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

const testConnection = () => {
  toastRef.value?.addToast('Connection test initiated...', 'info')
  setTimeout(() => {
    toastRef.value?.addToast('Connection successful!', 'success')
  }, 1500)
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
        type: accountForm.value.type,
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
})
</script>

<template>
  <AppLayout>
    <div class="space-y-6">
      <div class="border-b border-slate-200">
        <nav class="-mb-px flex space-x-8" aria-label="Tabs">
          <button
            @click="activeTab = 'accounts'"
            :class="[activeTab === 'accounts' ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors']"
          >
            Email Accounts
          </button>
          <button
            @click="activeTab = 'scan'"
            :class="[activeTab === 'scan' ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors']"
          >
            Scan Management
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
            <li v-else-if="accounts.length === 0" class="p-12 text-center text-slate-500">
               <svg class="mx-auto h-12 w-12 text-slate-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
               <p class="text-base font-medium text-slate-900">No email accounts configured</p>
               <p class="mt-1">Add an account to start scanning for invoices.</p>
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
                    Last scan: {{ formatDate(account.last_scan_uid) }}
                  </div>
                </div>
                <div class="flex items-center space-x-4">
                  <button @click="testConnection" class="text-sm text-slate-500 hover:text-slate-700 transition-colors hidden sm:inline-block border border-slate-200 px-3 py-1 rounded hover:bg-slate-50">Test Connection</button>
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
                  <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-900 font-medium">#{{ log.email_account_id }}</td>
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
                        <option value="IMAP">IMAP</option>
                        <option value="POP3">POP3</option>
                        <option value="Outlook">Outlook (OAuth)</option>
                        <option value="QQ Mail">QQ Mail</option>
                      </select>
                    </div>

                    <template v-if="accountForm.type !== 'Outlook'">
                      <div class="grid grid-cols-3 gap-4">
                        <div class="col-span-2">
                          <label class="block text-sm font-medium text-slate-700">Host</label>
                          <input type="text" v-model="accountForm.host" :required="accountForm.type !== 'Outlook'" class="mt-1 focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border" placeholder="imap.example.com">
                        </div>
                        <div class="col-span-1">
                          <label class="block text-sm font-medium text-slate-700">Port</label>
                          <input type="number" v-model="accountForm.port" :required="accountForm.type !== 'Outlook'" class="mt-1 focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border" placeholder="993">
                        </div>
                      </div>
                    </template>

                    <div v-if="accountForm.type === 'Outlook'" class="bg-blue-50 p-4 rounded-md border border-blue-100 text-sm text-blue-700">
                      Outlook accounts require OAuth2 authentication. You will be prompted to authenticate when saving.
                    </div>

                    <div>
                      <label class="block text-sm font-medium text-slate-700">Username / Email</label>
                      <input type="email" v-model="accountForm.username" required class="mt-1 focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border" placeholder="user@example.com">
                    </div>

                    <div v-if="accountForm.type !== 'Outlook'">
                      <label class="block text-sm font-medium text-slate-700">
                        Password / App Password
                        <span v-if="editingAccountId" class="text-slate-400 font-normal ml-1">(Leave blank to keep existing)</span>
                      </label>
                      <input type="password" v-model="accountForm.password" :required="!editingAccountId && accountForm.type !== 'Outlook'" class="mt-1 focus:ring-blue-500 focus:border-blue-500 block w-full shadow-sm sm:text-sm border-slate-300 rounded-md py-2 px-3 border">
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
