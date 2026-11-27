<script setup lang="ts">
import { ref } from 'vue'

defineProps<{
  title: string
  message: string
  confirmText?: string
  cancelText?: string
}>()

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()

const isOpen = ref(false)

const open = () => {
  isOpen.value = true
}

const close = () => {
  isOpen.value = false
  emit('cancel')
}

const confirm = () => {
  isOpen.value = false
  emit('confirm')
}

defineExpose({ open, close })
</script>

<template>
  <div v-if="isOpen" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-ink-900/35 backdrop-blur-[2px]" role="dialog" aria-modal="true">
    <div class="ledger-card max-w-md w-full p-6 shadow-paper-lg">
      <div class="flex items-start gap-3.5">
        <div class="flex-shrink-0 flex items-center justify-center h-10 w-10 rounded-full bg-cinnabar-50 border border-cinnabar-200 text-cinnabar-600">
          <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
        </div>
        <div class="flex-1 min-w-0">
          <h3 class="text-base font-bold text-ink-900">{{ title }}</h3>
          <p class="mt-1.5 text-sm text-ink-500 leading-relaxed">{{ message }}</p>
        </div>
      </div>
      <div class="mt-5 flex justify-end gap-2.5">
        <button @click="close" type="button" class="btn-cream">
          {{ cancelText || '取消' }}
        </button>
        <button @click="confirm" type="button" class="btn-danger-warm">
          {{ confirmText || '确认' }}
        </button>
      </div>
    </div>
  </div>
</template>
