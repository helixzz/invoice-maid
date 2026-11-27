<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'

const email = ref('')
const password = ref('')
const passwordConfirm = ref('')
const loading = ref(false)
const error = ref('')
const authStore = useAuthStore()

const handleSubmit = async () => {
  if (!email.value || !password.value || !passwordConfirm.value) return
  if (password.value !== passwordConfirm.value) {
    error.value = '两次输入的密码不一致，请重新核对。'
    return
  }
  if (password.value.length < 8) {
    error.value = '密码长度至少需要 8 位。'
    return
  }

  loading.value = true
  error.value = ''

  try {
    await authStore.register(email.value, password.value, passwordConfirm.value)
  } catch (err: any) {
    const detail = err.response?.data?.detail
    if (err.response?.status === 403) {
      error.value = '当前实例已关闭自助注册，请联系管理员。'
    } else if (typeof detail === 'string') {
      error.value = detail
    } else {
      error.value = '注册失败，请稍后重试。'
    }
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
            <p class="relative">你好呀，以后的票据都交给我来整理吧。</p>
          </div>
        </section>

        <!-- Right Register Form -->
        <section class="md:col-span-7 bg-cream-100 px-7 py-9 sm:px-10 sm:py-11 flex flex-col justify-center">
          <h1 class="font-display text-2xl font-bold text-ink-900">
            注册账号
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
                autocomplete="email"
                required
                v-model="email"
                :disabled="loading"
                class="input-warm py-2.5"
                placeholder="you@example.com"
              />
            </div>

            <div>
              <label for="password" class="block text-sm font-medium text-ink-700 mb-1.5">
                密码
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autocomplete="new-password"
                required
                minlength="8"
                v-model="password"
                :disabled="loading"
                class="input-warm py-2.5"
                placeholder="至少 8 位密码"
              />
            </div>

            <div>
              <label for="password_confirm" class="block text-sm font-medium text-ink-700 mb-1.5">
                确认密码
              </label>
              <input
                id="password_confirm"
                name="password_confirm"
                type="password"
                autocomplete="new-password"
                required
                minlength="8"
                v-model="passwordConfirm"
                :disabled="loading"
                class="input-warm py-2.5"
                placeholder="再次输入密码"
              />
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
                :disabled="loading || !email || !password || !passwordConfirm"
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
                {{ loading ? '创建中…' : '注册' }}
              </button>
            </div>

            <div class="pt-5 receipt-dashed flex items-center justify-between text-sm text-ink-500">
              <span>已有账号？</span>
              <router-link
                to="/login"
                class="font-medium text-mint-700 hover:text-mint-800 transition-colors"
              >
                去登录 →
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
