<template>
  <el-container class="layout layout--admin">
    <el-aside width="240px" class="sidebar">
      <div class="brand">
        <div class="brand-icon">
          <el-icon :size="22"><DataAnalysis /></el-icon>
        </div>
        <span class="brand-text">电商分析 Agent</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        class="sidebar-menu"
        background-color="transparent"
        text-color="#94a3b8"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/manager/home">
          <el-icon><icon-menu /></el-icon>
          <span>系统首页</span>
        </el-menu-item>
        <el-menu-item index="/manager/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>数据分析对话</span>
        </el-menu-item>
        <el-menu-item index="/manager/history">
          <el-icon><Clock /></el-icon>
          <span>运行历史</span>
        </el-menu-item>
        <el-menu-item index="/manager/reports">
          <el-icon><Document /></el-icon>
          <span>诊断报告</span>
        </el-menu-item>
        <el-menu-item index="/manager/metrics">
          <el-icon><TrendCharts /></el-icon>
          <span>观测看板</span>
        </el-menu-item>
        <el-menu-item index="/manager/product">
          <el-icon><Goods /></el-icon>
          <span>商品数据</span>
        </el-menu-item>
        <el-menu-item index="/manager/order">
          <el-icon><List /></el-icon>
          <span>订单数据</span>
        </el-menu-item>
        <el-menu-item index="/manager/review">
          <el-icon><ChatLineSquare /></el-icon>
          <span>评价数据</span>
        </el-menu-item>
        <el-menu-item index="/manager/semantic">
          <el-icon><Search /></el-icon><span>语义检索</span>
        </el-menu-item>
        <el-menu-item v-if="userInfo?.role === 'admin'" index="/manager/user">
          <el-icon><User /></el-icon>
          <span>用户管理</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container class="main-wrap">
      <el-header class="topbar" height="60px">
        <div class="topbar-left">
          <span class="topbar-title">{{ currentTitle }}</span>
        </div>
        <div class="topbar-right">
          <el-popover v-model:visible="alertVisible" placement="bottom-end" :width="340" trigger="click" popper-class="alert-popover" @show="loadRecent">
            <template #reference>
              <button class="topbar-bell" :aria-label="unread === null ? '预警通知，未读数暂不可用' : `预警通知，${unread}条未读`">
                <el-badge :value="unread" :hidden="!unread" :max="99"><el-icon :size="20"><Bell /></el-icon></el-badge>
              </button>
            </template>
            <div class="alert-panel">
              <div class="alert-panel__head"><strong>预警通知</strong><span class="alert-panel__unread">{{ unread === null ? '未读数待获取' : `${unread}条未读` }}</span></div>
              <div v-if="summaryError" role="alert">{{ summaryError }} <el-button link @click="refreshUnread()">重试未读统计</el-button></div>
              <div v-if="recentError" role="alert">{{ recentError }} <el-button link @click="loadRecent">重试最近预警</el-button></div>
              <div v-else-if="recentLoading">正在读取预警…</div>
              <template v-else>
                <ul v-if="recentAlerts.length" class="alert-panel__list">
                  <li v-for="item in recentAlerts" :key="item.id">
                    <button class="alert-panel__item" :title="`${item.title}：${item.content}`" @click="goAlerts">
                      <el-tag size="small" effect="light" round :type="{ critical: 'danger', warning: 'warning' }[item.level] || 'info'">{{ { critical: '严重', warning: '提醒' }[item.level] || '未知等级' }}</el-tag>
                      <span class="alert-panel__content">{{ item.content }}</span>
                      <time class="alert-panel__time" :datetime="item.create_time || undefined" :title="item.create_time?.replace('T', ' ') || '—'">{{ shortTime(item.create_time) }}</time>
                    </button>
                  </li>
                </ul>
                <div v-else class="alert-panel__empty">暂无预警</div>
              </template>
              <div class="alert-panel__foot"><el-button class="alert-panel__all" link type="primary" @click="goAlerts">查看全部预警</el-button></div>
            </div>
          </el-popover>
          <span class="topbar-user">{{ userInfo?.name || userInfo?.username }}</span>
          <el-dropdown trigger="click" placement="bottom-end" @command="handleCommand">
            <span class="topbar-dropdown-trigger">
              <el-avatar :size="36" class="topbar-avatar" :src="userInfo?.avatar || undefined">
                {{ avatarLetter }}
              </el-avatar>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><UserFilled /></el-icon>
                  个人信息
                </el-dropdown-item>
                <el-dropdown-item command="password">
                  <el-icon><Lock /></el-icon>
                  修改密码
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  Menu as IconMenu,
  User,
  DataAnalysis,
  UserFilled,
  Lock,
  SwitchButton,
  Goods,
  ShoppingCart,
  ChatDotRound,
  ChatLineRound,
  Bell,
  Clock,
  Document,
  TrendCharts,
  List,
  ChatLineSquare,
  Search
} from '@element-plus/icons-vue'
import router from '@/router'
import { logout } from '@/utils/auth'
import { useUser } from '@/utils/user'
import request from '@/utils/request'
import { getToken } from '@/utils/auth'
import { useAlertUnread } from '@/composables/useAlertUnread'

const route = useRoute()
const { userInfo } = useUser()
const { unread, error: summaryError, refresh: refreshUnread, reset: resetUnread } = useAlertUnread()
const alertVisible = ref(false), recentAlerts = ref([]), recentLoading = ref(false), recentError = ref('')
let recentSequence = 0, disposed = false, pollTimer
async function loadRecent() {
  refreshUnread()
  const id = ++recentSequence, token = getToken()
  recentLoading.value = true
  recentError.value = ''
  const current = () => !disposed && id === recentSequence && token === getToken()
  try {
    const res = await request.get('/api/ecommerce/alerts', { params: { page: 1, page_size: 5 } })
    if (res.code !== 200) throw new Error(res.message || '最近预警读取失败')
    if (current()) recentAlerts.value = res.data.list
  } catch (err) {
    if (current()) recentError.value = err.message || '最近预警读取失败'
  } finally { if (current()) recentLoading.value = false }
}
function shortTime(value) {
  // 完整时间保留在提示中，紧凑行只显示月日和时分；不把本地时间转成UTC。
  return value ? String(value).replace('T', ' ').slice(5, 16) : '—'
}
function goAlerts() {
  alertVisible.value = false
  router.push({ path: '/manager/reports', query: { tab: 'alerts' } })
}
function refreshVisible() {
  // 隐藏页面暂停轮询；回到页面或打开浮层时再读取最新数据。
  if (document.visibilityState !== 'visible') return
  if (alertVisible.value) loadRecent()
  else refreshUnread()
}
const LAYOUT_LOCK_CLASS = 'admin-layout-lock'

const TITLE_MAP = {
  '/manager/home': '系统首页',
  '/manager/semantic': '语义检索',
  '/manager/reports': '诊断报告',
  '/manager/history': '运行历史',
  '/manager/metrics': '观测看板',
  '/manager/user': '用户管理',
  '/manager/chat': '数据分析对话',
  '/manager/product': '商品数据',
  '/manager/order': '订单数据',
  '/manager/review': '评价数据',
  '/manager/profile': '个人信息',
  '/manager/password': '修改密码'
}

const activeMenu = computed(() => {
  if (route.path.startsWith('/manager/profile') || route.path.startsWith('/manager/password')) {
    return ''
  }
  return route.path
})

const currentTitle = computed(() => TITLE_MAP[route.path] || '电商分析 Agent')

const avatarLetter = computed(() => {
  const name = userInfo.value?.name || userInfo.value?.username || '?'
  return String(name).slice(0, 1).toUpperCase()
})

onMounted(() => {
  document.documentElement.classList.add(LAYOUT_LOCK_CLASS)
  refreshVisible()
  pollTimer = setInterval(refreshVisible, 60000)
  document.addEventListener('visibilitychange', refreshVisible)
})

onUnmounted(() => {
  disposed = true
  recentSequence++
  clearInterval(pollTimer)
  document.removeEventListener('visibilitychange', refreshVisible)
  resetUnread()
  document.documentElement.classList.remove(LAYOUT_LOCK_CLASS)
})

const handleCommand = (command) => {
  if (command === 'profile') {
    router.push('/manager/profile')
  } else if (command === 'password') {
    router.push('/manager/password')
  } else if (command === 'logout') {
    resetUnread()
    alertVisible.value = false
    logout()
    router.push('/login')
  }
}
</script>

<style>
.alert-popover.el-popover { max-width: calc(100vw - 24px); box-sizing: border-box; }
</style>
<style scoped>
.topbar-bell { width: 44px; height: 44px; border: 0; background: transparent; color: var(--app-text-secondary); cursor: pointer; display: grid; place-items: center; flex-shrink: 0; }
.topbar-bell:hover { color: var(--app-primary); }
.topbar-bell:focus-visible { outline: 2px solid #2563eb; border-radius: 8px; }
.alert-panel { font-size: 13px; max-height: calc(100dvh - 100px); overflow-y: auto; }
.alert-panel__head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--app-border); color: var(--app-text); }
.alert-panel__unread { color: #ef4444; font-size: 12px; }
.alert-panel__list { margin: 0; padding: 0; list-style: none; max-height: 260px; overflow-y: auto; }
.alert-panel__item { display: flex; align-items: center; gap: 8px; min-height: 44px; width: 100%; padding: 8px 0; border: 0; border-bottom: 1px dashed var(--app-border); background: transparent; cursor: pointer; text-align: left; color: var(--app-text); }
.alert-panel__list li:last-child .alert-panel__item { border-bottom: none; }
.alert-panel__item:hover .alert-panel__content { color: var(--app-primary); }
.alert-panel__item:focus-visible { outline: 2px solid var(--app-primary); outline-offset: -2px; }
.alert-panel__item :deep(.el-tag) { flex-shrink: 0; }
.alert-panel__content { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.alert-panel__time { flex-shrink: 0; color: var(--app-text-secondary); font-size: 12px; white-space: nowrap; }
.alert-panel__empty { padding: 14px 0; text-align: center; color: var(--app-text-secondary); font-size: 12px; }
.alert-panel__foot { padding-top: 8px; border-top: 1px solid var(--app-border); text-align: right; }
.alert-panel__all { min-height: 44px; }

.layout {
  width: 100%;
  max-width: 100vw;
  height: 100vh;
  max-height: 100vh;
  overflow: hidden;
  background: var(--app-bg);
}

.sidebar {
  background: var(--app-sidebar-bg);
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.brand {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.brand-icon {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--app-primary-light), var(--app-primary-dark));
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
}

.brand-text {
  font-size: 16px;
  font-weight: 600;
  color: #f8fafc;
  white-space: nowrap;
}

.sidebar-menu {
  border-right: none;
  padding: 12px 10px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.sidebar-menu :deep(.el-menu-item) {
  height: 44px;
  line-height: 44px;
  margin-bottom: 4px;
  border-radius: var(--app-radius-sm);
}

.sidebar-menu :deep(.el-menu-item:hover) {
  background: var(--app-sidebar-hover) !important;
}

.sidebar-menu :deep(.el-menu-item.is-active) {
  background: var(--app-sidebar-active) !important;
  font-weight: 500;
}

.main-wrap {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.topbar {
  flex-shrink: 0;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--app-space-page);
  background: var(--app-card-bg);
  border-bottom: 1px solid var(--app-border);
  box-shadow: var(--app-shadow);
}

.topbar-left {
  min-width: 0;
  flex: 1;
  overflow: hidden;
}

.topbar-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.topbar-dropdown-trigger {
  display: inline-flex;
  align-items: center;
  outline: none;
  cursor: pointer;
}

.topbar-user {
  font-size: 14px;
  color: var(--app-text-secondary);
}

.topbar-avatar {
  background: linear-gradient(135deg, var(--app-primary-light), var(--app-primary-dark));
  color: #fff;
  font-weight: 600;
}

.main-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  --el-main-padding: 0;
  padding: var(--app-space-page);
  overflow: hidden;
}

/* 窄屏保留图标导航 */
@media (max-width: 900px) {
  .sidebar {
    width: 72px !important;
  }
  .brand {
    padding: 0;
    justify-content: center;
  }
  .brand-text {
    display: none;
  }
  .sidebar-menu {
    padding: 12px 8px;
  }
  .sidebar-menu :deep(.el-menu-item) {
    justify-content: center;
    padding: 0 !important;
  }
  .sidebar-menu :deep(.el-menu-item .el-icon) {
    margin-right: 0;
  }
  .sidebar-menu :deep(.el-menu-item > span) {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  .topbar {
    padding: 0 16px;
  }
}
@media (max-width: 500px) {
  .topbar-user {
    display: none;
  }
  .topbar-right {
    gap: 8px;
  }
}
</style>

<style>
html.admin-layout-lock,
html.admin-layout-lock body,
html.admin-layout-lock #app {
  width: 100%;
  height: 100%;
  overflow: hidden;
}

html.admin-layout-lock .layout--admin .main-content {
  overflow-x: hidden;
  overflow-y: auto;
}
</style>
