# TermPrep — 部署与更新指南

> 线上 Space：https://huggingface.co/spaces/dzha0118/termprep
> 本地远程：`hf`（Space）、`origin`（GitHub）。
> 拖拽上传用的干净文件集：`hf_deploy/`（由源码派生，已加入 `.gitignore`，不入库）。

---

## 核心结论：为什么拖拽上传会「旧文件残留」

HF Space 网页的「拖拽上传」只会**新增 / 覆盖**文件，**永远不会删除**文件。
所以只要拖拽上传，被改名或被删除的旧文件就会一直留在 Space 里。

> **这不是理论问题**：本项目实测时 Space 里已经积了 **266 个残留文件**——
> `backend/`、`frontend/`、`docs/`、`sources/`、`reports/`、`__pycache__/`、
> 根目录一整套重复的 `*.py`，甚至一个被误提交的 `.env`（值为占位符，已核对无真实密钥）。
> 用 git 推送一次性清干净了。

三个办法：

1. **`python deploy_hf.py`（最推荐）** —— 自动克隆 → 全量同步 `hf_deploy/` → 推送，
   多余文件一并删除。见方式一。
2. **手动 git push** —— 同样会同步删除，但把仓库整个 `master` 推上去会把测试脚本
   也带进 Space（见"Dockerfile 之外的注意事项"）。
3. 继续拖拽上传 —— 必须遵守「固定文件名覆盖」规则 + 定期手动删残留（方式二）。

---

## 方式一：`deploy_hf.py`（推荐）

```bash
python build_hf_bundle.py     # 1. 由源码生成干净文件集 hf_deploy/（63 个文件）
python deploy_hf.py --dry-run # 2. 预览：会同步哪些文件、清掉哪些残留
python deploy_hf.py           # 3. 真正部署（Space 自动重建）
```

脚本做的事：克隆 Space → **清空**（保留 `.git`）→ 写入 `hf_deploy/` 全部内容
→ 逐文件校验一致 → 提交 → 推送。所以 Space 上永远不会积累残留。

可用参数：

| 参数 | 说明 |
|---|---|
| `--repo dzha0118/termprep` | Space 仓库 id |
| `--message "..."` | 自定义提交信息 |
| `--proxy http://127.0.0.1:7892` | **本机系统代理时必须加**（见下） |
| `--dry-run` | 只看不改 |
| `--keep` | 保留临时克隆目录，便于排查 |

> ### ⚠️ 系统代理的坑（本次真实踩到）
> 本机开着 `127.0.0.1:7892` 的系统代理时，**Python / 浏览器能连外网，但 git 不会用系统代理**，
> 表现是 `Failed to connect to huggingface.co port 443 after 21000 ms`。
> `deploy_hf.py` 会从注册表自动读出代理并传给 git；手动推送则要自己加
> `-c http.proxy=http://127.0.0.1:7892`。

> - 首次推送若要求认证，用 HF Access Token 当密码：https://huggingface.co/settings/tokens
> - 身份没配的话脚本会用本仓库的 `user.name` / `user.email` 兜底。

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
