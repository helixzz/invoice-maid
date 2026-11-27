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
    emit('toast', '加载分类规则失败', 'error')
  }
}

const saveSettings = async () => {
  saving.value = true
  try {
    await api.updateClassifierSettings(form.value)
    emit('toast', '分类规则已保存', 'success')
  } catch {
    emit('toast', '保存分类规则失败', 'error')
  } finally {
    saving.value = false
  }
}

onMounted(fetchSettings)
</script>

<template>
  <div class="bg-white shadow-sm rounded-xl border border-slate-200 overflow-hidden">
    <div class="p-6 border-b border-slate-200">
      <h3 class="text-lg font-medium leading-6 text-slate-900">邮件 Tier-1 分类规则</h3>
      <p class="mt-1 text-sm text-slate-500">通过白名单/黑名单发件人与关键词加速预筛，减少无效拉取与 LLM 消耗。</p>
    </div>

    <form @submit.prevent="saveSettings" class="p-6 space-y-5">
      <div>
        <label class="block text-sm font-medium text-slate-700 mb-1">信任发件人白名单（每行或逗号分隔）</label>
        <textarea
          v-model="form.trusted_senders"
          rows="4"
          class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
          placeholder="tax.gov.cn&#10;einvoice@company.com"
        />
      </div>

      <div>
        <label class="block text-sm font-medium text-slate-700 mb-1">黑名单 / 忽略发件人（不触发拉取与 AI 分类）</label>
        <textarea
          v-model="form.blocked_senders"
          rows="3"
          class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
          placeholder="notifications@github.com&#10;alerts@nas.local"
        />
        <p class="mt-1 text-xs text-slate-500">系统已内置 GitHub/GitLab CI 通知与备份任务告警拦截；若邮件带有 PDF/XML/OFD 发票附件仍会被安全放行。</p>
      </div>

      <div>
        <label class="block text-sm font-medium text-slate-700 mb-1">扩展发票关键词（每行或逗号分隔）</label>
        <textarea
          v-model="form.extra_keywords"
          rows="3"
          class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
          placeholder="财务&#10;billing"
        />
      </div>

      <div class="flex justify-end">
        <button
          type="submit"
          :disabled="saving"
          class="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {{ saving ? '保存中...' : '保存分类规则' }}
        </button>
      </div>
    </form>
  </div>
</template>
