<script setup lang="ts">
import { ref } from 'vue'

export interface ToastMessage {
  id: number
  message: string
  type: 'success' | 'error' | 'info'
}

const toasts = ref<ToastMessage[]>([])
let nextId = 0

const addToast = (message: string, type: 'success' | 'error' | 'info' = 'info', duration?: number) => {
  const id = nextId++
  const defaultDuration = type === 'error' ? 9000 : type === 'success' ? 4500 : 6000
  toasts.value.push({ id, message, type })
  setTimeout(() => {
    removeToast(id)
  }, duration ?? defaultDuration)
}

const removeToast = (id: number) => {
  const index = toasts.value.findIndex(t => t.id === id)
  if (index > -1) {
    toasts.value.splice(index, 1)
  }
}

defineExpose({ addToast })
</script>

<template>
  <div class="fixed top-16 right-5 z-50 flex flex-col gap-2.5 w-[360px] max-w-[calc(100vw-2rem)]">
    <transition-group name="toast" tag="div" class="space-y-2.5">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        class="w-full bg-cream-100 shadow-paper-lg rounded-xl border border-cream-400 pointer-events-auto overflow-hidden"
        :class="{
          'border-l-4 border-l-mint-600': toast.type === 'success',
          'border-l-4 border-l-cinnabar-600': toast.type === 'error',
          'border-l-4 border-l-ink-400': toast.type === 'info',
        }"
      >
        <div class="px-4 py-3 flex items-start gap-3">
          <p class="text-sm text-ink-900 flex-1 leading-snug">{{ toast.message }}</p>
          <button @click="removeToast(toast.id)" class="text-ink-400 hover:text-ink-900 text-base leading-none">×</button>
        </div>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.25s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
