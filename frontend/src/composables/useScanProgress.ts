import { ref, computed, onUnmounted } from 'vue'
import { useAuthStore } from '@/stores/auth'

export interface ScanProgressData {
  phase: 'idle' | 'scanning' | 'done' | 'error'
  total_accounts: number
  current_account_idx: number
  current_account_name: string
  total_emails: number
  current_email_idx: number
  current_email_subject: string
  total_attachments: number
  current_attachment_idx: number
  current_attachment_name: string
  current_attachment_url: string
  current_download_outcome: string
  current_parse_method: string
  current_parse_format: string
  last_classification_tier: number
  emails_processed: number
  invoices_found: number
  errors: number
  overall_pct: number
  account_pct: number
  email_pct: number
}

const defaultProgress: ScanProgressData = {
  phase: 'idle',
  total_accounts: 0,
  current_account_idx: 0,
  current_account_name: '',
  total_emails: 0,
  current_email_idx: 0,
  current_email_subject: '',
  total_attachments: 0,
  current_attachment_idx: 0,
  current_attachment_name: '',
  current_attachment_url: '',
  current_download_outcome: '',
  current_parse_method: '',
  current_parse_format: '',
  last_classification_tier: 0,
  emails_processed: 0,
  invoices_found: 0,
  errors: 0,
  overall_pct: 0,
  account_pct: 0,
  email_pct: 0
}

export function useScanProgress() {
  const progress = ref<ScanProgressData>({ ...defaultProgress })
  const eventSource = ref<EventSource | null>(null)
  const pollInterval = ref<number | null>(null)
  const reconnectTimeout = ref<number | null>(null)

  const isActive = computed(() => progress.value.phase === 'scanning')

  const statusLine = computed(() => {
    if (progress.value.phase === 'idle') return 'Ready'
    if (progress.value.phase === 'done') return 'Scan complete'
    if (progress.value.phase === 'error') return 'Scan failed'
    
    let parts = []
    if (progress.value.current_account_name) {
      parts.push(`Account: ${progress.value.current_account_name}`)
    }
    if (progress.value.current_email_subject) {
      parts.push(`Email: ${progress.value.current_email_subject}`)
    }
    if (progress.value.current_attachment_name) {
      parts.push(`File: ${progress.value.current_attachment_name}`)
    }
    
    return parts.length > 0 ? parts.join(' | ') : 'Scanning...'
  })

  const updateProgress = (data: Partial<ScanProgressData>) => {
    progress.value = { ...progress.value, ...data }
  }

  const cleanup = () => {
    if (eventSource.value) {
      eventSource.value.close()
      eventSource.value = null
    }
    if (pollInterval.value !== null) {
      clearInterval(pollInterval.value)
      pollInterval.value = null
    }
    if (reconnectTimeout.value !== null) {
      clearTimeout(reconnectTimeout.value)
      reconnectTimeout.value = null
    }
  }

  const pollProgress = async () => {
    const authStore = useAuthStore()
    if (!authStore.token) return

    try {
      const response = await fetch('/api/v1/scan/progress', {
        headers: {
          'Authorization': `Bearer ${authStore.token}`
        }
      })
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      
      const data = await response.json()
      updateProgress(data)
      
      if (data.phase === 'done' || data.phase === 'error') {
        cleanup()
      }
    } catch (error) {
      console.error('Failed to poll progress', error)
    }
  }

  const connect = () => {
    cleanup()
    const authStore = useAuthStore()
    if (!authStore.token) return

    pollProgress()

    try {
      eventSource.value = new EventSource(`/api/v1/scan/progress/stream?token=${encodeURIComponent(authStore.token)}`)

      const handleProgress = (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data)
          if (data && typeof data === 'object') {
            updateProgress(data)
            if (data.phase === 'done' || data.phase === 'error') {
              cleanup()
            }
          }
        } catch (e) {
          console.error('Error parsing SSE data', e)
        }
      }

      eventSource.value.addEventListener('progress', handleProgress)
      eventSource.value.onmessage = handleProgress

      eventSource.value.onerror = () => {
        cleanup()
        pollInterval.value = window.setInterval(pollProgress, 2000)
        pollProgress()
        reconnectTimeout.value = window.setTimeout(() => {
          if (progress.value.phase === 'scanning') {
            if (pollInterval.value) {
              clearInterval(pollInterval.value)
              pollInterval.value = null
            }
            connect()
          }
        }, 3000)
      }
    } catch (err) {
      console.error('Failed to setup EventSource, using polling', err)
      pollInterval.value = window.setInterval(pollProgress, 2000)
    }
  }

  const disconnect = () => {
    cleanup()
    progress.value = { ...defaultProgress }
  }

  onUnmounted(() => {
    cleanup()
  })

  return {
    progress,
    isActive,
    statusLine,
    connect,
    disconnect
  }
}
