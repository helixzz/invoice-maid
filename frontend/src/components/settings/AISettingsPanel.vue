<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '@/api/client'
import type { AISettingsResponse, AISettingsUpdate, AIConnectionTestResponse } from '@/types'

const emit = defineEmits<{
  (e: 'toast', message: string, type: 'success' | 'error' | 'info'): void
}>()

const aiSettings = ref<AISettingsResponse | null>(null)
const form = ref<AISettingsUpdate>({})
const loading = ref(false)
const saving = ref(false)
const fetchingModels = ref(false)
const availableModels = ref<string[]>([])
const modelsFetchFailed = ref(false)
const testing = ref(false)
const testResult = ref<AIConnectionTestResponse | null>(null)

const fetchSettings = async () => {
  loading.value = true
  try {
    const res = await api.getAISettings()
    aiSettings.value = res
    form.value = {
      llm_base_url: res.llm_base_url,
      llm_api_key: '',
      llm_model: res.llm_model,
      llm_fallback_models: res.llm_fallback_models,
      llm_vision_model: res.llm_vision_model,
      llm_embed_base_url: res.llm_embed_base_url,
      llm_embed_api_key: '',
      llm_embed_model: res.llm_embed_model,
      embed_dim: res.embed_dim,
    }
    await fetchModels(true)
  } catch {
    emit('toast', '加载 AI 配置失败', 'error')
  } finally {
    loading.value = false
  }
}

const fetchModels = async (silent = false) => {
  fetchingModels.value = true
  modelsFetchFailed.value = false
  try {
    const res = await api.getAIModels()
    availableModels.value = res.models || []
    if (!silent) emit('toast', `已获取 ${availableModels.value.length} 个可用模型`, 'success')
  } catch {
    modelsFetchFailed.value = true
    if (!silent) emit('toast', '获取上游模型列表失败，请手动填写模型名', 'error')
  } finally {
    fetchingModels.value = false
  }
}

const saveSettings = async () => {
  saving.value = true
  try {
    const payload: AISettingsUpdate = {
      llm_base_url: form.value.llm_base_url,
      llm_model: form.value.llm_model,
      llm_fallback_models: form.value.llm_fallback_models ?? '',
      llm_vision_model: form.value.llm_vision_model ?? '',
      llm_embed_base_url: form.value.llm_embed_base_url ?? '',
      llm_embed_model: form.value.llm_embed_model ?? '',
      embed_dim: form.value.embed_dim,
    }
    if (form.value.llm_api_key && form.value.llm_api_key.trim()) {
      payload.llm_api_key = form.value.llm_api_key.trim()
    }
    if (form.value.llm_embed_api_key && form.value.llm_embed_api_key.trim()) {
      payload.llm_embed_api_key = form.value.llm_embed_api_key.trim()
    }
    await api.updateAISettings(payload)
    emit('toast', 'AI 模型配置已保存', 'success')
    await fetchSettings()
  } catch (err: any) {
    emit('toast', err?.response?.data?.detail || '保存 AI 配置失败', 'error')
  } finally {
    saving.value = false
  }
}

const testConnection = async () => {
  testing.value = true
  testResult.value = null
  try {
    const res = await api.testAIConnection()
    testResult.value = res
    emit('toast', res.ok ? 'AI 连接测试通过' : 'AI 连接测试存在异常', res.ok ? 'success' : 'error')
  } catch {
    emit('toast', 'AI 连接测试请求失败', 'error')
  } finally {
    testing.value = false
  }
}

onMounted(fetchSettings)
</script>

<template>
  <div class="bg-white shadow-sm rounded-xl border border-slate-200 overflow-hidden">
    <div class="p-6 border-b border-slate-200 flex items-center justify-between">
      <div>
        <h3 class="text-lg font-medium leading-6 text-slate-900">AI 模型与路由配置</h3>
        <p class="mt-1 text-sm text-slate-500">支持 OpenAI 兼容网关（如本机 Magpie 自动路由）、多级备选模型降级、多模态视觉兜底及独立 Embedding 网关。</p>
      </div>
      <span
        v-if="aiSettings"
        class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium"
        :class="aiSettings.source === 'database' ? 'bg-blue-100 text-blue-800' : 'bg-slate-100 text-slate-700'"
      >
        {{ aiSettings.source === 'database' ? 'Database' : 'Environment' }}
      </span>
    </div>

    <div v-if="loading" class="p-8 text-center text-slate-500 text-sm">正在加载配置...</div>
    <form v-else @submit.prevent="saveSettings" class="p-6 space-y-5">
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label class="block text-sm font-medium text-slate-700 mb-1">LLM Base URL</label>
          <input
            v-model="form.llm_base_url"
            type="text"
            placeholder="http://127.0.0.1:3425/v1"
            class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border focus:ring-blue-500 focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-slate-700 mb-1">LLM API Key</label>
          <input
            v-model="form.llm_api_key"
            type="password"
            :placeholder="aiSettings?.llm_api_key_masked || '留空表示保持不变'"
            class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border focus:ring-blue-500 focus:border-blue-500"
          />
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <div class="flex items-center justify-between mb-1">
            <label class="block text-sm font-medium text-slate-700">主模型 (Chat Model)</label>
            <button
              type="button"
              @click="fetchModels(false)"
              :disabled="fetchingModels"
              class="text-xs text-blue-600 hover:text-blue-800"
            >
              {{ fetchingModels ? '获取中...' : '刷新模型列表' }}
            </button>
          </div>
          <select
            v-if="availableModels.length > 0 && !modelsFetchFailed"
            v-model="form.llm_model"
            class="block w-full py-2 px-3 border border-slate-300 bg-white rounded-md sm:text-sm"
          >
            <option v-if="form.llm_model && !availableModels.includes(form.llm_model)" :value="form.llm_model">
              {{ form.llm_model }}
            </option>
            <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
          </select>
          <input
            v-else
            v-model="form.llm_model"
            type="text"
            placeholder="auto/gemini-4-hydrogen"
            class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-700 mb-1">多模态视觉兜底模型 (Vision Model)</label>
          <input
            v-model="form.llm_vision_model"
            type="text"
            placeholder="auto/gemini-4-neon（留空则复用主模型链）"
            class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
          />
        </div>
      </div>

      <div>
        <label class="block text-sm font-medium text-slate-700 mb-1">多级备选模型链 (Fallback Models，逗号分隔)</label>
        <input
          v-model="form.llm_fallback_models"
          type="text"
          placeholder="auto/gpt-6-luna, modelverse/deepseek-v4.1-flash-1017, auto/gemini-4-neon"
          class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
        />
        <p class="mt-1 text-xs text-slate-500">主模型故障或返回 5xx/429 时按顺序自动切换，全部失败时触发熔断保护。</p>
      </div>

      <div class="border-t border-slate-200 pt-4">
        <h4 class="text-sm font-semibold text-slate-800 mb-3">向量 Embedding 独立网关（可选，留空回退至主 LLM 网关）</h4>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">Embedding Base URL</label>
            <input
              v-model="form.llm_embed_base_url"
              type="text"
              placeholder="留空则复用 LLM Base URL"
              class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">Embedding API Key</label>
            <input
              v-model="form.llm_embed_api_key"
              type="password"
              :placeholder="aiSettings?.llm_embed_api_key_masked || '留空则复用 LLM API Key'"
              class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">Embedding 模型（留空则禁用语义向量）</label>
            <input
              v-model="form.llm_embed_model"
              type="text"
              placeholder="text-embedding-3-small"
              class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">Embedding 维度 (Embed Dim)</label>
            <input
              v-model.number="form.embed_dim"
              type="number"
              placeholder="1536"
              class="block w-full sm:text-sm border-slate-300 rounded-md py-2 px-3 border"
            />
          </div>
        </div>
      </div>

      <div v-if="testResult" class="rounded-lg p-3 text-xs border" :class="testResult.ok ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 'bg-rose-50 border-rose-200 text-rose-800'">
        <div>Chat ({{ testResult.chat.model }}): {{ testResult.chat.ok ? '✅ OK' : '❌ 失败' }} — {{ testResult.chat.detail }}</div>
        <div class="mt-1">Embed ({{ testResult.embed.model || 'disabled' }}): {{ testResult.embed.ok ? '✅ OK' : '❌ 失败' }} — {{ testResult.embed.detail }}</div>
      </div>

      <div class="flex items-center justify-end space-x-3 pt-2">
        <button
          type="button"
          @click="testConnection"
          :disabled="testing"
          class="px-4 py-2 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          {{ testing ? '测试中...' : '测试连接' }}
        </button>
        <button
          type="submit"
          :disabled="saving"
          class="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {{ saving ? '保存中...' : '保存 AI 配置' }}
        </button>
      </div>
    </form>
  </div>
</template>
