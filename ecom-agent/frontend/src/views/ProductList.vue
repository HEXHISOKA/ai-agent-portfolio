<template>
  <div class="page-container">
    <div class="content-card">
      <div class="toolbar-row">
        <el-input
          class="search-input"
          placeholder="按商品名 / 类目搜索"
          v-model="params.keywords"
          clearable
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        />
        <el-button type="primary" :icon="Search" @click="handleSearch">查询</el-button>
      </div>

      <el-table
        :header-cell-style="tableHeaderStyle"
        :data="tableData"
        style="width: 100%"
        v-loading="loading"
        @row-click="openDetail"
      >
        <el-table-column prop="name" label="商品名称" min-width="140" show-overflow-tooltip />
        <el-table-column prop="category" label="类目" width="110" />
        <el-table-column prop="price" label="售价" width="90" />
        <el-table-column prop="stock" label="库存" width="80" />
        <el-table-column prop="shelf_status" label="上下架" width="100">
          <template #default="{ row }">
            <el-tag
              size="small"
              effect="light"
              round
              :type="row.shelf_status === 'on_sale' ? 'success' : 'info'"
            >
              {{ shelfLabel(row.shelf_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="review_status" label="复核状态" width="110">
          <template #default="{ row }">
            <el-tag
              size="small"
              effect="light"
              round
              :type="row.review_status === 'pending_review' ? 'warning' : 'success'"
            >
              {{ reviewStatusLabel(row.review_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip />
        <el-table-column prop="create_time" label="创建时间" width="170">
          <template #default="{ row }">
            <span class="time-text">{{ row.create_time }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click.stop="openDetail(row)">详情</el-button>
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

    <!-- 商品详情抽屉：把该商品的评价、订单与 Agent 产物串在一起 -->
    <el-drawer v-model="drawerVisible" title="商品详情" @close="closeDetail" size="min(540px, 100vw)">
      <div v-if="detailError" class="detail-error"><el-alert :title="detailError" type="error" :closable="false" /><el-button @click="openDetail({ id: detailId })">重试</el-button></div>
      <div v-else-if="detailLoading" class="detail-loading" v-loading="detailLoading"></div>
      <div v-else-if="detail" class="product-detail">
        <div class="product-detail__name">{{ detail.product.name }}</div>
        <div class="product-detail__tags">
          <el-tag size="small" effect="plain">{{ detail.product.category }}</el-tag>
          <el-tag
            size="small"
            effect="light"
            round
            :type="detail.product.shelf_status === 'on_sale' ? 'success' : 'info'"
          >
            {{ shelfLabel(detail.product.shelf_status) }}
          </el-tag>
          <el-tag
            size="small"
            effect="light"
            round
            :type="detail.product.review_status === 'pending_review' ? 'warning' : 'success'"
          >
            {{ reviewStatusLabel(detail.product.review_status) }}
          </el-tag>
        </div>

        <div class="product-detail__stats">
          <div class="product-detail__stat">
            <span class="product-detail__stat-label">售价</span>
            <span class="product-detail__stat-value">{{ formatMoney(detail.product.price) }}</span>
          </div>
          <div class="product-detail__stat">
            <span class="product-detail__stat-label">库存</span>
            <span class="product-detail__stat-value">{{ detail.product.stock }}</span>
          </div>
          <div class="product-detail__stat">
            <span class="product-detail__stat-label">评价数 / 平均分</span>
            <span class="product-detail__stat-value">
              {{ detail.review_summary.review_count }} / {{ detail.review_summary.avg_rating }}
            </span>
          </div>
          <div class="product-detail__stat">
            <span class="product-detail__stat-label">订单数 / 成交额</span>
            <span class="product-detail__stat-value">
              {{ detail.order_summary.order_count }} /
              {{ formatMoney(detail.order_summary.total_amount) }}
            </span>
          </div>
        </div>

        <div class="detail-note">订单数包含全部状态；成交额不含已取消订单。</div>
        <div class="product-detail__label">商品描述</div>
        <div class="product-detail__value">{{ detail.product.description }}</div>

        <div class="product-detail__label">
          评分分布（差评 {{ detail.review_summary.bad_count }} 条）
        </div>
        <ul class="rating-bars">
          <li
            v-for="bucket in detail.review_summary.rating_distribution"
            :key="bucket.rating"
            class="rating-bars__item"
          >
            <span class="rating-bars__star">{{ bucket.rating }} 星</span>
            <span class="rating-bars__track">
              <span
                class="rating-bars__fill"
                :class="`rating-bars__fill--${ratingTone(bucket.rating)}`"
                :style="{ width: barWidth(bucket.count) }"
              ></span>
            </span>
            <span class="rating-bars__count">{{ bucket.count }}</span>
          </li>
        </ul>

        <div class="product-detail__label">最近评价</div>
        <ul v-if="detail.recent_reviews.length" class="detail-list">
          <li v-for="review in detail.recent_reviews" :key="review.id" class="detail-item">
            <div class="detail-item__head">
              <el-tag
                size="small"
                effect="light"
                round
                :type="review.rating <= 2 ? 'danger' : review.rating >= 4 ? 'success' : 'warning'"
              >
                {{ review.rating }} 分
              </el-tag>
              <el-image
                v-if="review.img"
                class="review-thumb"
                :src="review.img"
                :alt="`${detail.product.name}评价晒图`"
                title="点击查看晒图"
                :preview-src-list="[review.img]"
                :initial-index="0"
                preview-teleported
                hide-on-click-modal
                fit="cover"
              />
              <span class="detail-item__time time-text">{{ review.create_time }}</span>
            </div>
            <div class="detail-item__text">{{ review.content }}</div>
          </li>
        </ul>
        <div v-else class="detail-empty">暂无评价</div>

        <div class="product-detail__label">
          最近订单
          <span v-if="detail.order_summary.last_order_time" class="detail-note">
            （最近一单 {{ detail.order_summary.last_order_time }}）
          </span>
        </div>
        <ul v-if="detail.recent_orders.length" class="detail-list">
          <li v-for="order in detail.recent_orders" :key="order.id" class="detail-item">
            <div class="detail-item__head">
              <span class="detail-item__title">{{ order.order_no }}</span>
              <el-tag size="small" effect="light" round>{{
                orderStatusLabel(order.status)
              }}</el-tag>
              <span class="detail-item__time time-text">{{ order.create_time }}</span>
            </div>
            <div class="detail-item__meta">
              {{ order.buyer_name }} · {{ formatMoney(order.amount) }} × {{ order.quantity }}
            </div>
          </li>
        </ul>
        <div v-else class="detail-empty">暂无订单</div>

        <div class="product-detail__label">本人相关诊断报告</div>
        <ul v-if="detail.reports.length" class="detail-list">
          <li v-for="report in detail.reports" :key="report.id" class="detail-item">
            <div class="detail-item__head">
              <el-tag
                size="small"
                effect="light"
                round
                :type="report.risk_level === 'high' ? 'danger' : 'success'"
              >
                {{ report.risk_level === 'high' ? '高风险' : '低风险' }}
              </el-tag>
              <span class="detail-item__time time-text">{{ report.create_time }}</span>
            </div>
            <div class="detail-item__text">{{ report.question }}</div>
          </li>
        </ul>
        <div v-else class="detail-empty">暂无本人相关报告</div>

        <div class="product-detail__label">本人相关预警通知</div>
        <ul v-if="detail.alerts.length" class="detail-list">
          <li v-for="alert in detail.alerts" :key="alert.id" class="detail-item">
            <div class="detail-item__head">
              <el-tag
                size="small"
                effect="light"
                round
                :type="alert.level === 'critical' ? 'danger' : 'warning'"
              >
                {{ alert.level === 'critical' ? '严重' : '提醒' }}
              </el-tag>
              <span class="detail-item__time time-text">{{ alert.create_time }}</span>
            </div>
            <div class="detail-item__text">{{ alert.content }}</div>
          </li>
        </ul>
        <div v-else class="detail-empty">暂无本人相关预警</div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
defineOptions({ name: 'ProductList' })
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { getProductPageList, getProductDetail } from '@/api/ecommerce'
const tableHeaderStyle = { backgroundColor: '#f8fafc', color: '#475569', fontWeight: 600 }
const params = reactive({ page: 1, pageSize: 10, keywords: '' })
const loading = ref(false)
const tableData = ref([])
const total = ref(0)
const drawerVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

function shelfLabel(v) {
  return v === 'on_sale' ? '在售' : v === 'off_shelf' ? '下架' : v || '-'
}

function reviewStatusLabel(v) {
  return v === 'pending_review' ? '待复核' : v === 'normal' ? '正常' : v || '-'
}

function orderStatusLabel(v) {
  const map = { paid: '已支付', shipped: '已发货', completed: '已完成', cancelled: '已取消' }
  return map[v] || v || '-'
}

function formatMoney(value) {
  const num = Number(value || 0)
  return `¥${num.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })}`
}

const maxRatingCount = computed(() =>
  Math.max(1, ...(detail.value?.review_summary?.rating_distribution || []).map((b) => b.count))
)

function barWidth(count) {
  return `${Math.round((count / maxRatingCount.value) * 100)}%`
}

function ratingTone(rating) {
  if (rating <= 2) return 'bad'
  if (rating >= 4) return 'good'
  return 'mid'
}

let latestRequestId = 0

// 搜索和翻页共用请求序号，只有最后发起的请求能更新当前列表。
const load = async () => {
  const requestId = ++latestRequestId
  loading.value = true
  try {
    const res = await getProductPageList({
      page: params.page,
      page_size: params.pageSize,
      keywords: params.keywords
    })
    if (requestId !== latestRequestId) return
    if (res.code === 200) {
      tableData.value = res.data?.list || []
      total.value = res.data?.total || 0
    }
  } catch {
    // 请求失败由统一拦截器提示，保留当前数据，下一次查询仍可继续。
  } finally {
    // 旧请求结束不能提前关闭新请求的加载状态。
    if (requestId === latestRequestId) loading.value = false
  }
}

const handleSearch = async () => {
  params.page = 1
  load()
}


const detailError = ref('')
const detailId = ref(null)
let latestDetailRequestId = 0
let disposed = false
// 切换详情、关闭抽屉和离页都会使旧请求失效，防止迟到响应显示到另一条记录上。
async function openDetail(row) {
  if (!row?.id || disposed) return
  const id = ++latestDetailRequestId
  detailId.value = row.id
  drawerVisible.value = true
  detailLoading.value = true
  detailError.value = ''
  detail.value = null
  try {
    const res = await getProductDetail(row.id)
    if (disposed || id !== latestDetailRequestId) return
    if (res.code !== 200) throw new Error(res.message || '详情读取失败')
    detail.value = res.data
  } catch (err) {
    if (!disposed && id === latestDetailRequestId) detailError.value = err.message || '详情读取失败'
  } finally {
    if (!disposed && id === latestDetailRequestId) detailLoading.value = false
  }
}
function closeDetail() {
  latestDetailRequestId++
  detailLoading.value = false
  detail.value = null
  detailError.value = ''
}
onBeforeUnmount(() => { disposed = true; latestDetailRequestId++; latestRequestId++ })
onMounted(load)
</script>
<style scoped>
.detail-loading {
  min-height: 200px;
}

.product-detail__name {
  font-size: 16px;
  font-weight: 600;
  color: var(--app-text);
}

.product-detail__tags {
  margin-top: 8px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.product-detail__stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 14px 0 4px;
}

.product-detail__stat {
  padding: 10px 12px;
  background: #f8fafc;
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius-sm);
}

.product-detail__stat-label {
  display: block;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.product-detail__stat-value {
  display: block;
  margin-top: 4px;
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text);
}

.product-detail__label {
  margin: 16px 0 6px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.product-detail__value {
  font-size: 14px;
  line-height: 1.7;
  color: var(--app-text);
  word-break: break-word;
}

.detail-note {
  color: var(--app-text-secondary);
}

.rating-bars {
  margin: 0;
  padding: 0;
  list-style: none;
}

.rating-bars__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
}

.rating-bars__star {
  width: 34px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.rating-bars__track {
  flex: 1;
  height: 8px;
  border-radius: 999px;
  background: #f1f5f9;
  overflow: hidden;
}

.rating-bars__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
}

.rating-bars__fill--bad {
  background: #ef4444;
}

.rating-bars__fill--mid {
  background: #94a3b8;
}

.rating-bars__fill--good {
  background: #10b981;
}

.rating-bars__count {
  width: 28px;
  text-align: right;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.detail-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.detail-item {
  padding: 10px 0;
  border-bottom: 1px dashed var(--app-border);
}

.detail-item:last-child {
  border-bottom: none;
}

.detail-item__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.detail-item__title {
  font-size: 13px;
  color: var(--app-text);
}

.detail-item__time {
  margin-left: auto;
  flex-shrink: 0;
}

.detail-item__text {
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--app-text);
  word-break: break-word;
}

.detail-item__meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.detail-empty {
  font-size: 12px;
  color: var(--app-text-secondary);
  padding: 4px 0;
}

.review-thumb {
  width: 44px;
  height: 44px;
  border-radius: 4px;
  cursor: pointer;
}

.detail-error { display: grid; gap: 12px; }
.detail-error .el-button { justify-self: start; }
.detail-item__head { flex-wrap: wrap; }
.detail-item__title, .order-detail__value { overflow-wrap: anywhere; min-width: 0; }
@media(max-width: 600px) { .product-detail__stats { grid-template-columns: minmax(0, 1fr); } }
</style>
