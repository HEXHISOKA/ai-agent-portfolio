<template>
  <div class="page-container metrics" v-loading="loading">
    <div class="content-card metrics__head">
      <div>
        <div class="metrics__title">Agent 运行观测</div>
        <div class="metrics__sub">
          只统计本人的运行记录：成功率、耗时与反馈，用来判断 Agent
          稳定性和瓶颈在哪一步。
        </div>
      </div>
      <div class="metrics__side">
        <span v-if="updatedAt" class="metrics__time">更新于 {{ updatedAt }}</span>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" />

    <div class="stat-grid">
      <div
        v-for="card in cards"
        :key="card.label"
        class="stat-card"
        :class="card.tone ? `stat-card--${card.tone}` : ''"
      >
        <div class="stat-card__label">{{ card.label }}</div>
        <div class="stat-card__value">{{ card.value }}</div>
        <div class="stat-card__hint">{{ card.hint }}</div>
      </div>
    </div>

    <div v-if="stats" class="metrics__statuses" aria-label="运行状态分布">
      <span v-if="!stats.total">暂无运行记录</span>
      <span v-for="(count, status) in stats.statuses" :key="status">{{ STATUS_LABELS[status] || status }} {{ count }}</span>
    </div>
    <div class="metrics__row">
      <div class="content-card">
        <div class="panel-head">
          <span class="panel-title">路由分布</span>
          <span class="panel-sub">结构化 / 语义检索 / 未判定</span>
        </div>
        <el-empty v-if="!stats?.route_distribution?.length" description="暂无路由数据" :image-size="60" />
        <div v-show="stats?.route_distribution?.length" ref="routeEl" class="chart" role="img" :aria-label="routeSummary"></div>
      </div>

      <div class="content-card">
        <div class="panel-head">
          <span class="panel-title">各阶段平均耗时</span>
          <span class="panel-sub">倒序取前 8，找瓶颈用</span>
        </div>
        <el-empty v-if="!stats?.step_stats?.length" description="暂无阶段耗时数据" :image-size="60" />
        <div v-show="stats?.step_stats?.length" ref="stepEl" class="chart" role="img" aria-label="阶段平均耗时，按平均值排序"></div>
      </div>
    </div>

    <div class="content-card">
      <div class="panel-head">
        <span class="panel-title">近 {{ stats?.trend_days || 14 }} 天运行量</span>
        <span class="panel-sub">共 {{ trendTotal }} 次</span>
      </div>
      <div ref="runEl" class="chart chart--line" role="img" :aria-label="`近${stats?.trend_days || 14}天运行量，共${trendTotal}次`"></div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import request from '@/utils/request'
import { CHART_COLORS, CHART_TEXT_STYLE, useEchart } from '@/composables/useEchart'

defineOptions({ name: 'Metrics' })

const loading = ref(false)
const stats = ref(null)
const updatedAt = ref('')
const error = ref('')
let requestId = 0, disposed = false

const routeEl = ref(null)
const stepEl = ref(null)
const runEl = ref(null)
const { render: renderRoute } = useEchart(routeEl)
const { render: renderStep } = useEchart(stepEl)
const { render: renderRun } = useEchart(runEl)

// 兼容当前节点名和历史阶段名；原始分组保持不变，只替换显示名称。
const STATUS_LABELS = {
  running: '进行中',
  success: '成功',
  error: '失败',
  awaiting_confirm: '待确认',
  cancelled: '已取消',
  interrupted: '已中断'
}
const STEP_LABELS = {
  scheduler_start: '调度开始',
  route_question: '问题路由',
  structured_query: 'SQL查询',
  semantic_query: '语义查询',
  judge_deepen: '补充检索判断',
  deepen_semantic: '补充评价检索',
  prepare_result: '证据整理',
  recent_logs: '读取历史摘要',
  scheduler_handoff: '调度交接',
  decide_actions: '动作决策',
  confirm_gate: '人工确认',
  execute_actions: '执行动作',
  starting: '启动',
  context: '多轮上下文',
  routing: '意图路由',
  route: '意图路由',
  scheduling: '任务调度',
  'agent:scheduler': '调度（子图）',
  'agent:analysis': '多角色分析',
  'agent:decision': '动作决策',
  querying: '数据查询',
  deepening: '语义深挖',
  preparing: '证据整合',
  judging: '风险判断',
  deciding: '动作决策',
  confirming: '人工确认',
  executing: '动作执行',
  answering: '生成回答',
  action_pending: '等待确认',
  cancelled: '已取消'
}

const cards = computed(() => {
  const c = stats.value?.cards
  // 首次请求尚未返回时不编造零值卡片。
  if (!c) return []
  const rated = c.up_count + c.down_count
  return [
    { label: '总运行次数', value: c.total_runs, hint: '本人全部运行状态' },
    { label: '成功', value: c.success_runs, hint: '正常跑完' },
    {
      label: '失败',
      value: c.failed_runs,
      hint: '抛异常终止',
      tone: c.failed_runs ? 'danger' : ''
    },
    { label: '已取消', value: c.cancelled_runs, hint: '用户取消动作，不含中断' },
    {
      label: '未完成',
      value: c.unfinished_runs,
      hint: '进行中 / 待确认',
      tone: c.unfinished_runs ? 'warning' : ''
    },
    {
      label: '成功率',
      value: formatPercent(c.success_rate),
      hint: '成功 ÷（成功＋失败）'
    },
    { label: '平均耗时', value: formatMs(c.avg_elapsed_ms), hint: '成功和失败运行的有效耗时' },
    {
      label: '点赞率',
      value: formatPercent(c.up_rate),
      hint: rated ? `赞 ${c.up_count} / 踩 ${c.down_count}` : '还没有收到反馈'
    }
  ]
})

const routeSummary = computed(() => (stats.value?.route_distribution || []).map(row => `${row.name}${row.count}次`).join('，') || '暂无路由数据')
const trendPoints = computed(() => stats.value?.run_trend || [])
const trendTotal = computed(() => trendPoints.value.reduce((sum, p) => sum + p.count, 0))

function formatPercent(value) { return value == null ? '—' : `${(value * 100).toFixed(1)}%` }

function formatMs(ms) {
  // 0ms是有效样本，只有缺少耗时时才显示占位符。
  if (ms == null) return '—'
  const value = Number(ms)
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`
}

function stepLabel(name) {
  return STEP_LABELS[name] || name
}

function drawRoute() {
  // 空数据由el-empty展示，不在隐藏容器里创建饼图。
  if (!stats.value?.route_distribution?.length) return
  const buckets = stats.value?.route_distribution || []
  return renderRoute({
    color: CHART_COLORS,
    textStyle: CHART_TEXT_STYLE,
    tooltip: { trigger: 'item', formatter: '{b}：{c} 次（{d}%）' },
    legend: { bottom: 0, itemWidth: 10, itemHeight: 10, textStyle: CHART_TEXT_STYLE },
    series: [
      {
        name: '路由分布',
        type: 'pie',
        radius: ['42%', '66%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        label: { formatter: '{b} {c}' },
        data: buckets.map((b) => ({ name: b.name, value: b.count }))
      }
    ]
  })
}

function drawStep() {
  // 没有耗时样本时显示空态，不能画成零耗时阶段。
  if (!stats.value?.step_stats?.length) return
  const rows = [...(stats.value?.step_stats || [])].reverse()
  return renderStep({
    color: [CHART_COLORS[0]],
    textStyle: CHART_TEXT_STYLE,
    grid: { left: 8, right: 64, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      renderMode: 'richText',
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const row = rows[params[0].dataIndex]
        return `${stepLabel(row.step_name)}\n平均 ${formatMs(row.avg_ms)} · 最大 ${formatMs(
          row.max_ms
        )}\n样本 ${row.count} 次`
      }
    },
    xAxis: {
      type: 'value',
      axisLabel: { formatter: (value) => formatMs(value) },
      splitLine: { lineStyle: { color: '#f1f5f9' } }
    },
    yAxis: {
      type: 'category',
      data: rows.map((r) => stepLabel(r.step_name)),
      axisLabel: { width: 110, overflow: 'truncate' },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { show: false }
    },
    series: [
      {
        name: '平均耗时',
        type: 'bar',
        barMaxWidth: 16,
        data: rows.map((r) => r.avg_ms),
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        label: {
          show: true,
          position: 'right',
          formatter: (params) => formatMs(params.value),
          color: '#64748b',
          fontSize: 12
        }
      }
    ]
  })
}

function drawRunTrend() {
  const points = trendPoints.value
  return renderRun({
    color: [CHART_COLORS[4]],
    textStyle: CHART_TEXT_STYLE,
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', valueFormatter: (value) => `${value} 次` },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: points.map((p) => p.date.slice(5)),
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { show: false }
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: '#f1f5f9' } }
    },
    series: [
      {
        name: '运行量',
        type: 'line',
        smooth: true,
        symbolSize: 6,
        data: points.map((p) => p.count),
        areaStyle: { opacity: 0.12 }
      }
    ]
  })
}

async function load() {
  // 刷新共用请求序号，只有最后一次请求能更新卡片、图表、错误和加载状态。
  const id = ++requestId
  loading.value = true
  error.value = ''
  try {
    const res = await request.get('/api/observability/stats', { params: { trend_days: 14 } })
    if (disposed || id !== requestId) return
    if (res.code !== 200) throw new Error(res.message || '读取失败')
    stats.value = res.data
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    await Promise.all([drawRoute(), drawStep(), drawRunTrend()])
  } catch (err) {
    // 刷新失败保留上次成功数据和更新时间，显示错误后允许重试。
    if (!disposed && id === requestId) error.value = err.message || '读取失败'
  } finally {
    if (id === requestId) loading.value = false
  }
}
onBeforeUnmount(() => { disposed = true; requestId++ })

onMounted(load)
</script>

<style scoped>
.metrics {
  display: flex;
  flex-direction: column;
  gap: var(--app-space-gap);
}

.metrics__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.metrics__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--app-text);
}

.metrics__sub {
  margin-top: 4px;
  font-size: 13px;
  color: var(--app-text-secondary);
}

.metrics__side {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.metrics__time {
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

.metrics__row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--app-space-gap);
}

.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
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

.chart {
  width: 100%;
  height: 260px;
}

.chart--line {
  height: 220px;
}

@media (max-width: 1100px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .metrics__row {
    grid-template-columns: minmax(0, 1fr);
  }
}

.metrics__statuses { display: flex; flex-wrap: wrap; gap: 12px; color: var(--app-text-secondary); font-size: 13px; }
.metrics__head, .metrics__side, .panel-head { flex-wrap: wrap; }
.metrics__head > div, .content-card { min-width: 0; }
.stat-card__value { overflow-wrap: anywhere; }
@media (max-width: 640px) {
  .stat-grid { grid-template-columns: minmax(0, 1fr); }
  .metrics__head { align-items: flex-start; }
  .metrics__side { gap: 8px; }
  .metrics__side :deep(.el-button) { min-height: 44px; }
}
</style>
