import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import { useAuthStore } from './stores/auth'
import './style.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)

const authStore = useAuthStore()
if (authStore.token) {
  void authStore.fetchMe()
}

app.mount('#app')
