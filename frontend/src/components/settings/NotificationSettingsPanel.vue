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
  telegram_agent_enabled: false,
  telegram_agent_bot_token: '',
  telegram_agent_allowed_chat_ids: '',
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
      telegram_agent_enabled: res.telegram_agent_enabled,
      telegram_agent_bot_token: '',
      telegram_agent_allowed_chat_ids: res.telegram_agent_allowed_chat_ids,
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
      telegram_agent_enabled: form.value.telegram_agent_enabled,
      telegram_agent_allowed_chat_ids: form.value.telegram_agent_allowed_chat_ids,
    }
    if (form.value.telegram_bot_token && form.value.telegram_bot_token.trim()) {
      payload.telegram_bot_token = form.value.telegram_bot_token.trim()
    }
    if (form.value.telegram_agent_bot_token && form.value.telegram_agent_bot_token.trim()) {
      payload.telegram_agent_bot_token = form.value.telegram_agent_bot_token.trim()
    }
    await api.updateNotificationSettings(payload)
    emit('toast', 'Telegram 通知配置已保存', 'success')
    await fetchSettings()
  } catch (err: any) {
    emit('toast', err?.response?.data?.detail || '保存通知配置失败', 'error')
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
  <div class="ledger-card overflow-hidden">
    <div class="px-6 py-5 border-b border-cream-400 bg-cream-300/35">
      <h3 class="font-display text-lg font-bold text-ink-900">Telegram 通知</h3>
      <p class="mt-1 text-sm text-ink-500">接收新票入账汇总与异常提醒，或绑定独立机器人直接发消息查账、发图拍码开票。</p>
    </div>

    <form @submit.prevent="saveSettings" class="p-6 space-y-6">
      <!-- One-way Notification Push -->
      <div class="space-y-4">
        <h4 class="text-sm font-semibold text-ink-900">一、单向提醒推送</h4>
        <label class="inline-flex items-center gap-2.5 text-sm font-medium text-ink-900 cursor-pointer">
          <input
            type="checkbox"
            v-model="form.telegram_enabled"
            class="h-4 w-4 rounded border-cream-500 accent-mint-600"
          />
          <span>启用 Telegram 单向通知</span>
        </label>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">推送 Bot Token</label>
            <input
              v-model="form.telegram_bot_token"
              type="password"
              :placeholder="settings?.telegram_bot_token_masked || '留空表示保持不变'"
              class="input-warm"
            />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">接收 Chat ID</label>
            <input
              v-model="form.telegram_chat_id"
              type="text"
              placeholder="例如 123456789"
              class="input-warm num-mono"
            />
          </div>
        </div>

        <div class="space-y-2.5 bg-cream-200/70 border border-cream-400 rounded-xl p-4">
          <label class="flex items-start gap-2.5 text-sm text-ink-700 cursor-pointer">
            <input
              type="checkbox"
              v-model="form.telegram_notify_on_error"
              class="mt-0.5 h-4 w-4 rounded border-cream-500 accent-mint-600"
            />
            <span>异常提醒（账号扫描失败、模型熔断或系统故障时推送，带 15 分钟冷却）</span>
          </label>
          <label class="flex items-start gap-2.5 text-sm text-ink-700 cursor-pointer">
            <input
              type="checkbox"
              v-model="form.telegram_notify_on_scan_summary"
              class="mt-0.5 h-4 w-4 rounded border-cream-500 accent-mint-600"
            />
            <span>入账汇总（仅当本次扫描发现新发票时发送一条汇总）</span>
          </label>
        </div>
      </div>

      <!-- Two-way Interactive Maid Bot -->
      <div class="receipt-dashed pt-5 space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h4 class="text-sm font-semibold text-ink-900">二、独立双向助手机器人</h4>
          <span
            v-if="settings?.telegram_agent_token_conflict"
            class="stamp-badge bg-cinnabar-50 text-cinnabar-600 border-cinnabar-200"
          >
            Token 冲突已拦截
          </span>
        </div>

        <div class="text-xs text-[#8B5E22] bg-[#FDF5E6] border border-[#EEDAB6] rounded-xl p-3.5 leading-relaxed">
          请通过 <code class="num-mono font-semibold">@BotFather</code> 单独创建一个新的机器人 Token，切勿与上方推送 Token 或其他正在轮询的机器人共用，否则会触发 409 冲突。系统已内置守卫，检测到重复 Token 时会自动停止监听。
        </div>

        <label class="inline-flex items-center gap-2.5 text-sm font-medium text-ink-900 cursor-pointer">
          <input
            type="checkbox"
            v-model="form.telegram_agent_enabled"
            class="h-4 w-4 rounded border-cream-500 accent-mint-600"
          />
          <span>启用双向助手监听（支持发文字查账、发小票照片自动开票）</span>
        </label>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">独立助手 Bot Token</label>
            <input
              v-model="form.telegram_agent_bot_token"
              type="password"
              :placeholder="settings?.telegram_agent_bot_token_masked || '从 @BotFather 新建的独立 Token'"
              class="input-warm"
            />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">允许对话的 Chat ID 白名单（逗号分隔）</label>
            <input
              v-model="form.telegram_agent_allowed_chat_ids"
              type="text"
              placeholder="例如 123456789（建议填写本人 Chat ID）"
              class="input-warm num-mono"
            />
          </div>
        </div>
      </div>

      <div class="flex items-center justify-end gap-2.5 pt-2">
        <button
          type="button"
          @click="sendTest"
          :disabled="testing"
          class="btn-cream"
        >
          {{ testing ? '发送中…' : '发送测试消息' }}
        </button>
        <button
          type="submit"
          :disabled="saving"
          class="btn-mint"
        >
          {{ saving ? '保存中…' : '保存配置' }}
        </button>
      </div>
    </form>
  </div>
</template>
