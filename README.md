---
title: TermPrep
emoji: 📝
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
---

# TermPrep v2

AI 驱动的术语管理与翻译工作台。

## 架构

```
termprep/
├── frontend/        # Vue 3 + Vite + TypeScript + Pinia + Element Plus
├── backend/         # FastAPI + SQLAlchemy + PostgreSQL + Celery
├── docker-compose.yml
└── .env
```

## 快速开始（本地开发）

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek / OAuth2 密钥
```

### 2. 启动服务

```bash
docker-compose up --build
```

访问：
- 前端：http://localhost:5173
- 后端 API：http://localhost:8000/api/v1
- API 文档：http://localhost:8000/docs

### 3. 数据库迁移（首次）

```bash
docker-compose exec backend alembic revision --autogenerate -m "init"
docker-compose exec backend alembic upgrade head
```

## 功能

- 🤖 AI 术语提取（DeepSeek / Kimi / OpenAI / Ollama）
- 🌐 中英互译 + 中英混合翻译
- 🔍 词典搜索 + AI 关联术语
- 📚 个人词库与项目管理
- ⚡ 翻译流水线（Celery 异步）
- 🔐 GitHub / Google OAuth2 登录
- 🌙 深色模式

## 部署到 Hugging Face Space

1. 创建 Space，选择 Docker 模板。
2. 在 Space 的 Settings → Secrets 中添加：
   - `DATABASE_URL`（外部 Postgres，如 Supabase / Neon）
   - `REDIS_URL`（外部 Redis，如 Upstash）
   - `SECRET_KEY`
   - `AI_API_KEY`
   - `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`（可选）
   - `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`（可选）
3. 推送代码。

> 前端构建后会输出到 `backend/app/static/`，FastAPI 会自动 serve。

## API 路由

| 路由 | 说明 |
|------|------|
| `GET /api/v1/auth/me` | 当前用户 |
| `GET /api/v1/auth/github/authorize` | GitHub 登录 |
| `GET /api/v1/auth/google/authorize` | Google 登录 |
| `POST /api/v1/analyze` | 文本分析 |
| `POST /api/v1/translate` | 文本翻译 |
| `POST /api/v1/translate/upload` | 文件翻译 |
| `POST /api/v1/extract` | 术语提取 |
| `POST /api/v1/search` | 词典搜索 |
| `POST /api/v1/associate` | 关联术语 |
| `POST /api/v1/pipeline` | 流水线 |
| `GET /api/v1/tasks/{id}` | 异步任务状态 |
| `GET/POST/PUT/DELETE /api/v1/terms` | 词库管理 |
| `GET/POST/PUT/DELETE /api/v1/projects` | 项目管理 |
| `GET /api/v1/engines` | 引擎健康状态 |

## 技术栈

- **前端**：Vue 3, Vite, TypeScript, Pinia, vue-router, Element Plus, Axios
- **后端**：FastAPI, SQLAlchemy, Alembic, PostgreSQL, Celery, Redis
- **AI**：DeepSeek / OpenAI / Kimi / Ollama（OpenAI-compatible）
- **部署**：Docker, Hugging Face Space

## 从 v1 迁移

v1 的前端单文件 `web/static/index.html` 和旧的 `termprep/web/server.py` 已被保留，但不再维护。新的入口为 `backend/app/main.py` 和 `frontend/`。
