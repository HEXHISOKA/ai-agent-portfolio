import router from '@/router'
import { ElMessage } from 'element-plus'
import { getToken, logout } from '@/utils/auth'

/**
 * 解析一段 SSE 文本，返回 { event, data }
 * 事件之间以空行分隔；单行格式为 `event: 名称` 与 `data: JSON`
 */
function parseBlock(block) {
  let event = ''
  const dataLines = []

  for (const raw of block.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim())
    }
  }

  if (!event) return null

  const raw = dataLines.join('\n')
  if (!raw) return { event, data: {} }

  try {
    return { event, data: JSON.parse(raw) }
  } catch {
    // JSON 解析失败就丢掉这一段，不能让整条流断掉
    return null
  }
}

/**
 * 以 SSE 的方式提问，逐条事件回调
 *
 * 不用 axios：axios 的响应拦截器会一次性读完整 body，拿不到 ReadableStream，
 * 所以这里走原生 fetch，并自行处理 401 与错误提示。
 */
export function chatStream(question, handlers = {}) {
  const controller = new AbortController()

  const run = async () => {
    let reader
    let terminal = false
    try {
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${getToken()}`
        },
        body: JSON.stringify({ question, history: handlers.history || [] }),
        signal: controller.signal
      })

      // fetch 不经过 axios 拦截器，登录失效要在这里自己处理
      if (res.status === 401) {
        ElMessage.error('登录状态已失效，请重新登录')
        logout()
        router.push('/login')
        return
      }
      if (!res.ok) {
        // HTTP失败也通知所属轮次，使页面保留问题并提供手动重试入口。
        handlers.onError?.(`请求失败（HTTP ${res.status}）`)
        return
      }

      reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      const dispatch = (block) => {
        const parsed = parseBlock(block)
        if (!parsed) return
        const { event, data } = parsed
        // status 额外带上完整 payload，页面可选用 role / node 等字段
        if (event === 'status') handlers.onStatus?.(data.message ?? '', data)
        else if (event === 'token') handlers.onToken?.(data.content ?? '')
        else if (event === 'done') {
          terminal = true
          handlers.onDone?.(data)
        } else if (event === 'error') {
          terminal = true
          handlers.onError?.(data.message || '对话处理失败', data)
        }
      }

      while (!terminal) {
        const { done, value } = await reader.read()
        if (done) break

        // 按块解码，避免多字节字符被截断；统一换行符方便按空行切分
        // 先累积再处理换行，兼容 CRLF 被网络分块拆开的情况。
        buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n')

        let idx
        // 网络分块与事件边界不对齐，一个 chunk 里可能有 0 条或多条事件
        while (!terminal && (idx = buffer.indexOf('\n\n')) !== -1) {
          dispatch(buffer.slice(0, idx))
          buffer = buffer.slice(idx + 2)
        }
      }

      // 收尾：解码器里可能还压着半个字符，末尾也可能没有空行
      buffer += decoder.decode()
      if (!terminal && buffer.trim()) dispatch(buffer)
      // 连接关闭不代表成功，必须收到 done 或 error 才算协议正常结束。
      if (!terminal) handlers.onError?.('对话连接提前结束，请重试')
    } catch (err) {
      if (err?.name === 'AbortError') return
      handlers.onError?.(err?.message || '网络异常，请检查后端服务')
    } finally {
      if (reader) {
        try {
          await reader.cancel()
        } catch {
          // 连接断开时仍需释放读取锁，并执行页面收尾。
        }
        reader.releaseLock()
      }
      handlers.onFinish?.()
    }
  }

  return {
    run,
    abort: () => controller.abort()
  }
}


/** 确认和取消都恢复原线程，不再发送一次分析问题。 */
export async function confirmActions(threadId, approved) {
  const res = await fetch('/api/chat/actions/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
    body: JSON.stringify({ thread_id: threadId, approved })
  })
  const payload = await res.json()
  // 业务失败沿用统一响应code，HTTP成功不能代表已确认。
  if (!res.ok || payload.code !== 200) throw new Error(payload.message || '确认失败')
  return payload.data
}
