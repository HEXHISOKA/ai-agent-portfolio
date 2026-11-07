<template>
  <div class="page-container chat-page">
    <!-- 对话区 -->
    <div class="content-card chat-main">
      <div ref="scrollRef" class="chat-scroll" @scroll="handleMessagesScroll">
        <div ref="contentRef" class="chat-content">
        <div v-if="!messages.length" class="chat-hint-empty">
          <p>输入商品、订单或评价相关问题，查看数据分析结果。</p>
          <div class="chat-examples">
            <span>可以试试：</span>
            <el-button v-for="example in exampleQuestions" :key="example" round
              :disabled="sending" @click="sendExample(example)">{{ example }}</el-button>
          </div>
        </div>

        <!-- 一轮对话：我的提问 + 分析过程 + 助手回答 -->
        <div v-for="m in messages" :key="m.id" class="turn">
          <div class="msg msg--user">
            <div class="msg__bubble">
              <div class="msg__label">我</div>
              <div class="msg__question">{{ m.question }}</div>
            </div>
          </div>

          <div v-if="m.steps.length" class="process">
            <button type="button" class="process__head" :aria-expanded="!m.collapsed" @click="m.collapsed = !m.collapsed">
              <span class="process__summary">
                <i class="process__dot" :class="{ 'process__dot--running': !m.done && !m.error && !m.stopped }" aria-hidden="true" />
                {{ m.stopped ? '已停止' : m.error ? '处理失败' : m.done ? '已处理' : '处理中' }} · {{ m.steps.length }} 步 ·
                {{ m.elapsed.toFixed(1) }} 秒
              </span>
              <span class="process__toggle">{{ m.collapsed ? '展开' : '收起' }}</span>
            </button>

            <div v-show="!m.collapsed" class="process__body" :ref="el => setProcessRef(m.id, el)" @scroll="handleProcessScroll(m, $event)">
              <div v-for="(step, index) in m.steps" :key="index" class="step">
                <span v-if="step.role" class="step__role" :class="step.roleClass">{{
                  step.role
                }}</span>
                <span v-if="step.node" class="step__node">{{ step.node }}</span>
                <span class="step__text">{{ step.text }}</span>
                <pre v-if="step.sql" class="step__sql"><code>{{ step.sql }}</code></pre>
              </div>
            </div>
          </div>

          <div v-if="m.answer" class="msg msg--assistant">
            <div class="msg__bubble">
              <div class="msg__label">助手</div>
              <div class="msg__content" v-html="renderMarkdown(m.answer)"></div>
              <QueryResult v-if="m.result" :result="m.result" />
              <div v-if="m.done || m.error" class="msg__foot">
                <template v-if="canFeedback(m)">
                  <el-tooltip content="点赞，反馈会记录到本次运行" placement="top">
                    <el-button text size="small" :icon="Select" aria-label="点赞" :type="m.feedback?.rating === 1 ? 'primary' : ''"
                      :aria-pressed="m.feedback?.rating === 1" :disabled="m.feedbackSaving"
                      :loading="m.feedbackSaving && m.feedbackTarget === 1" @click="handleFeedback(m, 1)" />
                  </el-tooltip>
                  <el-tooltip content="点踩，反馈可随时修改" placement="top">
                    <el-button text size="small" :icon="Close" aria-label="点踩" :type="m.feedback?.rating === -1 ? 'danger' : ''"
                      :aria-pressed="m.feedback?.rating === -1" :disabled="m.feedbackSaving"
                      :loading="m.feedbackSaving && m.feedbackTarget === -1" @click="handleFeedback(m, -1)" />
                  </el-tooltip>
                </template>
                <el-tooltip content="复制回答" placement="top">
                  <el-button text size="small" :icon="CopyDocument" aria-label="复制回答" @click="copyAnswer(m.answer)" />
                </el-tooltip>
              </div>
              <p v-if="m.feedbackError" class="answer-feedback-error" role="alert">{{ m.feedbackError }}</p>
            </div>
          </div>

          <div v-if="m.plan" class="content-card action-plan" :class="{ 'action-plan--pending': m.confirmation }">
            <h4>动作计划 · {{ m.plan.risk_level === 'high' ? '高风险' : '低风险' }}</h4>
            <p>{{ m.plan.decide_reason }}</p>
            <p v-if="!m.plan.pending_actions.length">没有待执行动作</p>
            <ul v-else>
              <li v-for="(action, index) in m.plan.pending_actions" :key="index">
                {{ action.title }} · {{ action.product_id ? `商品 ${action.product_id}` : '本次分析' }}
                <!-- 标签只转换显示文字，不修改动作参数中的预警等级。 -->
                <el-tag
                  v-if="action.level"
                  class="action-plan__level"
                  size="small"
                  effect="light"
                  :type="{ critical: 'danger', warning: 'warning' }[action.level] || 'info'"
                >
                  {{ { critical: '严重', warning: '提醒' }[action.level] || '未知等级' }}
                </el-tag>
              </li>
            </ul>
            <p>{{ m.actionMessage || '计划尚未执行' }}</p>
            <div v-if="m.confirmation" class="action-plan__buttons">
              <el-button :disabled="m.confirming" @click="handleConfirm(m, false)">取消动作</el-button>
              <el-button type="primary" :loading="m.confirming" :disabled="m.confirming" @click="handleConfirm(m, true)">确认执行</el-button>
            </div>
          </div>

          <el-alert
            v-if="m.error"
            class="turn__alert"
            type="error"
            :closable="false"
            show-icon
            :title="m.error"
          />

          <p v-if="m.stopped" class="turn__state" role="status">已停止接收本轮回答</p>
          <el-tooltip v-if="m.error && !m.answerFailedWithPlan" content="重新提问" placement="top">
            <el-button text class="turn__retry" type="primary" :icon="RefreshRight" aria-label="重新提问" :disabled="sending" @click="retryRound(m)" />
          </el-tooltip>
          <p v-if="m.id === activeId && sending && !m.answer" class="chat-typing">正在思考…</p>
        </div>
        </div>
      </div>
      <el-button v-if="!followBottom && messages.length" class="chat-latest" @click="jumpToLatest">回到最新</el-button>
    </div>

    <!-- 输入区 -->
    <div class="content-card chat-composer">
      <el-input
        v-model="question"
        type="textarea"
        :rows="3"
        resize="none"
        maxlength="1000"
        show-word-limit
        aria-label="分析问题"
        :disabled="sending"
        placeholder="输入关于商品、订单或评价的问题..."
        @keydown="handleKeydown"
      />
      <div class="chat-actions">
        <span class="chat-tip">Enter 发送 · Shift + Enter 换行</span>
        <div class="toolbar-spacer" />
        <div class="chat-buttons">
          <el-button :disabled="sending" @click="startNewConversation">新对话</el-button>
          <el-button :disabled="sending || !question.trim()" @click="clearInput">清空</el-button>
          <el-button v-if="sending" type="primary" @click="stopGeneration">停止生成</el-button>
          <el-button v-else
            type="primary"
            :disabled="sending || !question.trim()"
            @click="handleSend()"
          >
            发送
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'Chat' })
import { CopyDocument, Select, Close, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { chatStream, confirmActions } from '@/api/chat'
import QueryResult from '@/components/QueryResult.vue'
import { submitFeedback, getRun } from '@/api/observability'
import { getUserInfo, getToken } from '@/utils/auth'
import { readChatCache, writeChatCache, clearChatCaches } from '@/utils/chat-cache'
import { nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'

// 每次用累计正文重新解析，让跨 token 的加粗、列表和表格在补全后正确显示。
function renderMarkdown(text) {
  const html = marked.parse(text || '', { async: false, breaks: true })
  // 模型输出也可能含 HTML，清理脚本、事件属性和危险链接后再交给 v-html。
  return DOMPurify.sanitize(html)
}

// 过程区里的角色配色：角色名出现在消息开头时按它上色
const ROLE_RULES = [
  { name: '查询分析 Agent', roleClass: 'step__role--query' },
  { name: '报告决策 Agent', roleClass: 'step__role--report' },
  { name: '调度', roleClass: 'step__role--route' }
]

/**
 * 把一条过程消息拆成 角色 / 节点 / 描述
 * 后端给了 role、node 就直接用，否则从消息文本里认
 */
// 两种进度格式共用同一SQL字段；纯文本进度不会继承上一条SQL。
function parseStep(message, payload = {}) {
  return { ...parseStepText(message, payload), sql: typeof payload.sql === 'string' ? payload.sql : '' }
}
function parseStepText(message, payload = {}) {
  const text = String(message ?? '').trim()
  if (payload.role || payload.node) {
    const hit = ROLE_RULES.find((r) => r.name === payload.role)
    return {
      role: payload.role || '',
      roleClass: hit ? hit.roleClass : '',
      node: payload.node || '',
      text: payload.message ?? text
    }
  }

  const hit = ROLE_RULES.find((r) => text.startsWith(r.name))
  if (!hit) {
    // 有些步骤没有角色前缀，本身就是以节点名开头的
    const bare = text.match(/^([a-z][a-z0-9_]*)\s+([\s\S]+)$/i)
    if (bare) return { role: '', roleClass: '', node: bare[1], text: bare[2] }
    return { role: '', roleClass: '', node: '', text }
  }

  const rest = text.slice(hit.name.length).trim()
  // 节点名是 preparing_evidence 这种下划线写法，紧跟在角色名后面
  const matched = rest.match(/^([a-z][a-z0-9_]*)\s+([\s\S]*)$/i)
  return {
    role: hit.name,
    roleClass: hit.roleClass,
    node: matched ? matched[1] : '',
    text: matched ? matched[2] : rest
  }
}

// 一轮消息独立保存正文、计划和重试上下文，旧请求不能更新新轮次。
const cacheOwner = getUserInfo()?.id
const cacheToken = getToken()
const restored = readChatCache(cacheOwner)
const messages = ref(restored.messages)
const question = ref(restored.draft)
const sending = ref(false)
const scrollRef = ref()
const contentRef = ref()
const followBottom = ref(true)
const processRefs = new Map()
const exampleQuestions = [
  '洗衣液 2L 的价格是多少',
  '跑鞋脱胶',
  '轻云运动跑鞋的差评有点多，帮我看看并处理'
]
let seq = Math.max(0, ...messages.value.map(r => r.id))
let activeId = 0
let activeRun = null
let disposed = false
let resizeObserver = null
let cacheTimer = null
let cacheWarningShown = false
let accountReloading = false
const sameAccount = () => getUserInfo()?.id === cacheOwner && getToken() === cacheToken
function persistChat() {
  clearTimeout(cacheTimer)
  if (!sameAccount()) return
  const saved = writeChatCache(cacheOwner, messages.value, question.value)
  if (!saved && !cacheWarningShown) {
    cacheWarningShown = true
    ElMessage.warning('对话缓存未能保存，离开或刷新后可能无法恢复')
  }
}
// 合并逐token的写入，离页和刷新前再同步保存最后一份状态。
watch([messages, question], () => {
  if (disposed) return
  clearTimeout(cacheTimer)
  cacheTimer = setTimeout(persistChat, 300)
}, { deep: true })
async function reconcileRestoredRounds() {
  for (const round of [...messages.value]) {
    if (!validRunId(round.runId)) continue
    try {
      const data = await getRun(round.runId)
      if (disposed || !sameAccount() || !messages.value.includes(round)) return
      round.feedback = data.feedback || null
      // 缓存不能恢复执行权限；只展示后端核对归属和检查点后返回的入口。
      round.confirmation = data.pending_confirmation || null
      if (round.cacheHadConfirmation && !round.confirmation) {
        round.actionMessage = data.status === 'cancelled' ? '原计划已取消' :
          data.status === 'success' ? '原计划已处理' : '原计划检查点已不可用，请在运行历史核对'
      }
    } catch {
      if (disposed || !sameAccount() || !messages.value.includes(round)) return
      round.confirmation = null
      if (round.cacheHadConfirmation) round.actionMessage = '暂时无法核对原计划，请在运行历史查看'
    }
    delete round.cacheHadConfirmation
    round.restoring = false
  }
}
function handleAccountStorage(event) {
  if (accountReloading) return
  if (['token', 'userInfo', null].includes(event.key) && !sameAccount()) {
    accountReloading = true
    // 另一标签页切换身份时立即清空旧账号画面，并停止旧请求。
    stopGeneration()
    clearChatCaches()
    messages.value = []
    question.value = ''
    window.location.reload()
  }
}

const nearBottom = el => el.scrollHeight - el.clientHeight - el.scrollTop <= 60
function handleMessagesScroll() {
  if (scrollRef.value) followBottom.value = nearBottom(scrollRef.value)
}
function setProcessRef(id, el) {
  if (el) processRefs.set(id, el)
  else processRefs.delete(id)
}
function handleProcessScroll(round, event) {
  round.followProcess = nearBottom(event.currentTarget)
}
function scrollProcess(round, force = false) {
  nextTick(() => {
    if (disposed) return
    const el = processRefs.get(round.id)
    if (el && (force || round.followProcess)) el.scrollTop = el.scrollHeight
  })
}
// 用户向上阅读后不抢滚动位置；返回底部或点击“回到最新”后恢复跟随。
function scrollToBottom(force = false) {
  nextTick(() => {
    if (disposed) return
    const el = scrollRef.value
    if (el && (force || followBottom.value)) {
      el.scrollTop = el.scrollHeight
      followBottom.value = true
    }
  })
}
function jumpToLatest() {
  for (const round of messages.value) { round.followProcess = true; scrollProcess(round, true) }
  followBottom.value = true
  scrollToBottom(true)
}
const startNewConversation = () => {
  if (sending.value) return
  messages.value = []
  question.value = ''
  processRefs.clear()
  followBottom.value = true
}
const clearInput = () => { question.value = '' }
function buildHistory(endIndex) {
  return messages.value.slice(0, endIndex)
    // 失败、停止和未完成的部分回答不进入下一轮上下文。
    .filter(round => round.completed && !round.error && !round.stopped)
    .flatMap(round => [
      { role: 'user', content: round.question || '' },
      { role: 'assistant', content: round.answer || '' }
    ])
    .filter(turn => turn.content).slice(-6)
    .map(turn => ({ role: turn.role, content: turn.content.slice(0, 500) }))
}
function isCurrent(run) { return !disposed && activeRun === run }
function finishRun(run) {
  // 停止后可立即再提问；旧请求的finally不能关闭新请求的加载态或计时。
  if (!isCurrent(run)) return
  run.round.done = true
  run.round.elapsed = (Date.now() - run.startedAt) / 1000
  clearInterval(run.timer)
  activeRun = null
  sending.value = false
  scrollToBottom()
}
function stopGeneration() {
  const run = activeRun
  if (!run) return
  run.round.stopped = true
  run.round.completed = false
  finishRun(run)
  // 仅终止接收流，不调用动作确认/取消接口，也不承诺撤销服务端已执行的操作。
  run.request.abort()
}
function startQuestion(value, history, clearDraft = true) {
  if (sending.value || disposed) return
  value = value.trim()
  if (!value) { ElMessage.warning('请先输入问题'); return }
  const round = reactive({
    id: ++seq, question: value, steps: [], answer: '',
    requestHistory: history.map(turn => ({ ...turn })),
    confirmation: null, confirming: false, actionMessage: '', plan: null,
    result: null, error: '', done: false, completed: false, stopped: false,
    answerFailedWithPlan: false,
    runId: null, feedback: null, feedbackSaving: false, feedbackTarget: null, feedbackError: '',
    elapsed: 0, collapsed: false, followProcess: true
  })
  messages.value.push(round)
  if (clearDraft) question.value = ''
  activeId = round.id
  const run = { round, startedAt: Date.now(), timer: null, request: null }
  activeRun = run
  sending.value = true
  followBottom.value = true
  scrollToBottom(true)
  run.timer = setInterval(() => {
    if (isCurrent(run)) round.elapsed = (Date.now() - run.startedAt) / 1000
  }, 100)
  run.request = chatStream(value, {
    history: round.requestHistory,
    onStatus: (message, payload) => {
      if (!isCurrent(run)) return
      round.steps.push(parseStep(message, payload))
      scrollProcess(round)
      scrollToBottom()
    },
    onToken: content => {
      if (!isCurrent(run)) return
      round.answer += content
      scrollToBottom()
    },
    onError: (message, payload) => {
      if (!isCurrent(run)) return
      round.error = message || '分析失败，请稍后重试'
      // 出错后已有部分回答的运行也可能可评价，编号仍只取本轮终止事件。
      round.runId = validRunId(payload?.run_id)
      // 恢复原运行的待确认计划，不把回答失败变成重新分析；保留已收到的部分回答。
      if (round.runId && payload?.confirmation && payload.confirmation.thread_id === payload.thread_id) {
        round.result = payload.result || null
        round.plan = payload.plan || null
        round.confirmation = payload.confirmation
        round.actionMessage = payload.action_message || '原计划等待确认，尚未执行'
        round.answerFailedWithPlan = true
      }
    },
    onDone: payload => {
      if (!isCurrent(run)) return
      // 每个回答只关联自己的运行编号，不使用“最近一次运行”或其他轮次的编号。
      round.runId = validRunId(payload?.run_id)
      round.result = payload?.result || null
      round.plan = payload?.plan || null
      round.confirmation = payload?.confirmation || null
      round.actionMessage = payload?.action_message || ''
      // 入口引导不是业务结果，不能把引导文字当成后续商品指代的上下文。
      round.completed = !payload?.query_failed && !payload?.handled_at_entry
      if (payload?.query_failed) round.error = '数据库查询失败，请稍后重试'
      round.done = true
      scrollToBottom()
    },
    onFinish: () => finishRun(run)
  })
  run.request.run()
}
const handleSend = () => startQuestion(question.value, buildHistory(messages.value.length))
const sendExample = value => startQuestion(value, buildHistory(messages.value.length))
function retryRound(round) {
  if (sending.value || !round.error || round.answerFailedWithPlan) return
  // 原失败轮次保留供核对；新轮次使用原问题和发送前保存的上下文，不夹入失败片段或后来对话。
  startQuestion(round.question, round.requestHistory, false)
}
onMounted(() => {
  window.addEventListener('pagehide', persistChat)
  window.addEventListener('storage', handleAccountStorage)
  reconcileRestoredRounds()
  scrollToBottom(true)
  resizeObserver = new ResizeObserver(() => scrollToBottom())
  if (contentRef.value) resizeObserver.observe(contentRef.value)
  if (scrollRef.value) resizeObserver.observe(scrollRef.value)
})

function validRunId(value) {
  return Number.isSafeInteger(value) && value > 0 ? value : null
}
function canFeedback(round) {
  return round.done && !round.stopped && !round.restoring && !!round.answer.trim() && validRunId(round.runId) !== null
}
async function handleFeedback(round, rating) {
  // 一轮内串行写入；同一评分不重复提交，不影响其他轮次或正在生成的回答。
  if (!canFeedback(round) || round.feedbackSaving || round.feedback?.rating === rating) return
  round.feedbackSaving = true
  round.feedbackTarget = rating
  round.feedbackError = ''
  try {
    const data = await submitFeedback(round.runId, rating)
    if (disposed) return
    // 只有服务器保存成功后才更新高亮；失败保留上一次已保存的评分。
    round.feedback = data
  } catch (err) {
    if (!disposed) round.feedbackError = err.message || '反馈保存失败，请重试'
  } finally {
    round.feedbackSaving = false
    round.feedbackTarget = null
  }
}

// 请求只更新所属轮次；按钮加载期间阻止同一计划重复提交。
async function handleConfirm(round, approved) {
  if (round.confirming || !round.confirmation) return
  round.confirming = true
  try {
    const result = await confirmActions(round.confirmation.thread_id, approved)
    round.actionMessage = result.status_message
    round.confirmation = null
    // 计划处理后仍保留回答失败事实，但不再提示等待确认或提供重新规划的重试入口。
    if (round.answerFailedWithPlan) round.error = `回答生成失败；${result.status_message}`
  } catch (err) {
    ElMessage.error(err.message || '确认失败')
  } finally {
    round.confirming = false
  }
}

const handleKeydown = (event) => {
  // 输入法确认候选词和 Shift + Enter 换行都不触发发送。
  if (event.isComposing || event.key !== 'Enter' || event.shiftKey) return
  event.preventDefault()
  handleSend()
}

const copyAnswer = async (text) => {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制回答')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}

onUnmounted(() => {
  const leavingRun = activeRun
  if (leavingRun) {
    leavingRun.round.stopped = true
    leavingRun.round.completed = false
    leavingRun.round.done = true
    leavingRun.round.elapsed = (Date.now() - leavingRun.startedAt) / 1000
  }
  persistChat()
  window.removeEventListener('pagehide', persistChat)
  window.removeEventListener('storage', handleAccountStorage)
  disposed = true
  const run = activeRun
  activeRun = null
  clearInterval(run?.timer)
  run?.request.abort()
  resizeObserver?.disconnect()
  processRefs.clear()
})
</script>

<style scoped>
.chat-page { display: flex; flex-direction: column; gap: var(--app-space-gap); height: calc(100dvh - 92px); min-height: 0; }
.chat-main { flex: 1; min-height: 0; min-width: 0; padding: 0; display: flex; overflow: hidden; position: relative; }
.chat-scroll { flex: 1; min-width: 0; min-height: 0; overflow-y: auto; padding: var(--app-space-card); }
.chat-content { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.chat-hint-empty { padding: var(--app-space-card) 0; text-align: center; color: var(--app-text-secondary); font-size: 13px; }
.chat-hint-empty p { margin: 0 0 12px; }
.chat-examples { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 8px; }
.chat-examples :deep(.el-button) { height: auto; min-height: 32px; white-space: normal; }
.chat-examples :deep(.el-button + .el-button) { margin-left: 0; }
.turn { display: flex; flex-direction: column; gap: 8px; flex-shrink: 0; }
.msg { min-width: 0; }
.msg--user { align-self: flex-end; max-width: 80%; }
.msg--assistant { align-self: flex-start; max-width: 85%; }
.msg__bubble { padding: 10px 12px; border: 1px solid var(--app-border); border-radius: var(--app-radius-sm); background: #f8fafc; }
.msg--user .msg__bubble { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); overflow-wrap: anywhere; }
.msg__question { white-space: pre-wrap; }
.msg__label { font-size: 12px; color: var(--app-text-secondary); margin-bottom: 4px; }
.msg__content { min-width: 0; line-height: 1.6; overflow-wrap: anywhere; color: var(--app-text); }
.msg__content :deep(p) { margin: 0 0 0.5em; }
.msg__content :deep(p:last-child) { margin-bottom: 0; }
.msg__content :deep(ul), .msg__content :deep(ol) { padding-left: 1.2em; margin: 0.4em 0; }
.msg__content :deep(ul) { list-style: disc; }
.msg__content :deep(ol) { list-style: decimal; }
.msg__content :deep(table) { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; }
.msg__content :deep(th), .msg__content :deep(td) { padding: 8px; border: 1px solid var(--app-border); }
.msg__content :deep(th) { background: #f8fafc; }
.msg__content :deep(pre) { white-space: pre; overflow-x: auto; padding: 12px; background: #f1f5f9; }
.msg__foot { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
.msg__foot :deep(.el-button) { min-width: 32px; min-height: 32px; margin-left: 0; }
.process { max-width: 85%; min-width: 0; border: 1px solid var(--app-border); border-radius: var(--app-radius-sm); background: #f8fafc; overflow: hidden; }
.process__head { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; border: 0; background: transparent; padding: 8px 12px; text-align: left; cursor: pointer; color: var(--app-text-secondary); font-size: 13px; }
.process__head:hover { background: rgba(15,23,42,.04); }
.process__head:focus-visible { outline: 2px solid var(--app-primary); outline-offset: -2px; }
.process__summary { display: inline-flex; align-items: center; gap: 8px; min-width: 0; font-variant-numeric: tabular-nums; }
.process__toggle { flex-shrink: 0; color: var(--el-color-primary); font-size: 12px; }
.process__dot { width: 6px; height: 6px; margin: 0 3px; border-radius: 50%; background: var(--app-text-secondary); flex-shrink: 0; }
.process__dot--running { width: 12px; height: 12px; margin: 0; background: transparent; border: 2px solid var(--el-color-primary-light-7); border-top-color: var(--app-primary); animation: chat-spin .8s linear infinite; }
@keyframes chat-spin { to { transform: rotate(360deg); } }
@media(prefers-reduced-motion: reduce) { .process__dot--running { animation: none; } }
.process__body { display: flex; flex-direction: column; gap: 6px; max-height: 220px; overflow-y: auto; padding: 8px 12px 10px; border-top: 1px solid var(--app-border); }
.step { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; font-size: 13px; line-height: 1.5; }
.step__role { font-weight: 600; color: var(--app-primary); }
.step__role--report { color: var(--el-color-warning); }
.step__role--route, .step__node { color: var(--app-text-secondary); }
.step__node { font-size: 12px; }
.step__text { overflow-wrap: anywhere; min-width: 0; }
/* SQL占据独立一行，长语句在过程区内换行，不挤出窄屏容器。 */
.step__sql { flex-basis: 100%; min-width: 0; max-width: 100%; margin: 2px 0 4px; padding: 6px 8px; border: 1px solid var(--app-border); border-radius: var(--app-radius-sm); font: 12px/1.6 Consolas,Menlo,monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.action-plan { width: 100%; min-width: 0; padding: var(--app-space-card); border: 1px solid var(--app-border); background: var(--app-bg); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
/* 保留逐轮计划与中文等级；待确认卡片沿用成品的黄色虚线边框。 */
.action-plan--pending { border-style: dashed; border-color: var(--el-color-warning); background: var(--el-color-warning-light-9); }
.action-plan h4 { margin: 0 0 6px; font-size: 14px; color: var(--app-text); }
.action-plan p { color: var(--app-text-secondary); margin: 0 0 6px; }
.action-plan ul { list-style: disc; margin: 0 0 10px; padding-left: 1.2em; }
.action-plan__level { margin-left: 8px; vertical-align: middle; }
.action-plan__buttons { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.action-plan__buttons :deep(.el-button + .el-button) { margin-left: 0; }
.chat-composer { flex-shrink: 0; min-width: 0; }
.chat-actions { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin-top: var(--app-space-gap); }
.chat-tip, .chat-typing, .turn__state { color: var(--app-text-secondary); font-size: 12px; }
.chat-buttons { display: flex; flex-shrink: 0; gap: 8px; }
.chat-buttons :deep(.el-button + .el-button) { margin-left: 0; }
.chat-latest { position: absolute; right: 16px; bottom: 12px; min-height: 44px; z-index: 1; box-shadow: var(--app-shadow); }
.turn__retry { align-self: flex-start; min-height: 32px; }
.turn__alert { align-self: flex-start; max-width: 85%; }
.answer-feedback-error { margin: 6px 0 0; color: var(--el-color-danger); font-size: 12px; }
@media(max-width: 640px) {
  .msg--user, .msg--assistant, .process, .turn__alert { max-width: 100%; }
  .chat-tip, .toolbar-spacer { display: none; }
  .chat-actions { display: block; }
  .chat-buttons { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); width: 100%; }
  .chat-buttons :deep(.el-button) { min-width: 0; min-height: 44px; padding: 8px 6px; }
  .msg__foot :deep(.el-button), .turn__retry, .chat-examples :deep(.el-button), .process__head { min-height: 44px; }
  .msg__foot :deep(.el-button) { min-width: 44px; }
}
</style>
