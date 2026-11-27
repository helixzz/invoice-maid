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
    emit('toast', '加载模型配置失败', 'error')
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
    emit('toast', '模型与路由配置已保存', 'success')
    await fetchSettings()
  } catch (err: any) {
    emit('toast', err?.response?.data?.detail || '保存模型配置失败', 'error')
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
    emit('toast', res.ok ? '模型连通性测试通过' : '模型连通性测试存在异常', res.ok ? 'success' : 'error')
  } catch {
    emit('toast', '连通性测试请求失败', 'error')
  } finally {
    testing.value = false
  }
}

onMounted(fetchSettings)
</script>

<template>
  <div class="ledger-card overflow-hidden">
    <div class="px-6 py-5 border-b border-cream-400 bg-cream-300/35 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 class="font-display text-lg font-bold text-ink-900">模型与路由</h3>
        <p class="mt-1 text-sm text-ink-500">配置 OpenAI 兼容接口、备选降级模型、票面视觉识别与语义检索网关。</p>
      </div>
      <span
        v-if="aiSettings"
        class="stamp-badge"
        :class="aiSettings.source === 'database'
          ? 'bg-mint-50 text-mint-800 border-mint-200'
          : 'bg-cream-300/80 text-ink-700 border-cream-500'"
      >
        {{ aiSettings.source === 'database' ? '已保存配置' : '默认环境变量' }}
      </span>
    </div>

    <div v-if="loading" class="p-8 text-center text-ink-500 text-sm">正在读取模型配置…</div>
    <form v-else @submit.prevent="saveSettings" class="p-6 space-y-5">
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label class="block text-xs font-semibold text-ink-700 mb-1.5">接口地址 (Base URL)</label>
          <input
            v-model="form.llm_base_url"
            type="text"
            placeholder="http://127.0.0.1:3425/v1"
            class="input-warm num-mono text-xs sm:text-sm"
          />
        </div>
        <div>
          <label class="block text-xs font-semibold text-ink-700 mb-1.5">访问密钥 (API Key)</label>
          <input
            v-model="form.llm_api_key"
            type="password"
            :placeholder="aiSettings?.llm_api_key_masked || '留空表示保持不变'"
            class="input-warm"
          />
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <div class="flex items-center justify-between mb-1.5">
            <label class="block text-xs font-semibold text-ink-700">主识别模型</label>
            <button
              type="button"
              @click="fetchModels(false)"
              :disabled="fetchingModels"
              class="text-xs font-medium text-mint-700 hover:text-mint-800 disabled:opacity-50"
            >
              {{ fetchingModels ? '刷新中…' : '刷新模型列表' }}
            </button>
          </div>
          <select
            v-if="availableModels.length > 0 && !modelsFetchFailed"
            v-model="form.llm_model"
            class="input-warm"
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
            placeholder="输入模型名称"
            class="input-warm num-mono"
          />
        </div>

        <div>
          <label class="block text-xs font-semibold text-ink-700 mb-1.5">视觉识别模型（用于图片与扫描件）</label>
          <input
            v-model="form.llm_vision_model"
            type="text"
            placeholder="留空则沿用主模型链"
            class="input-warm num-mono"
          />
        </div>
      </div>

      <div>
        <label class="block text-xs font-semibold text-ink-700 mb-1.5">备选降级模型（多个以英文逗号分隔）</label>
        <input
          v-model="form.llm_fallback_models"
          type="text"
          placeholder="model-a, model-b, model-c"
          class="input-warm num-mono"
        />
        <p class="mt-1.5 text-xs text-ink-400">主模型限流或不可用时按顺序自动切换，全部失败时启用熔断保护。</p>
      </div>

      <div class="receipt-dashed pt-5 space-y-4">
        <div>
          <h4 class="text-sm font-semibold text-ink-900">语义检索向量网关（可选）</h4>
          <p class="text-xs text-ink-400 mt-0.5">地址或密钥留空时默认复用上方主接口；模型留空则关闭向量检索。</p>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">向量接口地址</label>
            <input
              v-model="form.llm_embed_base_url"
              type="text"
              placeholder="留空则复用主接口地址"
              class="input-warm num-mono text-xs sm:text-sm"
            />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">向量访问密钥</label>
            <input
              v-model="form.llm_embed_api_key"
              type="password"
              :placeholder="aiSettings?.llm_embed_api_key_masked || '留空则复用主密钥'"
              class="input-warm"
            />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">向量模型名称</label>
            <input
              v-model="form.llm_embed_model"
              type="text"
              placeholder="text-embedding-3-small"
              class="input-warm num-mono"
            />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">向量维度</label>
            <input
              v-model.number="form.embed_dim"
              type="number"
              placeholder="1536"
              class="input-warm num-mono"
            />
          </div>
        </div>
      </div>

      <div
        v-if="testResult"
        class="rounded-xl p-3.5 text-xs border space-y-1"
        :class="testResult.ok
          ? 'bg-mint-50 border-mint-200 text-mint-800'
          : 'bg-cinnabar-50 border-cinnabar-200 text-cinnabar-700'"
      >
        <div class="flex items-center gap-1.5">
          <span class="font-semibold">对话模型</span>
          <span class="num-mono">({{ testResult.chat.model }})</span>：
          <span>{{ testResult.chat.ok ? '正常' : '失败' }}</span>
          <span v-if="testResult.chat.detail" class="text-ink-500">— {{ testResult.chat.detail }}</span>
        </div>
        <div class="flex items-center gap-1.5">
          <span class="font-semibold">向量模型</span>
          <span class="num-mono">({{ testResult.embed.model || '未启用' }})</span>：
          <span>{{ testResult.embed.ok ? '正常' : '失败' }}</span>
          <span v-if="testResult.embed.detail" class="text-ink-500">— {{ testResult.embed.detail }}</span>
        </div>
      </div>

      <div class="flex items-center justify-end gap-2.5 pt-2">
        <button
          type="button"
          @click="testConnection"
          :disabled="testing"
          class="btn-cream"
        >
          {{ testing ? '测试中…' : '测试连接' }}
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
