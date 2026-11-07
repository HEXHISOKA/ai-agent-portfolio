<template>
  <div class="page-container page-container--center">
    <div class="content-card">
      <el-form
        ref="formRef"
        :rules="rules"
        :model="form"
        label-width="80px"
        v-loading="loading"
      >
        <el-form-item label="头像">
          <el-upload
            class="avatar-uploader"
            :http-request="handleFileUpload"
            :show-file-list="false"
            accept="image/jpeg,image/png,image/gif,image/webp"
            :before-upload="beforeAvatarUpload"
          >
            <img v-if="form.avatar" :src="form.avatar" class="avatar" alt="个人头像" />
            <el-icon v-else class="avatar-uploader-icon"><Plus /></el-icon>
          </el-upload>
        </el-form-item>
        <el-form-item label="账号">
          <el-input disabled v-model="form.username" placeholder="请输入账号" />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="请输入名称" />
        </el-form-item>
        <el-form-item label="角色">
          <el-input disabled v-model="roleLabel" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="请输入邮箱" />
        </el-form-item>
        <el-form-item label="手机号" prop="phone">
          <el-input v-model="form.phone" placeholder="请输入手机号" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="handleSubmit">保存修改</el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { getUserInfo, updateUserInfo } from '@/api/user'
import { uploadFileApi } from '@/api/file'
import { ref, reactive, onMounted, computed } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { useUser } from '@/utils/user'
import { ElMessage } from 'element-plus'

const { updateUser } = useUser()

const loading = ref(false)
const submitting = ref(false)
const formRef = ref()
const form = reactive({
  username: '',
  name: '',
  role: '',
  email: '',
  phone: '',
  avatar: ''
})

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  email: [{ type: 'email', message: '邮箱格式错误', trigger: 'blur' }],
  phone: [{ pattern: /^1[3-9]\d{9}$/, message: '手机号格式错误', trigger: 'blur' }]
}

const roleLabel = computed(() => {
  return form.role === 'admin' ? '管理员' : form.role === 'user' ? '运营' : '未知角色'
})

const beforeAvatarUpload = (file) => {
  const imageTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
  if (!imageTypes.includes(file.type)) {
    ElMessage.error('只能上传图片')
    return false
  }
  if (file.size / 1024 / 1024 > 2) {
    ElMessage.error('上传图片大小不能超过2M')
    return false
  }
  return true
}

const handleFileUpload = async ({ file }) => {
  try {
    const res = await uploadFileApi(file)
    if (res.code === 200) {
      form.avatar = res.data?.url
    }
  } catch {}
}

const loadUserInfo = async () => {
  loading.value = true
  try {
    const res = await getUserInfo()
    if (res.code === 200) {
      Object.assign(form, res.data)
    }
  } finally {
    loading.value = false
  }
}

// 发送请求更新用户信息
const handleSubmit = async () => {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    const res = await updateUserInfo(form)
    if (res.code === 200) {
      updateUser(res.data)
      ElMessage.success('更新成功')
    }
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  loadUserInfo()
})
</script>

<style scoped>
.avatar-uploader .avatar {
  width: 120px;
  height: 120px;
  display: block;
  object-fit: cover;
}
</style>
<style>
.avatar-uploader .el-upload {
  border: 1px dashed var(--app-border);
  border-radius: var(--app-radius-sm);
  cursor: pointer;
  position: relative;
  overflow: hidden;
  background: #f8fafc;
  transition: var(--el-transition-duration-fast);
}
.avatar-uploader .el-upload:hover {
  border-color: var(--el-color-primary);
  background: #f0f7ff;
}
.el-icon.avatar-uploader-icon {
  font-size: 28px;
  color: #8c939d;
  width: 120px;
  height: 120px;
  text-align: center;
}
</style>
