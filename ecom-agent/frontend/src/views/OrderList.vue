<template>
  <div class="page-container">
    <div class="content-card">
      <div class="toolbar-row">
        <el-input
          class="search-input"
          placeholder="按订单号 / 买家 / 商品名搜索"
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
        <el-table-column prop="order_no" label="订单号" min-width="140" show-overflow-tooltip />
        <el-table-column prop="product_name" label="商品" min-width="140" show-overflow-tooltip />
        <el-table-column prop="buyer_name" label="买家" width="100" />
        <el-table-column prop="quantity" label="数量" width="70" />
        <el-table-column prop="amount" label="实付" width="90" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="light" round :type="statusTag(row.status)">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="create_time" label="下单时间" width="170">
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

    <!-- 订单详情抽屉：订单本身 + 这笔订单的评价 -->
    <el-drawer v-model="drawerVisible" title="订单详情" @close="closeDetail" size="min(480px, 100vw)">
      <div v-if="detailError" class="detail-error"><el-alert :title="detailError" type="error" :closable="false" /><el-button @click="openDetail({ id: detailId })">重试</el-button></div>
      <div v-else-if="detailLoading" class="detail-loading" v-loading="detailLoading"></div>
      <div v-else-if="detail" class="order-detail">
        <div class="order-detail__no">{{ detail.order.order_no }}</div>
        <div class="order-detail__tags">
          <el-tag size="small" effect="light" round :type="statusTag(detail.order.status)">
            {{ statusLabel(detail.order.status) }}
          </el-tag>
        </div>

        <div class="order-detail__list">
          <div class="order-detail__row">
            <span class="order-detail__label">商品</span>
            <span class="order-detail__value">{{ detail.order.product_name || '-' }}</span>
          </div>
          <div class="order-detail__row">
            <span class="order-detail__label">买家</span>
            <span class="order-detail__value">{{ detail.order.buyer_name }}</span>
          </div>
          <div class="order-detail__row">
            <span class="order-detail__label">数量 / 实付</span>
            <span class="order-detail__value">
              {{ detail.order.quantity }} 件 / {{ formatMoney(detail.order.amount) }}
            </span>
          </div>
          <div class="order-detail__row">
            <span class="order-detail__label">下单时间</span>
            <span class="order-detail__value time-text">{{ detail.order.create_time }}</span>
          </div>
        </div>

        <div class="order-detail__section">这笔订单的评价</div>
        <div v-if="detail.review" class="order-detail__review">
          <div class="order-detail__review-head">
            <el-tag
              size="small"
              effect="light"
              round
              :type="
                detail.review.rating <= 2
                  ? 'danger'
                  : detail.review.rating >= 4
                    ? 'success'
                    : 'warning'
              "
            >
              {{ detail.review.rating }} 分
            </el-tag>
            <span class="order-detail__review-time time-text">
              {{ detail.review.create_time }}
            </span>
          </div>
          <div class="order-detail__review-text">{{ detail.review.content }}</div>
          <el-image
            v-if="detail.review.img"
            class="review-thumb"
            :src="detail.review.img"
            :alt="`${detail.order.product_name}评价晒图`"
            title="点击查看晒图"
            :preview-src-list="[detail.review.img]"
            :initial-index="0"
            preview-teleported
            hide-on-click-modal
            fit="cover"
          >
            <template #error>
              <div class="review-thumb__error">缺图</div>
            </template>
          </el-image>
        </div>
        <div v-else class="order-detail__empty">该订单暂无评价</div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
defineOptions({ name: 'OrderList' })
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { getOrderPageList, getOrderDetail } from '@/api/ecommerce'
const tableHeaderStyle = { backgroundColor: '#f8fafc', color: '#475569', fontWeight: 600 }
const params = reactive({ page: 1, pageSize: 10, keywords: '' })
const loading = ref(false)
const tableData = ref([])
const total = ref(0)
const drawerVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

function statusLabel(v) {
  const map = {
    paid: '已支付',
    shipped: '已发货',
    completed: '已完成',
    cancelled: '已取消'
  }
  return map[v] || v || '-'
}

function statusTag(v) {
  const map = {
    paid: '',
    shipped: 'warning',
    completed: 'success',
    cancelled: 'info'
  }
  return map[v] || 'info'
}

function formatMoney(value) {
  const num = Number(value || 0)
  return `¥${num.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })}`
}

let latestRequestId = 0

// 搜索和翻页共用请求序号，只有最后发起的请求能更新当前列表。
const load = async () => {
  const requestId = ++latestRequestId
  loading.value = true
  try {
    const res = await getOrderPageList({
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
    const res = await getOrderDetail(row.id)
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

.order-detail__no {
  font-size: 16px;
  font-weight: 600;
  color: var(--app-text);
}

.order-detail__tags {
  margin-top: 8px;
}

.order-detail__list {
  margin-top: 14px;
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius-sm);
  overflow: hidden;
}

.order-detail__row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 9px 12px;
  font-size: 13px;
}

.order-detail__row + .order-detail__row {
  border-top: 1px solid var(--app-border);
}

.order-detail__label {
  width: 76px;
  flex-shrink: 0;
  color: var(--app-text-secondary);
}

.order-detail__value {
  color: var(--app-text);
  word-break: break-word;
}

.order-detail__section {
  margin: 18px 0 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.order-detail__review {
  padding: 12px;
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius-sm);
  background: #f8fafc;
}

.order-detail__review-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.order-detail__review-time {
  font-size: 12px;
}

.order-detail__review-text {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--app-text);
  word-break: break-word;
}

.order-detail__empty {
  padding: 12px;
  font-size: 13px;
  color: var(--app-text-secondary);
  border: 1px dashed var(--app-border);
  border-radius: var(--app-radius-sm);
}

.review-thumb {
  display: block;
  width: 72px;
  height: 72px;
  margin-top: 10px;
  border-radius: 4px;
  cursor: pointer;
}

.review-thumb__error {
  width: 72px;
  height: 72px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: var(--app-text-secondary);
  background: #f1f5f9;
  border-radius: 4px;
}

.detail-error { display: grid; gap: 12px; }
.detail-error .el-button { justify-self: start; }
.detail-item__head { flex-wrap: wrap; }
.detail-item__title, .order-detail__value { overflow-wrap: anywhere; min-width: 0; }
@media(max-width: 600px) { .product-detail__stats { grid-template-columns: minmax(0, 1fr); } }
</style>
