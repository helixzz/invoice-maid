import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import LoginView from '@/views/LoginView.vue'
import InvoiceListView from '@/views/InvoiceListView.vue'
import InvoiceDetailView from '@/views/InvoiceDetailView.vue'
import SettingsView from '@/views/SettingsView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: '/invoices'
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView
    },
    {
      path: '/invoices',
      name: 'invoices',
      component: InvoiceListView,
      meta: { requiresAuth: true }
    },
    {
      path: '/invoices/:id',
      name: 'invoice-detail',
      component: InvoiceDetailView,
      meta: { requiresAuth: true }
    },
    {
      path: '/settings',
      name: 'settings',
      component: SettingsView,
      meta: { requiresAuth: true }
    }
  ]
})

router.beforeEach((to, _from, next) => {
  const authStore = useAuthStore()
  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    next({ name: 'login' })
  } else {
    next()
  }
})

export default router
