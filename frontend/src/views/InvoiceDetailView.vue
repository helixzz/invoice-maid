<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
const invoiceId = route.params.id
</script>

<template>
  <div class="min-h-screen bg-gray-100">
    <main>
      <div class="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <div class="px-4 py-6 sm:px-0">
          <div class="bg-white shadow overflow-hidden sm:rounded-lg">
            <div class="px-4 py-5 sm:px-6">
              <h3 class="text-lg leading-6 font-medium text-gray-900">
                Invoice Detail
              </h3>
              <p class="mt-1 max-w-2xl text-sm text-gray-500">
                ID: {{ invoiceId }}
              </p>
            </div>
            <div class="border-t border-gray-200">
              <dl>
                <div class="bg-gray-50 px-4 py-5 sm:grid sm:grid-cols-3 sm:gap-4 sm:px-6">
                  <dt class="text-sm font-medium text-gray-500">Invoice Information</dt>
                  <dd class="mt-1 text-sm text-gray-900 sm:mt-0 sm:col-span-2">
                    Details placeholder
                  </dd>
                </div>
              </dl>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
