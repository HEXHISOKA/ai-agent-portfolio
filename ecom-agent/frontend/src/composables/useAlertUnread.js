import { readonly, ref } from 'vue'
import request from '@/utils/request'
import { getToken } from '@/utils/auth'

// 三个入口共享本人全部未读数，不受列表页码、搜索词或概览响应影响。
const unread = ref(null), loading = ref(false), error = ref('')
let owner = null, sequence = 0, pending = null
function reset() {
  sequence++
  owner = getToken()
  pending = null
  unread.value = null
  error.value = ''
  loading.value = false
}
async function refresh(force = false) {
  if (owner !== getToken()) reset()
  if (!owner) return
  if (pending && !force) return pending
  const token = owner, id = ++sequence
  loading.value = true
  error.value = ''
  const current = () => id === sequence && token === getToken()
  // 已读操作强制开启新一轮统计，之前尚未返回的结果只能被丢弃。
  pending = (async () => {
    try {
      const res = await request.get('/api/ecommerce/alerts/summary')
      if (res.code !== 200 || !Number.isInteger(res.data?.unread) || res.data.unread < 0) {
        throw new Error(res.message || '未读统计读取失败')
      }
      if (current()) unread.value = res.data.unread
    } catch (err) {
      // 失败保留已知值；尚未取得统计时维持未知，不能显示成零。
      if (current()) error.value = err.message || '未读统计读取失败'
    } finally {
      if (current()) { loading.value = false; pending = null }
    }
  })()
  return pending
}
export function useAlertUnread() {
  return { unread: readonly(unread), loading: readonly(loading), error: readonly(error), refresh, reset }
}
