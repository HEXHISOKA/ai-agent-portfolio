import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readChatCache, writeChatCache, clearChatCaches } from '../src/utils/chat-cache.js'
const store = () => { const m = new Map(); return { get length() { return m.size }, key: i => [...m.keys()][i], getItem: k => m.get(k) ?? null, setItem: (k, v) => m.set(k, v), removeItem: k => m.delete(k) } }
const round = (id = 1) => ({ id, question: '价格', answer: '39.90', steps: [], done: true, completed: true, runId: id, requestHistory: [], plan: null, result: { rows: [], columns: [] } })
test('draft and full answer survive; accounts never share their cache', () => {
  const s = store(); assert(writeChatCache(1, [round()], '待发送', s, 0)); assert.equal(readChatCache(1, s, 1).draft, '待发送'); assert.equal(readChatCache(1, s, 1).messages[0].answer, '39.90'); assert.deepEqual(readChatCache(2, s, 1).messages, [])
})
test('pending action is hidden until server verification; busy flags reset', () => {
  const s = store(); writeChatCache(1, [{ ...round(), confirmation: { thread_id: 't' }, confirming: true, feedbackSaving: true }], '', s, 0)
  const r = readChatCache(1, s, 1).messages[0]; assert.equal(r.confirmation, null); assert(r.cacheHadConfirmation); assert(r.restoring); assert.equal(r.confirming, false); assert.equal(r.feedbackSaving, false)
})
test('unfinished stream restores as stopped and cannot enter business history', () => {
  const s = store(); writeChatCache(1, [{ ...round(), done: false, completed: false }], '', s, 0); const r = readChatCache(1, s, 1).messages[0]; assert(r.done && r.stopped && !r.completed)
})
test('corruption, wrong version, wrong owner, expiry and malformed plan are handled', () => {
  for (const raw of ['{', JSON.stringify({ version: 2 }), JSON.stringify({ version: 1, owner: 2 }), JSON.stringify({ version: 1, owner: 1, expiresAt: 0, messages: [], draft: '' })]) {
    const s = store(); s.setItem('ecom-chat:v1:1', raw); assert.deepEqual(readChatCache(1, s, 1).messages, []); assert.equal(s.length, 0)
  }
  const s = store(); writeChatCache(1, [{ ...round(), plan: {} }], '', s, 0); assert.deepEqual(readChatCache(1, s, 1).messages, [])
})
test('cache is bounded and keeps latest rounds', () => {
  const s = store(); writeChatCache(1, Array.from({ length: 25 }, (_, i) => round(i + 1)), '', s, 0); const rows = readChatCache(1, s, 1).messages; assert.equal(rows.length, 20); assert.equal(rows[0].id, 6)
  assert.equal(writeChatCache(1, [{ ...round(), answer: 'x'.repeat(1_100_000) }], '', s, 0), false); assert.equal(s.length, 0)
})
test('new conversation removes snapshot and logout preserves unrelated storage', () => {
  const s = store(); writeChatCache(1, [round()], '', s, 0); writeChatCache(1, [], '', s, 0); assert.equal(s.length, 0); s.setItem('other', 'keep'); writeChatCache(1, [round()], '', s, 0); clearChatCaches(s); assert.equal(s.length, 1); assert.equal(s.getItem('other'), 'keep')
})
test('quota errors and unavailable storage cannot break chat', () => {
  const denied = { setItem() { throw Error('quota') }, getItem() { throw Error('denied') }, removeItem() {} }; assert.equal(writeChatCache(1, [round()], '', denied), false); assert.deepEqual(readChatCache(1, denied).messages, [])
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, get() { throw Error('denied') } }); assert.doesNotThrow(() => clearChatCaches()); assert.deepEqual(readChatCache(1).messages, []); assert.equal(writeChatCache(1, [round()], ''), false); delete globalThis.sessionStorage
})
