<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import { api } from '@/api/client'
import type { InvoiceClaimTaskItem, InvoiceProfile, MaidMemoryItem } from '@/types'

const router = useRouter()

const claims = ref<InvoiceClaimTaskItem[]>([])
const profiles = ref<InvoiceProfile[]>([])
const memories = ref<MaidMemoryItem[]>([])
const loading = ref(false)
const uploading = ref(false)
const manualUrl = ref('')
const statusMessage = ref('')
const captchaInputs = ref<Record<number, string>>({})
const runningHeartbeat = ref(false)

const newMemory = ref({
  category: 'reimbursement',
  rule_text: '',
  match_pattern: '',
  reimbursement_status: '',
  tags: '',
})

const STATUS_META: Record<string, { label: string; cls: string }> = {
  awaiting_profile: { label: '待选抬头', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  queued: { label: '排队中', cls: 'bg-slate-100 text-slate-700 border-slate-200' },
  filling: { label: '自动填表中', cls: 'bg-blue-50 text-blue-700 border-blue-200' },
  needs_attention: { label: '需人工介入', cls: 'bg-rose-50 text-rose-700 border-rose-200' },
  submitted: { label: '等待收票中', cls: 'bg-indigo-50 text-indigo-700 border-indigo-200' },
  completed: { label: '已收票归档', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  cancelled: { label: '已取消', cls: 'bg-slate-100 text-slate-500 border-slate-200' },
}

const fetchAll = async () => {
  loading.value = true
  try {
    const [c, p, m] = await Promise.all([
      api.getClaimTasks(),
      api.getProfiles(),
      api.getMaidMemories(),
    ])
    claims.value = c
    profiles.value = p
    memories.value = m
  } finally {
    loading.value = false
  }
}

const handlePhotoUpload = async (e: Event) => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  uploading.value = true
  statusMessage.value = ''
  try {
    const res = await api.createClaimFromPhoto(file)
    statusMessage.value = res.message
    await fetchAll()
  } catch (err: any) {
    statusMessage.value = err?.response?.data?.detail || '上传识别失败'
  } finally {
    uploading.value = false
  }
}

const submitManualUrl = async () => {
  if (!manualUrl.value.trim()) return
  uploading.value = true
  statusMessage.value = ''
  try {
    const res = await api.createClaimFromUrl(manualUrl.value.trim())
    manualUrl.value = ''
    statusMessage.value = res.message
    await fetchAll()
  } catch (err: any) {
    statusMessage.value = err?.response?.data?.detail || '提交链接失败'
  } finally {
    uploading.value = false
  }
}

const pickProfile = async (taskId: number, profileId: number) => {
  uploading.value = true
  try {
    const res = await api.selectClaimProfile(taskId, profileId)
    statusMessage.value = res.message
    await fetchAll()
  } finally {
    uploading.value = false
  }
}

const retryClaim = async (taskId: number) => {
  uploading.value = true
  try {
    const res = await api.retryClaimTask(taskId, captchaInputs.value[taskId] || '')
    statusMessage.value = res.message
    captchaInputs.value[taskId] = ''
    await fetchAll()
  } finally {
    uploading.value = false
  }
}

const cancelClaim = async (taskId: number) => {
  await api.cancelClaimTask(taskId)
  await fetchAll()
}

const triggerHeartbeat = async () => {
  runningHeartbeat.value = true
  try {
    const res = await api.triggerMaidHeartbeat()
    statusMessage.value = `心跳巡检完成：处理排队任务 ${res.processed_queued} 个，闭环匹配新发票 ${res.matched_claims} 张。`
    await fetchAll()
  } finally {
    runningHeartbeat.value = false
  }
}

const addMemory = async () => {
  if (!newMemory.value.rule_text.trim()) return
  const action: Record<string, string> = {}
  if (newMemory.value.reimbursement_status) action.reimbursement_status = newMemory.value.reimbursement_status
  if (newMemory.value.tags.trim()) action.tags = newMemory.value.tags.trim()
  await api.createMaidMemory({
    category: newMemory.value.category,
    rule_text: newMemory.value.rule_text.trim(),
    match_pattern: newMemory.value.match_pattern.trim(),
    action_json: JSON.stringify(action),
  })
  newMemory.value = { category: 'reimbursement', rule_text: '', match_pattern: '', reimbursement_status: '', tags: '' }
  await fetchAll()
}

const removeMemory = async (id: number) => {
  await api.deleteMaidMemory(id)
  await fetchAll()
}

onMounted(fetchAll)
</script>

<template>
  <AppLayout>
    <div class="space-y-6">
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 class="text-xl font-bold text-slate-900">发票女仆工作台（Agent Loop v2.0）</h1>
          <p class="text-sm text-slate-500 mt-1">上传线下消费小票二维码，自动按预存抬头填表提交并持续盯梢收票闭环；亦可管理女仆长期记忆规则。</p>
        </div>
        <div class="flex items-center gap-2">
          <button
            @click="triggerHeartbeat"
            :disabled="runningHeartbeat"
            class="px-3.5 py-2 text-sm border border-slate-300 rounded-lg hover:bg-slate-50 disabled:opacity-50"
          >
            {{ runningHeartbeat ? '巡检中...' : '立即心跳巡检' }}
          </button>
          <router-link to="/settings" class="px-3.5 py-2 text-sm bg-slate-900 text-white rounded-lg hover:bg-slate-800">
            管理预存抬头 ({{ profiles.length }})
          </router-link>
        </div>
      </div>

      <div v-if="statusMessage" class="p-4 rounded-xl bg-blue-50 border border-blue-200 text-sm text-blue-900 whitespace-pre-wrap">
        {{ statusMessage }}
      </div>

      <!-- QR Photo / URL Intake -->
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4">
        <h2 class="text-base font-semibold text-slate-900">📷 拍照 / 扫码自动开票</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="flex flex-col items-center justify-center border-2 border-dashed border-slate-300 rounded-xl p-6 cursor-pointer hover:border-blue-400 hover:bg-blue-50/40 transition-colors">
            <span class="text-sm font-medium text-slate-700">{{ uploading ? '识别处理中...' : '点击选择或拍摄开票二维码照片' }}</span>
            <span class="text-xs text-slate-400 mt-1">自动解码 QR + VLM 识别商户/金额，单抬头自动填表提交，多抬头提示选择</span>
            <input type="file" accept="image/*" class="hidden" :disabled="uploading" @change="handlePhotoUpload" />
          </label>
          <form @submit.prevent="submitManualUrl" class="flex flex-col justify-center gap-2 border border-slate-200 rounded-xl p-4">
            <label class="text-sm font-medium text-slate-700">或直接粘贴开票二维码链接</label>
            <input
              v-model="manualUrl"
              type="url"
              placeholder="https://nnfp.jss.com.cn/..."
              class="border border-slate-300 rounded-lg px-3 py-2 text-sm"
            />
            <button
              type="submit"
              :disabled="uploading || !manualUrl.trim()"
              class="self-end px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              自动填表开票
            </button>
          </form>
        </div>
      </div>

      <!-- Claim Tasks List -->
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div class="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <h2 class="text-base font-semibold text-slate-900">开票追踪队列 ({{ claims.length }})</h2>
          <button @click="fetchAll" class="text-xs text-blue-600 hover:underline">刷新</button>
        </div>
        <div v-if="loading" class="p-6 text-sm text-slate-500">加载中...</div>
        <div v-else-if="claims.length === 0" class="p-8 text-center text-sm text-slate-400">暂无扫码开票任务</div>
        <ul v-else class="divide-y divide-slate-200">
          <li v-for="t in claims" :key="t.id" class="p-5 space-y-3">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="flex items-center gap-2">
                <span class="font-semibold text-slate-900">#{{ t.id }} {{ t.merchant_name || '线下扫码开票' }}</span>
                <span v-if="t.expected_amount !== null" class="text-sm font-medium text-blue-600">
                  {{ t.expected_currency }} {{ t.expected_amount.toFixed(2) }}
                </span>
                <span
                  class="px-2.5 py-0.5 text-xs font-semibold rounded-full border"
                  :class="STATUS_META[t.status]?.cls || 'bg-slate-100 text-slate-700 border-slate-200'"
                >
                  {{ STATUS_META[t.status]?.label || t.status }}
                </span>
                <span class="text-xs text-slate-400">来源: {{ t.source_channel }}</span>
              </div>
              <div class="flex items-center gap-2">
                <button
                  v-if="t.matched_invoice_id"
                  @click="router.push(`/invoices/${t.matched_invoice_id}`)"
                  class="px-3 py-1 text-xs rounded-md bg-emerald-600 text-white hover:bg-emerald-700"
                >
                  查看已归档发票 #{{ t.matched_invoice_id }}
                </button>
                <a
                  v-if="t.has_screenshot"
                  :href="`/api/v1/maid/claims/${t.id}/screenshot`"
                  target="_blank"
                  class="px-3 py-1 text-xs rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50"
                >
                  查看表单截图
                </a>
                <button
                  v-if="!['completed', 'cancelled'].includes(t.status)"
                  @click="cancelClaim(t.id)"
                  class="px-3 py-1 text-xs rounded-md border border-slate-200 text-slate-500 hover:bg-slate-50"
                >
                  取消
                </button>
              </div>
            </div>

            <div v-if="t.qr_url" class="text-xs text-slate-500 font-mono truncate">{{ t.qr_url }}</div>

            <!-- Multiple profile selection inline -->
            <div v-if="t.status === 'awaiting_profile' && profiles.length > 0" class="flex flex-wrap items-center gap-2 bg-amber-50 p-3 rounded-lg border border-amber-200">
              <span class="text-xs font-medium text-amber-800">请选择本次抬头：</span>
              <button
                v-for="p in profiles"
                :key="p.id"
                @click="pickProfile(t.id, p.id)"
                class="px-3 py-1 text-xs bg-white border border-amber-300 text-amber-900 rounded-md hover:bg-amber-600 hover:text-white"
              >
                🏢 {{ p.name }} ({{ p.title }})
              </button>
            </div>

            <!-- Escalated needs_attention panel -->
            <div v-if="t.status === 'needs_attention'" class="bg-rose-50 border border-rose-200 rounded-lg p-3 space-y-2">
              <div class="text-xs text-rose-800 whitespace-pre-wrap">{{ t.attention_reason }}</div>
              <div v-if="t.profile_id" class="flex items-center gap-2">
                <input
                  v-model="captchaInputs[t.id]"
                  type="text"
                  placeholder="如需验证码请在此输入（可选）"
                  class="text-xs border border-rose-300 rounded-md px-2.5 py-1.5 bg-white flex-1"
                />
                <button
                  @click="retryClaim(t.id)"
                  class="px-3 py-1.5 text-xs bg-rose-600 text-white rounded-md hover:bg-rose-700"
                >
                  重试提交
                </button>
              </div>
            </div>
          </li>
        </ul>
      </div>

      <!-- Maid Memories -->
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4">
        <div>
          <h2 class="text-base font-semibold text-slate-900">🧠 女仆记忆规则 ({{ memories.length }})</h2>
          <p class="text-xs text-slate-500 mt-1">匹配到销售方或摘要关键词时，新入库发票将自动应用报销状态与标签；对话时女仆也会遵循这些规则。</p>
        </div>

        <form @submit.prevent="addMemory" class="grid grid-cols-1 md:grid-cols-5 gap-2">
          <input v-model="newMemory.rule_text" required placeholder="规则描述（如：大药房无需报销）" class="md:col-span-2 border border-slate-300 rounded-lg px-3 py-2 text-sm" />
          <input v-model="newMemory.match_pattern" placeholder="匹配关键词（如：大药房）" class="border border-slate-300 rounded-lg px-3 py-2 text-sm" />
          <select v-model="newMemory.reimbursement_status" class="border border-slate-300 rounded-lg px-2 py-2 text-sm bg-white">
            <option value="">自动报销状态(可选)</option>
            <option value="not_applicable">无需报销</option>
            <option value="unreimbursed">未报销</option>
            <option value="pending">报销中</option>
            <option value="reimbursed">已报销</option>
          </select>
          <button type="submit" class="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700">添加规则</button>
        </form>

        <ul v-if="memories.length > 0" class="divide-y divide-slate-100">
          <li v-for="m in memories" :key="m.id" class="py-2.5 flex items-center justify-between text-sm">
            <div>
              <span class="font-medium text-slate-800">{{ m.rule_text }}</span>
              <span v-if="m.match_pattern" class="ml-2 text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600 font-mono">关键词: {{ m.match_pattern }}</span>
            </div>
            <button @click="removeMemory(m.id)" class="text-xs text-rose-600 hover:underline">删除</button>
          </li>
        </ul>
      </div>
    </div>
  </AppLayout>
</template>
