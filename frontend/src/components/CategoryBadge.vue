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
    class="px-2.5 py-0.5 inline-flex text-xs leading-5 font-semibold rounded-full border"
    :class="colorClass"
    :data-category="category"
  >
    {{ label }}
  </span>
</template>
