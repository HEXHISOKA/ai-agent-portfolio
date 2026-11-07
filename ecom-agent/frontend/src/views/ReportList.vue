<template>
  <div class="page-container report-page">
    <div class="content-card">
      <div class="toolbar-row">
        <el-input
          class="search-input"
          :placeholder="isReportTab ? '按问题 / 摘要 / 商品搜索' : '按标题 / 内容 / 商品搜索'"
          v-model="params.keywords"
          aria-label="报告与预警搜索"
          clearable
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        />
        <el-button type="primary" :icon="Search" @click="handleSearch">查询</el-button>
        <el-button v-if="!isReportTab" :disabled="reading || summaryLoading || !!summaryError || !alertUnread" @click="handleReadAll">
          全部标记已读
        </el-button>
      </div>

      <el-alert v-if="error" :title="error" type="error" :closable="false" class="report-page__error" />
      <div v-if="summaryError" class="report-page__error">
        <el-alert :title="summaryError" type="warning" :closable="false" />
        <el-button :disabled="summaryLoading" @click="refreshAlertUnread()">重试未读统计</el-button>
      </div>
      <el-tabs v-model="activeTab" @tab-change="handleTabChange">
        <el-tab-pane label="诊断报告" name="reports" />
        <el-tab-pane :label="alertTabLabel" name="alerts" />
      </el-tabs>

      <!-- 诊断报告 -->
      <el-table
        v-if="isReportTab"
        :header-cell-style="tableHeaderStyle"
        :data="tableData"
        style="width: 100%"
        v-loading="loading"
      >
        <el-table-column prop="question" label="问题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="product_name" label="关联商品" width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.product_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="risk_level" label="风险" width="100">
          <template #default="{ row }">
            <el-tag
              size="small"
              effect="light"
              round
              :type="{ high: 'danger', low: 'success' }[row.risk_level] || 'info'"
            >
              {{ riskLabel(row.risk_level) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="evidence_count" label="证据数" width="80" />
        <el-table-column prop="summary" label="摘要" min-width="220" show-overflow-tooltip />
        <el-table-column prop="create_time" label="生成时间" width="170"><template #default="{ row }">{{ formatTime(row.create_time) }}</template></el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 预警通知 -->
      <el-table
        v-else
        :header-cell-style="tableHeaderStyle"
        :data="tableData"
        style="width: 100%"
        v-loading="loading"
      >
        <el-table-column prop="level" label="级别" width="90">
          <template #default="{ row }">
            <el-tag
              size="small"
              effect="light"
              round
              :type="{ critical: 'danger', warning: 'warning' }[row.level] || 'info'"
            >
              {{ alertLabel(row.level) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" width="160" show-overflow-tooltip />
        <el-table-column prop="content" label="内容" min-width="240" show-overflow-tooltip />
        <el-table-column prop="product_name" label="关联商品" width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.product_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="create_time" label="触发时间" width="170"><template #default="{ row }">{{ formatTime(row.create_time) }}</template></el-table-column>
        <el-table-column prop="is_read" label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="light" round :type="row.is_read ? 'info' : 'danger'">
              {{ row.is_read ? '已读' : '未读' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="openDetail(row)">详情</el-button>
            <el-button
              v-if="!row.is_read"
              type="primary"
              link
              :loading="reading"
              :disabled="reading"
              @click.stop="handleReadOne(row)"
            >
              标为已读
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="table-footer">
        <el-pagination
          v-model:current-page="params.page"
          v-model:page-size="params.pageSize"
          :total="total"
          background
          small
          layout="total, prev, pager, next"
          @current-change="load"
        />
      </div>
    </div>

    <!-- 详情抽屉：报告正文 / 预警内容 -->
    <el-drawer
      v-model="drawerVisible"
      :title="detailKind === 'reports' ? '诊断报告详情' : '预警通知详情'"
      size="min(480px, 100vw)"
    >
      <div v-if="detail" class="report-detail">
        <template v-if="detailKind === 'reports'">
          <div class="report-detail__label">用户问题</div>
          <div class="report-detail__value">{{ detail.question }}</div>
          <div class="report-detail__label">关联商品</div>
          <div class="report-detail__value">
            {{ detail.product_name || '-' }}
          </div>
          <div class="report-detail__label">风险等级 / 查询路由</div>
          <div class="report-detail__value">
            {{ riskLabel(detail.risk_level) }} ·
            {{ routeLabel(detail.route) }}
          </div>
          <div class="report-detail__label">证据条数</div>
          <div class="report-detail__value">{{ detail.evidence_count }}</div>
          <div class="report-detail__label">报告正文</div>
          <pre class="report-detail__pre">{{ detail.summary }}</pre>
        </template>
        <template v-else>
          <div class="report-detail__label">标题</div>
          <div class="report-detail__value">{{ detail.title }}</div>
          <div class="report-detail__label">级别</div>
          <div class="report-detail__value">
            {{ alertLabel(detail.level) }}
          </div>
          <div class="report-detail__label">关联商品</div>
          <div class="report-detail__value">
            {{ detail.product_name || '-' }}
          </div>
          <div class="report-detail__label">预警内容</div>
          <pre class="report-detail__pre">{{ detail.content }}</pre>
        </template>
        <div class="report-detail__label">{{ detailKind === 'reports' ? '生成时间' : '触发时间' }}</div>
        <div class="report-detail__value">{{ formatTime(detail.create_time) }}</div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import request from '@/utils/request'
import { useAlertUnread } from '@/composables/useAlertUnread'

const tableHeaderStyle = { backgroundColor: '#f8fafc', color: '#475569', fontWeight: 600 }
const route = useRoute()
const activeTab = ref(route.query.tab === 'alerts' ? 'alerts' : 'reports')
const isReportTab = computed(() => activeTab.value === 'reports')
const loading = ref(false), reading = ref(false)
const tableData = ref([]), total = ref(0), error = ref('')
const drawerVisible = ref(false), detail = ref(null), detailKind = ref('reports')
const params = reactive({ page: 1, pageSize: 10, keywords: '' })
const { unread: alertUnread, loading: summaryLoading, error: summaryError, refresh: refreshAlertUnread } = useAlertUnread()
let requestId = 0, disposed = false
const alertTabLabel = computed(() => alertUnread.value ? `预警通知（${alertUnread.value} 未读）` : '预警通知')

function formatTime(value) { return value ? value.replace('T', ' ') : '—' }
function riskLabel(value) {
  return { high: '高风险', low: '低风险' }[value] || '未知风险'
}
function alertLabel(value) {
  return { critical: '严重', warning: '提醒' }[value] || '未知等级'
}
function routeLabel(value) {
  return { sql: '结构化', structured: '结构化', semantic: '语义检索' }[value] || '—'
}
async function call(url, options) {
  const res = await request({ url, ...options })
  // HTTP成功仍可能返回业务失败，不能把失败响应当作列表或统计数据。
  if (res.code !== 200) throw new Error(res.message || '操作失败')
  return res.data
}
async function load() {
  // 搜索、翻页和切换页签共用序号，迟到响应不能覆盖新结果、总数或加载状态。
  const id = ++requestId
  loading.value = true
  error.value = ''
  try {
    const data = await call(`/api/ecommerce/${activeTab.value}`, { params: {
      page: params.page, page_size: params.pageSize, keywords: params.keywords.trim() || undefined
    } })
    if (id !== requestId) return
    tableData.value = data.list
    total.value = data.total
  } catch (err) {
    if (id === requestId) error.value = err.message || '读取失败'
  } finally {
    if (id === requestId) loading.value = false
  }
}
function handleTabChange() {
  params.page = 1
  params.keywords = ''
  tableData.value = []
  total.value = 0
  drawerVisible.value = false
  detail.value = null
  load()
}
watch(() => route.query.tab, (tab) => {
  const next = tab === 'alerts' ? 'alerts' : 'reports'
  // 仅查询参数切换到另一页签时重置列表，避免同一页签重复读取。
  if (next === activeTab.value) return
  activeTab.value = next
  handleTabChange()
})
function handleSearch() {
  params.page = 1
  drawerVisible.value = false
  load()
}
async function markRead(row) {
  // 已有写请求时拒绝再次提交；单条操作必须有编号，null专供批量操作。
  if (reading.value || (!row?.id && row !== null)) return
  const originRequest = requestId
  reading.value = true
  error.value = ''
  try {
    // null表示本人全部未读；不向批量接口传入当前页或搜索词。
    const data = await call(row ? `/api/ecommerce/alerts/${row.id}/read` : '/api/ecommerce/alerts/read-all', { method: 'post' })
    // 离开页面后保留已提交的已读结果，不再刷新已卸载的列表。
    // 即使离开报告页，已经成功的已读操作也必须刷新顶栏统计。
    const summaryRefresh = refreshAlertUnread(true)
    if (disposed) return
    ElMessage.success(row ? '已标记为已读' : `已标记 ${data.updated} 条为已读`)
    await Promise.all([load(), summaryRefresh])
  } catch (err) {
    // 已切换页签或搜索后，不把旧操作的局部错误写到新的列表。
    if (!disposed && originRequest === requestId) error.value = err.message || '标记失败'
  } finally {
    reading.value = false
  }
}
function handleReadOne(row) { return markRead(row) }
function handleReadAll() { return markRead(null) }
function openDetail(row) {
  // 抽屉使用独立快照和页签类型，不随列表刷新变成另一类详情。
  detail.value = { ...row }
  detailKind.value = activeTab.value
  drawerVisible.value = true
}
onBeforeUnmount(() => { disposed = true; requestId++ })
onMounted(() => { refreshAlertUnread(); load() })
</script>

<style scoped>
.report-detail__label {
  margin: 14px 0 4px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.report-detail__value {
  font-size: 14px;
  line-height: 1.6;
  color: var(--app-text);
  word-break: break-word;
}

.report-detail__pre {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius-sm);
  background: #f8fafc;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.report-detail { overflow-wrap: anywhere; }
.report-detail__pre { overflow-wrap: anywhere; }
.report-page__error { margin: 12px 0; }
.report-page :deep(.toolbar-row) { flex-wrap: wrap; }
@media (max-width: 640px) {
  .report-page :deep(.toolbar-row .search-input) { width: 100%; flex-basis: 100%; min-width: 0; }
  .report-page :deep(.toolbar-row > .el-button) { min-height: 44px; margin-left: 0; }
  .report-page :deep(.table-footer) { overflow-x: auto; }
  .report-page :deep(.el-tabs__item) { min-height: 44px; }
}
</style>
