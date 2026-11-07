import request from '@/utils/request'

/** 恢复页面时以本人运行记录核对反馈和当前可操作的计划。 */
export async function getRun(runId) {
  const res = await request.get(`/api/observability/runs/${runId}`)
  if (res.code !== 200) throw new Error(res.message || '运行状态读取失败')
  return res.data
}

/** 只提交本轮评分，不传用户编号；不覆盖历史页已有的反馈备注。 */
export async function submitFeedback(runId, rating) {
  const res = await request.post(`/api/observability/runs/${runId}/feedback`, { rating })
  if (res.code !== 200) throw new Error(res.message || '反馈保存失败，请重试')
  if (res.data?.run_id !== runId || res.data?.rating !== rating) {
    throw new Error('反馈返回结果不匹配，请重试')
  }
  return res.data
}
