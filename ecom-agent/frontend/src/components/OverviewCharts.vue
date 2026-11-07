<template>
  <div class="overview-charts">
    <div class="content-card">
      <div class="panel-head">
        <span class="panel-title">近 {{ stats.trend_days }} 天订单量</span>
        <span class="panel-sub">共 {{ trendTotal }} 单 · {{ formatMoney(trendAmount) }}</span>
      </div>
      <div ref="trendRef" class="chart" />
    </div>
    <div class="dash-row">
      <div class="content-card">
        <div class="panel-head">
          <span class="panel-title">评分分布</span>
          <span class="panel-sub">平均 {{ stats.cards.avg_rating }} 分</span>
        </div>
        <div ref="ratingRef" class="chart" />
      </div>
      <div class="content-card">
        <div class="panel-head">
          <span class="panel-title">需要重点关注的商品</span>
          <span class="panel-sub">按差评（≤2 星）条数排序</span>
        </div>
        <ul v-if="stats.worst_products.length" class="rank-list">
          <li v-for="item in stats.worst_products" :key="item.product_id" class="rank-item">
            <div class="rank-item__head">
              <span class="rank-item__name">{{ item.product_name }}</span>
              <span class="rank-item__bad">{{ item.bad_count }} 条差评</span>
            </div>
            <div class="rank-item__bar"><span class="rank-item__bar-inner" :style="{ width: badBarWidth(item.bad_count) }" /></div>
            <div class="rank-item__meta">平均 {{ item.avg_rating }} 分 · 共 {{ item.review_count }} 条评价</div>
          </li>
        </ul>
        <el-empty v-else description="暂无评价数据" :image-size="60" />
      </div>
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ stats: { type: Object, required: true } })
const trendRef = ref(), ratingRef = ref()
let trendChart, ratingChart, observer
const trendTotal = computed(() => props.stats.order_trend.reduce((sum, row) => sum + row.count, 0))
// 金额由接口返回字符串，先转成分相加，再转回元，避免字符串拼接和小数累积误差。
const trendAmount = computed(() => props.stats.order_trend.reduce(
  (sum, row) => sum + Math.round(Number(row.amount) * 100), 0
) / 100)
const maxBadCount = computed(() => Math.max(1, ...props.stats.worst_products.map(row => row.bad_count)))
function formatMoney(value) {
  return `¥${Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
function badBarWidth(count) {
  // 按当前排行最大低分条数缩放，最短保留6%便于看清，不表示差评率。
  return `${Math.max(6, Math.round(count / maxBadCount.value * 100))}%`
}
function render() {
  // 容器挂载且具有高度后才能初始化，数据变化复用原实例。
  if (!trendRef.value || !ratingRef.value) return
  trendChart ||= echarts.init(trendRef.value)
  ratingChart ||= echarts.init(ratingRef.value)
  const points = props.stats.order_trend
  const textStyle = { color: '#64748b', fontFamily: 'inherit' }
  trendChart.setOption({
    color: ['#2563eb'], textStyle,
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        // 使用原始完整日期和同一天的金额，横轴只缩短显示，统计口径不变。
        const point = points[params[0].dataIndex]
        return `${point.date}<br/>订单 ${point.count} 单<br/>成交 ${formatMoney(point.amount)}`
      }
    },
    xAxis: {
      type: 'category', boundaryGap: false, data: points.map(row => row.date.slice(5)),
      axisLine: { lineStyle: { color: '#e2e8f0' } }, axisTick: { show: false },
      axisLabel: { interval: Math.max(0, Math.ceil(points.length / 10) - 1) }
    },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#f1f5f9' } } },
    series: [{ name: '订单量', type: 'line', smooth: true, symbolSize: 6,
      data: points.map(row => row.count), areaStyle: { opacity: 0.12 } }]
  }, true)
  const colors = { 1: '#ef4444', 2: '#f59e0b', 3: '#94a3b8', 4: '#38bdf8', 5: '#10b981' }
  ratingChart.setOption({
    textStyle, grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', valueFormatter: value => `${value} 条` },
    xAxis: { type: 'category', data: props.stats.rating_distribution.map(row => `${row.rating} 星`),
      axisLine: { lineStyle: { color: '#e2e8f0' } }, axisTick: { show: false } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#f1f5f9' } } },
    series: [{ name: '评价数', type: 'bar', barMaxWidth: 48,
      data: props.stats.rating_distribution.map(row => ({ value: row.count,
        itemStyle: { color: colors[row.rating] || '#2563eb', borderRadius: [4, 4, 0, 0] } })) }]
  }, true)
}
onMounted(() => {
  render()
  observer = new ResizeObserver(() => { trendChart?.resize(); ratingChart?.resize() })
  observer.observe(trendRef.value)
  observer.observe(ratingRef.value)
})
watch(() => props.stats, render)
onBeforeUnmount(() => {
  // 路由离开后容器会移除，先解除监听再销毁实例。
  observer?.disconnect()
  trendChart?.dispose()
  ratingChart?.dispose()
})
</script>
<style scoped>
.overview-charts { display: flex; flex-direction: column; gap: var(--app-space-gap); }
.dash-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--app-space-gap); }
.content-card { min-width: 0; }
.panel-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.panel-title { font-size: 14px; font-weight: 600; color: var(--app-text); }
.panel-sub { font-size: 12px; color: var(--app-text-secondary); }
.chart { width: 100%; height: 240px; }
.rank-list { margin: 0; padding: 0; list-style: none; }
.rank-item + .rank-item { margin-top: 14px; }
.rank-item__head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.rank-item__name { font-size: 13px; color: var(--app-text); }
.rank-item__bad { font-size: 12px; color: #ef4444; flex-shrink: 0; }
.rank-item__bar { margin: 6px 0 4px; height: 6px; border-radius: 999px; background: #f1f5f9; overflow: hidden; }
.rank-item__bar-inner { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #f59e0b, #ef4444); }
.rank-item__meta { font-size: 12px; color: var(--app-text-secondary); }
@media (max-width: 1100px) { .dash-row { grid-template-columns: minmax(0, 1fr); } }
</style>
