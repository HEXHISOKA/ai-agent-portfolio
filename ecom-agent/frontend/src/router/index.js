import { getToken } from '@/utils/auth'
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/manager/home' },
    {
      path: '/manager',
      name: 'Layout',
      component: () => import('@/layouts/Layout.vue'),
      children: [
        { path: 'home', name: 'Home', component: () => import('@/views/Home.vue') },
        { path: 'semantic', name: 'SemanticSearch', component: () => import('@/views/SemanticSearch.vue') },
        { path: 'history', name: 'History', component: () => import('@/views/History.vue') },
        { path: 'metrics', name: 'Metrics', component: () => import('@/views/Metrics.vue') },
        { path: 'reports', name: 'ReportList', component: () => import('@/views/ReportList.vue') },
        { path: 'profile', name: 'Profile', component: () => import('@/views/Profile.vue') },
        { path: 'password', name: 'Password', component: () => import('@/views/Password.vue') },
        { path: 'user', name: 'User', component: () => import('@/views/User.vue') },
        {
          path: 'product',
          name: 'ProductList',
          component: () => import('@/views/ProductList.vue')
        },
        {
          path: 'order',
          name: 'OrderList',
          component: () => import('@/views/OrderList.vue')
        },
        {
          path: 'review',
          name: 'ReviewList',
          component: () => import('@/views/ReviewList.vue')
        },
        {
          path: 'chat',
          name: 'Chat',
          component: () => import('@/views/Chat.vue')
        }
      ]
    },
    { path: '/login', name: 'Login', component: () => import('@/views/Login.vue') },
    { path: '/register', name: 'Register', component: () => import('@/views/Register.vue') }
  ]
})

// 路由守卫：验证token
router.beforeEach((to, from) => {
  const token = getToken()

  if (to.path === '/login' || to.path === '/register') {
    return true
  } else {
    return token ? true : '/login'
  }
})

export default router
