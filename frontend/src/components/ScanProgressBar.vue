<script setup lang="ts">
import type { ScanProgressData } from '@/composables/useScanProgress'

const props = defineProps<{
  progress: ScanProgressData
}>()
</script>

<template>
  <div v-if="props.progress.phase !== 'idle'" class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden mt-6">
    <div class="px-6 py-5 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
      <div>
        <h3 class="text-lg leading-6 font-medium text-slate-900 flex items-center">
          <svg v-if="props.progress.phase === 'scanning'" class="animate-spin -ml-1 mr-2 h-5 w-5 text-blue-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <svg v-else-if="props.progress.phase === 'done'" class="-ml-1 mr-2 h-5 w-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
          <svg v-else class="-ml-1 mr-2 h-5 w-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
          Scan Progress
        </h3>
        <p v-if="props.progress.phase === 'scanning'" class="mt-1 text-sm text-slate-500 truncate max-w-xl">
          <span v-if="props.progress.current_account_name">Account: <span class="font-medium text-slate-700">{{ props.progress.current_account_name }}</span></span>
          <span v-if="props.progress.current_email_subject" class="ml-2 pl-2 border-l border-slate-300">Email: <span class="font-medium text-slate-700">{{ props.progress.current_email_subject }}</span></span>
          <span v-if="props.progress.current_attachment_name" class="ml-2 pl-2 border-l border-slate-300">File: <span class="font-medium text-slate-700">{{ props.progress.current_attachment_name }}</span></span>
        </p>
      </div>
      <div class="flex gap-4 text-sm">
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

    <div class="p-6 space-y-5">
      <div v-if="props.progress.phase === 'done'" class="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-md mb-2 flex items-center">
        <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        Scan completed successfully. Found {{ props.progress.invoices_found }} invoices.
      </div>
      
      <div v-if="props.progress.phase === 'error'" class="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-md mb-2 flex items-center">
        <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        Scan failed. Please check the logs for details.
      </div>

      <div class="space-y-2">
        <div class="flex justify-between text-xs font-medium text-slate-700">
          <span>Overall Progress ({{ props.progress.current_account_idx }} / {{ props.progress.total_accounts }} Accounts)</span>
          <span>{{ Math.round(props.progress.overall_pct) }}%</span>
        </div>
        <div class="w-full bg-slate-200 rounded-full h-2.5 overflow-hidden">
          <div class="bg-blue-600 h-2.5 rounded-full transition-all duration-500 ease-out" :style="`width: ${props.progress.overall_pct}%`"></div>
        </div>
      </div>

      <div v-if="props.progress.total_accounts > 0" class="space-y-2">
        <div class="flex justify-between text-xs font-medium text-slate-600">
          <span>Current Account: {{ props.progress.current_account_name }}</span>
          <span>{{ Math.round(props.progress.account_pct) }}%</span>
        </div>
        <div class="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
          <div class="bg-indigo-500 h-2 rounded-full transition-all duration-300 ease-out" :style="`width: ${props.progress.account_pct}%`"></div>
        </div>
      </div>

      <div v-if="props.progress.total_emails > 0" class="space-y-2">
        <div class="flex justify-between text-xs font-medium text-slate-500">
          <span>Email Processing ({{ props.progress.current_email_idx }} / {{ props.progress.total_emails }})</span>
          <span>{{ Math.round(props.progress.email_pct) }}%</span>
        </div>
        <div class="w-full bg-slate-200 rounded-full h-1.5 overflow-hidden">
          <div class="bg-sky-400 h-1.5 rounded-full transition-all duration-200 ease-out" :style="`width: ${props.progress.email_pct}%`"></div>
        </div>
      </div>
    </div>
  </div>
</template>
