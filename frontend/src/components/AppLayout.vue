<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import MaidChatDrawer from '@/components/MaidChatDrawer.vue'

const route = useRoute()
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

const pageTitle = computed(() => {
  if (route.path.startsWith('/invoices/')) return '发票核对'
  if (route.path.startsWith('/invoices')) return '票据台账'
  if (route.path.startsWith('/upload')) return '上传票据'
  if (route.path.startsWith('/maid')) return '米尔工作台'
  if (route.path.startsWith('/settings')) return '系统设置'
  if (route.path.startsWith('/admin')) return '用户管理'
  return '票据台账'
})

const navItems = computed(() => {
  const items = [
    { to: '/invoices', label: '票据台账', group: 'daily', icon: 'ledger' },
    { to: '/upload', label: '上传票据', group: 'daily', icon: 'upload' },
    { to: '/maid', label: '米尔工作台', group: 'daily', icon: 'maid' },
    { to: '/settings', label: '系统设置', group: 'config', icon: 'settings' },
  ]
  if (authStore.isAdmin) {
    items.push({ to: '/admin', label: '用户管理', group: 'config', icon: 'admin' })
  }
  return items
})

const isActive = (path: string) => route.path.startsWith(path)

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
    changePasswordError.value = '两次输入的新密码不一致'
    return
  }
  if (newPassword.value.length < 8) {
    changePasswordError.value = '新密码至少 8 位'
    return
  }
  if (newPassword.value === currentPassword.value) {
    changePasswordError.value = '新密码不能与当前密码相同'
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
    changePasswordError.value = typeof detail === 'string' ? detail : '修改密码失败'
  } finally {
    changePasswordLoading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen bg-cream-200 text-ink-900 flex flex-col lg:flex-row">
    <!-- Desktop Left Stationery Sidebar (236px) -->
    <aside class="hidden lg:flex lg:flex-col lg:w-[236px] lg:fixed lg:inset-y-0 bg-cream-300 border-r border-cream-400 z-30">
      <!-- Brand Header -->
      <router-link to="/invoices" class="flex items-center gap-3 px-5 pt-5 pb-4">
        <img
          src="/favicon.png"
          alt="Miri"
          class="w-11 h-11 rounded-full ring-2 ring-cream-100 shadow-paper flex-shrink-0"
        />
        <div class="min-w-0">
          <div class="font-display font-bold text-base text-ink-900 leading-tight truncate">Invoice Maid</div>
          <div class="text-xs text-ink-500 flex items-center gap-1 mt-0.5">
            <span class="w-1.5 h-1.5 rounded-full bg-mint-500"></span>
            <span>米尔 Miri</span>
          </div>
        </div>
      </router-link>

      <!-- Navigation -->
      <nav class="flex-1 px-3 py-2 space-y-5 overflow-y-auto">
        <div>
          <div class="px-3 mb-1.5 text-[11px] font-medium text-ink-400 tracking-wide">日常账本</div>
          <div class="space-y-1">
            <router-link
              v-for="item in navItems.filter(i => i.group === 'daily')"
              :key="item.to"
              :to="item.to"
              class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors"
              :class="isActive(item.to)
                ? 'bg-mint-100/90 text-mint-800 border border-mint-200/80 shadow-sm'
                : 'text-ink-700 hover:bg-cream-100/70 hover:text-ink-900'"
            >
              <svg v-if="item.icon === 'ledger'" class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
              <svg v-else-if="item.icon === 'upload'" class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>
              <svg v-else class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" /></svg>
              <span>{{ item.label }}</span>
            </router-link>
          </div>
        </div>

        <div class="pt-3 border-t border-cream-400/80">
          <div class="px-3 mb-1.5 text-[11px] font-medium text-ink-400 tracking-wide">偏好与管理</div>
          <div class="space-y-1">
            <router-link
              v-for="item in navItems.filter(i => i.group === 'config')"
              :key="item.to"
              :to="item.to"
              class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors"
              :class="isActive(item.to)
                ? 'bg-mint-100/90 text-mint-800 border border-mint-200/80 shadow-sm'
                : 'text-ink-700 hover:bg-cream-100/70 hover:text-ink-900'"
            >
              <svg v-if="item.icon === 'settings'" class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
              <svg v-else class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
              <span>{{ item.label }}</span>
            </router-link>
          </div>
        </div>
      </nav>

      <!-- User Footer Card -->
      <div v-if="authStore.user" class="p-3 border-t border-cream-400 relative">
        <button
          @click="menuOpen = !menuOpen"
          class="w-full flex items-center gap-2.5 p-2.5 rounded-xl bg-cream-100/80 hover:bg-cream-100 border border-cream-400 text-left transition-colors"
          :aria-expanded="menuOpen"
          aria-haspopup="menu"
        >
          <span class="inline-flex h-8 w-8 items-center justify-center rounded-full bg-mint-200 text-mint-800 text-xs font-bold flex-shrink-0">
            {{ userInitials }}
          </span>
          <div class="min-w-0 flex-1">
            <div class="text-xs font-medium text-ink-900 truncate">{{ authStore.user.email }}</div>
            <div class="text-[11px] text-ink-400">{{ authStore.isAdmin ? '管理员' : '成员' }}</div>
          </div>
          <svg class="w-4 h-4 text-ink-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9l4-4 4 4m0 6l-4 4-4-4" /></svg>
        </button>

        <div
          v-if="menuOpen"
          class="absolute bottom-16 left-3 right-3 rounded-xl bg-cream-100 shadow-paper-lg border border-cream-400 py-1.5 z-50"
          role="menu"
        >
          <button
            @click="openChangePassword"
            class="w-full text-left px-3.5 py-2 text-sm text-ink-700 hover:bg-cream-200 transition-colors"
            role="menuitem"
          >
            修改密码
          </button>
          <button
            @click="handleLogout"
            class="w-full text-left px-3.5 py-2 text-sm text-cinnabar-600 hover:bg-cinnabar-50 transition-colors"
            role="menuitem"
          >
            退出登录
          </button>
        </div>
      </div>
    </aside>

    <!-- Main Content Column -->
    <div class="flex-1 flex flex-col lg:pl-[236px] min-w-0 pb-16 lg:pb-0">
      <!-- Slim Top Status Bar -->
      <header class="sticky top-0 z-20 h-14 bg-cream-200/90 backdrop-blur-md border-b border-cream-400 px-4 sm:px-6 lg:px-8 flex items-center justify-between gap-4">
        <div class="flex items-center gap-3 min-w-0">
          <img src="/favicon.png" alt="Miri" class="w-7 h-7 rounded-full lg:hidden" />
          <h1 class="font-display font-bold text-lg text-ink-900 truncate">{{ pageTitle }}</h1>
        </div>
        <div class="flex items-center gap-2.5">
          <router-link to="/upload" class="btn-mint text-xs sm:text-sm py-1.5 px-3.5">
            + 上传票据
          </router-link>
          <button
            v-if="authStore.user"
            @click="menuOpen = !menuOpen"
            class="lg:hidden inline-flex h-8 w-8 items-center justify-center rounded-full bg-mint-200 text-mint-800 text-xs font-bold"
            aria-label="账号菜单"
          >
            {{ userInitials }}
          </button>
        </div>
      </header>

      <!-- Mobile Dropdown Menu -->
      <div
        v-if="menuOpen"
        class="lg:hidden fixed top-14 right-4 w-48 rounded-xl bg-cream-100 shadow-paper-lg border border-cream-400 py-1.5 z-50"
      >
        <div class="px-3.5 py-1.5 text-xs text-ink-400 truncate border-b border-cream-400">{{ authStore.user?.email }}</div>
        <button @click="openChangePassword" class="w-full text-left px-3.5 py-2 text-sm text-ink-700 hover:bg-cream-200">修改密码</button>
        <button @click="handleLogout" class="w-full text-left px-3.5 py-2 text-sm text-cinnabar-600 hover:bg-cinnabar-50">退出登录</button>
      </div>

      <main class="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <slot />
      </main>
    </div>

    <!-- Mobile Bottom Tab Bar (4 items) -->
    <nav class="lg:hidden fixed bottom-0 inset-x-0 z-30 bg-cream-100/95 backdrop-blur border-t border-cream-400 grid grid-cols-4 h-14">
      <router-link
        to="/invoices"
        class="flex flex-col items-center justify-center text-[11px] font-medium"
        :class="isActive('/invoices') ? 'text-mint-700' : 'text-ink-500'"
      >
        <span>台账</span>
      </router-link>
      <router-link
        to="/upload"
        class="flex flex-col items-center justify-center text-[11px] font-medium"
        :class="isActive('/upload') ? 'text-mint-700' : 'text-ink-500'"
      >
        <span>上传</span>
      </router-link>
      <router-link
        to="/maid"
        class="flex flex-col items-center justify-center text-[11px] font-medium"
        :class="isActive('/maid') ? 'text-mint-700' : 'text-ink-500'"
      >
        <span>米尔</span>
      </router-link>
      <router-link
        to="/settings"
        class="flex flex-col items-center justify-center text-[11px] font-medium"
        :class="isActive('/settings') ? 'text-mint-700' : 'text-ink-500'"
      >
        <span>设置</span>
      </router-link>
    </nav>

    <!-- Change Password Modal -->
    <div
      v-if="changePasswordOpen"
      class="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/35 backdrop-blur-[2px] p-4"
      role="dialog"
      aria-modal="true"
    >
      <div class="ledger-card w-full max-w-md p-6 shadow-paper-lg">
        <h2 class="font-display text-lg font-bold text-ink-900 mb-4">修改密码</h2>
        <form @submit.prevent="submitChangePassword" class="space-y-4">
          <div>
            <label class="block text-xs font-medium text-ink-500 mb-1">当前密码</label>
            <input v-model="currentPassword" type="password" required class="input-warm" />
          </div>
          <div>
            <label class="block text-xs font-medium text-ink-500 mb-1">新密码（至少 8 位）</label>
            <input v-model="newPassword" type="password" required class="input-warm" />
          </div>
          <div>
            <label class="block text-xs font-medium text-ink-500 mb-1">确认新密码</label>
            <input v-model="newPasswordConfirm" type="password" required class="input-warm" />
          </div>
          <div v-if="changePasswordError" class="text-xs text-cinnabar-600 bg-cinnabar-50 border border-cinnabar-200 rounded-xl p-2.5">
            {{ changePasswordError }}
          </div>
          <div v-if="changePasswordSuccess" class="text-xs text-mint-700 bg-mint-50 border border-mint-200 rounded-xl p-2.5">
            密码已更新，其他设备会话已自动注销。
          </div>
          <div class="flex justify-end gap-2 pt-2">
            <button type="button" @click="changePasswordOpen = false" class="btn-cream">关闭</button>
            <button
              type="submit"
              :disabled="changePasswordLoading || !currentPassword || !newPassword || !newPasswordConfirm"
              class="btn-mint"
            >
              {{ changePasswordLoading ? '保存中…' : '更新密码' }}
            </button>
          </div>
        </form>
      </div>
    </div>

    <MaidChatDrawer v-if="authStore.isAuthenticated" />
  </div>
</template>
