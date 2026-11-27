<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import Toast from '@/components/Toast.vue'
import ScanProgressBar from '@/components/ScanProgressBar.vue'
import AISettingsPanel from '@/components/settings/AISettingsPanel.vue'
import ClassifierSettingsPanel from '@/components/settings/ClassifierSettingsPanel.vue'
import InvoiceProfilesPanel from '@/components/settings/InvoiceProfilesPanel.vue'
import NotificationSettingsPanel from '@/components/settings/NotificationSettingsPanel.vue'
import { useScanProgress } from '@/composables/useScanProgress'
import { api } from '@/api/client'
import type {
  EmailAccount,
  AccountCreate,
  AccountUpdate,
  ScanLog,
  ExtractionLog,
  ExtractionSummary,
  OAuthInitiateResponse,
  OAuthStatusResponse
} from '@/types'
import { aggregateByEmail, summarizeEmails } from './scanAggregation'

type SettingsTabId = 'accounts' | 'scan' | 'profiles' | 'ai' | 'classifier' | 'notifications'

interface NavGroup {
  title: string
  items: { id: SettingsTabId; label: string }[]
}

const navGroups: NavGroup[] = [
  {
    title: '采集与开票',
    items: [
      { id: 'accounts', label: '邮箱与来源' },
      { id: 'scan', label: '扫描记录' },
      { id: 'profiles', label: '发票抬头' },
    ],
  },
  {
    title: '智能与通知',
    items: [
      { id: 'ai', label: '模型与路由' },
      { id: 'classifier', label: '过滤规则' },
      { id: 'notifications', label: 'Telegram 通知' },
    ],
  },
]

const validTabs = new Set<SettingsTabId>([
  'accounts',
  'scan',
  'profiles',
  'ai',
  'classifier',
  'notifications',
])

const route = useRoute()
const initialTab = typeof route.query.tab === 'string' && validTabs.has(route.query.tab as SettingsTabId)
  ? (route.query.tab as SettingsTabId)
  : 'accounts'
const activeTab = ref<SettingsTabId>(initialTab)

watch(
  () => route.query.tab,
  (tab) => {
    if (typeof tab === 'string' && validTabs.has(tab as SettingsTabId)) {
      activeTab.value = tab as SettingsTabId
    }
  }
)

const handlePanelToast = (message: string, type: 'success' | 'error' | 'info') => {
  toastRef.value?.addToast(message, type)
}

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
const scanAccountId = ref<number>(0) // 0 = all accounts
const expandedLogId = ref<number | null>(null)
const extractionLogs = ref<Record<number, ExtractionLog[]>>({})
const extractionSummaries = ref<Record<number, ExtractionSummary>>({})
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
      const [logs, summary] = await Promise.all([
        api.getExtractionLogs(logId),
        api.getScanLogSummary(logId).catch(() => null),
      ])
      extractionLogs.value[logId] = logs
      if (summary) {
        extractionSummaries.value[logId] = summary
      }
    } catch (error) {
      console.error('Failed to load extraction logs', error)
      toastRef.value?.addToast('加载提取明细失败', 'error')
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
  outlook_account_type: 'personal',
  playwright_storage_state: '',
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
    toastRef.value?.addToast('设备验证码已复制到剪贴板', 'info')
  }
}

const pollOAuthStatus = async (accountId: number) => {
  try {
    const status = await api.getOAuthStatus(accountId)
    oauthStatusData.value = status

    if (status.status === 'authorized') {
      stopOAuthPolling()
      oauthStatusMap.value[accountId] = 'authorized'
      toastRef.value?.addToast('授权成功，Outlook 账号已连接', 'success')
      setTimeout(() => {
        closeOAuthModal()
        testConnection(accountId)
      }, 2000)
    } else if (status.status === 'expired' || status.status === 'error') {
      stopOAuthPolling()
      oauthStatusMap.value[accountId] = status.status as 'expired' | 'error'
      const detail = status.detail || (status.status === 'expired' ? '设备码已过期，请重试。' : '授权失败，请重试。')
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
      toastRef.value?.addToast('该账号已完成授权', 'success')
    } else if (res.status === 'pending') {
      showOAuthModal.value = true
      updateOAuthCountdown()

      oauthCountdownInterval.value = window.setInterval(updateOAuthCountdown, 1000)

      // Poll every 3 seconds
      oauthPollingInterval.value = window.setInterval(() => pollOAuthStatus(accountId), 3000)
    }
  } catch (error) {
    console.error('Failed to initiate OAuth', error)
    toastRef.value?.addToast('启动 Outlook 授权失败', 'error')
  }
}

const fetchAccounts = async () => {
  loadingAccounts.value = true
  try {
    accounts.value = await api.getAccounts()
    refreshOAuthStatuses(accounts.value)
  } catch (error) {
    console.error('Failed to fetch accounts', error)
    toastRef.value?.addToast('加载邮箱账号失败', 'error')
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
    toastRef.value?.addToast('加载扫描记录失败', 'error')
  } finally {
    loadingLogs.value = false
  }
}

const scanOptionsForm = ref<{ unread_only: boolean; since_preset: string; since_custom: string }>({
  unread_only: false,
  since_preset: 'all',
  since_custom: '',
})

const sincePresets: { value: string; label: string }[] = [
  { value: 'all', label: '全部时间' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: '6m', label: '近 6 个月' },
  { value: '1y', label: '近 1 年' },
  { value: 'custom', label: '自定义日期…' },
]

const hasPop3Account = computed(() => accounts.value.some(a => a.type === 'pop3'))

const resolveScanSince = (): string | null => {
  const preset = scanOptionsForm.value.since_preset
  if (preset === 'all') return null
  if (preset === 'custom') {
    if (!scanOptionsForm.value.since_custom) return null
    return new Date(scanOptionsForm.value.since_custom).toISOString()
  }
  const now = new Date()
  const delta: Record<string, number> = { '7d': 7, '30d': 30, '6m': 30 * 6, '1y': 365 }
  const days = delta[preset]
  if (!days) return null
  const since = new Date(now.getTime() - days * 86400 * 1000)
  return since.toISOString()
}

const triggerScan = async (full: boolean = false) => {
  scanning.value = true
  try {
    const since = resolveScanSince()
    const accountId = scanAccountId.value === 0 ? null : scanAccountId.value
    const res = await api.triggerScan({
      full,
      unread_only: scanOptionsForm.value.unread_only,
      since,
      email_account_id: accountId,
    })
    const label = full ? '全量重扫' : '增量扫描'
    const detailBits: string[] = []
    if (scanOptionsForm.value.unread_only) detailBits.push('仅未读')
    if (since) detailBits.push(`自 ${since.slice(0, 10)} 起`)
    const suffix = detailBits.length ? `（${detailBits.join('，')}）` : ''
    toastRef.value?.addToast(`${label}${suffix}已启动：${res.status}`, 'success')
    connectScanProgress()
  } catch (error) {
    console.error('Failed to trigger scan', error)
    toastRef.value?.addToast('启动扫描失败', 'error')
    scanning.value = false
  }
}

watch(() => scanProgress.value.phase, (newPhase) => {
  if (newPhase === 'done' || newPhase === 'error') {
    scanning.value = false
    fetchLogs()
  } else if (newPhase === 'scanning') {
    scanning.value = true
    activeTab.value = 'scan'
  }
})

const testConnection = async (accountId: number) => {
  toastRef.value?.addToast('正在测试连接…', 'info')
  try {
    const response = await api.testConnection(accountId)
    if (response.ok) {
      toastRef.value?.addToast('连接成功', 'success')
    } else {
      toastRef.value?.addToast(response.detail || '连接失败', 'error')
      if (response.detail?.toLowerCase().includes('outlook authorization required')) {
        startOAuthFlow(accountId)
      }
    }
  } catch (error: any) {
    console.error('Failed to test connection', error)
    const detail = error?.response?.data?.detail
    toastRef.value?.addToast(detail || '连接测试失败', 'error')
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
      if (accountForm.value.type === 'cursor' && accountForm.value.playwright_storage_state) {
        updateData.playwright_storage_state = accountForm.value.playwright_storage_state
      }
      if (accountForm.value.password) {
        updateData.password = accountForm.value.password
      }
      const updatedAccount = await api.updateAccount(editingAccountId.value, updateData)
      toastRef.value?.addToast('账号配置已更新', 'success')

      if (updatedAccount.type === 'outlook') {
        startOAuthFlow(updatedAccount.id)
      }
    } else {
      const createData = { ...accountForm.value }
      if (createData.type !== 'outlook') {
        delete createData.outlook_account_type
      }
      if (createData.type !== 'cursor') {
        delete createData.playwright_storage_state
      }
      const newAccount = await api.createAccount(createData)
      toastRef.value?.addToast('账号已添加', 'success')

      if (newAccount.type === 'outlook') {
        startOAuthFlow(newAccount.id)
      }
    }
    showAccountModal.value = false
    fetchAccounts()
  } catch (error) {
    console.error('Failed to save account', error)
    toastRef.value?.addToast('保存账号失败', 'error')
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
      toastRef.value?.addToast('账号已删除', 'success')
      fetchAccounts()
    } catch (error) {
      console.error('Failed to delete account', error)
      toastRef.value?.addToast('删除账号失败', 'error')
    } finally {
      deletingAccountId.value = null
    }
  }
}

const formatDate = (dateStr: string | null) => {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleString()
}

const formatScanState = (raw: string | null, accountType: string = 'imap') => {
  if (!raw) return '尚未扫描'
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return '暂无扫描状态'

    if (accountType === 'outlook') {
      const timestamps = Object.values(parsed)
        .filter((v): v is string => typeof v === 'string')
        .map((v) => new Date(v).getTime())
        .filter((t) => !Number.isNaN(t))
      const folderCount = Object.keys(parsed).length
      if (!folderCount) return '暂无扫描状态'
      if (timestamps.length === 0) return `${folderCount} 个文件夹 · 尚未同步`
      const latest = new Date(Math.max(...timestamps))
      return `${folderCount} 个文件夹 · 上次同步 ${latest.toLocaleString()}`
    }

    const entries = Object.values(parsed as Record<string, any>)
    let totalMessages = 0
    let maxUid = 0
    for (const value of entries) {
      if (value && typeof value === 'object') {
        totalMessages += Number(value.messages || 0)
        maxUid = Math.max(maxUid, Number(value.uid || 0))
      }
    }
    const folders = Object.keys(parsed).length
    if (!folders) return '暂无扫描状态'
    const messagePart = totalMessages > 0 ? `${totalMessages.toLocaleString()} 封邮件` : '0 封邮件'
    const uidPart = maxUid > 0 ? `UID ${maxUid}` : 'UID 未知'
    return `${folders} 个文件夹 · ${messagePart} · ${uidPart}`
  } catch {
    return '暂无扫描状态'
  }
}

const outcomeBadgeClass = (outcome: string) => {
  if (outcome === 'saved' || outcome === 'success') return 'bg-mint-50 text-mint-800 border-mint-200'
  if (outcome === 'duplicate') return 'bg-[#EBF2F5] text-[#2F5566] border-[#B4CBD6]'
  if (outcome === 'skipped_seen' || outcome === 'not_invoice') return 'bg-cream-300/70 text-ink-500 border-cream-500'
  if (outcome === 'low_confidence' || outcome === 'not_vat_invoice' || outcome === 'scam_detected') {
    return 'bg-[#FDF5E6] text-[#8B5E22] border-[#EEDAB6]'
  }
  return 'bg-cinnabar-50 text-cinnabar-600 border-cinnabar-200'
}

onMounted(() => {
  fetchAccounts()
  fetchLogs()
  connectScanProgress()
})

onUnmounted(() => {
  stopOAuthPolling()
  disconnectScanProgress()
})
</script>

<template>
  <AppLayout>
    <div class="flex flex-col lg:flex-row lg:items-start gap-6">
      <!-- Mobile Horizontal Scrollable Category Tabs -->
      <nav
        class="lg:hidden -mx-1 px-1 flex items-center gap-2 overflow-x-auto pb-1"
        aria-label="设置分组导航"
      >
        <template v-for="group in navGroups" :key="group.title">
          <button
            v-for="item in group.items"
            :key="item.id"
            type="button"
            @click="activeTab = item.id"
            class="flex-shrink-0 inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-medium border transition-colors whitespace-nowrap"
            :class="activeTab === item.id
              ? 'bg-mint-600 text-white border-mint-600 shadow-sm'
              : 'bg-cream-100 text-ink-700 border-cream-400 hover:bg-cream-300/70'"
          >
            <span>{{ item.label }}</span>
            <span
              v-if="item.id === 'scan' && scanIsActive"
              class="w-1.5 h-1.5 rounded-full bg-peach-200 animate-pulse"
            />
          </button>
        </template>
      </nav>

      <!-- Desktop Left 200px Warm Paper Vertical Grouped Menu -->
      <aside class="hidden lg:block w-[200px] shrink-0 sticky top-20">
        <div class="ledger-card p-3 space-y-5">
          <div
            v-for="(group, idx) in navGroups"
            :key="group.title"
            :class="idx > 0 ? 'receipt-dashed pt-4' : ''"
          >
            <div class="px-2.5 mb-2 text-[11px] font-semibold text-ink-400 tracking-wide">
              {{ group.title }}
            </div>
            <div class="space-y-1">
              <button
                v-for="item in group.items"
                :key="item.id"
                type="button"
                @click="activeTab = item.id"
                class="w-full flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors text-left"
                :class="activeTab === item.id
                  ? 'bg-mint-100/90 text-mint-800 border border-mint-200/80 shadow-sm'
                  : 'text-ink-700 hover:bg-cream-200 hover:text-ink-900 border border-transparent'"
              >
                <span class="flex items-center gap-2.5 min-w-0">
                  <!-- Icons per tab -->
                  <svg v-if="item.id === 'accounts'" class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                  <svg v-else-if="item.id === 'scan'" class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  <svg v-else-if="item.id === 'profiles'" class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                  <svg v-else-if="item.id === 'ai'" class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M13 10V3L4 14h7v7l9-11h-7z" />
                  </svg>
                  <svg v-else-if="item.id === 'classifier'" class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
                  </svg>
                  <svg v-else class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                  </svg>
                  <span class="truncate">{{ item.label }}</span>
                </span>
                <span
                  v-if="item.id === 'scan' && scanIsActive"
                  class="w-2 h-2 rounded-full bg-mint-500 animate-pulse flex-shrink-0"
                  title="扫描进行中"
                />
              </button>
            </div>
          </div>
        </div>
      </aside>

      <!-- Right Content Area -->
      <div class="flex-1 min-w-0 space-y-6">
        <!-- Tab 1: Accounts -->
        <div v-if="activeTab === 'accounts'" class="space-y-6">
          <div class="ledger-card px-6 py-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h3 class="font-display text-lg font-bold text-ink-900">邮箱与来源</h3>
              <p class="mt-1 text-sm text-ink-500">管理用于自动收取发票的邮箱与外部票源账号。</p>
            </div>
            <button @click="openAddModal" class="btn-mint self-start sm:self-auto">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
              </svg>
              <span>添加账号</span>
            </button>
          </div>

          <div class="ledger-card overflow-hidden">
            <ul role="list" class="divide-y divide-cream-400">
              <li v-if="loadingAccounts" class="p-8 text-center text-sm text-ink-500">
                正在读取邮箱列表…
              </li>
              <li v-else-if="accounts.length === 0" class="p-12 text-center paper-dots">
                <div class="mx-auto w-14 h-14 rounded-2xl bg-mint-100 border border-mint-200 flex items-center justify-center text-mint-700 mb-4">
                  <svg class="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.6" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                </div>
                <h3 class="font-display text-lg font-bold text-ink-900">连接您的第一个收票邮箱</h3>
                <p class="mt-2 text-sm text-ink-500 max-w-md mx-auto mb-6">
                  支持 IMAP、POP3、Outlook (OAuth)、QQ 邮箱与外部网页票源，配好后将自动收取并归档新发票。
                </p>
                <button @click="openAddModal" class="btn-mint px-5 py-2.5">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                  </svg>
                  <span>添加发票来源</span>
                </button>
              </li>
              <li
                v-else
                v-for="account in accounts"
                :key="account.id"
                class="p-5 sm:p-6 hover:bg-cream-200/55 transition-colors"
              >
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                  <div class="min-w-0 space-y-1.5">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="text-sm font-semibold text-ink-900 truncate">{{ account.name }}</span>
                      <span
                        class="stamp-badge"
                        :class="account.is_active
                          ? 'bg-mint-50 text-mint-800 border-mint-200'
                          : 'bg-cream-300/80 text-ink-500 border-cream-500'"
                      >
                        {{ account.is_active ? '启用中' : '已停用' }}
                      </span>
                      <span class="stamp-badge bg-cream-200 text-ink-700 border-cream-400 uppercase num-mono">
                        {{ account.type }}
                      </span>
                      <span
                        v-if="account.type === 'outlook' && account.outlook_account_type"
                        class="stamp-badge"
                        :class="account.outlook_account_type === 'personal'
                          ? 'bg-cream-200 text-ink-700 border-cream-400'
                          : 'bg-[#EBF2F5] text-[#2F5566] border-[#B4CBD6]'"
                      >
                        {{ account.outlook_account_type === 'personal' ? '个人版' : '企业/组织' }}
                      </span>
                      <span
                        v-if="account.type === 'outlook'"
                        class="stamp-badge transition-colors"
                        :class="oauthStatusMap[account.id] === 'authorized'
                          ? 'bg-mint-50 text-mint-800 border-mint-200'
                          : oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined
                            ? 'bg-cream-200 text-ink-400 border-cream-400'
                            : 'bg-[#FDF5E6] text-[#8B5E22] border-[#EEDAB6]'"
                      >
                        <svg v-if="oauthStatusMap[account.id] === 'authorized'" class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
                        </svg>
                        <svg v-else-if="oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined" class="w-3 h-3 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                        </svg>
                        <svg v-else class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        <span v-if="oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined">检测中…</span>
                        <span v-else>{{ oauthStatusMap[account.id] === 'authorized' ? '已授权' : '未授权' }}</span>
                      </span>
                    </div>
                    <div class="text-sm text-ink-700 num-mono truncate">{{ account.username }}</div>
                    <div class="text-xs text-ink-400" :title="account.last_scan_uid || '尚未扫描'">
                      同步状态：{{ formatScanState(account.last_scan_uid, account.type) }}
                    </div>
                  </div>

                  <div class="flex flex-wrap items-center gap-2 flex-shrink-0">
                    <button
                      v-if="account.type === 'outlook'"
                      type="button"
                      @click="startOAuthFlow(account.id)"
                      class="text-xs px-3 py-1.5 rounded-xl font-medium transition-colors"
                      :class="oauthStatusMap[account.id] === 'authorized'
                        ? 'btn-cream py-1.5 px-3 text-xs'
                        : oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined
                          ? 'bg-cream-200 text-ink-400 border border-cream-400 cursor-wait'
                          : 'btn-mint py-1.5 px-3 text-xs'"
                      :disabled="oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined"
                    >
                      <span v-if="oauthStatusMap[account.id] === 'loading' || oauthStatusMap[account.id] === undefined">检测中…</span>
                      <span v-else-if="oauthStatusMap[account.id] === 'authorized'">重新授权</span>
                      <span v-else>去授权</span>
                    </button>
                    <button
                      type="button"
                      @click="testConnection(account.id)"
                      class="inline-flex items-center px-3 py-1.5 rounded-xl text-xs font-medium text-mint-800 bg-mint-50 border border-mint-200 hover:bg-mint-100 transition-colors"
                    >
                      测试连接
                    </button>
                    <button type="button" @click="openEditModal(account)" class="btn-cream py-1.5 px-3 text-xs">
                      编辑
                    </button>
                    <button
                      type="button"
                      @click="confirmDeleteAccount(account.id)"
                      class="inline-flex items-center px-3 py-1.5 rounded-xl text-xs font-medium text-cinnabar-600 bg-cinnabar-50/70 border border-cinnabar-200 hover:bg-cinnabar-50 transition-colors"
                    >
                      删除
                    </button>
                  </div>
                </div>
              </li>
            </ul>
          </div>
        </div>

        <!-- Tab 2: Scan Operations & Logs -->
        <div v-if="activeTab === 'scan'" class="space-y-6">
          <div class="ledger-card p-6">
            <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-5">
              <div class="space-y-3.5 min-w-0 flex-1">
                <div>
                  <h3 class="font-display text-lg font-bold text-ink-900">手动扫描</h3>
                  <p class="mt-1 text-sm text-ink-500">立即扫描全部启用账号或指定邮箱，并实时追踪解析进度。</p>
                </div>

                <div class="flex flex-wrap items-center gap-3 text-sm">
                  <div class="flex items-center gap-2">
                    <label class="text-xs font-semibold text-ink-700 whitespace-nowrap">扫描账号</label>
                    <select
                      v-model="scanAccountId"
                      :disabled="scanning"
                      class="input-warm py-1.5 px-3 w-auto min-w-[160px]"
                    >
                      <option :value="0">全部账号</option>
                      <option v-for="acc in accounts" :key="acc.id" :value="acc.id">
                        {{ acc.name }} ({{ acc.type }})
                      </option>
                    </select>
                  </div>

                  <div class="flex flex-wrap items-center gap-2">
                    <label class="text-xs font-semibold text-ink-700 whitespace-nowrap" for="scan-since-preset">时间范围</label>
                    <select
                      id="scan-since-preset"
                      v-model="scanOptionsForm.since_preset"
                      :disabled="scanning"
                      class="input-warm py-1.5 px-3 w-auto"
                    >
                      <option v-for="p in sincePresets" :key="p.value" :value="p.value">{{ p.label }}</option>
                    </select>
                    <input
                      v-if="scanOptionsForm.since_preset === 'custom'"
                      type="datetime-local"
                      v-model="scanOptionsForm.since_custom"
                      :disabled="scanning"
                      class="input-warm py-1.5 px-3 w-auto num-mono"
                    />
                  </div>
                </div>

                <div class="flex flex-wrap items-center gap-4">
                  <label class="inline-flex items-center gap-2 text-sm text-ink-700 cursor-pointer">
                    <input
                      type="checkbox"
                      v-model="scanOptionsForm.unread_only"
                      :disabled="scanning"
                      class="h-4 w-4 rounded border-cream-500 accent-mint-600"
                    />
                    <span>仅扫描未读邮件</span>
                  </label>
                </div>

                <p
                  v-if="hasPop3Account && scanOptionsForm.unread_only"
                  class="text-xs text-[#8B5E22] bg-[#FDF5E6] border border-[#EEDAB6] rounded-xl px-3 py-2 inline-block"
                >
                  提示：POP3 协议不支持服务端按未读筛选，此选项对 POP3 邮箱会自动忽略。
                </p>

                <p
                  v-if="scanning && scanStatusLine"
                  class="text-xs font-medium text-mint-700 bg-mint-50 border border-mint-200 rounded-xl px-3 py-2 truncate max-w-xl"
                  :title="scanStatusLine"
                >
                  {{ scanStatusLine }}
                </p>
              </div>

              <div class="flex flex-wrap sm:flex-nowrap items-center gap-2.5 flex-shrink-0">
                <button
                  type="button"
                  @click="triggerScan(true)"
                  :disabled="scanning"
                  class="btn-cream py-2.5 px-4"
                >
                  全量重扫
                </button>
                <button
                  type="button"
                  @click="triggerScan(false)"
                  :disabled="scanning"
                  class="btn-mint py-2.5 px-5"
                >
                  <svg
                    v-if="scanning"
                    class="animate-spin h-4 w-4 text-white"
                    xmlns="http://www.w3.org/2000/svg"
                    fill="none"
                    viewBox="0 0 24 24"
                  >
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  <svg v-else class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  <span>{{ scanning ? '扫描中…' : '立即扫描' }}</span>
                </button>
              </div>
            </div>
          </div>

          <ScanProgressBar
            :progress="scanProgress"
            v-if="scanProgress.phase !== 'idle' || scanProgress.emails_processed > 0"
          />

          <div class="ledger-card overflow-hidden">
            <div class="px-6 py-4 border-b border-cream-400 bg-cream-300/35 flex items-center justify-between">
              <h3 class="font-display text-base font-bold text-ink-900">扫描记录</h3>
              <span class="text-xs text-ink-400">点击任意记录可展开查看每封邮件的提取与去重明细</span>
            </div>

            <div class="overflow-x-auto">
              <table class="min-w-full divide-y divide-cream-400">
                <thead class="bg-cream-300/45">
                  <tr>
                    <th scope="col" class="px-5 py-3 text-left text-xs font-semibold text-ink-500">邮箱账号</th>
                    <th scope="col" class="px-5 py-3 text-left text-xs font-semibold text-ink-500">开始时间</th>
                    <th scope="col" class="px-5 py-3 text-left text-xs font-semibold text-ink-500">完成时间</th>
                    <th scope="col" class="px-5 py-3 text-right text-xs font-semibold text-ink-500">扫描邮件</th>
                    <th scope="col" class="px-5 py-3 text-right text-xs font-semibold text-ink-500">新发票</th>
                    <th scope="col" class="px-5 py-3 text-left text-xs font-semibold text-ink-500">状态</th>
                  </tr>
                </thead>
                <tbody class="bg-cream-100 divide-y divide-cream-400">
                  <tr v-if="loadingLogs">
                    <td colspan="6" class="px-6 py-6 text-center text-sm text-ink-500">正在加载扫描记录…</td>
                  </tr>
                  <tr v-else-if="scanLogs.length === 0">
                    <td colspan="6" class="px-6 py-10 text-center text-sm text-ink-400">暂无扫描记录</td>
                  </tr>
                  <template v-else v-for="log in scanLogs" :key="log.id">
                    <tr
                      @click="toggleLogExpansion(log.id)"
                      class="hover:bg-cream-200/70 cursor-pointer transition-colors"
                      :class="{ 'bg-mint-50/45': expandedLogId === log.id }"
                    >
                      <td class="px-5 py-3.5 whitespace-nowrap text-sm">
                        <div class="flex items-center gap-2">
                          <svg
                            class="w-4 h-4 text-ink-400 transition-transform flex-shrink-0"
                            :class="{ 'rotate-90 text-mint-600': expandedLogId === log.id }"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                          </svg>
                          <span
                            class="inline-flex items-center px-2.5 py-0.5 rounded-lg text-xs font-medium bg-cream-200 text-ink-900 border border-cream-400 truncate max-w-[160px]"
                            :title="accountNameById(log.email_account_id)"
                          >
                            {{ accountNameById(log.email_account_id) }}
                          </span>
                        </div>
                      </td>
                      <td class="px-5 py-3.5 whitespace-nowrap text-xs text-ink-500 num-mono">{{ formatDate(log.started_at) }}</td>
                      <td class="px-5 py-3.5 whitespace-nowrap text-xs text-ink-500 num-mono">{{ formatDate(log.finished_at) }}</td>
                      <td class="px-5 py-3.5 whitespace-nowrap text-sm text-ink-900 text-right num-mono">{{ log.emails_scanned }}</td>
                      <td class="px-5 py-3.5 whitespace-nowrap text-sm font-semibold text-mint-700 text-right num-mono">{{ log.invoices_found }}</td>
                      <td class="px-5 py-3.5 text-sm">
                        <span
                          v-if="log.error_message"
                          class="stamp-badge bg-cinnabar-50 text-cinnabar-600 border-cinnabar-200"
                          :title="log.error_message"
                        >
                          异常
                        </span>
                        <span
                          v-else-if="log.finished_at"
                          class="stamp-badge bg-mint-50 text-mint-800 border-mint-200"
                        >
                          已完成
                        </span>
                        <span
                          v-else
                          class="stamp-badge bg-[#FDF5E6] text-[#8B5E22] border-[#EEDAB6]"
                        >
                          扫描中
                        </span>
                      </td>
                    </tr>

                    <!-- Fix 8 Per-Email Aggregated Extraction Detail Row -->
                    <tr v-if="expandedLogId === log.id" :key="`detail-${log.id}`" class="bg-cream-200/65">
                      <td colspan="6" class="px-5 sm:px-6 py-4">
                        <div v-if="loadingExtractionLogs[log.id]" class="text-sm text-ink-500 py-2">
                          正在加载提取明细…
                        </div>
                        <div v-else-if="!extractionLogs[log.id] || extractionLogs[log.id].length === 0" class="text-sm text-ink-400 py-2">
                          本次扫描没有需要处理的邮件。
                        </div>
                        <div v-else class="space-y-3.5">
                          <!-- Per-email summary banner -->
                          <div class="bg-cream-100 border border-cream-400 rounded-xl p-4 shadow-paper space-y-2.5">
                            <div class="text-xs font-semibold text-ink-500 tracking-wide">按邮件汇总</div>
                            <div class="flex flex-wrap items-center gap-2 text-sm">
                              <span class="font-semibold text-ink-900 num-mono">
                                {{ summarizeEmails(aggregateByEmail(extractionLogs[log.id])).total_emails }} 封邮件
                              </span>
                              <span class="text-ink-300">·</span>
                              <span class="stamp-badge bg-mint-50 text-mint-800 border-mint-200">
                                保存 {{ summarizeEmails(aggregateByEmail(extractionLogs[log.id])).saved }} 张新发票
                              </span>
                              <span
                                class="stamp-badge bg-[#EBF2F5] text-[#2F5566] border-[#B4CBD6]"
                                title="Same invoice_no already exists — correctly deduped, no action needed"
                              >
                                去重 {{ summarizeEmails(aggregateByEmail(extractionLogs[log.id])).duplicates }} 封
                              </span>
                              <span
                                class="stamp-badge bg-cream-200 text-ink-500 border-cream-400"
                                title="之前已扫描处理过的附件，直接跳过"
                              >
                                已见 {{ summarizeEmails(aggregateByEmail(extractionLogs[log.id])).skipped }} 封
                              </span>
                              <span class="stamp-badge bg-cream-200 text-ink-500 border-cream-400">
                                非发票 {{ summarizeEmails(aggregateByEmail(extractionLogs[log.id])).not_invoice }} 封
                              </span>
                              <span
                                v-if="summarizeEmails(aggregateByEmail(extractionLogs[log.id])).errors > 0"
                                class="stamp-badge bg-cinnabar-50 text-cinnabar-600 border-cinnabar-200"
                              >
                                错误 {{ summarizeEmails(aggregateByEmail(extractionLogs[log.id])).errors }} 封
                              </span>
                            </div>

                            <div
                              v-if="extractionSummaries[log.id] && Object.keys(extractionSummaries[log.id].parse_methods).length > 0"
                              class="flex flex-wrap items-center gap-2 pt-1 text-xs text-ink-500"
                            >
                              <span class="font-medium text-ink-400">解析方式：</span>
                              <span
                                v-for="(count, method) in extractionSummaries[log.id].parse_methods"
                                :key="method"
                                class="inline-flex items-center gap-1 bg-[#F3EFFA] text-[#5B4583] border border-[#DDD3F0] px-2 py-0.5 rounded-md num-mono"
                              >
                                <span>{{ method }}</span>
                                <span class="opacity-60">·</span>
                                <span>{{ count }}</span>
                              </span>
                            </div>

                            <div
                              v-if="extractionSummaries[log.id] && Object.keys(extractionSummaries[log.id].classification_tiers).length > 0"
                              class="flex flex-wrap items-center gap-2 text-xs text-ink-500"
                            >
                              <span class="font-medium text-ink-400">分类层级：</span>
                              <span
                                v-for="(count, tier) in extractionSummaries[log.id].classification_tiers"
                                :key="tier"
                                class="inline-flex items-center gap-1 bg-cream-200 text-ink-700 border border-cream-400 px-2 py-0.5 rounded-md num-mono"
                              >
                                <span>{{ tier }}</span>
                                <span class="opacity-60">·</span>
                                <span>{{ count }}</span>
                              </span>
                            </div>
                          </div>

                          <div class="text-xs font-semibold text-ink-500 tracking-wide">已处理邮件明细</div>
                          <div class="space-y-2.5">
                            <div
                              v-for="agg in aggregateByEmail(extractionLogs[log.id])"
                              :key="agg.email_uid || agg.extractions[0].id"
                              class="bg-cream-100 border border-cream-400 rounded-xl p-3.5 text-sm flex flex-col gap-2 shadow-paper"
                            >
                              <div class="flex items-center justify-between gap-3">
                                <div class="flex-1 font-medium text-ink-900 truncate" :title="agg.email_subject">
                                  {{ agg.email_subject || '无主题邮件' }}
                                </div>
                                <div class="flex items-center gap-1.5 flex-wrap flex-shrink-0">
                                  <span
                                    v-if="agg.highest_tier != null"
                                    class="inline-flex items-center px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-cream-200 text-ink-700 border border-cream-400 num-mono"
                                    :title="`分类层级 T${agg.highest_tier}`"
                                  >
                                    T{{ agg.highest_tier }}
                                  </span>
                                  <span class="stamp-badge" :class="outcomeBadgeClass(agg.best_outcome)">
                                    {{ agg.best_outcome }}
                                  </span>
                                </div>
                              </div>

                              <div v-if="agg.invoice_no" class="flex items-center gap-1.5 text-xs text-ink-500">
                                <svg class="w-3.5 h-3.5 text-ink-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                </svg>
                                <span class="num-mono text-ink-700">{{ agg.invoice_no }}</span>
                              </div>

                              <div
                                v-if="agg.extractions.length > 1 || agg.extractions[0].attachment_filename"
                                class="flex flex-wrap gap-1.5 text-xs"
                              >
                                <span
                                  v-for="ext in agg.extractions"
                                  :key="ext.id"
                                  class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border"
                                  :class="outcomeBadgeClass(ext.outcome)"
                                  :title="(ext.parse_method || '') + (ext.parse_format ? ' · ' + ext.parse_format : '') + (ext.error_detail ? ' — ' + ext.error_detail : '')"
                                >
                                  <span v-if="ext.parse_format" class="num-mono uppercase">{{ ext.parse_format }}</span>
                                  <span v-else-if="ext.attachment_filename" class="truncate max-w-[18ch]">{{ ext.attachment_filename }}</span>
                                  <span v-else>{{ ext.outcome }}</span>
                                  <span class="opacity-50">·</span>
                                  <span>{{ ext.outcome }}</span>
                                  <span v-if="ext.confidence" class="opacity-70 num-mono">· {{ (ext.confidence * 100).toFixed(0) }}%</span>
                                </span>
                              </div>

                              <div
                                v-for="ext in agg.extractions.filter(e => e.error_detail)"
                                :key="`err-${ext.id}`"
                                class="text-xs text-cinnabar-700 bg-cinnabar-50 px-3 py-2 rounded-lg border border-cinnabar-200"
                              >
                                <span v-if="ext.parse_format" class="num-mono uppercase font-semibold mr-1">{{ ext.parse_format }}:</span>
                                <span>{{ ext.error_detail }}</span>
                              </div>
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

        <!-- Tab 3: Invoice Profiles -->
        <div v-if="activeTab === 'profiles'" class="space-y-6">
          <InvoiceProfilesPanel @toast="handlePanelToast" />
        </div>

        <!-- Tab 4: AI Settings -->
        <div v-if="activeTab === 'ai'" class="space-y-6">
          <AISettingsPanel @toast="handlePanelToast" />
        </div>

        <!-- Tab 5: Classifier Settings -->
        <div v-if="activeTab === 'classifier'" class="space-y-6">
          <ClassifierSettingsPanel @toast="handlePanelToast" />
        </div>

        <!-- Tab 6: Telegram Notifications -->
        <div v-if="activeTab === 'notifications'" class="space-y-6">
          <NotificationSettingsPanel @toast="handlePanelToast" />
        </div>
      </div>
    </div>

    <!-- Account Form Modal -->
    <div
      v-if="showAccountModal"
      class="fixed inset-0 z-50 overflow-y-auto flex items-center justify-center p-4"
      aria-labelledby="modal-title"
      role="dialog"
      aria-modal="true"
    >
      <div class="fixed inset-0 bg-ink-900/35 backdrop-blur-[2px]" aria-hidden="true" @click="showAccountModal = false" />
      <div class="relative z-10 ledger-card w-full max-w-lg shadow-paper-lg overflow-hidden">
        <form @submit.prevent="saveAccount">
          <div class="px-6 py-5 border-b border-cream-400 bg-cream-300/35 flex items-center justify-between">
            <h3 class="font-display text-lg font-bold text-ink-900" id="modal-title">
              {{ editingAccountId ? '编辑邮箱账号' : '添加发票来源' }}
            </h3>
            <button
              type="button"
              @click="showAccountModal = false"
              class="text-ink-400 hover:text-ink-700 p-1 rounded-lg transition-colors"
              aria-label="关闭"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div class="p-6 space-y-4 max-h-[72vh] overflow-y-auto">
            <div>
              <label class="block text-xs font-semibold text-ink-700 mb-1.5">账号名称</label>
              <input
                type="text"
                v-model="accountForm.name"
                required
                class="input-warm"
                placeholder="例如：工作邮箱 / 个人 QQ"
              />
            </div>

            <div>
              <label class="block text-xs font-semibold text-ink-700 mb-1.5">接入协议</label>
              <select v-model="accountForm.type" class="input-warm">
                <option value="imap">IMAP</option>
                <option value="pop3">POP3</option>
                <option value="outlook">Outlook (OAuth)</option>
                <option value="qq">QQ 邮箱</option>
                <option value="cursor">Cursor 网页账单</option>
              </select>
            </div>

            <!-- Cursor-specific: storage_state JSON -->
            <template v-if="accountForm.type === 'cursor'">
              <div>
                <label class="block text-xs font-semibold text-ink-700 mb-1.5">登录凭证 JSON (Storage State)</label>
                <textarea
                  v-model="accountForm.playwright_storage_state"
                  rows="6"
                  required
                  class="input-warm num-mono text-xs"
                  placeholder='{"cookies": [...], "origins": [...]}'
                />
                <p class="mt-1.5 text-xs text-ink-400">
                  请粘贴通过 <code class="num-mono">scripts/cursor_login_local.py</code> 导出的登录态 JSON。
                </p>
              </div>
            </template>

            <template v-if="accountForm.type !== 'outlook' && accountForm.type !== 'cursor'">
              <div class="grid grid-cols-3 gap-3">
                <div class="col-span-2">
                  <label class="block text-xs font-semibold text-ink-700 mb-1.5">服务器地址</label>
                  <input
                    type="text"
                    v-model="accountForm.host"
                    :required="accountForm.type !== 'outlook'"
                    class="input-warm num-mono"
                    placeholder="imap.example.com"
                  />
                </div>
                <div class="col-span-1">
                  <label class="block text-xs font-semibold text-ink-700 mb-1.5">端口</label>
                  <input
                    type="number"
                    v-model="accountForm.port"
                    :required="accountForm.type !== 'outlook'"
                    class="input-warm num-mono"
                    placeholder="993"
                  />
                </div>
              </div>
            </template>

            <div
              v-if="accountForm.type === 'outlook'"
              class="bg-mint-50 p-3.5 rounded-xl border border-mint-200 text-xs text-mint-800 leading-relaxed"
            >
              Outlook 账号使用 OAuth2 安全授权，保存后将自动弹出设备码授权窗口。
            </div>

            <div>
              <label class="block text-xs font-semibold text-ink-700 mb-1.5">
                {{ accountForm.type === 'outlook' ? '微软账号邮箱' : '用户名 / 邮箱地址' }}
              </label>
              <input
                type="text"
                v-model="accountForm.username"
                required
                class="input-warm"
                :placeholder="accountForm.type === 'outlook' ? 'you@outlook.com 或 you@live.cn' : 'user@example.com'"
              />
              <p v-if="accountForm.type === 'outlook'" class="mt-1 text-xs text-ink-400">
                请输入微软邮箱地址，保存后按引导完成授权即可。
              </p>
            </div>

            <div v-if="accountForm.type === 'outlook'" class="space-y-2.5 pt-1">
              <label class="block text-xs font-semibold text-ink-700">微软账号类型</label>
              <div class="space-y-2.5">
                <label
                  for="type-personal"
                  class="flex items-start gap-2.5 p-3 rounded-xl border cursor-pointer transition-colors"
                  :class="accountForm.outlook_account_type === 'personal'
                    ? 'bg-mint-50/70 border-mint-300'
                    : 'bg-cream-50 border-cream-400 hover:bg-cream-200/50'"
                >
                  <input
                    id="type-personal"
                    v-model="accountForm.outlook_account_type"
                    type="radio"
                    value="personal"
                    class="mt-0.5 h-4 w-4 accent-mint-600"
                  />
                  <div class="text-xs">
                    <div class="font-semibold text-ink-900">个人账号</div>
                    <p class="text-ink-500 mt-0.5">适用于 @outlook.com、@live.com、@live.cn、@hotmail.com 等个人邮箱</p>
                  </div>
                </label>
                <label
                  for="type-org"
                  class="flex items-start gap-2.5 p-3 rounded-xl border cursor-pointer transition-colors"
                  :class="accountForm.outlook_account_type === 'organizational'
                    ? 'bg-mint-50/70 border-mint-300'
                    : 'bg-cream-50 border-cream-400 hover:bg-cream-200/50'"
                >
                  <input
                    id="type-org"
                    v-model="accountForm.outlook_account_type"
                    type="radio"
                    value="organizational"
                    class="mt-0.5 h-4 w-4 accent-mint-600"
                  />
                  <div class="text-xs">
                    <div class="font-semibold text-ink-900">企业 / 组织账号</div>
                    <p class="text-ink-500 mt-0.5">适用于公司或学校自定义域名邮箱</p>
                    <p v-if="accountForm.outlook_account_type === 'organizational'" class="text-ink-400 mt-1">
                      部分组织开启了管理员审批，可能需要配合配置应用注册。
                    </p>
                  </div>
                </label>
              </div>
            </div>

            <div v-if="accountForm.type !== 'outlook'">
              <label class="block text-xs font-semibold text-ink-700 mb-1.5">
                密码 / 授权码
                <span v-if="editingAccountId" class="text-ink-400 font-normal ml-1">（留空则保持不变）</span>
              </label>
              <input
                type="password"
                v-model="accountForm.password"
                :required="!editingAccountId && accountForm.type !== 'outlook'"
                class="input-warm"
              />
            </div>
          </div>

          <div class="bg-cream-300/40 border-t border-cream-400 px-6 py-3.5 flex items-center justify-end gap-2.5">
            <button type="button" @click="showAccountModal = false" class="btn-cream">取消</button>
            <button type="submit" :disabled="savingAccount" class="btn-mint">
              {{ savingAccount ? '保存中…' : '保存账号' }}
            </button>
          </div>
        </form>
      </div>
    </div>

    <!-- OAuth Device Code Modal -->
    <div
      v-if="showOAuthModal"
      class="fixed inset-0 z-50 overflow-y-auto flex items-center justify-center p-4"
      aria-labelledby="oauth-modal-title"
      role="dialog"
      aria-modal="true"
    >
      <div class="fixed inset-0 bg-ink-900/35 backdrop-blur-[2px]" aria-hidden="true" @click="closeOAuthModal" />
      <div class="relative z-10 ledger-card w-full max-w-lg shadow-paper-lg overflow-hidden">
        <div class="p-6 space-y-4">
          <div class="flex items-start gap-3.5">
            <div class="flex-shrink-0 flex items-center justify-center h-10 w-10 rounded-xl bg-mint-100 border border-mint-200 text-mint-700">
              <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
            </div>
            <div class="min-w-0 flex-1">
              <h3 class="font-display text-lg font-bold text-ink-900" id="oauth-modal-title">
                Outlook 安全授权
              </h3>
              <p class="mt-1 text-sm text-ink-500">
                请在浏览器中打开下方验证链接，并输入设备验证码完成授权：
              </p>
            </div>
          </div>

          <div v-if="oauthInitiateData?.verification_uri" class="text-center pt-1">
            <a
              :href="oauthInitiateData.verification_uri"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex items-center justify-center gap-1.5 text-sm font-medium text-mint-700 hover:text-mint-800 underline underline-offset-4"
            >
              <span>{{ oauthInitiateData.verification_uri }}</span>
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </a>
          </div>

          <div class="bg-cream-200 p-5 rounded-2xl border border-dashed border-cream-500 text-center">
            <div class="text-3xl num-mono tracking-[0.22em] text-ink-900 font-bold select-all flex items-center justify-center gap-3">
              <span>{{ oauthInitiateData?.user_code }}</span>
              <button
                @click="copyDeviceCode"
                type="button"
                class="p-1.5 rounded-lg text-ink-400 hover:text-mint-700 hover:bg-cream-100 transition-colors"
                title="复制验证码"
              >
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              </button>
            </div>
          </div>

          <div
            v-if="oauthStatusData?.status === 'authorized'"
            class="p-3.5 bg-mint-50 text-mint-800 rounded-xl border border-mint-200 flex items-center gap-2 text-sm"
          >
            <svg class="w-5 h-5 text-mint-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
            </svg>
            <span>授权成功，即将自动验证连接…</span>
          </div>
          <div
            v-else-if="oauthStatusData?.status === 'error' || oauthStatusData?.status === 'expired' || oauthTimeRemaining <= 0"
            class="p-3.5 bg-cinnabar-50 text-cinnabar-700 rounded-xl border border-cinnabar-200 flex items-center justify-between gap-3 text-sm"
          >
            <div class="flex items-center gap-2">
              <svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span>{{ oauthStatusData?.status === 'expired' || oauthTimeRemaining <= 0 ? '验证码已过期。' : (oauthStatusData?.detail || '授权失败。') }}</span>
            </div>
            <button
              @click="startOAuthFlow(oauthAccountId!)"
              type="button"
              class="text-xs font-semibold underline text-cinnabar-700 hover:text-cinnabar-600 flex-shrink-0"
            >
              重新获取
            </button>
          </div>
          <div v-else class="flex flex-col items-center space-y-1.5 pt-1">
            <div class="flex items-center text-sm text-ink-500">
              <svg class="animate-spin mr-2 h-4 w-4 text-mint-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              <span>正在等待浏览器完成授权…</span>
            </div>
            <div
              class="text-xs font-medium num-mono"
              :class="oauthTimeRemaining < 60 ? 'text-cinnabar-600' : 'text-ink-400'"
            >
              剩余有效期：{{ formatTimeRemaining(oauthTimeRemaining) }}
            </div>
          </div>
        </div>

        <div class="bg-cream-300/40 border-t border-cream-400 px-6 py-3.5 flex justify-end">
          <button type="button" @click="closeOAuthModal" class="btn-cream">
            {{ oauthStatusData?.status === 'authorized' ? '关闭' : '取消' }}
          </button>
        </div>
      </div>
    </div>

    <ConfirmDialog
      ref="confirmDialog"
      title="删除邮箱账号"
      message="确认删除该邮箱账号吗？删除后将停止扫描该邮箱，已归档的历史发票不会被删除。"
      confirmText="确认删除"
      @confirm="executeDeleteAccount"
    />
    <Toast ref="toastRef" />
  </AppLayout>
</template>
