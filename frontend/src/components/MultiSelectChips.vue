<script setup lang="ts">
import { computed } from 'vue'

interface ChipOption {
  value: string
  label: string
}

const props = defineProps<{
  modelValue: string[]
  options: ChipOption[]
  label: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string[]]
}>()

const selected = computed(() => new Set(props.modelValue))

function toggle(value: string) {
  const next = new Set(props.modelValue)
  if (next.has(value)) {
    next.delete(value)
  } else {
    next.add(value)
  }
  emit('update:modelValue', [...next])
}

function onKeydown(event: KeyboardEvent, value: string) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    toggle(value)
  }
}

function clearAll() {
  if (props.modelValue.length > 0) {
    emit('update:modelValue', [])
  }
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-1.5" role="group" :aria-label="label">
    <span class="text-xs font-medium text-ink-400 mr-1">
      {{ label }}
    </span>
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      :aria-pressed="selected.has(option.value)"
      :data-chip-value="option.value"
      :class="[
        'px-3 py-1 text-xs font-medium rounded-full border transition-colors focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-mint-400',
        selected.has(option.value)
          ? 'bg-mint-600 text-white border-mint-600 hover:bg-mint-700'
          : 'bg-cream-50 text-ink-700 border-cream-500 hover:bg-cream-300/60'
      ]"
      @click="toggle(option.value)"
      @keydown="onKeydown($event, option.value)"
    >
      {{ option.label }}
    </button>
    <button
      v-if="modelValue.length > 0"
      type="button"
      class="text-xs text-ink-500 hover:text-ink-900 underline underline-offset-2 ml-1"
      @click="clearAll"
    >
      清除
    </button>
  </div>
</template>
