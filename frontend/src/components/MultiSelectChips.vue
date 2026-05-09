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
  <div class="flex flex-wrap items-center gap-2" role="group" :aria-label="label">
    <span class="text-xs font-medium uppercase tracking-wider text-slate-500 mr-1">
      {{ label }}
    </span>
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      :aria-pressed="selected.has(option.value)"
      :data-chip-value="option.value"
      :class="[
        'px-3 py-1 text-xs font-medium rounded-full border transition-colors focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-blue-500',
        selected.has(option.value)
          ? 'bg-blue-600 text-white border-blue-600 hover:bg-blue-700'
          : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
      ]"
      @click="toggle(option.value)"
      @keydown="onKeydown($event, option.value)"
    >
      {{ option.label }}
    </button>
    <button
      v-if="modelValue.length > 0"
      type="button"
      class="text-xs text-slate-500 hover:text-slate-700 underline underline-offset-2"
      @click="clearAll"
    >
      Clear
    </button>
  </div>
</template>
