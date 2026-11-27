<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { AdminUserSummary } from '@/types'
import AppLayout from '@/components/AppLayout.vue'

const authStore = useAuthStore()

const users = ref<AdminUserSummary[]>([])
const loading = ref(false)
const error = ref('')
const actionError = ref('')
const actionSuccess = ref('')

const deleteModalFor = ref<AdminUserSummary | null>(null)
const deleting = ref(false)

const totalUsers = computed(() => users.value.length)
const activeUsers = computed(() => users.value.filter((u) => u.is_active).length)
const totalInvoices = computed(() => users.value.reduce((sum, u) => sum + (u.invoice_count || 0), 0))

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    users.value = await api.adminListUsers()
  } catch (err: any) {
    error.value = err.response?.data?.detail || '加载用户列表失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const formatDate = (iso: string) => {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

const updateUser = async (user: AdminUserSummary, patch: { is_active?: boolean; is_admin?: boolean }) => {
  actionError.value = ''
  actionSuccess.value = ''
  try {
    const updated = await api.adminUpdateUser(user.id, patch)
    const idx = users.value.findIndex((u) => u.id === user.id)
    if (idx >= 0) users.value[idx] = updated
    actionSuccess.value = `已更新 ${updated.email}`
  } catch (err: any) {
    actionError.value = err.response?.data?.detail || '更新用户失败'
  }
}

const toggleActive = (user: AdminUserSummary) => {
  const next = !user.is_active
  if (!next && !confirm(`确定停用 ${user.email}？停用后该账号将无法登录。`)) return
  void updateUser(user, { is_active: next })
}

const toggleAdmin = (user: AdminUserSummary) => {
  const next = !user.is_admin
  const verb = next ? '设为管理员' : '取消管理员权限'
  if (!confirm(`确定为 ${user.email} ${verb}？`)) return
  void updateUser(user, { is_admin: next })
}

const openDelete = (user: AdminUserSummary) => {
  actionError.value = ''
  actionSuccess.value = ''
  deleteModalFor.value = user
}

const confirmDelete = async () => {
  if (!deleteModalFor.value) return
  deleting.value = true
  actionError.value = ''
  try {
    await api.adminDeleteUser(deleteModalFor.value.id)
    users.value = users.value.filter((u) => u.id !== deleteModalFor.value!.id)
    actionSuccess.value = `已删除 ${deleteModalFor.value.email}`
    deleteModalFor.value = null
  } catch (err: any) {
    actionError.value = err.response?.data?.detail || '删除用户失败'
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <AppLayout>
    <div class="max-w-5xl mx-auto space-y-5">
      <!-- Header -->
      <div class="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <h2 class="font-display text-2xl font-bold text-ink-900">用户管理</h2>
          <p class="mt-1 text-sm text-ink-500">管理实例成员状态、管理员权限与账号数据。</p>
        </div>
        <button
          type="button"
          @click="load"
          :disabled="loading"
          class="btn-cream self-start sm:self-auto"
        >
          <svg
            class="w-4 h-4"
            :class="{ 'animate-spin': loading }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="1.8"
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
          刷新
        </button>
      </div>

      <!-- Summary Strip -->
      <div v-if="!loading && !error" class="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div class="ledger-card px-5 py-4">
          <div class="text-xs font-medium text-ink-500">用户总数</div>
          <div class="mt-1.5 text-2xl font-bold text-ink-900 num-mono">{{ totalUsers }}</div>
        </div>
        <div class="ledger-card px-5 py-4">
          <div class="text-xs font-medium text-ink-500">启用中</div>
          <div class="mt-1.5 text-2xl font-bold text-mint-700 num-mono">{{ activeUsers }}</div>
        </div>
        <div class="ledger-card px-5 py-4">
          <div class="text-xs font-medium text-ink-500">累计票据</div>
          <div class="mt-1.5 text-2xl font-bold text-ink-900 num-mono">{{ totalInvoices.toLocaleString() }}</div>
        </div>
      </div>

      <!-- Feedback Banners -->
      <div
        v-if="actionError"
        role="alert"
        class="text-sm text-cinnabar-700 bg-cinnabar-50 px-4 py-3 rounded-xl border border-cinnabar-200"
      >
        {{ actionError }}
      </div>
      <div
        v-if="actionSuccess"
        role="status"
        class="text-sm text-mint-800 bg-mint-50 px-4 py-3 rounded-xl border border-mint-200"
      >
        {{ actionSuccess }}
      </div>

      <!-- Loading / Error / Table -->
      <div v-if="loading" class="ledger-card p-10 text-center text-sm text-ink-500">
        正在加载用户列表…
      </div>
      <div
        v-else-if="error"
        role="alert"
        class="text-sm text-cinnabar-700 bg-cinnabar-50 px-4 py-3 rounded-xl border border-cinnabar-200"
      >
        {{ error }}
      </div>

      <div v-else class="ledger-card overflow-hidden">
        <div class="overflow-x-auto">
          <table class="min-w-full divide-y divide-cream-400">
            <thead class="bg-cream-300">
              <tr>
                <th class="px-6 py-3.5 text-left text-xs font-semibold text-ink-500 tracking-wide">邮箱</th>
                <th class="px-6 py-3.5 text-left text-xs font-semibold text-ink-500 tracking-wide">状态</th>
                <th class="px-6 py-3.5 text-left text-xs font-semibold text-ink-500 tracking-wide">角色</th>
                <th class="px-6 py-3.5 text-left text-xs font-semibold text-ink-500 tracking-wide">注册时间</th>
                <th class="px-6 py-3.5 text-right text-xs font-semibold text-ink-500 tracking-wide">票据数</th>
                <th class="px-6 py-3.5 text-right text-xs font-semibold text-ink-500 tracking-wide">操作</th>
              </tr>
            </thead>
            <tbody class="bg-cream-100 divide-y divide-cream-400/70">
              <tr
                v-for="user in users"
                :key="user.id"
                class="hover:bg-cream-50/90 transition-colors"
              >
                <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-ink-900">
                  <span>{{ user.email }}</span>
                  <span
                    v-if="user.id === authStore.user?.id"
                    class="ml-2 inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium bg-mint-50 text-mint-700 border border-mint-200"
                  >
                    当前
                  </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                  <span
                    v-if="user.is_active"
                    class="stamp-badge bg-mint-100 text-mint-800 border-mint-300/80"
                  >
                    <span class="w-1.5 h-1.5 rounded-full bg-mint-600" aria-hidden="true" />
                    启用
                  </span>
                  <span
                    v-else
                    class="stamp-badge bg-cream-300 text-ink-500 border-cream-500"
                  >
                    <span class="w-1.5 h-1.5 rounded-full bg-ink-300" aria-hidden="true" />
                    已停用
                  </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                  <span
                    v-if="user.is_admin"
                    class="stamp-badge bg-peach-100 text-ink-900 border-peach-200"
                  >
                    管理员
                  </span>
                  <span
                    v-else
                    class="stamp-badge bg-cream-200 text-ink-700 border-cream-400"
                  >
                    成员
                  </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-ink-500 num-mono">
                  {{ formatDate(user.created_at) }}
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-ink-900 text-right num-mono">
                  {{ user.invoice_count.toLocaleString() }}
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-right text-xs font-medium">
                  <div class="inline-flex items-center justify-end gap-2">
                    <button
                      type="button"
                      @click="toggleActive(user)"
                      class="px-2.5 py-1 rounded-lg border transition-colors"
                      :class="user.is_active
                        ? 'text-ink-700 bg-cream-50 border-cream-500 hover:bg-cream-300/70'
                        : 'text-mint-700 bg-mint-50 border-mint-300 hover:bg-mint-100'"
                    >
                      {{ user.is_active ? '停用' : '启用' }}
                    </button>
                    <button
                      type="button"
                      @click="toggleAdmin(user)"
                      class="px-2.5 py-1 rounded-lg text-ink-700 bg-cream-50 border border-cream-500 hover:bg-peach-50 hover:border-peach-200 transition-colors"
                    >
                      {{ user.is_admin ? '取消管理' : '设为管理' }}
                    </button>
                    <button
                      type="button"
                      @click="openDelete(user)"
                      :disabled="user.id === authStore.user?.id"
                      class="px-2.5 py-1 rounded-lg text-cinnabar-600 bg-cinnabar-50/70 border border-cinnabar-200 hover:bg-cinnabar-50 hover:text-cinnabar-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    >
                      删除
                    </button>
                  </div>
                </td>
              </tr>
              <tr v-if="users.length === 0">
                <td colspan="6" class="px-6 py-10 text-center text-sm text-ink-500">
                  暂无用户记录
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Delete Confirmation Modal -->
    <div
      v-if="deleteModalFor"
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-user-title"
      class="fixed inset-0 bg-ink-900/35 backdrop-blur-[2px] flex items-center justify-center p-4 z-50"
      @click.self="deleteModalFor = null"
    >
      <div class="ledger-card shadow-paper-lg w-full max-w-md p-6">
        <h3 id="delete-user-title" class="font-display text-lg font-bold text-ink-900">
          删除用户
        </h3>
        <p class="mt-2.5 text-sm text-ink-700">
          确认删除账号 <span class="font-semibold text-ink-900">{{ deleteModalFor.email }}</span>？
        </p>
        <div class="mt-3 rounded-xl bg-cinnabar-50 border border-cinnabar-200 px-3.5 py-2.5 text-sm text-cinnabar-700 leading-relaxed">
          此操作将永久删除该用户的全部
          <span class="num-mono font-semibold">{{ deleteModalFor.invoice_count }}</span>
          张票据、邮箱配置、扫描日志与附件文件，且无法撤销。
        </div>
        <div class="mt-6 flex justify-end gap-2.5">
          <button
            type="button"
            @click="deleteModalFor = null"
            class="btn-cream"
          >
            取消
          </button>
          <button
            type="button"
            @click="confirmDelete"
            :disabled="deleting"
            class="btn-danger-warm"
          >
            {{ deleting ? '删除中…' : '确认删除' }}
          </button>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
