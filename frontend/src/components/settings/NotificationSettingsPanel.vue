<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '@/api/client'
import type { NotificationSettingsResponse, NotificationSettingsUpdate } from '@/types'

const emit = defineEmits<{
  (e: 'toast', message: string, type: 'success' | 'error' | 'info'): void
}>()

const settings = ref<NotificationSettingsResponse | null>(null)
const form = ref<NotificationSettingsUpdate>({
  telegram_enabled: false,
  telegram_bot_token: '',
  telegram_chat_id: '',
  telegram_notify_on_error: true,
  telegram_notify_on_scan_summary: true,
})
const saving = ref(false)
const testing = ref(false)

const fetchSettings = async () => {
  try {
    const res = await api.getNotificationSettings()
    settings.value = res
    form.value = {
      telegram_enabled: res.telegram_enabled,
      telegram_bot_token: '',
      telegram_chat_id: res.telegram_chat_id,
      telegram_notify_on_error: res.telegram_notify_on_error,
      telegram_notify_on_scan_summary: res.telegram_notify_on_scan_summary,
    }
  } catch {
    emit('toast', '加载通知配置失败', 'error')
  }
}

const saveSettings = async () => {
  saving.value = true
  try {
    const payload: NotificationSettingsUpdate = {
      telegram_enabled: form.value.telegram_enabled,
      telegram_chat_id: form.value.telegram_chat_id,
      telegram_notify_on_error: form.value.telegram_notify_on_error,
      telegram_notify_on_scan_summary: form.value.telegram_notify_on_scan_summary,
    }
    if (form.value.telegram_bot_token && form.value.telegram_bot_token.trim()) {
      payload.telegram_bot_token = form.value.telegram_bot_token.trim()
    }
    await api.updateNotificationSettings(payload)
    emit('toast', 'Telegram 通知配置已保存', 'success')
    await fetchSettings()
  } catch {
    emit('toast', '保存通知配置失败', 'error')
  } finally {
    saving.value = false
  }
}

const sendTest = async () => {
  testing.value = true
  try {
    const res = await api.testNotification()
    emit('toast', res.ok ? '测试消息已发送至 Telegram' : `发送失败：${res.detail}`, res.ok ? 'success' : 'error')
  } catch {
    emit('toast', '发送测试通知失败', 'error')
  } finally {
    testing.value = false
  }
}

onMounted(fetchSettings)
</script>

<template>
  <div class="bg-white shadow-sm rounded-xl border border-slate-200 overflow-hidden">
    <div class="p-6 border-b border-slate-200">
      <h3 class="text-lg font-medium leading-6 text-slate-900">IM 通知（Telegram Bot）</h3>
      <p class="mt-1 text-sm text-slate-500">支持扫描异常 / LLM 熔断告警，以及扫描到新发票时的精简汇总推送。</p>
    </div>

    <form @submit.prevent="saveSettings" class="p-6 space-y-5">
      <label class="flex items-center space-x-2 text-sm font-medium text-slate-800">
        <input type="checkbox" v-model="form.telegram_enabled" class="rounded border-slate-300 text-blue-600" />
        <span>启用 Telegram 通知</span>
      </label>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label class="block text-sm font-medium text-slate-700 mb-1">Telegram Bot Token</label>
          <input
            v-model="form.telegram_bot_token"
            type="password"
            :placeholder="settings?.telegram_bot_token_masked || '123456:ABC...（留空保持不变）'"
            class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-slate-700 mb-1">目标 Chat ID</label>
          <input
            v-model="form.telegram_chat_id"
            type="text"
            placeholder="例如 123456789"
            class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
          />
        </div>
      </div>

      <div class="space-y-2">
        <label class="flex items-center space-x-2 text-sm text-slate-700">
          <input type="checkbox" v-model="form.telegram_notify_on_error" class="rounded border-slate-300 text-blue-600" />
          <span>异常告警（LLM 熔断、账号扫描失败、系统性错误，带 15 分钟防风暴冷却）</span>
        </label>
        <label class="flex items-center space-x-2 text-sm text-slate-700">
          <input type="checkbox" v-model="form.telegram_notify_on_scan_summary" class="rounded border-slate-300 text-blue-600" />
          <span>扫描汇总（仅当本次扫描新增发票 &gt; 0 时推送一条汇总）</span>
        </label>
      </div>

      <div class="flex items-center justify-end space-x-3 pt-2">
        <button
          type="button"
          @click="sendTest"
          :disabled="testing"
          class="px-4 py-2 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          {{ testing ? '发送中...' : '发送测试通知' }}
        </button>
        <button
          type="submit"
          :disabled="saving"
          class="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {{ saving ? '保存中...' : '保存通知配置' }}
        </button>
      </div>
    </form>
  </div>
</template>
