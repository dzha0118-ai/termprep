---
title: TermPrep
emoji: 📝
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
---

# TermPrep — 译前术语准备与翻译编排平台

> 把"一键跑到底"的翻译流水线，改成译员能在关键节点**暂停、裁决、修正、再继续**的
> Human-in-the-loop 工作流。术语提取、中英互译、术语库、翻译记忆、可复现的个案数据包。

**在线体验：** https://dzha0118-termprep.hf.space

- 后端：FastAPI（单进程 gunicorn + uvicorn worker）
- 前端：**单个 `web/static/index.html`**（原生 JS，零构建、零 npm）
- 存储：SQLite（术语库 / 翻译记忆 / 裁决日志 / 检查点会话）
- 部署：Docker（Hugging Face Space 或任意容器平台）

---

## ✨ 核心特性

### 两种流水线模式

| 模式 | 端点 | 说明 |
|---|---|---|
| **检查点模式**（默认，推荐） | `POST /api/pipeline/start` → 译员裁决 → `POST /api/pipeline/{id}/resume` | 跑到术语提取后**暂停**，译员逐条裁决后再继续 ④术语表 → ⑤全文翻译 → ⑥术语库 → ⑦报告 → ⑧导出 |
| 全自动模式 | `POST /api/pipeline` / `POST /api/pipeline/v2` | 一键跑到底，中途不可干预 |

检查点模式支持五种裁决：**采纳 / 剔除 / 修正 / 合并 / 新增**。
被剔除的术语绝不入库；修正项以译员给定值入库；合并项只保留一条并记录 `merged_from` 溯源；
新增项走裁决日志（无需管理员令牌）。

裁决表里每一条候选**在检查点阶段就已填好建议译文**（AI 批量 → 小批量重试 → 免费引擎 → 单条 AI
四通道补齐，任何通道失败都不阻断检查点）。没有译文的行会给出「＋ 补译」入口——看不到译文就没法裁。

裁决完成后进入 **④→⑧ 分步视图**：术语表 / 全文翻译 / 入库术语库 / 报告 / 导出，
一次展开一张，底部「下一步」逐格推进，顶部阶段条**只标到当前这一步**（不会提前全绿）。
每一步都能拿到真实产物：报告 `.md`、术语表 `.csv` / `.json`、个案数据包 `.zip`。
刷新页面不会丢——结果存在会话里，`has_result` 为真时直接还原整套分步视图。

### 术语提取（多层校验）

```
分析 → 规则提取（jieba/YAKE/C-Value/英文 NP 分块）→ 片段过滤 → 可选 AI 语义复核 → 排序
```

- **谓词框架词表**剔除动宾/子句片段（`考察译员`、`study examines post-editing`…），
  同时不误杀 `术语提取`、`联合化疗` 这类同构的真术语
- **英文规则名词短语分块**：jieba 把英文一律标 `eng`，词性通道对英文无效，故单列一套
- **可解释评分**：`score = 0.4·频次 + 0.3·词表命中 + 0.2·长度 + 0.1·形态`，并返回 `score_breakdown`
- **可选 AI 语义复核**（P4）：一次批量调用剔掉"形态合法但非术语"的描述性短语；
  未配置 AI、调用失败或回复不可解析时**完全不改动候选**
- 内部评测集：4 篇文本 · 57 个金标准术语，`eval_term_extraction.py --check` 作为回归门禁

### 检索与翻译

- **多源并发聚合**：有道 + Wikipedia + AI 同时检索，合并去重、`also_from` 跨源印证，词典源优先
- 词典搜索支持逐条**采用 / 弃用**，采用结果在 resume 时优先作为该术语译文
- 术语翻译**四通道补齐**（批量 → 小批量重试 → 免费引擎 → 单条 AI），并容忍 8 种回复格式
- 术语高亮**与实际译文对齐**：`target_span` 必须能在译文中原样找到，否则不展示（宁可少标，不可错标）

### 数据留存与可复现（论文级）

容器本地 SQLite 会随重建清空，因此提供**可下载的个案数据包**：

```bash
GET /api/cases/bundle?session_id=<id>&format=zip
```

内含：`manifest`（版本/确定性说明/隐私说明）、`input`（完整原文 + sha256）、`parameters`
（运行参数 + provider/model，**绝无 key**）、`terms_recomputed`（用存储的入参**重跑**规则层）、
`decisions`（裁决日志）、`termbases`（术语库全量）、`tm`，以及 `decisions.csv` / `terms.csv` / `README.txt`。

> 规则层确定性 → 可用数据包复现；AI 阶段不确定 → 记录当次观测值，`manifest` 明确标注。

### 安全

- 写库 / 上传 / 调试端点需 `X-Admin-Token`（未配置令牌时 **fail-closed 503**）
- 未配置令牌不泄露任何信息：`/api/debug/env` 收敛为 `{ai_configured, provider}`
- 上传：扩展名白名单 + 单文件 10MB + 请求体上限 + 文件名穿越清洗

---

## 🚀 快速开始（本地）

```bash
python -m venv .venv && . .venv/Scripts/activate     # Windows
pip install -e .

# 配置（可选；不配也能用免费引擎）
cp .env.example .env    # 填写 TERMPREP_ADMIN_TOKEN / TERMPREP_AI_API_KEY

python -m uvicorn web_entry:app --host 127.0.0.1 --port 8672
# 打开 http://127.0.0.1:8672
```

## 🐳 部署到 Hugging Face Space

Space 需为 **Docker** 类型。仓库里带两个部署工具（都不随镜像发布）：

```bash
python build_hf_bundle.py      # 由源码生成干净文件集 hf_deploy/（63 个文件）
python deploy_hf.py --dry-run  # 预览会同步/清理哪些文件
python deploy_hf.py            # 全量同步并推送，Space 自动重建
```

> ⚠️ **不要用网页拖拽上传更新。** HF 的拖拽只会新增/覆盖，**永不删除**——
> 本项目实测时 Space 里已累积 **266 个残留文件**（`backend/`、`frontend/`、`docs/`、
> `__pycache__/`、根目录重复的 `*.py`，以及一个被误提交的 `.env`）。
> `deploy_hf.py` 先清空再写入，并逐文件校验一致后才推送。

> ⚠️ `Dockerfile` 里刻意用 **`gunicorn -w 1`**：检查点会话/裁决日志/翻译记忆都在容器内 SQLite，
> 多 worker 会导致 `/start` 与 `/resume` 落到不同进程而报「session not found」。
> 另设 `--timeout 300`，避免长 AI 调用被默认 30s 超时杀掉。

> 💡 本机开着系统代理时，**git 不会自动走系统代理**（表现为 `Failed to connect to huggingface.co port 443`）。
> `deploy_hf.py` 会从注册表自动读取并传给 git；手动推送需加 `-c http.proxy=http://127.0.0.1:7892`。

### 需要配置的 Secrets

| 名称 | 必填 | 说明 |
|---|---|---|
| `TERMPREP_ADMIN_TOKEN` | ✅ | 写库/上传/调试端点令牌，不配置则这些端点返回 503 |
| `TERMPREP_AI_API_KEY` | 建议 | AI 密钥（术语翻译、全文翻译、AI 术语复核） |
| `TERMPREP_AI_PROVIDER` | | `deepseek` / `kimi` / `openai` / `ollama` / `custom` |
| `TERMPREP_AI_MODEL` | | 模型名 |
| `TERMPREP_AI_BASE_URL` | | OpenAI-compatible 端点 |
| `TERMPREP_YOUDAO_KEY` / `_SECRET` | | 有道词典（可选，双源检索） |
| `TERMPREP_DATA_DIR` | 建议 | 指向持久目录（如 `/data`），否则容器重建会清空数据 |

**自检：** `GET /api/health` 返回版本、引擎健康、术语库规模、令牌配置状态、
数据留存状态（`storage.persistent`）与检查点存储自检（`hitl.self_test`）。

---

## 🔌 API 速览

| 分类 | 端点 |
|---|---|
| 分析/提取 | `POST /api/analyze` · `POST /api/extract` |
| 检索/关联 | `POST /api/search` · `POST /api/associate` · `POST /api/termbase/lookup` |
| 翻译 | `POST /api/translate` · `POST /api/translate/upload`(admin) |
| **检查点** | `POST /api/pipeline/start` · `GET /api/pipeline/{id}` · `POST /api/pipeline/{id}/resume` |
| **检查点下载** | `GET /api/pipeline/{id}/report` · `GET /api/pipeline/{id}/export/{csv,json}` |
| 全自动 | `POST /api/pipeline` · `POST /api/pipeline/v2` · `GET /api/pipeline/templates` |
| 裁决日志 | `POST /api/decisions/record` · `GET /api/decisions/{list,summary,export}` |
| **个案数据包** | `GET /api/cases/bundle` · `POST /api/cases/snapshot` · `GET /api/cases/snapshots` |
| 术语库 | `GET /api/db/{list,info}` · `POST /api/term/{add(admin),search}` |
| 运维 | `GET /api/health` · `GET /api/sources` · `GET /api/agents/engines` · `GET /api/debug/env`(admin) |

完整的 OpenAPI 文档：`/docs`（Swagger UI）与 `/openapi.json`。

---

## 🧪 测试

全部为本地可跑的回归套件（桩引擎，不消耗付费额度）：

| 套件 | 覆盖 |
|---|---|
| `verify_hitl.py` | 检查点全链路（44 项）：start → 剔除/修正/合并/新增 → resume，逐项核对**真实术语库** |
| `verify_frontend_ui.py` | 前端静态一致性（34 项）：id 唯一、`getElementById` 目标存在、内联处理函数已定义、后端字段有渲染、下载按钮指向真实接口（**零依赖**） |
| `verify_ui_e2e.js` | UI 端到端（40 项，可选）：jsdom 加载真实页面驱动真实接口，点「下一步」并**真的下载**报告/CSV/JSON/ZIP（需 `npm i --no-save jsdom`） |
| `verify_hitl_storage.py` | 检查点存储的**多进程并发**稳定性（4 进程 × 60 次读写） |
| `verify_acceptance.py` | 安全与功能验收（40 项） |
| `verify_pipeline_fixes.py` | 流水线步骤/全文翻译回归（29 项） |
| `verify_term_verifier.py` | AI 术语复核的降级与保守匹配（24 项） |
| `verify_case_bundle.py` | **清库后仅凭数据包复现**（45 项） |
| `eval_term_extraction.py --check` | 术语提取质量回归门禁 |
| `live_test_space.py` | 线上 Space **只读**体检（42 项） |

```bash
python verify_hitl.py
python verify_frontend_ui.py
python eval_term_extraction.py --check
```

---

## 📁 目录结构

```
.
├── Dockerfile                 # 单 worker gunicorn + 300s 超时
├── web_entry.py               # ASGI 入口（gunicorn web_entry:app）
├── pyproject.toml             # 依赖
├── termprep/                  # 后端包
│   ├── web/server.py          # FastAPI 全部端点
│   ├── web/security.py        # 鉴权 + 上传校验
│   ├── pipeline.py            # 8 步流水线（支持 preset / on_event / step_status）
│   ├── extractor.py           # 多层术语提取 + 片段过滤 + 可解释评分
│   ├── analyzer.py            # 语言/领域/难度分析
│   ├── searcher.py            # 多源并发聚合检索
│   ├── translator.py          # 翻译与高亮对齐
│   ├── hitl_store.py          # 检查点会话（SQLite，多进程安全）
│   ├── decision_log.py        # 裁决日志 + 会话输入留存
│   ├── export_bundle.py       # 可复现个案数据包
│   ├── storage.py             # 数据目录解析 + SQLite 连接（busy_timeout）
│   ├── agents/ engines/ sources/ services/ rag/ interfaces/
│   └── ...
├── web/static/index.html      # 前端（单文件，无构建）
└── verify_*.py / eval_*       # 回归套件
```

---

## 📝 变更摘要（本轮优化）

- **安全**：写/上传端点鉴权、上传校验、debug 端点信息收敛
- **提取**：片段过滤 + 英文 NP 分块 + 可解释评分；精确率 35.2% → 63.8%，片段泄漏 23 → 0
- **检索**：双源并发聚合（不再被 AI 短路），`definition` 具备实质内容
- **高亮**：`target_span` 与译文强对齐，杜绝误导
- **领域**：新增 `academic` / `translation` / `humanities`，扩表 + 词边界匹配
- **流水线**：真实 `step_status`、模板真正生效、全文翻译可见、SSE 真推送
- **HITL**：检查点机制 + 页面阶段条 + 五种裁决 + 会话刷新可恢复
- **数据留存**：裁决日志 + 可复现个案数据包；修正数据目录静默回退
- **修复**：多 worker 导致检查点会话丢失（改单 worker + 读路径纯读 + busy_timeout）

详细的逐项说明与验收记录见 [`OPTIMIZATION_REPORT.md`](OPTIMIZATION_REPORT.md)。
