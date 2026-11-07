/**
 * ECharts 组合式封装：只注册用到的图表与组件，避免整包引入。
 * 组件里只要拿到容器 ref，然后在数据到位时调用 render(option) 即可。
 */
import { nextTick, onBeforeUnmount, ref } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer
])

export const CHART_COLORS = ['#2563eb', '#38bdf8', '#f59e0b', '#ef4444', '#10b981', '#8b5cf6']

/** 图表里统一的字体与文字色，页面按需覆盖 */
export const CHART_TEXT_STYLE = {
  color: '#64748b',
  fontFamily: 'inherit'
}

export function useEchart(elRef) {
  const ready = ref(false)
  let chart = null
  let observer = null
  let disposed = false

  async function render(option) {
    // 数据晚于挂载到达时容器才刚渲染出来，等一次 DOM 再初始化
    await nextTick()
    const el = elRef.value
    // 离页后的异步渲染不得重新创建实例和观察器。
    if (disposed || !el) return
    if (!chart) {
      chart = echarts.init(el)
      // 窗口或侧栏改变宽度时，通过ResizeObserver调整图表尺寸
      observer = new ResizeObserver(() => chart && chart.resize())
      observer.observe(el)
      ready.value = true
    }
    chart.setOption(option, true)
  }

  onBeforeUnmount(() => {
    disposed = true
    if (observer) observer.disconnect()
    if (chart) chart.dispose()
    observer = null
    chart = null
  })

  return { render, ready }
}
