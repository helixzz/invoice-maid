<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '@/api/client'
import type { ClassifierSettingsUpdate } from '@/types'

const emit = defineEmits<{
  (e: 'toast', message: string, type: 'success' | 'error' | 'info'): void
}>()

const form = ref<ClassifierSettingsUpdate>({
  trusted_senders: '',
  extra_keywords: '',
  blocked_senders: '',
})
const saving = ref(false)

const fetchSettings = async () => {
  try {
    const res = await api.getClassifierSettings()
    form.value = {
      trusted_senders: res.trusted_senders,
      extra_keywords: res.extra_keywords,
      blocked_senders: res.blocked_senders || '',
    }
  } catch {
    emit('toast', '加载过滤规则失败', 'error')
  }
}

const saveSettings = async () => {
  saving.value = true
  try {
    await api.updateClassifierSettings(form.value)
    emit('toast', '过滤规则已保存', 'success')
  } catch {
    emit('toast', '保存过滤规则失败', 'error')
  } finally {
    saving.value = false
  }
}

onMounted(fetchSettings)
</script>

<template>
  <div class="ledger-card overflow-hidden">
    <div class="px-6 py-5 border-b border-cream-400 bg-cream-300/35">
      <h3 class="font-display text-lg font-bold text-ink-900">过滤规则</h3>
      <p class="mt-1 text-sm text-ink-500">通过发件人白名单、黑名单与关键词预先筛选邮件，减少无效拉取与模型消耗。</p>
    </div>

    <form @submit.prevent="saveSettings" class="p-6 space-y-5">
      <div>
        <label class="block text-xs font-semibold text-ink-700 mb-1.5">可信发件人白名单（每行一个或逗号分隔）</label>
        <textarea
          v-model="form.trusted_senders"
          rows="4"
          class="input-warm num-mono text-xs sm:text-sm"
          placeholder="tax.gov.cn&#10;einvoice@company.com"
        />
        <p class="mt-1.5 text-xs text-ink-400">来自这些域名或邮箱的邮件会优先按发票邮件处理。</p>
      </div>

      <div class="receipt-dashed pt-5">
        <label class="block text-xs font-semibold text-ink-700 mb-1.5">忽略发件人黑名单（跳过解析与模型判断）</label>
        <textarea
          v-model="form.blocked_senders"
          rows="3"
          class="input-warm num-mono text-xs sm:text-sm"
          placeholder="notifications@github.com&#10;alerts@nas.local"
        />
        <p class="mt-1.5 text-xs text-ink-400">系统已内置常见告警通知拦截；若邮件带有 PDF / XML / OFD 发票附件仍会安全放行。</p>
      </div>

      <div class="receipt-dashed pt-5">
        <label class="block text-xs font-semibold text-ink-700 mb-1.5">补充发票关键词（每行一个或逗号分隔）</label>
        <textarea
          v-model="form.extra_keywords"
          rows="3"
          class="input-warm"
          placeholder="财务&#10;billing"
        />
      </div>

      <div class="flex justify-end pt-2">
        <button
          type="submit"
          :disabled="saving"
          class="btn-mint"
        >
          {{ saving ? '保存中…' : '保存规则' }}
        </button>
      </div>
    </form>
  </div>
</template>
