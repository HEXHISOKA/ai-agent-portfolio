<template>
  <div class="page-container dashboard">
    <div class="content-card dash-head">
      <div class="dash-head__text">
        <div class="dash-head__title">你好，{{ userInfo?.name || userInfo?.username || '同学' }}</div>
        <div class="dash-head__sub">这里是店铺整体数据概览；需要下钻分析时，去「数据分析对话」直接提问即可。</div>
      </div>
      <div class="dash-head__side">
        <span v-if="updatedAt" class="dash-head__time">更新于 {{ updatedAt }}</span>
        <el-tooltip placement="bottom" content="按固定问题执行分析，需人工确认的动作仍在运行历史中处理">
          <el-button type="primary" :icon="MagicStick" :loading="patrolling" :disabled="patrolling || loading" @click="runPatrol">一键巡检</el-button>
        </el-tooltip>
        <el-button :icon="Refresh" :loading="loading" :disabled="loading || patrolling" @click="load">刷新</el-button>
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />

    <div v-if="patrol" class="content-card patrol-card">
      <div class="panel-head">
        <span class="panel-title">巡检结果</span>
        <span v-if="patrol.reused" class="panel-sub">沿用待确认运行 {{ patrol.run_id }}，未重复分析</span>
      </div>
      <p class="panel-sub">{{ patrol.question }}</p>
      <!-- 回答失败时也保留摘要、计划与历史入口，不能用错误分支隐藏可恢复计划。 -->
      <el-alert v-if="patrol.error" class="patrol-card__error" :title="patrol.error" type="error" :closable="false" />
      <div v-if="patrol.route" class="patrol-card__meta">
        <el-tag size="small" effect="light" round :type="patrol.route === 'sql' ? 'success' : 'info'">
          {{ patrol.route === 'sql' ? '结构化查询' : '语义检索' }}
        </el-tag>
        <span v-if="patrol.run_id">过程已写入「运行历史」</span>
      </div>
      <p v-if="patrol.reused && patrol.answer" class="panel-sub">原运行回答摘要：</p>
      <div v-if="patrol.answer" class="patrol-card__answer" v-html="renderMarkdown(patrol.answer)"></div>
      <QueryResult v-if="patrol.result" :result="patrol.result" />
      <div class="patrol-card__actions">
        <template v-if="patrol.plan?.pending_actions?.length">
          <div class="patrol-card__actions-title">本次计划包含 {{ patrol.plan.pending_actions.length }} 个动作：</div>
          <ul>
            <li v-for="(action, index) in patrol.plan.pending_actions" :key="index">
              {{ action.title || action.type }} · 商品 {{ action.product_id || '综合' }}
            </li>
          </ul>
        </template>
        <p v-if="patrol.confirmation">动作等待人工确认，尚未执行。</p>
        <span v-else-if="!patrol.error" class="patrol-card__ok">
          {{ patrol.action_message || '本次巡检没有需要确认的动作。' }}
        </span>
        <el-button v-if="patrol.run_id" type="primary" @click="openRun">查看本次运行</el-button>
      </div>
    </div>

    <div v-loading="loading" class="dashboard">
      <template v-if="stats">
        <div class="stat-grid">
          <div v-for="card in cards" :key="card.label" class="stat-card" :class="card.tone ? `stat-card--${card.tone}` : ''">
            <div class="stat-card__label">{{ card.label }}</div>
            <div class="stat-card__value">{{ card.value }}</div>
            <div class="stat-card__hint">{{ card.hint }}</div>
          </div>
        </div>
        <OverviewCharts :stats="stats" />
        <div class="content-card">
          <div class="panel-head">
            <span class="panel-title">最近预警</span>
            <el-button type="primary" link @click="router.push({ path: '/manager/reports', query: { tab: 'alerts' } })">查看全部</el-button>
          </div>
          <ul v-if="stats.recent_alerts.length" class="alert-list">
            <li v-for="alert in stats.recent_alerts" :key="alert.id" class="alert-item">
              <el-tag size="small" effect="light" round :type="alert.level === 'critical' ? 'danger' : 'warning'">
                {{ alert.level === 'critical' ? '严重' : '提醒' }}
              </el-tag>
              <span class="alert-item__content" :title="alert.content">{{ alert.content }}</span>
              <span class="alert-item__product" :title="alert.product_name || (alert.product_id ? `商品 ${alert.product_id}` : '全店')">{{ alert.product_name || (alert.product_id ? `商品 ${alert.product_id}` : '全店') }}</span>
              <time class="alert-item__time" :datetime="alert.create_time || undefined">{{ formatAlertTime(alert.create_time) }}</time>
            </li>
          </ul>
          <el-empty v-else description="还没有触发过预警" :image-size="60" />
        </div>
      </template>
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { MagicStick, Refresh } from '@element-plus/icons-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import request from '@/utils/request'
import { useAlertUnread } from '@/composables/useAlertUnread'
import { useUser } from '@/utils/user'
import OverviewCharts from '@/components/OverviewCharts.vue'
import QueryResult from '@/components/QueryResult.vue'

const { unread: alertUnread, error: alertSummaryError, refresh: refreshAlertUnread } = useAlertUnread()

const router = useRouter()
const { userInfo } = useUser()
const stats = ref(null), loading = ref(false), error = ref(''), updatedAt = ref('')
const patrol = ref(null), patrolling = ref(false)

function formatMoney(value) {
  return `¥${Number(value || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2, maximumFractionDigits: 2
  })}`
}
const cards = computed(() => {
  const c = stats.value?.cards
  if (!c) return []
  return [
    { label: '商品数', value: c.product_count, hint: '全部商品' },
    { label: '订单数', value: c.order_count, hint: '累计订单' },
    { label: '评价数', value: c.review_count, hint: '累计评价' },
    { label: '平均评分', value: c.avg_rating, hint: '满分 5 分' },
    { label: '累计成交', value: formatMoney(c.total_amount), hint: '订单实付合计（不含已取消）' },
    { label: '高风险诊断报告', value: c.high_risk_reports, hint: '本人高风险分析结果', tone: c.high_risk_reports ? 'warning' : '' },
    { label: '未读预警', value: alertUnread.value ?? '—', hint: alertSummaryError.value ? '统计读取失败，请刷新' : '本人站内通知', tone: alertUnread.value ? 'danger' : '' },
    { label: '待复核商品', value: c.pending_review_products, hint: '人工复核状态' }
  ]
})
function formatAlertTime(value) {
  // 接口时间按原值展示，只把日期和时间之间的T替换为空格，不做时区换算。
  return value ? String(value).replace('T', ' ') : '—'
}
function renderMarkdown(text) {
  // 模型输出先渲染Markdown，再过滤不安全的HTML。
  return DOMPurify.sanitize(marked.parse(text || '', { async: false, breaks: true }))
}
async function load() {
  // 刷新期间禁用按钮，避免较早响应覆盖最新概览。
  if (loading.value) return
  loading.value = true
  refreshAlertUnread()
  error.value = ''
  try {
    const res = await request.get('/api/ecommerce/overview')
    if (res.code !== 200) throw new Error(res.message)
    stats.value = res.data
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (err) { error.value = err.message || '读取失败' }
  finally { loading.value = false }
}
async function runPatrol() {
  if (patrolling.value || loading.value) return
  patrolling.value = true
  try {
    // 巡检包含模型与多工具调用，保留独立的180秒超时。
    const res = await request.post('/api/patrol/run', {}, { timeout: 180000 })
    if (res.code !== 200) throw new Error(res.message)
    patrol.value = res.data
    await load()
  } catch (err) { ElMessage.error(err.message || '巡检失败') }
  finally { patrolling.value = false }
}
function openRun() {
  // 按返回的运行编号定位原计划，详情接口继续校验当前用户归属。
  router.push({ path: '/manager/history', query: { run_id: patrol.value.run_id } })
}
onMounted(load)
</script>
<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: var(--app-space-gap);
}

.dash-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.dash-head__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--app-text);
}

.dash-head__sub {
  margin-top: 4px;
  font-size: 13px;
  color: var(--app-text-secondary);
}

.dash-head__side {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.dash-head__time {
  font-size: 12px;
  color: var(--app-text-secondary);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--app-space-gap);
}

.stat-card {
  padding: 14px 16px;
  background: var(--app-card-bg);
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius-sm);
  box-shadow: var(--app-shadow);
}

.stat-card__label {
  font-size: 13px;
  color: var(--app-text-secondary);
}

.stat-card__value {
  margin: 6px 0 2px;
  font-size: 24px;
  font-weight: 600;
  color: var(--app-text);
  line-height: 1.2;
}

.stat-card__hint {
  font-size: 12px;
  color: var(--app-text-secondary);
}

.stat-card--warning .stat-card__value {
  color: #f59e0b;
}

.stat-card--danger .stat-card__value {
  color: #ef4444;
}


.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.patrol-card__meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.patrol-card__answer {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius-sm);
  background: #f8fafc;
  font-size: 13px;
  line-height: 1.8;
  color: var(--app-text);
  word-break: break-word;
  max-height: 260px;
  overflow-y: auto;
}

/* 巡检回答是 Markdown，压一下标题和段落的默认间距 */
.patrol-card__answer :deep(h1),
.patrol-card__answer :deep(h2),
.patrol-card__answer :deep(h3),
.patrol-card__answer :deep(h4) {
  margin: 12px 0 6px;
  font-size: 14px;
}

.patrol-card__answer :deep(h1:first-child),
.patrol-card__answer :deep(h2:first-child),
.patrol-card__answer :deep(h3:first-child) {
  margin-top: 0;
}

.patrol-card__answer :deep(p) {
  margin: 6px 0;
}

.patrol-card__answer :deep(ul),
.patrol-card__answer :deep(ol) {
  margin: 6px 0;
  padding-left: 20px;
}

.patrol-card__answer :deep(hr) {
  border: none;
  border-top: 1px dashed var(--app-border);
  margin: 10px 0;
}

.patrol-card__actions {
  margin-top: 12px;
}

.patrol-card__actions-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text);
}

.patrol-card__actions ul {
  margin: 6px 0 10px;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--app-text);
}

.patrol-card__ok {
  font-size: 13px;
  color: var(--app-text-secondary);
}

.patrol-card__error {
  font-size: 13px;
  color: #b91c1c;
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--app-text);
}

.panel-sub {
  font-size: 12px;
  color: var(--app-text-secondary);
}

.alert-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
  font-size: 13px;
  border-bottom: 1px dashed var(--app-border);
}

.alert-item:last-child {
  border-bottom: none;
}

.alert-item__content {
  flex: 1;
  color: var(--app-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.alert-item__product {
  color: var(--app-text-secondary);
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
}
.alert-item__time { color: var(--app-text-secondary); font-size: 12px; flex-shrink: 0; }
.alert-item__content { min-width: 0; }
@media(max-width: 600px) {
  .alert-item__product { max-width: 100%; }
  .alert-item__time { flex-basis: 100%; }
}


@media (max-width: 1100px) {
  .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dash-head { align-items: flex-start; flex-wrap: wrap; }
  .dash-head__side { flex-wrap: wrap; }
}
@media (max-width: 600px) {
  .stat-grid { grid-template-columns: minmax(0, 1fr); }
  .dash-head__side { width: 100%; }
  .alert-item { flex-wrap: wrap; }
  .alert-item__content { flex-basis: calc(100% - 70px); white-space: normal; }
}
.stat-card, .dash-head__text { min-width: 0; }
.stat-card__value { overflow-wrap: anywhere; }
.panel-head { flex-wrap: wrap; }
.patrol-card__answer { overflow-x: auto; }
.patrol-card__answer :deep(table) { border-collapse: collapse; width: 100%; }
.patrol-card__answer :deep(th), .patrol-card__answer :deep(td) {
  padding: 6px 10px; border: 1px solid var(--app-border); text-align: left;
}
.patrol-card__actions .el-button { margin-top: 8px; }
.patrol-card__error { margin: 10px 0; }

</style>
