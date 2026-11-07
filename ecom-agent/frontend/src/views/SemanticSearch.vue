<template>
  <div class="page-container semantic">
    <div class="content-card">
      <div class="semantic__title">语义检索调试</div>
      <div class="semantic__sub">跳过查询路由，直接检索现有商品与评价索引。余弦距离越小越相似，相似度 = 1 − 距离；Top K 候选不保证相关，也不代表正确率。</div>
      <div class="semantic__toolbar">
        <el-input v-model="query" class="semantic__input" aria-label="检索内容" placeholder="例如：跑鞋脱胶 / 耳机有杂音 / 洗衣液味道" clearable maxlength="500" @keyup.enter="handleEnter" />
        <el-select v-model="topK" class="semantic__topk" aria-label="候选条数" @change="handleSearch">
          <el-option v-for="n in [3, 5, 8, 10, 20]" :key="n" :label="`Top ${n}`" :value="n" />
        </el-select>
        <el-button type="primary" :loading="loading" :disabled="!query.trim()" @click="handleSearch">检索</el-button>
      </div>
      <div class="semantic__examples">
        <span class="semantic__examples-label">试试：</span>
        <el-button v-for="example in examples" :key="example" class="semantic__example" size="small" round @click="runExample(example)">{{ example }}</el-button>
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" />
    <div v-if="result" class="content-card" :aria-busy="loading">
      <div class="panel-head">
        <span class="panel-title">检索结果：{{ result.query }}</span>
        <span class="panel-sub">命中 {{ result.hit_count }} 条 · 向量库共 {{ result.collection_size }} 条文档</span>
      </div>
      <p class="semantic__sub">相似度 ≥ {{ result.related_min_similarity }} 标为相关候选，沿用当前语义候选筛选规则；此页保留全部 Top K 候选供对照。</p>
      <p v-if="result.skipped_count" class="semantic__sub">已跳过 {{ result.skipped_count }} 条原文不存在的候选，请重建索引。</p>
      <ul v-if="result.hits.length" class="hit-list">
        <li v-for="(hit, index) in result.hits" :key="hit.id" class="hit-item">
          <div class="hit-item__head">
            <span class="hit-item__rank">#{{ index + 1 }}</span>
            <el-tag size="small" effect="light" round :type="hit.metadata.doc_type === 'product' ? 'warning' : 'info'">{{ hit.metadata.doc_type === 'product' ? '商品' : '评价' }}</el-tag>
            <span v-if="hit.metadata.rating" class="hit-item__rating">{{ '★'.repeat(hit.metadata.rating) }}{{ '☆'.repeat(5 - hit.metadata.rating) }}</span>
            <span class="hit-item__product">{{ hit.metadata.name }}</span>
            <span class="hit-item__tags">
              <el-tag v-if="hit.metadata.has_image" size="small" effect="plain" type="success">含晒图</el-tag>
              <span class="hit-item__distance" :class="hit.related ? 'is-related' : 'is-unrelated'">距离 {{ formatDistance(hit.distance) }}</span>
            </span>
          </div>
          <div class="hit-item__text" v-html="renderMarkdown(hit.text)"></div>
          <div class="hit-item__meta">来源{{ hit.metadata.doc_type === 'product' ? '商品描述' : '买家评价' }} · 相似度 {{ hit.similarity.toFixed(4) }} · {{ hit.related ? '相关候选' : '低于相关阈值' }}</div>
          <el-alert v-if="hit.index_stale" class="hit-item__stale" title="原文或图片已变更，距离仍基于旧索引，请重建索引。此处展示当前可核验内容。" type="warning" :closable="false" />
        </li>
      </ul>
      <el-empty v-else description="没有命中可回查的文档" :image-size="60" />
    </div>
    <div v-else class="content-card semantic__placeholder">
      <el-empty :description="loading ? '正在检索…' : '输入一句话，看看向量库会捞回什么'" :image-size="70" />
    </div>
  </div>
</template>
<script setup>
import { onBeforeUnmount, ref } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { searchSemantic } from '@/api/semantic'

defineOptions({ name: 'SemanticSearch' })
const examples = ['跑鞋脱胶', '耳机有杂音', '洗了缩水', '包装破损']
const query = ref(''), topK = ref(5), result = ref(null), loading = ref(false), error = ref('')
let requestId = 0, disposed = false
function formatDistance(value) { return Number.isFinite(value) ? value.toFixed(3) : '—' }
function renderMarkdown(value) {
  return DOMPurify.sanitize(marked.parse(value || '', { async: false, breaks: true }))
}
async function handleSearch() {
  const text = query.value.trim()
  if (!text || disposed) return
  const id = ++requestId
  loading.value = true
  error.value = ''
  try {
    const data = await searchSemantic(text, topK.value)
    // 搜索词、示例与Top K共用序号，旧响应不能覆盖最新结果或加载状态。
    if (!disposed && id === requestId) result.value = data
  } catch (err) {
    if (!disposed && id === requestId) error.value = err.message || '检索失败，请重试'
  } finally {
    if (!disposed && id === requestId) loading.value = false
  }
}
function handleEnter(event) {
  // 中文输入法确认候选词时不提交，完成输入后再按回车检索。
  if (!event.isComposing && event.keyCode !== 229) handleSearch()
}
function runExample(value) { query.value = value; handleSearch() }
onBeforeUnmount(() => { disposed = true; requestId++ })
</script>
<style scoped>

.semantic {
  display: flex;
  flex-direction: column;
  gap: var(--app-space-gap);
}

.semantic__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--app-text);
}

.semantic__sub {
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--app-text-secondary);
}

.semantic__toolbar {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}

.semantic__input {
  max-width: 460px;
}

.semantic__topk {
  width: 110px;
}

.semantic__examples {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.semantic__examples-label {
  font-size: 12px;
  color: var(--app-text-secondary);
}

.semantic__example {
  cursor: pointer;
}

.semantic__placeholder {
  padding: 24px 0;
}

.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
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

.hit-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.hit-item {
  padding: 12px 0;
  border-bottom: 1px dashed var(--app-border);
}

.hit-item:last-child {
  border-bottom: none;
}

.hit-item__head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.hit-item__rank {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-secondary);
}

.hit-item__rating {
  font-size: 13px;
  color: #f59e0b;
  letter-spacing: 1px;
}

.hit-item__product {
  font-size: 13px;
  color: var(--app-text);
}

.hit-item__tags {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.hit-item__distance {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  background: #f1f5f9;
  color: var(--app-text-secondary);
}

.hit-item__distance.is-related {
  background: #dcfce7;
  color: #15803d;
}

.hit-item__distance.is-unrelated {
  background: #fee2e2;
  color: #b91c1c;
}

.hit-item__text {
  margin: 8px 0 4px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--app-text);
  word-break: break-word;
}

/* 渲染出来的 Markdown 压一下默认间距，避免命中列表被撑得过松 */
.hit-item__text :deep(p) {
  margin: 6px 0;
}

.hit-item__text :deep(p:first-child) {
  margin-top: 0;
}

.hit-item__text :deep(p:last-child) {
  margin-bottom: 0;
}

.hit-item__text :deep(ul),
.hit-item__text :deep(ol) {
  margin: 6px 0;
  padding-left: 20px;
}

.hit-item__text :deep(li) {
  margin: 2px 0;
}

.hit-item__text :deep(h1),
.hit-item__text :deep(h2),
.hit-item__text :deep(h3),
.hit-item__text :deep(h4) {
  margin: 10px 0 4px;
  font-size: 14px;
}

.hit-item__text :deep(strong) {
  font-weight: 600;
}

.hit-item__meta {
  font-size: 12px;
  color: var(--app-text-secondary);
}
.panel-head, .semantic__toolbar { flex-wrap: wrap; }
.semantic__input { min-width: 0; flex: 1; }
.semantic__topk { width: 110px; flex-shrink: 0; }
.semantic__examples :deep(.el-button + .el-button) { margin-left: 0; }
.semantic__title, .panel-title, .hit-item__product, .hit-item__meta { overflow-wrap: anywhere; }
.hit-item__stale { margin-top: 8px; }
.hit-item__text :deep(pre), .hit-item__text :deep(table) { max-width: 100%; overflow-x: auto; }
@media(max-width: 640px) {
  .semantic__input { flex-basis: 100%; max-width: none; }
  .semantic__toolbar :deep(.el-button), .semantic__examples :deep(.el-button) { min-height: 44px; }
  .hit-item__tags { margin-left: 0; flex-wrap: wrap; }
}
</style>
