## 电商数据分析 Agent

基于 **FastAPI + Vue 3 + MySQL** 的电商数据分析课程项目，围绕商品、订单和评价数据，实现自然语言查询、SQL 校验、语义检索与流式结果展示。

本仓库用于同步课堂代码，随课程进度更新。**课程笔记和详细操作步骤在[课程网站](https://ai.codenice.cn/courses/ecom-analysis-agent)阅读。**

## 已包含能力

| 模块 | 能力 |
| --- | --- |
| 电商数据 | 商品、订单、评价的查询与页面展示 |
| 自然语言查询 | 调用聊天模型生成 SQL，校验查询结构，通过只读数据库连接执行 |
| 语义检索 | 商品与评价向量化、余弦相似度排序、Top K 候选及数据库回查 |
| 查询路由 | 根据编号、统计、明确条件和完整商品名称等规则选择 SQL 或语义检索 |
| 对话页面 | SSE 流式状态、查询摘要、结构化表格与执行 SQL 展示 |
| 基础功能 | 登录注册、JWT 认证、用户管理、个人信息、密码修改及文件上传 |

技术栈：Vue 3、Element Plus、Axios / Fetch、FastAPI、SQLAlchemy、MySQL、OpenAI 兼容接口、SQLGlot。

## 环境要求

- Node.js：按 `frontend/package.json` 的 `engines` 要求配置
- Python：本地验证使用 3.13.0，依赖版本见 `backend/requirements.txt`
- MySQL 8.0+

## 目录结构

```text
ecom-analysis-agent/
├── sql/
│   └── ecom-analysis-agent.sql  # 当前数据库的完整表结构与数据快照
├── backend/                 # FastAPI
│   ├── app/
│   │   ├── api/             # 路由
│   │   ├── services/        # 业务逻辑
│   │   ├── models/          # ORM
│   │   ├── schemas/         # 请求/响应模型
│   │   ├── common/          # 统一返回、异常
│   │   ├── dependencies/    # JWT 依赖
│   │   └── utils/
│   ├── uploads/             # 上传文件目录
│   ├── requirements.txt
│   ├── .env.example
│   └── .env                 # 本地配置（勿提交真实密钥）
├── frontend/                # Vue3
│   ├── src/
│   │   ├── api/
│   │   ├── views/
│   │   ├── layouts/
│   │   └── utils/
│   └── .env.development
└── README.md
```

## 1. 准备数据库

先在 MySQL 中创建空库（库名可自定，需与 `.env` 一致），例如：

```sql
CREATE DATABASE `ecom-analysis-agent` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

使用 Navicat、DBeaver、MySQL Workbench 等工具，在刚创建的数据库中打开并执行 `sql/ecom-analysis-agent.sql`，还原当前项目数据库的表结构与全部数据。

该文件直接从本机 `ecom-analysis-agent` 数据库导出，包含 `products`、`orders`、`reviews`、`users` 四张表，以及导出时的全部记录。表字段、索引、默认值、自增值和数据均按本机快照保留。

说明：

- 教学环境导入 `sql/ecom-analysis-agent.sql` 后，具备商品、订单、评价和演示账号数据。
- 后端启动时仍有 `Base.metadata.create_all`；若表已由 SQL 建好，不会覆盖已有数据
- 该文件包含四张表的 `DROP TABLE IF EXISTS` 与重建语句，重复导入会用快照覆盖现有表和数据，只在准备还原的数据库中执行。

## 2. 启动后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

在 `backend` 目录创建或编辑 `.env`：

```env
DATABASE_URL=mysql+pymysql://用户名:密码@127.0.0.1:3306/ecom-analysis-agent
JWT_SECRET_KEY=请换成足够长的随机字符串
JWT_EXPIRE_HOURS=24
JWT_ALGORITHM=HS256
```

使用自然语言查询还需配置 `QUERY_DATABASE_URL`、`LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`；语义检索还需配置 `EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY` 和 `EMBEDDING_MODEL`，并按课程笔记建立本地向量索引。`QUERY_DATABASE_URL` 使用只读账号，本地索引不随 Git 提交。

在项目根目录新开终端，启动后端：

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器访问：

- 健康检查：<http://127.0.0.1:8000/>
- 接口文档：<http://127.0.0.1:8000/docs>

VsCode Python相关的配置，搜索 打开  settings.json，设置文件保存格式化和自动导入：

```json
{
  "python.analysis.typeCheckingMode": "off",
  "redhat.telemetry.enabled": true,
  "python.analysis.autoImportCompletions": true,
  "python.languageServer": "Pylance",
  "python.analysis.extraPaths": ["./backend"],
  "editor.quickSuggestions": {
      "other": true,
      "comments": false,
      "strings": false
  },
  "[python]": {
    "editor.codeActionsOnSave": {
      "source.organizeImports": "explicit",
      "source.fixAll": "explicit"
    },
    "editor.defaultFormatter": "ms-python.black-formatter"
  },
}
```

## 3. 启动前端

```powershell
cd frontend
npm install
npm run dev
```

开发地址一般为 <http://127.0.0.1:5173>。

`frontend/.env.development` 默认：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

开发模式下，Vite 已将 `/api`、`/uploads` 代理到 `127.0.0.1:8000`，一般无需改前端代理配置。

VsCode配置，搜索 打开  settings.json，设置文件保存格式化：

```json
{
  "editor.formatOnSave": true,
  "editor.formatOnPaste": true,
 "[vue]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[javascript]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[json]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
}
```

## 4. 内置演示账号

导入 `sql/ecom-analysis-agent.sql` 后可用：

| 用户名 | 密码 | 角色 | 用途 |
| --- | --- | --- | --- |
| `admin` | `admin` | 管理员 | 登录后可见「用户管理」，可维护用户 |
| `aaa` | `123` | 普通用户 | 登录与普通用户操作演示 |
| `bbb`   | `123`   | 普通用户 | 同上，第二个普通用户账号 |

说明：

- 所有账号的密码已按当前 SQL 中的哈希校验通过，可直接登录
- 页面自行注册的用户角色固定为 `user`，不会自动成为管理员

## 5. 冒烟检查

按顺序确认：

1. 用 `admin` / `admin` 登录，侧栏出现「用户管理」
2. 查看用户列表，能看到 `admin`、`aaa`、`bbb`
3. 修改个人信息、上传头像
4. 修改密码后重新登录（演示完可改回，避免影响后续跟课）
5. 可选：再注册一个新账号，确认角色为普通用户

## 演示数据日期同步

种子数据的日期固定。查看最近 30 天的订单图表前，在 `backend` 目录预览并同步到本机当天：

```powershell
python -m scripts.sync_demo_dates
python -m scripts.sync_demo_dates --apply
```

脚本按相同天数平移商品、订单和评价的历史日期，保留时间间隔及业务字段；写入前自动保存原日期到 `backend/data/demo-date-backups/`。同一天重复运行不重复平移。同步后刷新首页即可，无需重建向量索引。仅用于课程演示库。

## 常见问题

| 现象 | 排查 |
| --- | --- |
| 后端启动报数据库连接错误 | 确认 MySQL 已启动、库已创建、`.env` 中 `DATABASE_URL` 账号密码正确 |
| 前端登录后接口 401 | 确认后端已启动；浏览器本地是否仍有过期 token，可清站点数据后重登 |
| 注册后没有「用户管理」 | 正常：新用户是 `user`；请用 `admin` 登录，或把该用户角色改为 `admin` |
| 导入 SQL 后登录失败 | 确认连的是导入过 `ecom-analysis-agent.sql` 的那个库；`admin` 密码为 `admin`；`aaa`/`bbb` 可先由管理员重置密码 |
| 上传头像失败 | 确认 `backend/uploads` 可写；文件类型在后端允许扩展名列表内 |
| 导入异常 / 应用起不来 | 先看启动终端完整报错；确认在 `backend` 目录、虚拟环境已激活，且依赖已按 `requirements.txt` 安装 |

## 说明

- `.env` 中的真实密码和 API Key 不提交到仓库，分享配置时使用占位内容。
- 课程笔记在网站阅读，`docs/`、AI 协作说明、本地依赖、运行数据和发布包均不纳入版本管理。
