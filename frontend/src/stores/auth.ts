import { defineStore } from 'pinia'
import apiClient from '@/api/client'
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
    async login(password: string) {
      try {
        const response = await apiClient.post('/auth/login', { password })
        this.token = response.data.token
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
