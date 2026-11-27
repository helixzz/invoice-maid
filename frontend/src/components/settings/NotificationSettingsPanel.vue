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
    emit('toast', 'Telegram 通知与女仆 Bot 配置已保存', 'success')
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
  <div class="bg-white shadow-sm rounded-xl border border-slate-200 overflow-hidden">
    <div class="p-6 border-b border-slate-200">
      <h3 class="text-lg font-medium leading-6 text-slate-900">IM 通知 & 独立双向女仆 Bot（Telegram）</h3>
      <p class="mt-1 text-sm text-slate-500">支持单向告警/汇总推送（可安全复用现有 Bot Token），以及独立的双向对话/拍照扫码开票女仆 Bot。</p>
    </div>

    <form @submit.prevent="saveSettings" class="p-6 space-y-6">
      <div class="space-y-4">
        <h4 class="text-sm font-semibold text-slate-800">一、单向通知推送（告警 + 新发票汇总）</h4>
        <label class="flex items-center space-x-2 text-sm font-medium text-slate-800">
          <input type="checkbox" v-model="form.telegram_enabled" class="rounded border-slate-300 text-blue-600" />
          <span>启用 Telegram 单向推送</span>
        </label>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">推送 Bot Token</label>
            <input
              v-model="form.telegram_bot_token"
              type="password"
              :placeholder="settings?.telegram_bot_token_masked || '留空保持不变'"
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
      </div>

      <div class="border-t border-slate-200 pt-5 space-y-4">
        <div class="flex items-center justify-between">
          <h4 class="text-sm font-semibold text-slate-800">二、独立双向发票女仆 Bot（v2.0 查账 / 拍照扫码自动开票）</h4>
          <span
            v-if="settings?.telegram_agent_token_conflict"
            class="px-2 py-0.5 text-xs rounded-full bg-rose-100 text-rose-700 font-medium"
          >
            ⚠️ Token 冲突已拦截
          </span>
        </div>
        <p class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-3">
          防冲突说明：请通过 <code>@BotFather</code> 为发票女仆创建<b>独立的新 Bot Token</b>，切勿填写与 Hermes Agent 共用的推送 Token，否则 Telegram 会报 409 Conflict。系统已内置冲突守卫，若 Token 相同将自动拒绝轮询。
        </p>

        <label class="flex items-center space-x-2 text-sm font-medium text-slate-800">
          <input type="checkbox" v-model="form.telegram_agent_enabled" class="rounded border-slate-300 text-blue-600" />
          <span>启用独立女仆 Bot 双向监听（支持发文字查账、拍照发二维码自动填表开票）</span>
        </label>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">独立女仆 Bot Token</label>
            <input
              v-model="form.telegram_agent_bot_token"
              type="password"
              :placeholder="settings?.telegram_agent_bot_token_masked || '从 @BotFather 新建的独立 Token'"
              class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">允许交互的 Chat ID 白名单（逗号分隔）</label>
            <input
              v-model="form.telegram_agent_allowed_chat_ids"
              type="text"
              placeholder="例如 123456789（强烈建议填写本人 Chat ID）"
              class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
            />
          </div>
        </div>
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
          {{ saving ? '保存中...' : '保存配置' }}
        </button>
      </div>
    </form>
  </div>
</template>
