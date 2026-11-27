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

const sendMessage = async () => {
  const text = inputText.value.trim()
  if (!text || sending.value) return
  inputText.value = ''
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
      content: `抱歉，处理请求时遇到问题：${err?.response?.data?.detail || err?.message || '请稍后重试'}`,
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
    content: `📷 上传开票二维码照片：${file.name}`,
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
    content: `选择抬头：${profileName}`,
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
      content: `提交开票失败：${err?.response?.data?.detail || '请前往工作台查看'}`,
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
    <!-- Floating Trigger Button -->
    <button
      @click="open = !open"
      class="fixed bottom-6 right-6 z-40 flex items-center gap-2 pl-2.5 pr-4 py-2 rounded-full shadow-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition-all"
      title="呼出发票女仆对话"
    >
      <img src="/favicon.png" alt="" class="w-7 h-7 rounded-full bg-white/90" />
      <span>发票女仆</span>
    </button>

    <!-- Slide-over Chat Drawer -->
    <div
      v-if="open"
      class="fixed bottom-20 right-6 z-40 w-[390px] max-w-[calc(100vw-2rem)] h-[560px] max-h-[calc(100vh-7rem)] bg-white rounded-2xl shadow-2xl border border-slate-200 flex flex-col overflow-hidden"
    >
      <div class="px-4 py-3 bg-slate-900 text-white flex items-center justify-between">
        <div class="flex items-center gap-2.5">
          <img src="/favicon.png" alt="" class="w-8 h-8 rounded-full bg-white/90" />
          <div>
            <div class="text-sm font-semibold">Invoice Maid 发票女仆 v2.0</div>
            <div class="text-xs text-slate-300">查账 · 报销归类 · 拍码自动开票</div>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <button @click="clearChat" class="text-xs text-slate-300 hover:text-white px-2 py-1 rounded">清空</button>
          <button @click="open = false" class="text-slate-300 hover:text-white text-lg leading-none px-1">×</button>
        </div>
      </div>

      <div ref="listRef" class="flex-1 overflow-y-auto p-4 space-y-3 bg-slate-50">
        <div v-if="messages.length === 0" class="text-xs text-slate-500 bg-white border border-slate-200 rounded-xl p-3 space-y-1.5">
          <div class="font-medium text-slate-700">你可以直接告诉我：</div>
          <div>• 「查一下 11 月未报销的美元发票有多少」</div>
          <div>• 「记住：大药房的发票默认标为无需报销」</div>
          <div>• 点击左下角 📷 上传线下消费小票二维码，我帮你自动填表申请开票！</div>
        </div>

        <div
          v-for="m in messages"
          :key="m.id"
          class="flex flex-col"
          :class="m.role === 'user' ? 'items-end' : 'items-start'"
        >
          <div
            class="max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm whitespace-pre-wrap break-words"
            :class="m.role === 'user' ? 'bg-blue-600 text-white' : 'bg-white border border-slate-200 text-slate-800'"
          >
            {{ m.content }}
          </div>
          <div v-if="m.tool_events && m.tool_events.length > 0" class="mt-1 flex flex-wrap gap-1">
            <span
              v-for="(ev, idx) in m.tool_events"
              :key="idx"
              class="text-[11px] px-2 py-0.5 rounded-full bg-slate-200 text-slate-600 font-mono"
            >
              ⚡ {{ ev.tool }}
            </span>
          </div>
        </div>

        <!-- Inline Profile Picker when multiple profiles exist -->
        <div v-if="pendingClaimChoice" class="bg-blue-50 border border-blue-200 rounded-xl p-3 space-y-2">
          <div class="text-xs font-semibold text-blue-900">请选择本次开票抬头：</div>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="p in pendingClaimChoice.profiles"
              :key="p.id"
              @click="pickProfileForClaim(pendingClaimChoice.task.id, p.id, p.name)"
              class="px-3 py-1.5 text-xs font-medium rounded-lg bg-white border border-blue-300 text-blue-700 hover:bg-blue-600 hover:text-white transition-colors"
            >
              🏢 {{ p.name }}
            </button>
          </div>
        </div>

        <div v-if="sending || uploadingPhoto" class="text-xs text-slate-400 animate-pulse">
          {{ uploadingPhoto ? '正在识别二维码与小票...' : '女仆思考与执行中...' }}
        </div>
      </div>

      <form @submit.prevent="sendMessage" class="p-3 border-t border-slate-200 bg-white flex items-center gap-2">
        <input ref="fileInputRef" type="file" accept="image/*" class="hidden" @change="handlePhotoSelected" />
        <button
          type="button"
          @click="onPickPhoto"
          :disabled="uploadingPhoto || sending"
          class="p-2 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-100 text-sm"
          title="拍照/上传开票二维码"
        >
          📷
        </button>
        <input
          v-model="inputText"
          type="text"
          placeholder="给发票女仆发指令..."
          class="flex-1 text-sm border border-slate-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <button
          type="submit"
          :disabled="sending || !inputText.trim()"
          class="px-3.5 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          发送
        </button>
      </form>
    </div>
  </div>
</template>
