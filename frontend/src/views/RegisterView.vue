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
    error.value = 'Passwords do not match'
    return
  }
  if (password.value.length < 8) {
    error.value = 'Password must be at least 8 characters'
    return
  }

  loading.value = true
  error.value = ''

  try {
    await authStore.register(email.value, password.value, passwordConfirm.value)
  } catch (err: any) {
    const detail = err.response?.data?.detail
    if (err.response?.status === 403) {
      error.value = 'Registration is disabled on this instance. Contact the administrator.'
    } else if (typeof detail === 'string') {
      error.value = detail
    } else {
      error.value = 'Registration failed. Please try again.'
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen bg-slate-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
    <div class="sm:mx-auto sm:w-full sm:max-w-md flex flex-col items-center">
      <img src="/favicon.png" alt="Invoice Maid" width="64" height="64" class="mb-4">
      <h2 class="mt-6 text-center text-3xl font-extrabold text-slate-900">
        Create an account
      </h2>
      <p class="mt-2 text-center text-sm text-slate-600">
        Invoice Maid — AI-Powered Invoice Extraction Service
      </p>
    </div>

    <div class="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
      <div class="bg-white py-8 px-4 shadow-sm border border-slate-200 sm:rounded-xl sm:px-10">
        <form class="space-y-6" @submit.prevent="handleSubmit">
          <div>
            <label for="email" class="block text-sm font-medium text-slate-700">
              Email
            </label>
            <div class="mt-1">
              <input
                id="email"
                name="email"
                type="email"
                autocomplete="email"
                required
                v-model="email"
                :disabled="loading"
                class="appearance-none block w-full px-3 py-2 border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm transition-colors"
                placeholder="you@example.com"
              />
            </div>
          </div>

          <div>
            <label for="password" class="block text-sm font-medium text-slate-700">
              Password
            </label>
            <div class="mt-1">
              <input
                id="password"
                name="password"
                type="password"
                autocomplete="new-password"
                required
                minlength="8"
                v-model="password"
                :disabled="loading"
                class="appearance-none block w-full px-3 py-2 border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm transition-colors"
                placeholder="At least 8 characters"
              />
            </div>
          </div>

          <div>
            <label for="password_confirm" class="block text-sm font-medium text-slate-700">
              Confirm Password
            </label>
            <div class="mt-1">
              <input
                id="password_confirm"
                name="password_confirm"
                type="password"
                autocomplete="new-password"
                required
                minlength="8"
                v-model="passwordConfirm"
                :disabled="loading"
                class="appearance-none block w-full px-3 py-2 border border-slate-300 rounded-md shadow-sm placeholder-slate-400 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm transition-colors"
                placeholder="Re-enter password"
              />
            </div>
          </div>

          <div v-if="error" class="text-sm text-red-600 bg-red-50 p-3 rounded-md border border-red-100">
            {{ error }}
          </div>

          <div>
            <button
              type="submit"
              :disabled="loading || !email || !password || !passwordConfirm"
              class="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <svg v-if="loading" class="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              {{ loading ? 'Creating account...' : 'Sign up' }}
            </button>
          </div>

          <div class="text-center text-sm text-slate-500">
            Already have an account?
            <router-link to="/login" class="font-medium text-blue-600 hover:text-blue-500">
              Sign in
            </router-link>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>
