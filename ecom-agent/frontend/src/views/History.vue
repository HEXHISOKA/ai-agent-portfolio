<template>
  <div class="page-container history-page">
    <div class="content-card">
      <el-table :data="rows" v-loading="loading" :header-cell-style="tableHeaderStyle" @row-click="openRow">
        <el-table-column prop="question" label="问题" min-width="220" show-overflow-tooltip />
        <el-table-column label="路由" width="110">
          <template #default="{ row }">{{ routeLabel(row.route) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="light" round :type="statusTag(row.status)">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="elapsed_ms" label="耗时(ms)" width="100" />
        <el-table-column prop="create_time" label="时间" width="180" />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link :disabled="detailLoading || confirming || feedbackLoading" @click.stop="openDetail(row.id)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="table-footer">
        <el-pagination v-model:current-page="page" :page-size="10" :total="total" background small layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>
    <el-drawer v-model="opened" title="运行详情" size="min(480px, 100vw)">
      <template v-if="detail">
        <div class="history-page__detail-block">
          <div class="history-page__detail-label">问题 · 运行 #{{ detail.id }}</div>
          <div>{{ detail.question }}</div>
        </div>
        <div class="history-page__detail-block">
          <div class="history-page__detail-label">状态 / 路由 / 耗时</div>
          <div>{{ statusLabel(detail.status) }} · {{ routeLabel(detail.route) }} · {{ detail.elapsed_ms ?? '—' }} ms</div>
        </div>
        <div v-if="detail.answer_preview" class="history-page__detail-block">
          <div class="history-page__detail-label">回答摘要（只留前500字，完整回答见对话页）</div>
          <div class="history-page__preview" v-html="renderMarkdown(detail.answer_preview)"></div>
        </div>
        <div v-if="detail.error_message" class="history-page__detail-block">
          <div class="history-page__detail-label">错误</div>
          <el-alert :title="detail.error_message" type="error" :closable="false" />
        </div>
        <div v-if="detail.pending_confirmation" class="history-page__detail-block">
          <div class="history-page__detail-label">需要人工确认 · {{ riskLabel(detail.pending_confirmation.risk_level) }}</div>
          <p>{{ detail.pending_confirmation.reason }}</p>
          <ul>
            <li v-for="(action, index) in detail.pending_confirmation.actions" :key="index">
              {{ action.title || action.type }}<span v-if="action.product_id">（商品 {{ action.product_id }}）</span>
            </li>
          </ul>
          <div class="history-page__buttons">
            <el-button :disabled="confirming" @click="confirm(false)">取消动作</el-button>
            <el-button type="primary" :loading="confirming" :disabled="confirming" @click="confirm(true)">确认执行</el-button>
          </div>
        </div>
        <p v-else-if="detail.status === 'awaiting_confirm'" class="history-page__detail-block">待确认检查点已不可用，请重新提交问题。</p>
        <div class="history-page__detail-block">
          <div class="history-page__detail-label">步骤</div>
          <ul class="history-page__steps">
            <li v-for="step in detail.steps || []" :key="step.id || step.step_order">
              <span class="history-page__step-order">#{{ step.step_order }}</span>
              <span class="history-page__step-name">{{ step.step_name }}</span>
              <span>{{ step.summary }}</span>
              <span v-if="step.elapsed_ms != null" class="history-page__step-ms">{{ step.elapsed_ms }} ms</span>
              <pre v-if="stepSql(step.detail)" class="history-page__step-sql">{{ stepSql(step.detail) }}</pre>
            </li>
          </ul>
        </div>
        <div v-if="detail.answer_preview" class="history-page__detail-block">
          <div class="history-page__detail-label">反馈</div>
          <el-input v-model="comment" aria-label="反馈备注" placeholder="反馈备注（可选）" maxlength="500" />
          <div class="history-page__buttons">
            <el-button :disabled="feedbackLoading" @click="feedback(1)">有帮助</el-button>
            <el-button :disabled="feedbackLoading" @click="feedback(-1)">需改进</el-button>
          </div>
          <p v-if="detail.feedback">当前反馈：{{ detail.feedback.rating === 1 ? '有帮助' : '需改进' }}</p>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '@/utils/request'
import { confirmActions } from '@/api/chat'
const rows = ref([]), total = ref(0), page = ref(1), loading = ref(false)
const detail = ref(null), opened = ref(false), detailLoading = ref(false)
const confirming = ref(false), feedbackLoading = ref(false), comment = ref('')
let listRequest = 0

const tableHeaderStyle = { backgroundColor: '#f8fafc', color: '#475569', fontWeight: 600 }

function routeLabel(value) {
  return { sql: '结构化', structured: '结构化', semantic: '语义检索' }[value] || '—'
}
function statusLabel(value) {
  return { running: '进行中', success: '成功', error: '失败', awaiting_confirm: '待确认', cancelled: '已取消', interrupted: '已中断' }[value] || '未知状态'
}
function statusTag(value) {
  return { running: 'primary', success: 'success', error: 'danger', awaiting_confirm: 'warning', cancelled: 'info', interrupted: 'warning' }[value] || 'info'
}
function riskLabel(value) {
  return { low: '低风险', medium: '中风险', high: '高风险' }[value] || '风险待核对'
}

// 模型摘要按Markdown排版，清理HTML后再交给v-html，避免注入脚本或事件属性。
function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text || '', { async: false, breaks: true }))
}

// 步骤明细在数据库中是JSON文本；兼容已解析的对象，缺失或损坏时不展示SQL。
function stepSql(value) {
  try {
    const data = typeof value === 'string' ? JSON.parse(value) : value
    return typeof data?.sql === 'string' ? data.sql : ''
  } catch {
    return ''
  }
}
async function call(url, body) {
  const res = body === undefined ? await request.get(url) : await request.post(url, body)
  if (res.code !== 200) throw new Error(res.message)
  return res.data
}
async function load() {
  // 仅允许最后一次翻页请求更新列表、错误提示和加载状态。
  const id = ++listRequest
  loading.value = true
  try {
    const data = await call(`/api/observability/runs?page=${page.value}`)
    if (id === listRequest) { rows.value = data.list; total.value = data.total }
  } catch (err) { if (id === listRequest) ElMessage.error(err.message || '读取失败') }
  finally { if (id === listRequest) loading.value = false }
}
function openRow(row) {
  // 整行与详情按钮进入同一详情；写操作进行中不切换运行，避免结果更新到其他记录。
  if (detailLoading.value || confirming.value || feedbackLoading.value) return
  if (!Number.isSafeInteger(row?.id) || row.id <= 0) return
  return openDetail(row.id)
}
async function openDetail(id) {
  // 详情加载时阻止切换其他运行，避免旧详情覆盖新对象。
  if (detailLoading.value) return
  detailLoading.value = true
  try {
    detail.value = await call(`/api/observability/runs/${id}`)
    comment.value = detail.value.feedback?.comment || ''
    opened.value = true
  } catch (err) { ElMessage.error(err.message || '读取失败') }
  finally { detailLoading.value = false }
}
async function confirm(approved) {
  if (confirming.value || !detail.value?.pending_confirmation) return
  confirming.value = true
  try {
    await confirmActions(detail.value.thread_id, approved)
    await openDetail(detail.value.id)
    await load()
  } catch (err) { ElMessage.error(err.message || '确认失败') }
  finally { confirming.value = false }
}
async function feedback(rating) {
  if (feedbackLoading.value || !detail.value) return
  feedbackLoading.value = true
  try {
    const data = await call(`/api/observability/runs/${detail.value.id}/feedback`, { rating, comment: comment.value })
    detail.value.feedback = data
  } catch (err) { ElMessage.error(err.message || '反馈失败') }
  finally { feedbackLoading.value = false }
}
const route = useRoute()
onMounted(async () => {
  await load()
  const runId = Number(route.query.run_id)
  // 首页只传运行编号，详情仍由服务端校验当前用户归属。
  if (Number.isInteger(runId) && runId > 0) await openDetail(runId)
})
</script>

<style scoped>
.history-page :deep(.el-table__body tr) { cursor: pointer; }
.history-page__detail-block {
  margin-bottom: 16px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--app-text);
}

.history-page__detail-label {
  margin-bottom: 4px;
  font-weight: 600;
  font-size: 13px;
  color: var(--app-text-secondary);
}

.history-page__preview {
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
  padding: 8px 10px;
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius-sm);
  background: #f8fafc;
}

/* 回答摘要是 Markdown：渲染后压掉默认间距，首尾不留空白 */
.history-page__preview :deep(p) {
  margin: 6px 0;
}

.history-page__preview :deep(p:first-child) {
  margin-top: 0;
}

.history-page__preview :deep(p:last-child) {
  margin-bottom: 0;
}

.history-page__preview :deep(ul),
.history-page__preview :deep(ol) {
  margin: 6px 0;
  padding-left: 20px;
}

.history-page__preview :deep(li) {
  margin: 2px 0;
}

.history-page__preview :deep(h1),
.history-page__preview :deep(h2),
.history-page__preview :deep(h3),
.history-page__preview :deep(h4) {
  margin: 8px 0 4px;
  font-size: 14px;
}

.history-page__preview :deep(strong) {
  font-weight: 600;
}

.history-page__preview :deep(hr) {
  border: none;
  border-top: 1px dashed var(--app-border);
  margin: 8px 0;
}

.history-page__preview :deep(code) {
  padding: 1px 4px;
  border-radius: 4px;
  background: #eef2f7;
  font-size: 12px;
}

.history-page__steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.history-page__steps li {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 13px;
  line-height: 1.5;
}

.history-page__step-order {
  color: var(--app-text-secondary);
}

.history-page__step-name {
  font-weight: 600;
}

.history-page__step-sql {
  width: 100%;
  margin: 2px 0 4px;
  padding: 6px 8px;
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius-sm);
  background: #f8fafc;
  font-family: Consolas, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: var(--app-text);
  white-space: pre-wrap;
  word-break: break-all;
}

.history-page__step-ms {
  color: var(--app-text-secondary);
  margin-left: auto;
}

.history-page__detail-block { overflow-wrap: anywhere; }
.history-page__preview { overflow: auto; }
.history-page__preview :deep(table) { border-collapse: collapse; width: 100%; }
.history-page__preview :deep(th),
.history-page__preview :deep(td) { border: 1px solid var(--app-border); padding: 6px 8px; text-align: left; min-width: 60px; }
.history-page__preview :deep(pre) { white-space: pre-wrap; overflow-wrap: anywhere; }
.history-page__buttons { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.history-page__buttons :deep(.el-button + .el-button) { margin-left: 0; }
@media (max-width: 640px) {
  .history-page__buttons :deep(.el-button) { min-height: 44px; }
}
</style>
