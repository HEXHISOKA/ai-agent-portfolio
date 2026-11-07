<template>
  <div class="page-container">
    <div class="content-card">
      <div class="toolbar-row">
        <el-input
          class="search-input"
          placeholder="按评价内容 / 商品名搜索"
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
      >
        <el-table-column prop="product_name" label="商品" min-width="140" show-overflow-tooltip />
        <el-table-column prop="rating" label="评分" width="80">
          <template #default="{ row }">
            <el-tag
              size="small"
              effect="light"
              round
              :type="row.rating <= 2 ? 'danger' : row.rating >= 4 ? 'success' : 'warning'"
            >
              {{ row.rating }} 分
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="content" label="评价内容" min-width="240" show-overflow-tooltip />
        <el-table-column prop="img" label="晒图" width="96">
          <template #default="{ row }">
            <el-image
              v-if="row.img"
              class="review-thumb"
              :src="row.img"
              :alt="`${row.product_name}评价晒图`"
              title="点击查看晒图"
              :preview-src-list="[row.img]"
              :initial-index="0"
              preview-teleported
              hide-on-click-modal
              fit="cover"
            >
              <template #error>
                <div class="review-thumb__error">缺图</div>
              </template>
            </el-image>
            <span v-else class="muted">无</span>
          </template>
        </el-table-column>
        <el-table-column prop="create_time" label="评价时间" width="170">
          <template #default="{ row }">
            <span class="time-text">{{ row.create_time }}</span>
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
  </div>
</template>

<script setup>
defineOptions({ name: 'ReviewList' })
import { Search } from '@element-plus/icons-vue'
import { getReviewPageList } from '@/api/ecommerce'
import { ref, reactive, onMounted } from 'vue'

const tableHeaderStyle = {
  backgroundColor: '#f8fafc',
  color: '#475569',
  fontWeight: 600
}

// 1-2 星视为差评，用红色标出
const ratingType = (rating) => {
  if (rating <= 2) return 'danger'
  if (rating === 3) return 'warning'
  return 'success'
}

const params = reactive({
  page: 1,
  pageSize: 10,
  keywords: ''
})
const loading = ref(false)
const tableData = ref([])
const total = ref(0)

let latestRequestId = 0

// 搜索和翻页共用请求序号，只有最后发起的请求能更新当前列表。
const load = async () => {
  const requestId = ++latestRequestId
  loading.value = true
  try {
    const res = await getReviewPageList({
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

onMounted(() => {
  load()
})
</script>

<style scoped>
.muted {
  color: var(--app-text-secondary);
  font-size: 13px;
}

.review-thumb {
  width: 52px;
  height: 52px;
  border-radius: 4px;
  cursor: pointer;
}

.review-thumb__error {
  width: 52px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: var(--app-text-secondary);
  background: #f1f5f9;
  border-radius: 4px;
}
</style>
