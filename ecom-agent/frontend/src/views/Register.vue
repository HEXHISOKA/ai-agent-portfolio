<template>
  <div class="auth-page">
    <el-card class="auth-card" shadow="hover">
      <h2 class="title">注册</h2>
      <p class="subtitle">创建电商分析 Agent 账号</p>
      <el-form
        ref="formRef"
        :rules="rules"
        :model="form"
        label-width="0"
        size="large"
        @keyup.enter="register"
      >
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="请输入账号" :prefix-icon="User" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            type="password"
            v-model="form.password"
            placeholder="请输入密码"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>
        <el-form-item prop="confirmPassword">
          <el-input
            type="password"
            v-model="form.confirmPassword"
            placeholder="请确认密码"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            class="submit-btn"
            :loading="loadingValue"
            @click="register"
          >
            注册
          </el-button>
        </el-form-item>
        <div class="footer-link">
          已有账号？<router-link to="/login">返回登录</router-link>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { User, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { registerApi } from '@/api/auth'
import router from '@/router'

const form = reactive({
  username: '',
  password: '',
  confirmPassword: ''
})

const loadingValue = ref(false)
const formRef = ref()

const validatePass = (rule, value, callback) => {
  if (value === '') {
    callback(new Error('请确认密码'))
  } else if (value !== form.password) {
    callback(new Error('两次密码输入不一致'))
  } else {
    callback()
  }
}

const rules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码长度 6-64 位', trigger: 'blur' }
  ],
  confirmPassword: [{ validator: validatePass, trigger: 'blur' }]
}

const register = async () => {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loadingValue.value = true
  try {
    const res = await registerApi(form)
    if (res.code === 200) {
      ElMessage.success('注册成功')
      await router.push('/login')
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
  align-items: center;
  justify-content: center;
  background: var(--app-bg);
  padding: 24px;
}

.auth-card {
  width: 100%;
  max-width: 420px;
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius);
  padding: 8px 4px 4px;
}

.title {
  font-size: 24px;
  font-weight: 600;
  color: var(--app-text);
  text-align: center;
}

.subtitle {
  margin: 8px 0 28px;
  font-size: 14px;
  color: var(--app-text-secondary);
  text-align: center;
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
</style>
