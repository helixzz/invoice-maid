<script setup lang="ts">
import type { ScanProgressData } from '@/composables/useScanProgress'

const props = defineProps<{
  progress: ScanProgressData
}>()

const tierLabel = (tier: number) => {
  if (tier === 1) return 'T1'
  if (tier === 2) return 'T2'
  if (tier === 3) return 'T3·LLM'
  return ''
}

const parseMethodLabel = (method: string) => {
  const labels: Record<string, string> = {
    qr: 'QR',
    xml_xpath: 'XML',
    ofd_struct: 'OFD',
    llm: 'LLM',
    regex: 'Regex',
  }
  return labels[method] || method
}

const outcomeClass = (outcome: string) => {
  if (outcome === 'saved') return 'text-green-600'
  if (outcome === 'failed' || outcome === 'parse_error') return 'text-red-500'
  if (outcome === 'downloading') return 'text-blue-500'
  return 'text-slate-400'
}
</script>

<template>
  <div v-if="props.progress.phase !== 'idle'" class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden mt-6">
    <div class="px-6 py-5 bg-slate-50 border-b border-slate-200">
      <div class="flex items-start justify-between gap-4">
        <div class="min-w-0 flex-1">
          <h3 class="text-lg leading-6 font-medium text-slate-900 flex items-center">
            <svg v-if="props.progress.phase === 'scanning'" class="animate-spin -ml-1 mr-2 h-5 w-5 text-blue-600 flex-shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <svg v-else-if="props.progress.phase === 'done'" class="-ml-1 mr-2 h-5 w-5 text-green-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
            <svg v-else class="-ml-1 mr-2 h-5 w-5 text-red-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            Scan Progress
          </h3>

          <div v-if="props.progress.phase === 'scanning'" class="mt-2 space-y-1 text-sm">
            <div v-if="props.progress.current_account_name" class="flex items-center gap-2 text-slate-600">
              <span class="text-slate-400 text-xs">Account</span>
              <span class="font-medium text-slate-800 truncate">{{ props.progress.current_account_name }}</span>
            </div>
            <div v-if="props.progress.total_folders > 0" class="flex items-center gap-2 text-slate-600">
              <span class="text-slate-400 text-xs">Folder</span>
              <span class="font-mono text-xs bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded flex-shrink-0">
                {{ props.progress.current_folder_idx }}/{{ props.progress.total_folders }}
              </span>
              <span class="font-medium text-slate-800 truncate">{{ props.progress.current_folder_name || '—' }}</span>
            </div>
            <div v-if="props.progress.folder_fetch_msg" class="flex items-center gap-2 text-slate-500">
              <span class="text-slate-400 text-xs">Fetching</span>
              <span class="text-xs italic truncate">{{ props.progress.folder_fetch_msg }}</span>
            </div>
            <div v-if="props.progress.current_email_subject" class="flex items-center gap-2 text-slate-600">
              <span class="text-slate-400 text-xs">Email</span>
              <span class="font-medium text-slate-800 truncate">{{ props.progress.current_email_subject }}</span>
              <span v-if="props.progress.last_classification_tier" class="flex-shrink-0 text-xs font-mono px-1.5 py-0.5 rounded"
                :class="props.progress.last_classification_tier === 3
                  ? 'bg-amber-100 text-amber-700'
                  : 'bg-slate-100 text-slate-500'">
                {{ tierLabel(props.progress.last_classification_tier) }}
              </span>
            </div>
            <div v-if="props.progress.current_attachment_url && !props.progress.current_attachment_name" class="flex items-center gap-2 text-slate-500">
              <span class="text-slate-400 text-xs">Link</span>
              <span class="truncate text-xs font-mono">{{ props.progress.current_attachment_url }}</span>
              <span :class="outcomeClass(props.progress.current_download_outcome)" class="flex-shrink-0 text-xs capitalize">
                {{ props.progress.current_download_outcome || '…' }}
              </span>
            </div>
            <div v-if="props.progress.current_attachment_name" class="flex items-center gap-2 text-slate-600">
              <span class="text-slate-400 text-xs">File</span>
              <span class="font-medium text-slate-800 truncate">{{ props.progress.current_attachment_name }}</span>
              <span v-if="props.progress.current_parse_method" class="flex-shrink-0 text-xs bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded font-mono">
                {{ parseMethodLabel(props.progress.current_parse_method) }}
              </span>
              <span v-if="props.progress.current_parse_format" class="flex-shrink-0 text-xs bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded uppercase">
                {{ props.progress.current_parse_format }}
              </span>
            </div>
          </div>
        </div>

        <div class="flex gap-4 text-sm flex-shrink-0">
          <div class="flex flex-col items-center">
            <span class="text-slate-500 text-xs uppercase tracking-wider">Emails</span>
            <span class="font-bold text-slate-900">{{ props.progress.emails_processed }}</span>
          </div>
          <div class="flex flex-col items-center">
            <span class="text-slate-500 text-xs uppercase tracking-wider">Invoices</span>
            <span class="font-bold text-blue-600">{{ props.progress.invoices_found }}</span>
          </div>
          <div class="flex flex-col items-center">
            <span class="text-slate-500 text-xs uppercase tracking-wider">Errors</span>
            <span class="font-bold" :class="props.progress.errors > 0 ? 'text-red-600' : 'text-slate-900'">{{ props.progress.errors }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="p-6 space-y-5">
      <div v-if="props.progress.phase === 'done'" class="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-md flex items-center">
        <svg class="w-5 h-5 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        Scan completed. Found {{ props.progress.invoices_found }} invoice{{ props.progress.invoices_found !== 1 ? 's' : '' }} from {{ props.progress.emails_processed }} emails.
      </div>

      <div v-if="props.progress.phase === 'error'" class="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-md flex items-center">
        <svg class="w-5 h-5 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        Scan failed. Check the Recent Scans log for details.
      </div>

      <div class="space-y-2">
        <div class="flex justify-between text-xs font-medium text-slate-700">
          <span>Overall ({{ props.progress.current_account_idx }} / {{ props.progress.total_accounts }} accounts)</span>
          <span>{{ Math.round(props.progress.overall_pct) }}%</span>
        </div>
        <div class="w-full bg-slate-200 rounded-full h-2.5 overflow-hidden">
          <div class="bg-blue-600 h-2.5 rounded-full transition-all duration-500 ease-out" :style="`width: ${props.progress.overall_pct}%`"></div>
        </div>
      </div>

      <div v-if="props.progress.total_accounts > 0" class="space-y-2">
        <div class="flex justify-between text-xs font-medium text-slate-600">
          <span>Account: {{ props.progress.current_account_name || '—' }}</span>
          <span>{{ Math.round(props.progress.account_pct) }}%</span>
        </div>
        <div class="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
          <div class="bg-indigo-500 h-2 rounded-full transition-all duration-300 ease-out" :style="`width: ${props.progress.account_pct}%`"></div>
        </div>
      </div>

      <div v-if="props.progress.total_folders > 0" class="space-y-2">
        <div class="flex justify-between text-xs font-medium text-slate-600">
          <span>Folders ({{ props.progress.current_folder_idx }} / {{ props.progress.total_folders }}) <span v-if="props.progress.current_folder_name" class="text-slate-500">— {{ props.progress.current_folder_name }}</span></span>
          <span>{{ Math.round((props.progress.current_folder_idx / Math.max(props.progress.total_folders, 1)) * 100) }}%</span>
        </div>
        <div class="w-full bg-slate-200 rounded-full h-1.5 overflow-hidden">
          <div class="bg-violet-500 h-1.5 rounded-full transition-all duration-300 ease-out" :style="`width: ${(props.progress.current_folder_idx / Math.max(props.progress.total_folders, 1)) * 100}%`"></div>
        </div>
        <div v-if="props.progress.folder_fetch_msg" class="text-xs text-slate-500 italic truncate">
          {{ props.progress.folder_fetch_msg }}
        </div>
      </div>

      <div v-if="props.progress.total_emails > 0" class="space-y-2">
        <div class="flex justify-between text-xs font-medium text-slate-500">
          <span>Emails ({{ props.progress.current_email_idx }} / {{ props.progress.total_emails }})</span>
          <span>{{ Math.round(props.progress.email_pct) }}%</span>
        </div>
        <div class="w-full bg-slate-200 rounded-full h-1.5 overflow-hidden">
          <div class="bg-sky-400 h-1.5 rounded-full transition-all duration-200 ease-out" :style="`width: ${props.progress.email_pct}%`"></div>
        </div>
      </div>
    </div>
  </div>
</template>
