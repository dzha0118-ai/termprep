# TermPrep — 部署与更新指南

> 线上 Space：https://huggingface.co/spaces/dzha0118/termprep
> 本地远程：`hf`（Space）、`origin`（GitHub）。
> 拖拽上传用的干净文件集：`hf_deploy/`（由源码派生，已加入 `.gitignore`，不入库）。

---

## 核心结论：为什么拖拽上传会「旧文件残留」

HF Space 网页的「拖拽上传」只会**新增 / 覆盖**文件，**永远不会删除**文件。
所以只要拖拽上传，被改名或被删除的旧文件就会一直留在 Space 里。

两个解决办法：

1. **git push（推荐，一条命令根治）** —— git 会同步删除，不留任何旧文件。
2. 继续拖拽上传，但必须遵守「固定文件名覆盖」规则 + 手动清理。

---

## 方式一：git push（推荐）

```bash
git status                 # 1. 查看改动
git add -A
git commit -m "HITL 检查点 + 术语提取重构 + 可复现数据包"
git push hf master         # 2. 推送到 Space，自动触发 Docker 重建
```

推送后 HF 会用 `Dockerfile` 重新构建，日志在 Space 的 **Settings → Logs**。

> - 首次推送若要求认证，用 HF Access Token 当密码：https://huggingface.co/settings/tokens
> - 本地从未提交过的话，先设一次身份：
>   `git config --global user.name "你的名字"` 与 `git config --global user.email "你的邮箱"`

---

## 方式二：拖拽上传（网页 Files 页）

### 用 `hf_deploy/` 整个文件夹拖上去

`hf_deploy/` 是专为拖拽准备好的文件集：只含运行必需文件，没有 `__pycache__`、
没有临时目录、没有 `.db`，`data/` 里只有 `.gitkeep`。整个文件夹直接拖到 Space 的
**Files** 页即可。

重新生成（源码改动后执行）：

```bash
python build_hf_bundle.py     # 若不存在，用 --include 手工挑选：Dockerfile web_entry.py
                              # pyproject.toml requirements.txt termprep/ web/ README.md data/.gitkeep
```

### 铁律：文件名不变，只覆盖同名文件

| 改了哪里 | 拖哪个文件 |
|---------|-----------|
| 前端界面 / 样式 / 逻辑 | `web/static/index.html`（唯一前端文件，无构建） |
| 后端接口 / 服务 | 对应的 `termprep/.../*.py` |
| 依赖 / 启动方式 | `requirements.txt`、`pyproject.toml`、`Dockerfile`、`web_entry.py` |

需要删除某个文件时，在 HF **Files 页 → 选中文件 → ⋯ → Delete** 手动删。

---

## 必须配置的 Secrets

| 名称 | 必填 | 说明 |
|---|---|---|
| `TERMPREP_ADMIN_TOKEN` | ✅ | 写库/上传/调试端点令牌；不配置则这些端点一律 503 |
| `TERMPREP_AI_API_KEY` | 建议 | AI 密钥（术语翻译、全文翻译、AI 术语复核） |
| `TERMPREP_DATA_DIR` | 建议 | 指向持久目录（如 `/data`），否则容器重建清空数据 |

API Key 只放 HF 的 **Settings → Secrets**，绝不进 git（`.env` 已被 `.gitignore` 排除）。

---

## 部署后验证

1. 打开 `https://dzha0118-termprep.hf.space`，硬刷新（`Ctrl+Shift+R`）。
2. `GET /api/health` 检查四项：
   - `admin_token_configured` 应为 `true`
   - `storage.persistent` 应为 `true`（说明持久存储生效）
   - `hitl.backend` 应指向 SQLite 且 `hitl.self_test.ok = true`
   - `termbases` 数量与预期一致
3. 跑一次**检查点模式**流水线：`/api/pipeline/start` 拿到 `session_id` 后，
   `/api/pipeline/{id}` 反复查询应**稳定 200**（不再出现 404）。
4. DevTools → Network 确认 `index.html` 响应头有 `Content-Encoding: gzip`。

---

## 已知运维雷区（不要改）

- **`Dockerfile` 必须 `gunicorn -w 1`**：检查点会话、裁决日志、翻译记忆都在容器内
  SQLite，多 worker 会让 `/start` 与 `/resume` 落到不同进程 → 「session not found」。
- **`--timeout 300`**：长 AI 调用会被 gunicorn 默认 30s 超时杀掉。
- **`data/` 必须挂持久存储**：否则每次重建都会清空术语库。
  即便如此，论文级复现请用 `/api/cases/bundle` 导出的数据包（自带原文与参数）。
