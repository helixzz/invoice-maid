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
      emit('toast', '抬头已更新', 'success')
    } else {
      await api.createProfile(form.value)
      emit('toast', '抬头已添加', 'success')
    }
    resetForm()
    await fetchProfiles()
  } catch {
    emit('toast', '保存抬头失败', 'error')
  } finally {
    saving.value = false
  }
}

const removeProfile = async (id: number) => {
  try {
    await api.deleteProfile(id)
    emit('toast', '抬头已删除', 'success')
    await fetchProfiles()
  } catch {
    emit('toast', '删除抬头失败', 'error')
  }
}

onMounted(fetchProfiles)
</script>

<template>
  <div class="space-y-6">
    <div class="bg-white shadow-sm rounded-xl border border-slate-200 overflow-hidden">
      <div class="p-6 border-b border-slate-200">
        <h3 class="text-lg font-medium text-slate-900">预存发票抬头（扫码自动开票用）</h3>
        <p class="mt-1 text-sm text-slate-500">可预存多个公司或个人抬头。收票邮箱建议填写 Invoice Maid 正在监控的邮箱，开出后将自动闭环归档。</p>
      </div>

      <div v-if="loading" class="p-6 text-sm text-slate-500">加载中...</div>
      <div v-else-if="profiles.length === 0" class="p-6 text-sm text-slate-500">暂无预存抬头，请在下方添加第一个抬头。</div>
      <ul v-else class="divide-y divide-slate-200">
        <li v-for="p in profiles" :key="p.id" class="p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <div class="flex items-center gap-2">
              <span class="font-semibold text-slate-900">{{ p.name }}</span>
              <span v-if="p.is_default" class="px-2 py-0.5 text-xs rounded-full bg-blue-100 text-blue-800 font-medium">默认</span>
              <span class="px-2 py-0.5 text-xs rounded-full bg-slate-100 text-slate-700">{{ p.invoice_type_pref }}</span>
            </div>
            <p class="text-sm text-slate-700 mt-1">{{ p.title }} <span v-if="p.tax_id" class="text-slate-500 font-mono text-xs ml-2">{{ p.tax_id }}</span></p>
            <p class="text-xs text-slate-500 mt-0.5">
              <span v-if="p.email">邮箱：{{ p.email }}</span>
              <span v-if="p.phone" class="ml-3">手机：{{ p.phone }}</span>
            </p>
          </div>
          <div class="flex items-center gap-2">
            <button @click="editProfile(p)" class="px-3 py-1.5 text-xs border border-slate-300 rounded-md hover:bg-slate-50">编辑</button>
            <button @click="removeProfile(p.id)" class="px-3 py-1.5 text-xs border border-rose-200 text-rose-600 rounded-md hover:bg-rose-50">删除</button>
          </div>
        </li>
      </ul>
    </div>

    <div class="bg-white shadow-sm rounded-xl border border-slate-200 p-6">
      <h4 class="text-base font-medium text-slate-900 mb-4">{{ editingId ? '编辑发票抬头' : '新增发票抬头' }}</h4>
      <form @submit.prevent="saveProfile" class="space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">简称 *</label>
            <input v-model="form.name" required type="text" placeholder="例如：上海公司专票 / 个人" class="w-full border border-slate-300 rounded-md py-2 px-3 text-sm" />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">发票类型偏好</label>
            <select v-model="form.invoice_type_pref" class="w-full border border-slate-300 rounded-md py-2 px-3 text-sm bg-white">
              <option value="数电普票">数电普票</option>
              <option value="数电专票">数电专票</option>
            </select>
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">购方名称（抬头全称）*</label>
            <input v-model="form.title" required type="text" placeholder="XX科技有限公司" class="w-full border border-slate-300 rounded-md py-2 px-3 text-sm" />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">纳税人识别号（税号）</label>
            <input v-model="form.tax_id" type="text" placeholder="91310000XXXXXXXXXX（个人可留空）" class="w-full border border-slate-300 rounded-md py-2 px-3 text-sm font-mono" />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">收票邮箱</label>
            <input v-model="form.email" type="email" placeholder="填 Invoice Maid 监控邮箱" class="w-full border border-slate-300 rounded-md py-2 px-3 text-sm" />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">手机号</label>
            <input v-model="form.phone" type="text" class="w-full border border-slate-300 rounded-md py-2 px-3 text-sm" />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">注册地址与电话（专票可选）</label>
            <input v-model="form.address" type="text" class="w-full border border-slate-300 rounded-md py-2 px-3 text-sm" />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">开户行名称（专票可选）</label>
            <input v-model="form.bank_name" type="text" class="w-full border border-slate-300 rounded-md py-2 px-3 text-sm" />
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">银行账号（专票可选）</label>
            <input v-model="form.bank_account" type="text" class="w-full border border-slate-300 rounded-md py-2 px-3 text-sm font-mono" />
          </div>
          <div class="flex items-center pt-5">
            <label class="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" v-model="form.is_default" class="rounded border-slate-300 text-blue-600" />
              <span>设为默认抬头</span>
            </label>
          </div>
        </div>
        <div class="flex justify-end gap-2 pt-2">
          <button v-if="editingId" type="button" @click="resetForm" class="px-4 py-2 border border-slate-300 rounded-lg text-sm">取消编辑</button>
          <button type="submit" :disabled="saving" class="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
            {{ saving ? '保存中...' : (editingId ? '更新抬头' : '添加抬头') }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>
