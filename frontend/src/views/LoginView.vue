<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'

const email = ref('')
const password = ref('')
const showPassword = ref(false)
const loading = ref(false)
const error = ref('')
const authStore = useAuthStore()

const handleSubmit = async () => {
  if (!email.value || !password.value) return

  loading.value = true
  error.value = ''

  try {
    await authStore.login(email.value, password.value)
  } catch (err: any) {
    error.value = err.response?.data?.detail || '登录失败，请检查邮箱或密码后重试。'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen paper-dots flex items-center justify-center px-4 py-10">
    <div class="w-full max-w-[820px] drop-shadow-[0_14px_28px_rgba(72,36,24,0.10)]">
      <div class="grid grid-cols-1 md:grid-cols-12 bg-cream-100 border border-b-0 border-cream-400 rounded-t-2xl overflow-hidden">
        <!-- Left Brand Panel -->
        <section
          class="md:col-span-5 bg-gradient-to-b from-mint-50 via-mint-50/75 to-cream-100 px-7 py-10 md:py-12 flex flex-col items-center justify-center text-center border-b border-dashed border-cream-500 md:border-b-0 md:border-r"
        >
          <div class="p-2 rounded-full border border-dashed border-mint-300 bg-cream-50/60">
            <div class="p-1.5 rounded-full bg-cream-100 ring-1 ring-mint-200/80 shadow-paper">
              <img
                src="/favicon.png"
                alt="Invoice Maid"
                width="140"
                height="140"
                class="w-[140px] h-[140px] rounded-full object-cover block"
              />
            </div>
          </div>

          <h2 class="mt-6 font-display text-xl font-bold text-ink-900 tracking-tight">
            Invoice Maid · 米尔 Miri
          </h2>

          <div
            class="relative mt-5 max-w-[260px] rounded-xl bg-cream-100/95 border border-cream-400 px-4 py-3 text-sm leading-relaxed text-ink-700 shadow-paper"
          >
            <span
              class="absolute -top-1.5 left-1/2 -translate-x-1/2 w-3 h-3 rotate-45 bg-cream-100 border-l border-t border-cream-400"
              aria-hidden="true"
            />
            <p class="relative">欢迎回来，票据都帮你整理好啦。</p>
          </div>
        </section>

        <!-- Right Login Form -->
        <section class="md:col-span-7 bg-cream-100 px-7 py-9 sm:px-10 sm:py-11 flex flex-col justify-center">
          <h1 class="font-display text-2xl font-bold text-ink-900">
            登录
          </h1>

          <form class="mt-7 space-y-5" @submit.prevent="handleSubmit">
            <div>
              <label for="email" class="block text-sm font-medium text-ink-700 mb-1.5">
                邮箱
              </label>
              <input
                id="email"
                name="email"
                type="email"
                autocomplete="username"
                required
                v-model="email"
                :disabled="loading"
                class="input-warm py-2.5"
                placeholder="admin@local"
              />
            </div>

            <div>
              <label for="password" class="block text-sm font-medium text-ink-700 mb-1.5">
                密码
              </label>
              <div class="relative">
                <input
                  id="password"
                  name="password"
                  :type="showPassword ? 'text' : 'password'"
                  autocomplete="current-password"
                  required
                  v-model="password"
                  :disabled="loading"
                  class="input-warm py-2.5 pr-10"
                  placeholder="请输入密码"
                />
                <button
                  type="button"
                  @click="showPassword = !showPassword"
                  :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                  class="absolute inset-y-0 right-0 px-3 flex items-center text-ink-400 hover:text-ink-700 transition-colors"
                >
                  <svg v-if="!showPassword" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                  </svg>
                  <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                  </svg>
                </button>
              </div>
            </div>

            <div
              v-if="error"
              role="alert"
              class="text-sm text-cinnabar-700 bg-cinnabar-50 px-3.5 py-2.5 rounded-xl border border-cinnabar-200"
            >
              {{ error }}
            </div>

            <div class="pt-1">
              <button
                type="submit"
                :disabled="loading || !email || !password"
                class="btn-mint w-full py-2.5"
              >
                <svg
                  v-if="loading"
                  class="animate-spin h-4 w-4 text-white"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                  <path
                    class="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  />
                </svg>
                {{ loading ? '登录中…' : '登录' }}
              </button>
            </div>

            <div class="pt-5 receipt-dashed flex items-center justify-between text-sm text-ink-500">
              <span>还没有账号？</span>
              <router-link
                to="/register"
                class="font-medium text-mint-700 hover:text-mint-800 transition-colors"
              >
                去注册 →
              </router-link>
            </div>
          </form>
        </section>
      </div>

      <!-- Perforated Receipt Bottom Edge -->
      <div class="receipt-zig w-full" aria-hidden="true" />
    </div>
  </div>
</template>
