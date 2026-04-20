<script setup lang="ts">
import { ref, computed } from 'vue'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

const menuOpen = ref(false)
const changePasswordOpen = ref(false)
const currentPassword = ref('')
const newPassword = ref('')
const newPasswordConfirm = ref('')
const changePasswordError = ref('')
const changePasswordSuccess = ref(false)
const changePasswordLoading = ref(false)

const userInitials = computed(() => {
  const email = authStore.user?.email
  if (!email) return '?'
  return email.slice(0, 2).toUpperCase()
})

const handleLogout = () => {
  menuOpen.value = false
  authStore.logout()
}

const openChangePassword = () => {
  menuOpen.value = false
  changePasswordOpen.value = true
  changePasswordError.value = ''
  changePasswordSuccess.value = false
  currentPassword.value = ''
  newPassword.value = ''
  newPasswordConfirm.value = ''
}

const submitChangePassword = async () => {
  if (newPassword.value !== newPasswordConfirm.value) {
    changePasswordError.value = 'New passwords do not match'
    return
  }
  if (newPassword.value.length < 8) {
    changePasswordError.value = 'New password must be at least 8 characters'
    return
  }
  if (newPassword.value === currentPassword.value) {
    changePasswordError.value = 'New password must differ from current'
    return
  }

  changePasswordLoading.value = true
  changePasswordError.value = ''
  changePasswordSuccess.value = false

  try {
    await authStore.changePassword(
      currentPassword.value,
      newPassword.value,
      newPasswordConfirm.value,
    )
    changePasswordSuccess.value = true
    currentPassword.value = ''
    newPassword.value = ''
    newPasswordConfirm.value = ''
  } catch (err: any) {
    const detail = err.response?.data?.detail
    changePasswordError.value =
      typeof detail === 'string' ? detail : 'Failed to change password'
  } finally {
    changePasswordLoading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen bg-slate-50">
    <nav class="bg-white shadow-sm border-b border-slate-200">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="flex justify-between h-16">
          <div class="flex">
            <div class="flex-shrink-0 flex items-center">
              <img src="/favicon.png" alt="" width="28" height="28" class="mr-2">
              <span class="text-xl font-bold text-slate-800">Invoice Maid</span>
            </div>
            <div class="hidden sm:-my-px sm:ml-6 sm:flex sm:space-x-8">
              <router-link
                to="/invoices"
                class="inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium"
                :class="[$route.path.startsWith('/invoices') ? 'border-blue-500 text-slate-900' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300']"
              >
                Invoices
              </router-link>
              <router-link
                to="/upload"
                class="inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium"
                :class="[$route.path.startsWith('/upload') ? 'border-blue-500 text-slate-900' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300']"
              >
                Upload
              </router-link>
              <router-link
                to="/settings"
                class="inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium"
                :class="[$route.path.startsWith('/settings') ? 'border-blue-500 text-slate-900' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300']"
              >
                Settings
              </router-link>
            </div>
          </div>
          <div class="hidden sm:ml-6 sm:flex sm:items-center relative">
            <button
              v-if="authStore.user"
              @click="menuOpen = !menuOpen"
              class="flex items-center gap-2 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 rounded-md focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
              :aria-expanded="menuOpen"
              aria-haspopup="menu"
            >
              <span class="inline-flex h-7 w-7 items-center justify-center rounded-full bg-blue-100 text-blue-700 text-xs font-semibold">
                {{ userInitials }}
              </span>
              <span class="hidden md:inline max-w-[18ch] truncate">{{ authStore.user.email }}</span>
              <svg class="h-4 w-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/>
              </svg>
            </button>
            <button
              v-else
              @click="handleLogout"
              class="inline-flex items-center px-3 py-2 border border-transparent text-sm font-medium rounded-md text-slate-500 hover:text-slate-700 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors"
            >
              Logout
            </button>

            <div
              v-if="menuOpen"
              class="absolute right-0 top-full mt-2 w-56 rounded-md shadow-lg bg-white ring-1 ring-slate-200 z-50 overflow-hidden"
              role="menu"
              @click.outside="menuOpen = false"
            >
              <div class="px-4 py-3 border-b border-slate-100 text-sm text-slate-600">
                <div class="font-medium text-slate-900 truncate">{{ authStore.user?.email }}</div>
                <div v-if="authStore.user?.is_admin" class="mt-0.5 text-xs text-amber-600 font-semibold uppercase tracking-wide">
                  Admin
                </div>
              </div>
              <button
                @click="openChangePassword"
                class="block w-full text-left px-4 py-2 text-sm text-slate-700 hover:bg-slate-100"
                role="menuitem"
              >
                Change password
              </button>
              <button
                @click="handleLogout"
                class="block w-full text-left px-4 py-2 text-sm text-slate-700 hover:bg-slate-100 border-t border-slate-100"
                role="menuitem"
              >
                Logout
              </button>
            </div>
          </div>
        </div>
      </div>
    </nav>

    <main class="py-10">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <slot />
      </div>
    </main>

    <div
      v-if="changePasswordOpen"
      class="fixed inset-0 bg-slate-900/50 flex items-center justify-center p-4 z-50"
      @click.self="changePasswordOpen = false"
    >
      <div class="bg-white rounded-xl shadow-xl w-full max-w-md">
        <div class="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <h3 class="text-lg font-semibold text-slate-900">Change password</h3>
          <button
            @click="changePasswordOpen = false"
            class="text-slate-400 hover:text-slate-600"
            aria-label="Close"
          >
            <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <form @submit.prevent="submitChangePassword" class="p-6 space-y-4">
          <div>
            <label class="block text-sm font-medium text-slate-700">Current password</label>
            <input
              type="password"
              v-model="currentPassword"
              required
              autocomplete="current-password"
              class="mt-1 block w-full px-3 py-2 border border-slate-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700">New password</label>
            <input
              type="password"
              v-model="newPassword"
              required
              minlength="8"
              autocomplete="new-password"
              class="mt-1 block w-full px-3 py-2 border border-slate-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
              placeholder="At least 8 characters"
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700">Confirm new password</label>
            <input
              type="password"
              v-model="newPasswordConfirm"
              required
              minlength="8"
              autocomplete="new-password"
              class="mt-1 block w-full px-3 py-2 border border-slate-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
            />
          </div>

          <div v-if="changePasswordError" class="text-sm text-red-600 bg-red-50 p-3 rounded-md border border-red-100">
            {{ changePasswordError }}
          </div>
          <div v-if="changePasswordSuccess" class="text-sm text-green-700 bg-green-50 p-3 rounded-md border border-green-100">
            Password updated. Sessions on other devices have been signed out.
          </div>

          <div class="flex justify-end gap-2 pt-2">
            <button
              type="button"
              @click="changePasswordOpen = false"
              class="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50"
            >
              Close
            </button>
            <button
              type="submit"
              :disabled="changePasswordLoading || !currentPassword || !newPassword || !newPasswordConfirm"
              class="px-4 py-2 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {{ changePasswordLoading ? 'Saving…' : 'Update password' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>
