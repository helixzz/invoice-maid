<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import { api } from '@/api/client'
import type { InvoiceClaimTaskItem, InvoiceProfile, MaidMemoryItem } from '@/types'

const router = useRouter()

const activeSection = ref<'claims' | 'memories'>('claims')
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
  awaiting_profile: { label: '待选抬头', cls: 'bg-[#FDF5E6] text-[#8B5E22] border-[#EEDAB6]' },
  queued: { label: '排队中', cls: 'bg-cream-300/80 text-ink-700 border-cream-500' },
  filling: { label: '自动填表中', cls: 'bg-mint-50 text-mint-700 border-mint-200' },
  needs_attention: { label: '需您协助', cls: 'bg-cinnabar-50 text-cinnabar-600 border-cinnabar-200' },
  submitted: { label: '等待收票', cls: 'bg-[#F3EFFA] text-[#5B4583] border-[#DDD3F0]' },
  completed: { label: '已收票归档', cls: 'bg-mint-100 text-mint-800 border-mint-300' },
  cancelled: { label: '已取消', cls: 'bg-cream-200 text-ink-400 border-cream-400' },
}

const REIMBURSEMENT_TEXT: Record<string, string> = {
  not_applicable: '无需报销',
  unreimbursed: '未报销',
  pending: '报销中',
  reimbursed: '已报销',
}

const pendingAttentionCount = computed(() =>
  claims.value.filter(t => t.status === 'needs_attention' || t.status === 'awaiting_profile').length
)

const parseMemoryAction = (raw: string): { statusLabel: string; tags: string } => {
  if (!raw) return { statusLabel: '', tags: '' }
  try {
    const parsed = JSON.parse(raw) as Record<string, string>
    return {
      statusLabel: parsed.reimbursement_status ? (REIMBURSEMENT_TEXT[parsed.reimbursement_status] || parsed.reimbursement_status) : '',
      tags: parsed.tags || '',
    }
  } catch {
    return { statusLabel: '', tags: '' }
  }
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
    activeSection.value = 'claims'
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
    activeSection.value = 'claims'
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
    statusMessage.value = `巡检完成：推进排队任务 ${res.processed_queued} 个，匹配归档新发票 ${res.matched_claims} 张。`
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
      <!-- Top Header Card -->
      <div class="ledger-card px-6 py-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div class="flex items-center gap-3.5 min-w-0">
          <img
            src="/favicon.png"
            alt="米尔"
            class="w-12 h-12 rounded-full ring-2 ring-mint-200 bg-mint-50 flex-shrink-0 shadow-paper"
          />
          <div class="min-w-0">
            <h1 class="font-display text-xl font-bold text-ink-900">米尔工作台</h1>
            <p class="text-sm text-ink-500 mt-0.5">拍小票自动填表开票、持续盯梢收票归档，并牢记您的报销习惯。</p>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2.5 flex-shrink-0">
          <button
            type="button"
            @click="triggerHeartbeat"
            :disabled="runningHeartbeat"
            class="btn-cream"
          >
            <svg
              class="w-4 h-4"
              :class="{ 'animate-spin': runningHeartbeat }"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            <span>{{ runningHeartbeat ? '巡检中…' : '立即巡检' }}</span>
          </button>
          <router-link :to="{ path: '/settings', query: { tab: 'profiles' } }" class="btn-mint">
            <span>管理抬头</span>
            <span class="num-mono text-xs bg-white/20 px-1.5 py-0.5 rounded-md">{{ profiles.length }}</span>
          </router-link>
        </div>
      </div>

      <!-- Status Banner -->
      <div
        v-if="statusMessage"
        class="rounded-2xl bg-mint-50 border border-mint-200 px-4 py-3.5 text-sm text-mint-800 flex items-start justify-between gap-3 shadow-paper"
      >
        <div class="whitespace-pre-wrap leading-relaxed">{{ statusMessage }}</div>
        <button
          type="button"
          @click="statusMessage = ''"
          class="text-mint-700/70 hover:text-mint-900 p-0.5 flex-shrink-0"
          aria-label="关闭提示"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <!-- Dual-Entry QR Intake Cream Dashed Card -->
      <div class="ledger-card p-6 space-y-4">
        <div class="flex items-center justify-between gap-2">
          <div>
            <h2 class="font-display text-base font-bold text-ink-900">拍码开票</h2>
            <p class="text-xs text-ink-500 mt-0.5">上传小票二维码或粘贴开票链接，单抬头自动提交，多抬头供您一键选择。</p>
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <!-- Photo Upload Dashed Card -->
          <label
            class="group flex flex-col items-center justify-center text-center border-2 border-dashed border-cream-500 bg-cream-50/75 rounded-2xl p-6 cursor-pointer hover:border-mint-400 hover:bg-mint-50/50 transition-colors"
            :class="{ 'opacity-60 pointer-events-none': uploading }"
          >
            <div class="w-11 h-11 rounded-2xl bg-mint-100/90 border border-mint-200 text-mint-700 flex items-center justify-center mb-3 group-hover:scale-105 transition-transform">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </div>
            <span class="text-sm font-semibold text-ink-900">
              {{ uploading ? '正在识别小票二维码…' : '拍照或选择小票二维码照片' }}
            </span>
            <span class="text-xs text-ink-400 mt-1">自动识别商户与金额，按预存抬头填表开票</span>
            <input type="file" accept="image/*" class="hidden" :disabled="uploading" @change="handlePhotoUpload" />
          </label>

          <!-- Paste URL Dashed Card -->
          <form
            @submit.prevent="submitManualUrl"
            class="flex flex-col justify-between gap-3 border-2 border-dashed border-cream-500 bg-cream-50/60 rounded-2xl p-5"
          >
            <div class="space-y-2">
              <div class="flex items-center gap-2">
                <span class="w-7 h-7 rounded-xl bg-peach-100 border border-peach-200 text-ink-700 inline-flex items-center justify-center flex-shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                  </svg>
                </span>
                <label class="text-sm font-semibold text-ink-900">粘贴开票链接</label>
              </div>
              <input
                v-model="manualUrl"
                type="url"
                placeholder="https://nnfp.jss.com.cn/..."
                class="input-warm num-mono text-xs sm:text-sm"
              />
            </div>

            <div class="flex items-center justify-between gap-2 pt-1">
              <span class="text-xs text-ink-400">支持主流电子发票扫码网址</span>
              <button
                type="submit"
                :disabled="uploading || !manualUrl.trim()"
                class="btn-mint py-2 px-4 text-xs sm:text-sm"
              >
                自动填表开票
              </button>
            </div>
          </form>
        </div>
      </div>

      <!-- Segmented Tabs + Content Card -->
      <div class="space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="inline-flex items-center p-1 rounded-2xl bg-cream-300/80 border border-cream-400">
            <button
              type="button"
              @click="activeSection = 'claims'"
              class="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all"
              :class="activeSection === 'claims'
                ? 'bg-cream-100 text-ink-900 shadow-paper border border-cream-400/70'
                : 'text-ink-500 hover:text-ink-900'"
            >
              <span>开票追踪</span>
              <span class="num-mono text-xs px-1.5 py-0.5 rounded-md bg-cream-200 text-ink-700">{{ claims.length }}</span>
              <span
                v-if="pendingAttentionCount > 0"
                class="num-mono text-[11px] font-semibold px-1.5 py-0.5 rounded-full bg-cinnabar-600 text-white"
                title="有需要您确认或协助的任务"
              >
                {{ pendingAttentionCount }}
              </span>
            </button>
            <button
              type="button"
              @click="activeSection = 'memories'"
              class="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all"
              :class="activeSection === 'memories'
                ? 'bg-cream-100 text-ink-900 shadow-paper border border-cream-400/70'
                : 'text-ink-500 hover:text-ink-900'"
            >
              <span>记忆规则</span>
              <span class="num-mono text-xs px-1.5 py-0.5 rounded-md bg-cream-200 text-ink-700">{{ memories.length }}</span>
            </button>
          </div>

          <button type="button" @click="fetchAll" :disabled="loading" class="btn-cream py-1.5 px-3 text-xs">
            {{ loading ? '刷新中…' : '刷新列表' }}
          </button>
        </div>

        <!-- Section 1: Claim Tasks -->
        <div v-if="activeSection === 'claims'" class="ledger-card overflow-hidden">
          <div v-if="loading && claims.length === 0" class="p-8 text-center text-sm text-ink-500">
            正在整理开票任务…
          </div>
          <div v-else-if="claims.length === 0" class="p-10 text-center paper-dots">
            <p class="font-display text-base font-bold text-ink-900">暂无进行中的开票任务</p>
            <p class="text-xs text-ink-500 mt-1.5">把线下消费小票的开票二维码拍给米尔，就能在这里实时追踪进度。</p>
          </div>
          <ul v-else class="divide-y divide-cream-400">
            <li v-for="t in claims" :key="t.id" class="p-5 sm:p-6 space-y-3 hover:bg-cream-200/40 transition-colors">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div class="flex flex-wrap items-center gap-2 min-w-0">
                  <span class="num-mono text-xs text-ink-400 bg-cream-200 border border-cream-400 px-1.5 py-0.5 rounded-md">
                    #{{ t.id }}
                  </span>
                  <span class="font-semibold text-ink-900">{{ t.merchant_name || '线下扫码开票' }}</span>
                  <span v-if="t.expected_amount !== null" class="num-mono text-sm font-semibold text-mint-700">
                    {{ t.expected_currency }} {{ t.expected_amount.toFixed(2) }}
                  </span>
                  <span
                    class="stamp-badge"
                    :class="STATUS_META[t.status]?.cls || 'bg-cream-200 text-ink-700 border-cream-400'"
                  >
                    {{ STATUS_META[t.status]?.label || t.status }}
                  </span>
                  <span class="text-xs text-ink-400">来源：{{ t.source_channel }}</span>
                </div>

                <div class="flex flex-wrap items-center gap-2 flex-shrink-0">
                  <button
                    v-if="t.matched_invoice_id"
                    type="button"
                    @click="router.push(`/invoices/${t.matched_invoice_id}`)"
                    class="btn-mint py-1.5 px-3 text-xs"
                  >
                    查看归档发票 #{{ t.matched_invoice_id }}
                  </button>
                  <a
                    v-if="t.has_screenshot && t.status !== 'needs_attention'"
                    :href="`/api/v1/maid/claims/${t.id}/screenshot`"
                    target="_blank"
                    class="btn-cream py-1.5 px-3 text-xs"
                  >
                    查看截图
                  </a>
                  <button
                    v-if="!['completed', 'cancelled'].includes(t.status)"
                    type="button"
                    @click="cancelClaim(t.id)"
                    class="inline-flex items-center px-3 py-1.5 rounded-xl text-xs font-medium text-ink-500 bg-cream-200/80 border border-cream-400 hover:bg-cinnabar-50 hover:text-cinnabar-600 hover:border-cinnabar-200 transition-colors"
                  >
                    取消
                  </button>
                </div>
              </div>

              <div v-if="t.qr_url" class="text-xs text-ink-400 num-mono truncate">{{ t.qr_url }}</div>

              <!-- Multiple profile selection inline -->
              <div
                v-if="t.status === 'awaiting_profile' && profiles.length > 0"
                class="flex flex-wrap items-center gap-2 bg-[#FDF5E6] p-3.5 rounded-xl border border-[#EEDAB6]"
              >
                <span class="text-xs font-semibold text-[#8B5E22]">请选择本次开票抬头：</span>
                <button
                  v-for="p in profiles"
                  :key="p.id"
                  type="button"
                  :disabled="uploading"
                  @click="pickProfile(t.id, p.id)"
                  class="px-3 py-1.5 text-xs font-medium bg-cream-100 border border-[#DFB97A] text-ink-900 rounded-xl hover:bg-mint-600 hover:text-white hover:border-mint-600 transition-colors disabled:opacity-50"
                >
                  {{ p.name }}（{{ p.title }}）
                </button>
              </div>

              <!-- Escalated needs_attention panel with side-by-side screenshot + captcha -->
              <div
                v-if="t.status === 'needs_attention'"
                class="bg-cinnabar-50/75 border border-cinnabar-200 rounded-xl p-4 space-y-3"
              >
                <div class="text-xs text-cinnabar-700 whitespace-pre-wrap leading-relaxed">
                  {{ t.attention_reason || '开票页面需要您协助核对或输入验证码后继续提交。' }}
                </div>

                <div v-if="t.has_screenshot || t.profile_id" class="flex flex-col sm:flex-row sm:items-center gap-2.5">
                  <a
                    v-if="t.has_screenshot"
                    :href="`/api/v1/maid/claims/${t.id}/screenshot`"
                    target="_blank"
                    class="inline-flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-medium text-ink-900 bg-cream-100 border border-cinnabar-200 hover:bg-peach-50 transition-colors flex-shrink-0"
                  >
                    <svg class="w-4 h-4 text-cinnabar-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                    </svg>
                    <span>查看截图</span>
                  </a>

                  <template v-if="t.profile_id">
                    <input
                      v-model="captchaInputs[t.id]"
                      type="text"
                      placeholder="对照截图输入验证码（如无需验证码可直接重试）"
                      class="input-warm py-2 text-xs flex-1 bg-cream-50"
                    />
                    <button
                      type="button"
                      :disabled="uploading"
                      @click="retryClaim(t.id)"
                      class="btn-danger-warm py-2 px-4 text-xs flex-shrink-0"
                    >
                      提交重试
                    </button>
                  </template>
                </div>
              </div>
            </li>
          </ul>
        </div>

        <!-- Section 2: Memory Rules -->
        <div v-else class="ledger-card p-6 space-y-5">
          <div>
            <h2 class="font-display text-base font-bold text-ink-900">记忆规则</h2>
            <p class="text-xs text-ink-500 mt-1">匹配到销售方或项目摘要关键词时，新入账发票将自动套用报销状态与标签。</p>
          </div>

          <form @submit.prevent="addMemory" class="bg-cream-200/70 border border-cream-400 rounded-2xl p-4 space-y-3">
            <div class="grid grid-cols-1 md:grid-cols-6 gap-2.5">
              <input
                v-model="newMemory.rule_text"
                required
                placeholder="规则描述（如：大药房消费无需报销）"
                class="md:col-span-2 input-warm"
              />
              <input
                v-model="newMemory.match_pattern"
                placeholder="匹配关键词（如：大药房）"
                class="input-warm"
              />
              <select v-model="newMemory.reimbursement_status" class="input-warm">
                <option value="">自动报销状态（可选）</option>
                <option value="not_applicable">无需报销</option>
                <option value="unreimbursed">未报销</option>
                <option value="pending">报销中</option>
                <option value="reimbursed">已报销</option>
              </select>
              <input
                v-model="newMemory.tags"
                placeholder="自动标签（可选，逗号分隔）"
                class="input-warm"
              />
              <button type="submit" class="btn-mint w-full">添加规则</button>
            </div>
          </form>

          <div v-if="memories.length === 0" class="py-8 text-center text-sm text-ink-400">
            还没有记下任何报销习惯，在上方添加第一条规则吧。
          </div>
          <ul v-else class="divide-y divide-cream-400">
            <li
              v-for="m in memories"
              :key="m.id"
              class="py-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-sm"
            >
              <div class="flex flex-wrap items-center gap-2 min-w-0">
                <span class="font-medium text-ink-900">{{ m.rule_text }}</span>
                <span
                  v-if="m.match_pattern"
                  class="text-xs px-2 py-0.5 rounded-md bg-cream-200 text-ink-700 border border-cream-400 num-mono"
                >
                  关键词：{{ m.match_pattern }}
                </span>
                <span
                  v-if="parseMemoryAction(m.action_json).statusLabel"
                  class="stamp-badge bg-mint-50 text-mint-800 border-mint-200"
                >
                  {{ parseMemoryAction(m.action_json).statusLabel }}
                </span>
                <span
                  v-if="parseMemoryAction(m.action_json).tags"
                  class="stamp-badge bg-peach-50 text-ink-700 border-peach-200"
                >
                  #{{ parseMemoryAction(m.action_json).tags }}
                </span>
              </div>
              <button
                type="button"
                @click="removeMemory(m.id)"
                class="self-start sm:self-auto text-xs font-medium text-cinnabar-600 hover:text-cinnabar-700 hover:underline flex-shrink-0"
              >
                删除
              </button>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
