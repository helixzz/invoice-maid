<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '@/api/client'
import type { InvoiceProfile, InvoiceProfileCreate } from '@/types'

const emit = defineEmits<{
  (e: 'toast', message: string, type: 'success' | 'error' | 'info'): void
}>()

const profiles = ref<InvoiceProfile[]>([])
const loading = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const form = ref<InvoiceProfileCreate>({
  name: '',
  title: '',
  tax_id: '',
  invoice_type_pref: '数电普票',
  email: '',
  phone: '',
  address: '',
  bank_name: '',
  bank_account: '',
  is_default: false,
})

const resetForm = () => {
  editingId.value = null
  form.value = {
    name: '',
    title: '',
    tax_id: '',
    invoice_type_pref: '数电普票',
    email: '',
    phone: '',
    address: '',
    bank_name: '',
    bank_account: '',
    is_default: false,
  }
}

const fetchProfiles = async () => {
  loading.value = true
  try {
    profiles.value = await api.getProfiles()
  } catch {
    emit('toast', '加载发票抬头失败', 'error')
  } finally {
    loading.value = false
  }
}

const editProfile = (p: InvoiceProfile) => {
  editingId.value = p.id
  form.value = {
    name: p.name,
    title: p.title,
    tax_id: p.tax_id,
    invoice_type_pref: p.invoice_type_pref,
    email: p.email,
    phone: p.phone,
    address: p.address,
    bank_name: p.bank_name,
    bank_account: p.bank_account,
    is_default: p.is_default,
  }
}

const saveProfile = async () => {
  if (!form.value.name.trim() || !form.value.title.trim()) return
  saving.value = true
  try {
    if (editingId.value !== null) {
      await api.updateProfile(editingId.value, form.value)
      emit('toast', '发票抬头已更新', 'success')
    } else {
      await api.createProfile(form.value)
      emit('toast', '发票抬头已添加', 'success')
    }
    resetForm()
    await fetchProfiles()
  } catch {
    emit('toast', '保存发票抬头失败', 'error')
  } finally {
    saving.value = false
  }
}

const removeProfile = async (id: number) => {
  try {
    await api.deleteProfile(id)
    emit('toast', '发票抬头已删除', 'success')
    await fetchProfiles()
  } catch {
    emit('toast', '删除发票抬头失败', 'error')
  }
}

onMounted(fetchProfiles)
</script>

<template>
  <div class="space-y-6">
    <!-- Saved Profiles Card -->
    <div class="ledger-card overflow-hidden">
      <div class="px-6 py-5 border-b border-cream-400 bg-cream-300/35">
        <h3 class="font-display text-lg font-bold text-ink-900">发票抬头</h3>
        <p class="mt-1 text-sm text-ink-500">可预存多个公司或个人抬头，拍码开票时将自动填入。收票邮箱建议填正在监控的邮箱以便自动归档。</p>
      </div>

      <div v-if="loading" class="p-6 text-sm text-ink-500">正在加载抬头列表…</div>
      <div v-else-if="profiles.length === 0" class="p-8 text-center text-sm text-ink-400">
        还没有预存抬头，请在下方添加第一个常用开票抬头。
      </div>
      <ul v-else class="divide-y divide-cream-400">
        <li
          v-for="p in profiles"
          :key="p.id"
          class="p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 hover:bg-cream-200/50 transition-colors"
        >
          <div class="min-w-0 space-y-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-semibold text-ink-900">{{ p.name }}</span>
              <span
                v-if="p.is_default"
                class="stamp-badge bg-[#FDF5E6] text-[#8B5E22] border-[#EEDAB6]"
              >
                ★ 默认抬头
              </span>
              <span class="stamp-badge bg-mint-50 text-mint-800 border-mint-200">
                {{ p.invoice_type_pref }}
              </span>
            </div>
            <p class="text-sm text-ink-700">
              <span>{{ p.title }}</span>
              <span v-if="p.tax_id" class="num-mono text-xs text-ink-500 ml-2 px-2 py-0.5 rounded bg-cream-200 border border-cream-400">
                {{ p.tax_id }}
              </span>
            </p>
            <p v-if="p.email || p.phone" class="text-xs text-ink-500 flex flex-wrap gap-x-4 gap-y-0.5">
              <span v-if="p.email">收票邮箱：{{ p.email }}</span>
              <span v-if="p.phone">手机：<span class="num-mono">{{ p.phone }}</span></span>
            </p>
          </div>

          <div class="flex items-center gap-2 flex-shrink-0">
            <button type="button" @click="editProfile(p)" class="btn-cream py-1.5 px-3 text-xs">编辑</button>
            <button
              type="button"
              @click="removeProfile(p.id)"
              class="inline-flex items-center justify-center px-3 py-1.5 rounded-xl text-xs font-medium text-cinnabar-600 bg-cinnabar-50/70 border border-cinnabar-200 hover:bg-cinnabar-50 transition-colors"
            >
              删除
            </button>
          </div>
        </li>
      </ul>
    </div>

    <!-- Add / Edit Profile Form -->
    <div class="ledger-card p-6">
      <div class="flex items-center justify-between mb-4">
        <h4 class="font-display text-base font-bold text-ink-900">
          {{ editingId ? '编辑发票抬头' : '新增发票抬头' }}
        </h4>
        <span v-if="editingId" class="text-xs text-mint-700 bg-mint-50 border border-mint-200 px-2.5 py-0.5 rounded-full">
          正在编辑抬头 #{{ editingId }}
        </span>
      </div>

      <form @submit.prevent="saveProfile" class="space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">简称 *</label>
            <input v-model="form.name" required type="text" placeholder="例如：上海公司专票 / 个人" class="input-warm" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">发票类型偏好</label>
            <select v-model="form.invoice_type_pref" class="input-warm">
              <option value="数电普票">数电普票</option>
              <option value="数电专票">数电专票</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">购方名称（抬头全称）*</label>
            <input v-model="form.title" required type="text" placeholder="XX科技有限公司" class="input-warm" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">纳税人识别号（税号）</label>
            <input v-model="form.tax_id" type="text" placeholder="91310000XXXXXXXXXX（个人可留空）" class="input-warm num-mono" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">收票邮箱</label>
            <input v-model="form.email" type="email" placeholder="建议填正在监控的收票邮箱" class="input-warm" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">手机号</label>
            <input v-model="form.phone" type="text" class="input-warm num-mono" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">注册地址与电话（专票可选）</label>
            <input v-model="form.address" type="text" class="input-warm" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">开户行名称（专票可选）</label>
            <input v-model="form.bank_name" type="text" class="input-warm" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-ink-700 mb-1.5">银行账号（专票可选）</label>
            <input v-model="form.bank_account" type="text" class="input-warm num-mono" />
          </div>
          <div class="flex items-center pt-5">
            <label class="inline-flex items-center gap-2.5 text-sm text-ink-700 cursor-pointer">
              <input type="checkbox" v-model="form.is_default" class="h-4 w-4 rounded border-cream-500 accent-mint-600" />
              <span>设为默认抬头</span>
            </label>
          </div>
        </div>

        <div class="flex justify-end gap-2.5 pt-2">
          <button v-if="editingId" type="button" @click="resetForm" class="btn-cream">取消编辑</button>
          <button type="submit" :disabled="saving" class="btn-mint">
            {{ saving ? '保存中…' : (editingId ? '更新抬头' : '添加抬头') }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>
