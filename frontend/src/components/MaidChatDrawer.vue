<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import { api } from '@/api/client'
import type { MaidMessageItem, ClaimIntakeResponse } from '@/types'

const open = ref(false)
const messages = ref<MaidMessageItem[]>([])
const inputText = ref('')
const sending = ref(false)
const uploadingPhoto = ref(false)
const pendingClaimChoice = ref<ClaimIntakeResponse | null>(null)
const listRef = ref<HTMLElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)

const quickPrompts = [
  '本月未报销统计',
  '查看开票队列',
  '查看预存抬头',
]

const scrollToBottom = async () => {
  await nextTick()
  if (listRef.value) {
    listRef.value.scrollTop = listRef.value.scrollHeight
  }
}

const loadHistory = async () => {
  try {
    messages.value = await api.getMaidHistory('web')
    await scrollToBottom()
  } catch {
    // ignore transient load failure
  }
}

watch(open, (val) => {
  if (val) loadHistory()
})

const sendMessage = async (overrideText?: string) => {
  const text = (overrideText ?? inputText.value).trim()
  if (!text || sending.value) return
  if (!overrideText) inputText.value = ''
  messages.value.push({
    id: Date.now(),
    role: 'user',
    content: text,
    tool_events: [],
    created_at: new Date().toISOString(),
  })
  await scrollToBottom()
  sending.value = true
  try {
    const res = await api.maidChat(text, 'web')
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: res.reply,
      tool_events: res.tool_events || [],
      created_at: new Date().toISOString(),
    })
  } catch (err: any) {
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: `处理请求时出错：${err?.response?.data?.detail || err?.message || '请稍后重试'}`,
      tool_events: [],
      created_at: new Date().toISOString(),
    })
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

const onPickPhoto = () => {
  fileInputRef.value?.click()
}

const handlePhotoSelected = async (e: Event) => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  uploadingPhoto.value = true
  messages.value.push({
    id: Date.now(),
    role: 'user',
    content: `上传开票二维码：${file.name}`,
    tool_events: [],
    created_at: new Date().toISOString(),
  })
  await scrollToBottom()
  try {
    const res = await api.createClaimFromPhoto(file)
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: res.message,
      tool_events: [],
      created_at: new Date().toISOString(),
    })
    pendingClaimChoice.value = res.needs_profile_choice ? res : null
  } catch (err: any) {
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: `照片解析失败：${err?.response?.data?.detail || '请重试'}`,
      tool_events: [],
      created_at: new Date().toISOString(),
    })
  } finally {
    uploadingPhoto.value = false
    await scrollToBottom()
  }
}

const pickProfileForClaim = async (taskId: number, profileId: number, profileName: string) => {
  pendingClaimChoice.value = null
  sending.value = true
  messages.value.push({
    id: Date.now(),
    role: 'user',
    content: `使用抬头：${profileName}`,
    tool_events: [],
    created_at: new Date().toISOString(),
  })
  await scrollToBottom()
  try {
    const res = await api.selectClaimProfile(taskId, profileId)
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: res.message,
      tool_events: [],
      created_at: new Date().toISOString(),
    })
  } catch (err: any) {
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: `提交失败：${err?.response?.data?.detail || '可在米尔工作台查看详情'}`,
      tool_events: [],
      created_at: new Date().toISOString(),
    })
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

const clearChat = async () => {
  await api.clearMaidHistory('web')
  messages.value = []
  pendingClaimChoice.value = null
}
</script>

<template>
  <div>
    <!-- Floating Trigger -->
    <button
      @click="open = !open"
      class="fixed bottom-16 lg:bottom-6 right-5 z-40 flex items-center gap-2 pl-2 pr-4 py-1.5 rounded-full shadow-paper-lg bg-cream-100 hover:bg-mint-50 border border-mint-300 text-ink-900 text-sm font-medium transition-all"
      title="打开米尔对话框"
    >
      <img src="/favicon.png" alt="Miri" class="w-8 h-8 rounded-full" />
      <span>米尔 Miri</span>
    </button>

    <!-- Warm Mint-Cream Companion Drawer -->
    <div
      v-if="open"
      class="fixed bottom-32 lg:bottom-20 right-5 z-40 w-[390px] max-w-[calc(100vw-2rem)] h-[540px] max-h-[calc(100vh-8rem)] bg-cream-100 rounded-2xl shadow-paper-lg border border-cream-500 flex flex-col overflow-hidden"
    >
      <div class="px-4 py-3 bg-gradient-to-r from-mint-100 via-mint-50 to-cream-100 border-b border-cream-400 flex items-center justify-between">
        <div class="flex items-center gap-2.5">
          <img src="/favicon.png" alt="Miri" class="w-9 h-9 rounded-full ring-2 ring-cream-100" />
          <div>
            <div class="text-sm font-bold text-ink-900 flex items-center gap-1.5">
              <span>米尔 Miri</span>
              <span class="px-1.5 py-0.5 text-[10px] rounded-full bg-mint-200/80 text-mint-800 font-medium">在线</span>
            </div>
            <div class="text-xs text-ink-500">查账 · 报销 · 拍码开票</div>
          </div>
        </div>
        <div class="flex items-center gap-1.5">
          <button @click="clearChat" class="text-xs text-ink-500 hover:text-ink-900 px-2 py-1 rounded-lg hover:bg-cream-300/60">清空</button>
          <button @click="open = false" class="text-ink-500 hover:text-ink-900 text-lg leading-none px-1.5 py-0.5 rounded-lg hover:bg-cream-300/60">×</button>
        </div>
      </div>

      <div ref="listRef" class="flex-1 overflow-y-auto p-4 space-y-3 bg-cream-200/50">
        <div v-if="messages.length === 0" class="text-xs text-ink-700 bg-cream-100 border border-cream-400 rounded-xl p-3.5 space-y-2">
          <div>需要查发票、改报销状态，或者拍张开票二维码交给我填表吗？</div>
          <div class="flex flex-wrap gap-1.5 pt-1">
            <button
              v-for="q in quickPrompts"
              :key="q"
              type="button"
              @click="sendMessage(q)"
              class="px-2.5 py-1 rounded-lg bg-mint-50 border border-mint-200 text-mint-800 hover:bg-mint-100 transition-colors"
            >
              {{ q }}
            </button>
          </div>
        </div>

        <div
          v-for="m in messages"
          :key="m.id"
          class="flex flex-col"
          :class="m.role === 'user' ? 'items-end' : 'items-start'"
        >
          <div
            class="max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm whitespace-pre-wrap break-words leading-relaxed"
            :class="m.role === 'user'
              ? 'bg-mint-600 text-white rounded-br-sm'
              : 'bg-cream-100 border border-cream-400 text-ink-900 rounded-bl-sm shadow-sm'"
          >
            {{ m.content }}
          </div>
          <div v-if="m.tool_events && m.tool_events.length > 0" class="mt-1 flex flex-wrap gap-1">
            <span
              v-for="(ev, idx) in m.tool_events"
              :key="idx"
              class="text-[11px] px-2 py-0.5 rounded-full bg-cream-300 text-ink-500 font-mono"
            >
              {{ ev.tool }}
            </span>
          </div>
        </div>

        <!-- Inline Profile Picker -->
        <div v-if="pendingClaimChoice" class="bg-mint-50 border border-mint-200 rounded-xl p-3 space-y-2">
          <div class="text-xs font-semibold text-mint-800">请选择开票抬头：</div>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="p in pendingClaimChoice.profiles"
              :key="p.id"
              @click="pickProfileForClaim(pendingClaimChoice.task.id, p.id, p.name)"
              class="px-3 py-1.5 text-xs font-medium rounded-lg bg-cream-100 border border-mint-300 text-mint-800 hover:bg-mint-600 hover:text-white transition-colors"
            >
              {{ p.name }}
            </button>
          </div>
        </div>

        <div v-if="sending || uploadingPhoto" class="text-xs text-ink-400 animate-pulse">
          {{ uploadingPhoto ? '正在识别二维码…' : '米尔整理中…' }}
        </div>
      </div>

      <form @submit.prevent="sendMessage()" class="p-3 border-t border-cream-400 bg-cream-100 flex items-center gap-2">
        <input ref="fileInputRef" type="file" accept="image/*" class="hidden" @change="handlePhotoSelected" />
        <button
          type="button"
          @click="onPickPhoto"
          :disabled="uploadingPhoto || sending"
          class="btn-cream px-2.5 py-2 text-xs whitespace-nowrap"
          title="拍码开票"
        >
          拍码开票
        </button>
        <input
          v-model="inputText"
          type="text"
          placeholder="和米尔说说，如「查京东的票」…"
          class="input-warm flex-1 py-2"
        />
        <button
          type="submit"
          :disabled="sending || !inputText.trim()"
          class="btn-mint px-3.5 py-2"
        >
          发送
        </button>
      </form>
    </div>
  </div>
</template>
