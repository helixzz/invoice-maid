<script setup lang="ts">
import { computed } from 'vue'
import { CATEGORY_COLORS, CATEGORY_LABELS } from '@/types'
import type { InvoiceCategory } from '@/types'

const props = defineProps<{
  category: InvoiceCategory
}>()

const colorClass = computed(() => CATEGORY_COLORS[props.category] ?? CATEGORY_COLORS.other)
const label = computed(() => CATEGORY_LABELS[props.category] ?? CATEGORY_LABELS.other)
</script>

<template>
  <span
    class="px-2.5 py-0.5 inline-flex items-center gap-1.5 text-xs leading-5 font-medium rounded-full border"
    :class="colorClass"
    :data-category="category"
  >
    <span class="w-1.5 h-1.5 rounded-full bg-current opacity-70"></span>
    {{ label }}
  </span>
</template>
