import { defineStore } from 'pinia'
import { api } from '@/api/client'
import router from '@/router'
import type { UserInfo } from '@/types'

interface AuthState {
  token: string | null
  user: UserInfo | null
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    token: localStorage.getItem('token') || null,
    user: null,
  }),
  getters: {
    isAuthenticated: (state) => !!state.token,
    isAdmin: (state) => state.user?.is_admin === true,
  },
  actions: {
    async login(email: string, password: string) {
      try {
        const response = await api.login(email, password)
        const token = response.access_token || (response as any).token
        this.token = token
        if (this.token) {
          localStorage.setItem('token', this.token)
          await this.fetchMe()
          router.push({ name: 'invoices' })
        }
      } catch (error) {
        console.error('Login failed', error)
        throw error
      }
    },
    async register(email: string, password: string, passwordConfirm: string) {
      const response = await api.register(email, password, passwordConfirm)
      const token = response.access_token
      this.token = token
      if (this.token) {
        localStorage.setItem('token', this.token)
        await this.fetchMe()
        router.push({ name: 'invoices' })
      }
    },
    async fetchMe() {
      if (!this.token) {
        this.user = null
        return
      }
      try {
        this.user = await api.me()
      } catch (error) {
        console.error('fetchMe failed', error)
        this.user = null
      }
    },
    async changePassword(
      currentPassword: string,
      newPassword: string,
      newPasswordConfirm: string,
    ) {
      await api.changePassword(currentPassword, newPassword, newPasswordConfirm)
    },
    logout() {
      this.token = null
      this.user = null
      localStorage.removeItem('token')
      router.push({ name: 'login' })
    },
  },
})
