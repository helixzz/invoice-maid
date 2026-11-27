<script setup lang="ts">
import type { ScanProgressData } from '@/composables/useScanProgress'

const props = defineProps<{
  progress: ScanProgressData
}>()

const tierLabel = (tier: number) => {
  if (tier === 1) return '规则'
  if (tier === 2) return '特征'
  if (tier === 3) return '模型'
  return ''
}

const parseMethodLabel = (method: string) => {
  const labels: Record<string, string> = {
    qr: '二维码',
    xml_xpath: 'XML',
    ofd_struct: 'OFD',
    llm: '模型',
    regex: '规则',
  }
  return labels[method] || method
}

const outcomeClass = (outcome: string) => {
  if (outcome === 'saved') return 'text-mint-700'
  if (outcome === 'failed' || outcome === 'parse_error') return 'text-cinnabar-600'
  if (outcome === 'downloading') return 'text-[#8B5E22]'
  return 'text-ink-400'
}
</script>

<template>
  <div v-if="props.progress.phase !== 'idle'" class="ledger-card overflow-hidden">
    <div class="px-6 py-4 bg-cream-300/45 border-b border-cream-400">
      <div class="flex items-start justify-between gap-4">
        <div class="min-w-0 flex-1">
          <h3 class="font-display text-base font-bold text-ink-900 flex items-center gap-2">
            <svg
              v-if="props.progress.phase === 'scanning'"
              class="animate-spin h-4 w-4 text-mint-600 flex-shrink-0"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <svg
              v-else-if="props.progress.phase === 'done'"
              class="h-4 w-4 text-mint-600 flex-shrink-0"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M5 13l4 4L19 7" />
            </svg>
            <svg
              v-else
              class="h-4 w-4 text-cinnabar-600 flex-shrink-0"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>实时扫描进度</span>
          </h3>

          <div v-if="props.progress.phase === 'scanning'" class="mt-2.5 space-y-1.5 text-sm">
            <div v-if="props.progress.current_account_name" class="flex items-center gap-2 text-ink-700">
              <span class="text-ink-400 text-xs w-11 flex-shrink-0">邮箱</span>
              <span class="font-medium text-ink-900 truncate">{{ props.progress.current_account_name }}</span>
            </div>
            <div v-if="props.progress.total_folders > 0" class="flex items-center gap-2 text-ink-700">
              <span class="text-ink-400 text-xs w-11 flex-shrink-0">文件夹</span>
              <span class="num-mono text-xs bg-cream-100 border border-cream-400 text-ink-700 px-1.5 py-0.5 rounded flex-shrink-0">
                {{ props.progress.current_folder_idx }}/{{ props.progress.total_folders }}
              </span>
              <span class="font-medium text-ink-900 truncate">{{ props.progress.current_folder_name || '—' }}</span>
            </div>
            <div v-if="props.progress.folder_fetch_msg" class="flex items-center gap-2 text-ink-500">
              <span class="text-ink-400 text-xs w-11 flex-shrink-0">拉取</span>
              <span class="text-xs italic truncate">{{ props.progress.folder_fetch_msg }}</span>
            </div>
            <div v-if="props.progress.current_email_subject" class="flex items-center gap-2 text-ink-700">
              <span class="text-ink-400 text-xs w-11 flex-shrink-0">邮件</span>
              <span class="font-medium text-ink-900 truncate">{{ props.progress.current_email_subject }}</span>
              <span
                v-if="props.progress.last_classification_tier"
                class="flex-shrink-0 text-[11px] font-medium px-1.5 py-0.5 rounded border"
                :class="props.progress.last_classification_tier === 3
                  ? 'bg-[#FDF5E6] text-[#8B5E22] border-[#EEDAB6]'
                  : 'bg-cream-100 text-ink-500 border-cream-400'"
              >
                {{ tierLabel(props.progress.last_classification_tier) }}
              </span>
            </div>
            <div v-if="props.progress.current_attachment_url && !props.progress.current_attachment_name" class="flex items-center gap-2 text-ink-500">
              <span class="text-ink-400 text-xs w-11 flex-shrink-0">链接</span>
              <span class="truncate text-xs num-mono">{{ props.progress.current_attachment_url }}</span>
              <span :class="outcomeClass(props.progress.current_download_outcome)" class="flex-shrink-0 text-xs font-medium">
                {{ props.progress.current_download_outcome || '…' }}
              </span>
            </div>
            <div v-if="props.progress.current_attachment_name" class="flex items-center gap-2 text-ink-700">
              <span class="text-ink-400 text-xs w-11 flex-shrink-0">附件</span>
              <span class="font-medium text-ink-900 truncate">{{ props.progress.current_attachment_name }}</span>
              <span v-if="props.progress.current_parse_method" class="flex-shrink-0 text-[11px] bg-mint-50 text-mint-800 border border-mint-200 px-1.5 py-0.5 rounded font-medium">
                {{ parseMethodLabel(props.progress.current_parse_method) }}
              </span>
              <span v-if="props.progress.current_parse_format" class="flex-shrink-0 text-[11px] bg-cream-100 text-ink-500 border border-cream-400 px-1.5 py-0.5 rounded uppercase num-mono">
                {{ props.progress.current_parse_format }}
              </span>
            </div>
          </div>
        </div>

        <div class="flex gap-3 sm:gap-5 text-sm flex-shrink-0">
          <div class="flex flex-col items-center px-2.5 py-1.5 rounded-xl bg-cream-100/90 border border-cream-400 min-w-[60px]">
            <span class="text-ink-400 text-[11px]">邮件</span>
            <span class="num-mono font-bold text-ink-900">{{ props.progress.emails_processed }}</span>
          </div>
          <div class="flex flex-col items-center px-2.5 py-1.5 rounded-xl bg-mint-50/90 border border-mint-200 min-w-[60px]">
            <span class="text-mint-700 text-[11px]">新发票</span>
            <span class="num-mono font-bold text-mint-700">{{ props.progress.invoices_found }}</span>
          </div>
          <div
            class="flex flex-col items-center px-2.5 py-1.5 rounded-xl border min-w-[60px]"
            :class="props.progress.errors > 0 ? 'bg-cinnabar-50 border-cinnabar-200' : 'bg-cream-100/90 border-cream-400'"
          >
            <span class="text-[11px]" :class="props.progress.errors > 0 ? 'text-cinnabar-600' : 'text-ink-400'">异常</span>
            <span class="num-mono font-bold" :class="props.progress.errors > 0 ? 'text-cinnabar-600' : 'text-ink-900'">
              {{ props.progress.errors }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="p-6 space-y-4">
      <div
        v-if="props.progress.phase === 'done'"
        class="bg-mint-50 border border-mint-200 text-mint-800 px-4 py-3 rounded-xl flex items-center gap-2 text-sm"
      >
        <svg class="w-5 h-5 text-mint-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span>
          扫描完成，共查验 <strong class="num-mono">{{ props.progress.emails_processed }}</strong> 封邮件，收入
          <strong class="num-mono">{{ props.progress.invoices_found }}</strong> 张新发票。
        </span>
      </div>

      <div
        v-if="props.progress.phase === 'error'"
        class="bg-cinnabar-50 border border-cinnabar-200 text-cinnabar-700 px-4 py-3 rounded-xl flex items-center gap-2 text-sm"
      >
        <svg class="w-5 h-5 text-cinnabar-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span>扫描出现异常，可在下方「扫描记录」中展开查看详情。</span>
      </div>

      <div class="space-y-1.5">
        <div class="flex justify-between text-xs font-medium text-ink-700">
          <span>总体进度（<span class="num-mono">{{ props.progress.current_account_idx }} / {{ props.progress.total_accounts }}</span> 个账号）</span>
          <span class="num-mono text-mint-700">{{ Math.round(props.progress.overall_pct) }}%</span>
        </div>
        <div class="w-full bg-cream-300 rounded-full h-2.5 overflow-hidden">
          <div
            class="bg-mint-600 h-2.5 rounded-full transition-all duration-500 ease-out"
            :style="`width: ${props.progress.overall_pct}%`"
          />
        </div>
      </div>

      <div v-if="props.progress.total_accounts > 0" class="space-y-1.5">
        <div class="flex justify-between text-xs font-medium text-ink-500">
          <span>当前账号：{{ props.progress.current_account_name || '—' }}</span>
          <span class="num-mono">{{ Math.round(props.progress.account_pct) }}%</span>
        </div>
        <div class="w-full bg-cream-300 rounded-full h-2 overflow-hidden">
          <div
            class="bg-mint-500 h-2 rounded-full transition-all duration-300 ease-out"
            :style="`width: ${props.progress.account_pct}%`"
          />
        </div>
      </div>

      <div v-if="props.progress.total_folders > 0" class="space-y-1.5">
        <div class="flex justify-between text-xs font-medium text-ink-500">
          <span>
            文件夹（<span class="num-mono">{{ props.progress.current_folder_idx }} / {{ props.progress.total_folders }}</span>）
            <span v-if="props.progress.current_folder_name" class="text-ink-400">· {{ props.progress.current_folder_name }}</span>
          </span>
          <span class="num-mono">{{ Math.round((props.progress.current_folder_idx / Math.max(props.progress.total_folders, 1)) * 100) }}%</span>
        </div>
        <div class="w-full bg-cream-300 rounded-full h-1.5 overflow-hidden">
          <div
            class="bg-mint-400 h-1.5 rounded-full transition-all duration-300 ease-out"
            :style="`width: ${(props.progress.current_folder_idx / Math.max(props.progress.total_folders, 1)) * 100}%`"
          />
        </div>
        <div v-if="props.progress.folder_fetch_msg" class="text-xs text-ink-400 italic truncate">
          {{ props.progress.folder_fetch_msg }}
        </div>
      </div>

      <div v-if="props.progress.total_emails > 0" class="space-y-1.5">
        <div class="flex justify-between text-xs font-medium text-ink-500">
          <span>邮件进度（<span class="num-mono">{{ props.progress.current_email_idx }} / {{ props.progress.total_emails }}</span>）</span>
          <span class="num-mono">{{ Math.round(props.progress.email_pct) }}%</span>
        </div>
        <div class="w-full bg-cream-300 rounded-full h-1.5 overflow-hidden">
          <div
            class="bg-[#C78A3B] h-1.5 rounded-full transition-all duration-200 ease-out"
            :style="`width: ${props.progress.email_pct}%`"
          />
        </div>
      </div>
    </div>
  </div>
</template>
