<template>
  <div class="auth-page">
    <div class="auth-panel auth-panel--brand">
      <div class="brand-content">
        <div class="brand-logo">
          <el-icon :size="36"><DataAnalysis /></el-icon>
        </div>
        <h1>电商分析 Agent</h1>
        <p>
          自然语言提问 · 结构化 / 语义检索 · 多智能体协作<br />
          FastAPI · Vue 3 · Element Plus · LangGraph
        </p>
      </div>
    </div>
    <div class="auth-panel auth-panel--form">
      <el-card class="auth-card" shadow="never">
        <h2 class="title">登录</h2>
        <p class="subtitle">请输入您的账号信息</p>
        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-width="0"
          size="large"
          @keyup.enter="login"
        >
          <el-form-item prop="username">
            <el-input v-model="form.username" placeholder="用户名" :prefix-icon="User" />
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="密码"
              :prefix-icon="Lock"
              show-password
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" class="submit-btn" :loading="loadingValue" @click="login">
              登录
            </el-button>
          </el-form-item>
          <div class="footer-link">
            还没有账号？<router-link to="/register">立即注册</router-link>
          </div>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { User, Lock, DataAnalysis } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import router from '@/router'
import { loginApi } from '@/api/auth'
import { useUser } from '@/utils/user'

const { saveLoginData } = useUser()

const formRef = ref()
const form = reactive({
  username: '',
  password: ''
})
const loadingValue = ref(false)

const rules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

const login = async () => {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loadingValue.value = true
  try {
    const res = await loginApi(form)
    if (res.code === 200) {
      saveLoginData(res.data)
      ElMessage.success('登录成功')
      await router.push('/manager/home')
    }
  } finally {
    loadingValue.value = false
  }
}
</script>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
}

.auth-panel {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.auth-panel--brand {
  background: linear-gradient(145deg, #0f172a 0%, var(--app-primary-dark) 50%, var(--app-primary) 100%);
  color: #fff;
  padding: 48px;
}

.brand-content {
  max-width: 400px;
}

.brand-logo {
  width: 64px;
  height: 64px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.15);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 24px;
  backdrop-filter: blur(8px);
}

.brand-content h1 {
  font-size: 32px;
  font-weight: 700;
  margin-bottom: 16px;
}

.brand-content p {
  font-size: 15px;
  line-height: 1.8;
  opacity: 0.85;
}

.auth-panel--form {
  background: var(--app-bg);
  padding: 24px;
}

.auth-card {
  width: 100%;
  max-width: 400px;
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius);
  padding: 8px 4px 4px;
}

.title {
  font-size: 24px;
  font-weight: 600;
  color: var(--app-text);
}

.subtitle {
  margin: 8px 0 28px;
  font-size: 14px;
  color: var(--app-text-secondary);
}

.submit-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
}

.footer-link {
  text-align: center;
  font-size: 14px;
  color: var(--app-text-secondary);
}

.footer-link a {
  color: var(--app-primary);
  text-decoration: none;
  font-weight: 500;
}

.footer-link a:hover {
  text-decoration: underline;
}

@media (max-width: 768px) {
  .auth-panel--brand {
    display: none;
  }
}
</style>
