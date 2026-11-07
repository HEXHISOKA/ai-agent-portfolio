// 仅保存当前标签页的对话；账号隔离，退出登录清除，不保存令牌或请求对象。
const PREFIX = 'ecom-chat:v1:'
// 最多保留最近20轮；按JS字符串的UTF-16长度估算占用，超过约2MB先淘汰旧轮次。
const MAX_ROUNDS = 20
const MAX_BYTES = 2_000_000
// 每次保存刷新有效期；读取时丢弃超过24小时未更新的快照。
const TTL = 24 * 60 * 60 * 1000
const keyFor = owner => Number.isSafeInteger(owner) && owner > 0 ? PREFIX + owner : null

export function clearChatCaches(storage) {
  try {
    storage ??= globalThis.sessionStorage
    for (let i = storage.length - 1; i >= 0; i--) {
      const key = storage.key(i)
      if (key?.startsWith(PREFIX)) storage.removeItem(key)
    }
  } catch { /* 浏览器禁止存储时仍允许正常登录和对话。 */ }
}

function validRound(r) {
  // 校验模板必需的字段结构，丢弃无法安全显示的缓存轮次。
  return r && Number.isSafeInteger(r.id) && r.id > 0 &&
    typeof r.question === 'string' && typeof r.answer === 'string' &&
    Array.isArray(r.steps) && r.steps.every(s => s && typeof s.text === 'string') &&
    (!r.plan || Array.isArray(r.plan.pending_actions)) &&
    (!r.result || (Array.isArray(r.result.rows) && Array.isArray(r.result.columns)))
}

export function readChatCache(owner, storage, now = Date.now()) {
  const empty = { messages: [], draft: '' }, key = keyFor(owner)
  if (!key) return empty
  try {
    // 读取sessionStorage属性本身也可能被浏览器拒绝，因此放在异常保护内。
    storage ??= globalThis.sessionStorage
    const raw = storage.getItem(key)
    if (!raw) return empty
    if (raw.length * 2 > MAX_BYTES) throw new Error('cache too large')
    const data = JSON.parse(raw)
    if (data.version !== 1 || data.owner !== owner || !Number.isFinite(data.expiresAt) || data.expiresAt <= now ||
        !Array.isArray(data.messages) || typeof data.draft !== 'string') throw new Error('invalid cache')
    const messages = data.messages.slice(-MAX_ROUNDS).filter(validRound).map(r => ({
      ...r,
      // 刷新或离页已经断开旧流；不把缓存中的加载态当成仍有请求在执行。
      stopped: Boolean(r.stopped || !r.done), completed: Boolean(r.done && r.completed && !r.stopped),
      done: true, confirming: false, feedbackSaving: false, feedbackTarget: null,
      restoring: Number.isSafeInteger(r.runId) && r.runId > 0,
      elapsed: Number.isFinite(r.elapsed) && r.elapsed >= 0 ? r.elapsed : 0,
      feedbackError: '', confirmation: null, cacheHadConfirmation: Boolean(r.confirmation || r.cacheHadConfirmation),
      followProcess: true,
      requestHistory: Array.isArray(r.requestHistory) ? r.requestHistory.filter(t =>
        t && ['user', 'assistant'].includes(t.role) && typeof t.content === 'string').slice(-6) : []
    }))
    return { messages, draft: data.draft.slice(0, 1000) }
  } catch {
    try { storage.removeItem(key) } catch { /* 存储被禁用时降级为当前页面内存。 */ }
    return empty
  }
}

export function writeChatCache(owner, messages, draft, storage, now = Date.now()) {
  const key = keyFor(owner)
  if (!key) return false
  try {
    storage ??= globalThis.sessionStorage
    if (!messages.length && !draft) { storage.removeItem(key); return true }
    const rounds = messages.slice(-MAX_ROUNDS)
    let raw
    // 大表格优先淘汰旧轮次；不截断当前回答或伪造完整结果。
    do {
      raw = JSON.stringify({ version: 1, owner, expiresAt: now + TTL, messages: rounds, draft: draft.slice(0, 1000) })
      if (raw.length * 2 <= MAX_BYTES) { storage.setItem(key, raw); return true }
      rounds.shift()
    } while (rounds.length)
    storage.removeItem(key)
    return false
  } catch { return false }
}
