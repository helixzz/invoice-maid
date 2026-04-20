import { defineStore } from 'pinia'
import { api } from '@/api/client'
import router from '@/router'

interface AuthState {
  token: string | null
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    token: localStorage.getItem('token') || null,
  }),
  getters: {
    isAuthenticated: (state) => !!state.token,
  },
  actions: {
    async login(email: string, password: string) {
      try {
        const response = await api.login(email, password)
        const token = response.access_token || (response as any).token
        this.token = token
        if (this.token) {
          localStorage.setItem('token', this.token)
          router.push({ name: 'invoices' })
        }
      } catch (error) {
        console.error('Login failed', error)
        throw error
      }
    },
    logout() {
      this.token = null
      localStorage.removeItem('token')
      router.push({ name: 'login' })
    },
  },
})
