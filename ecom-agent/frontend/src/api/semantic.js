import request from '@/utils/request'

export async function searchSemantic(query, topK) {
  const res = await request.post('/api/semantic/search', { query, top_k: topK })
  if (res.code !== 200) throw new Error(res.message || '检索失败')
  return res.data
}
