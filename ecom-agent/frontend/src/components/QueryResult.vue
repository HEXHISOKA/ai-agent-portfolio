<template>
  <section class="query-result">
    <div class="result-heading">
      <strong>查询结果</strong>
      <span>{{ result.rows.length }} 条记录</span>
    </div>
    <el-table v-if="result.rows.length" :data="result.rows" max-height="360" stripe border>
      <el-table-column type="index" label="#" width="52" />
      <el-table-column
        v-for="column in result.columns"
        :key="column"
        :prop="column"
        :label="COLUMN_LABELS[column] || column"
        :min-width="column === 'content' || column === 'description' ? 320 : 150"
      >
        <template #default="{ row }">
          <span class="cell-text">{{ displayValue(row[column]) }}</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-else description="没有符合条件的数据，请检查查询条件" :image-size="64" />
    <p v-if="result.truncated" class="result-note">已达到本次查询的行数上限，可能还有更多记录。</p>
    <details v-if="result.sql" class="result-sql">
      <summary>查看执行 SQL</summary>
      <pre><code>{{ result.sql }}</code></pre>
    </details>
  </section>
</template>
<script setup>
defineProps({ result: { type: Object, required: true } })

// 常用字段显示中文；模型生成的其他统计别名保留原名称。
const COLUMN_LABELS = {
  id: '编号',
  similarity: '相似度', // 语义检索结果里的相似度列，值来自 similarity 字段。
  product_id: '商品编号',
  name: '商品名称',
  product_name: '商品名称',
  order_id: '订单编号',
  order_no: '订单号',
  rating: '评分',
  content: '评价内容',
  img: '图片路径',
  create_time: '创建时间',
  update_time: '更新时间',
  price: '售价',
  amount: '实付金额',
  quantity: '购买数量',
  category: '类目',
  description: '商品描述',
  stock: '库存',
  status: '订单状态',
  buyer_name: '买家',
  shelf_status: '上下架状态',
  review_status: '复核状态',
  order_count: '订单笔数',
  total_quantity: '购买件数',
  total_amount: '实付总金额'
}

/** 接收单元格值，返回可显示的文本；保留数字 0，空值显示为破折号。 */
function displayValue(value) {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}
</script>
<style scoped>
.query-result {
  margin-top: 12px;
  min-width: 0;
}
.result-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.result-heading strong {
  font-size: 14px;
}
.result-heading span,
.result-note {
  font-size: 12px;
  color: var(--app-text-secondary);
}
.cell-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.6;
}
.result-sql {
  margin-top: 12px;
  border: 1px solid var(--app-border);
  border-radius: 6px;
}
.result-sql summary {
  padding: 10px 12px;
  cursor: pointer;
  color: var(--app-text-secondary);
  font-size: 13px;
}
.result-sql pre {
  margin: 0;
  padding: 12px;
  overflow-x: auto;
  background: #f8fafc;
  font:
    12px/1.7 Consolas,
    monospace;
}
</style>
