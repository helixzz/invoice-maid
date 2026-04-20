<script setup lang="ts">
import { onMounted, ref } from 'vue'
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

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    users.value = await api.adminListUsers()
  } catch (err: any) {
    error.value = err.response?.data?.detail || 'Failed to load users'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const formatDate = (iso: string) => {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

const updateUser = async (user: AdminUserSummary, patch: { is_active?: boolean; is_admin?: boolean }) => {
  actionError.value = ''
  actionSuccess.value = ''
  try {
    const updated = await api.adminUpdateUser(user.id, patch)
    const idx = users.value.findIndex((u) => u.id === user.id)
    if (idx >= 0) users.value[idx] = updated
    actionSuccess.value = `Updated ${updated.email}`
  } catch (err: any) {
    actionError.value = err.response?.data?.detail || 'Failed to update user'
  }
}

const toggleActive = (user: AdminUserSummary) => {
  const next = !user.is_active
  if (!next && !confirm(`Deactivate ${user.email}? They will not be able to log in.`)) return
  void updateUser(user, { is_active: next })
}

const toggleAdmin = (user: AdminUserSummary) => {
  const next = !user.is_admin
  const verb = next ? 'Promote to admin' : 'Demote from admin'
  if (!confirm(`${verb}: ${user.email}?`)) return
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
    actionSuccess.value = `Deleted ${deleteModalFor.value.email}`
    deleteModalFor.value = null
  } catch (err: any) {
    actionError.value = err.response?.data?.detail || 'Failed to delete user'
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <AppLayout>
    <div class="max-w-5xl mx-auto">
      <div class="mb-6">
        <h1 class="text-2xl font-bold text-slate-900">User Management</h1>
        <p class="mt-1 text-sm text-slate-500">
          Activate, deactivate, promote, or delete users. Admin access required.
        </p>
      </div>

      <div v-if="actionError" class="mb-4 text-sm text-red-600 bg-red-50 p-3 rounded-md border border-red-100">
        {{ actionError }}
      </div>
      <div v-if="actionSuccess" class="mb-4 text-sm text-green-700 bg-green-50 p-3 rounded-md border border-green-100">
        {{ actionSuccess }}
      </div>

      <div v-if="loading" class="text-sm text-slate-500">Loading users…</div>
      <div v-else-if="error" class="text-sm text-red-600 bg-red-50 p-3 rounded-md border border-red-100">
        {{ error }}
      </div>

      <div v-else class="bg-white shadow-sm border border-slate-200 rounded-xl overflow-hidden">
        <table class="min-w-full divide-y divide-slate-200">
          <thead class="bg-slate-50">
            <tr>
              <th class="px-6 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Email</th>
              <th class="px-6 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Status</th>
              <th class="px-6 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Role</th>
              <th class="px-6 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Joined</th>
              <th class="px-6 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Invoices</th>
              <th class="px-6 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Actions</th>
            </tr>
          </thead>
          <tbody class="bg-white divide-y divide-slate-100">
            <tr v-for="user in users" :key="user.id">
              <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-slate-900">
                {{ user.email }}
                <span v-if="user.id === authStore.user?.id" class="ml-2 text-xs font-normal text-slate-400">(you)</span>
              </td>
              <td class="px-6 py-4 whitespace-nowrap">
                <span
                  v-if="user.is_active"
                  class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800"
                >Active</span>
                <span
                  v-else
                  class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600"
                >Inactive</span>
              </td>
              <td class="px-6 py-4 whitespace-nowrap">
                <span
                  v-if="user.is_admin"
                  class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800"
                >Admin</span>
                <span
                  v-else
                  class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600"
                >User</span>
              </td>
              <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{{ formatDate(user.created_at) }}</td>
              <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-700">{{ user.invoice_count }}</td>
              <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium space-x-2">
                <button
                  @click="toggleActive(user)"
                  class="text-slate-600 hover:text-slate-900"
                >
                  {{ user.is_active ? 'Deactivate' : 'Activate' }}
                </button>
                <button
                  @click="toggleAdmin(user)"
                  class="text-amber-600 hover:text-amber-900"
                >
                  {{ user.is_admin ? 'Demote' : 'Promote' }}
                </button>
                <button
                  @click="openDelete(user)"
                  :disabled="user.id === authStore.user?.id"
                  class="text-red-600 hover:text-red-900 disabled:text-slate-300 disabled:cursor-not-allowed"
                >
                  Delete
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div
      v-if="deleteModalFor"
      class="fixed inset-0 bg-slate-900/50 flex items-center justify-center p-4 z-50"
      @click.self="deleteModalFor = null"
    >
      <div class="bg-white rounded-xl shadow-xl w-full max-w-md">
        <div class="p-6">
          <h3 class="text-lg font-semibold text-slate-900">Delete user</h3>
          <p class="mt-2 text-sm text-slate-600">
            Delete <span class="font-medium text-slate-900">{{ deleteModalFor.email }}</span>?
          </p>
          <p class="mt-2 text-sm text-red-600">
            This will permanently delete all {{ deleteModalFor.invoice_count }} of their invoices,
            email accounts, scan logs, and files. This cannot be undone.
          </p>
          <div class="mt-6 flex justify-end gap-2">
            <button
              @click="deleteModalFor = null"
              class="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              @click="confirmDelete"
              :disabled="deleting"
              class="px-4 py-2 text-sm font-medium text-white bg-red-600 border border-transparent rounded-md hover:bg-red-700 disabled:opacity-50"
            >
              {{ deleting ? 'Deleting…' : 'Delete user' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>
