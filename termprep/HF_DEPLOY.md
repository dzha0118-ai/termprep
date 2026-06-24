# Hugging Face Space 部署指南

## ⚠️ 安全警告：不要提交 API Key 到 Git！

你的 `.env` 文件现在已被 `.gitignore` 排除，但请确认：

```bash
# 检查 Git 状态，确保 .env 未被跟踪
git status

# 如果 .env 已被跟踪，从 Git 中移除但保留本地文件
git rm --cached .env
```

---

## 1. HF Space 配置 Secrets

1. 打开你的 HF Space：`https://huggingface.co/spaces/<your-username>/<your-space>`
2. 点击 **Settings → Secrets**
3. 添加以下环境变量：

| Secret Name | Value |
|-------------|-------|
| `TERMPREP_AI_PROVIDER` | `kimi` |
| `TERMPREP_AI_API_KEY` | `sk-kimi-...` |
| `TERMPREP_AI_MODEL` | `moonshot-v1-128k` |
| `TERMPREP_AI_BASE_URL` | `https://api.moonshot.cn/v1` |
| `TERMPREP_AI_TEMPERATURE` | `0.3` |

HF Space 启动时，这些环境变量会自动注入到容器中，`config.py` 会自动读取。

---

## 2. HF Space 部署步骤

### 方法 A：Git 推送（推荐）

```bash
# 1. 确保 .env 不在 Git 中
git rm --cached .env 2>/dev/null

# 2. 提交代码（不包含 .env）
git add .
git commit -m "Add TermPrep multi-agent support"

# 3. 推送至 HF Space
git remote add hf https://huggingface.co/spaces/<your-username>/<your-space>
git push hf main
```

### 方法 B：直接上传 ZIP

在 HF Space 的 Files 页面上传，但 **不要上传 .env**。

---

## 3. HF Space 目录结构要求

```
app/
├── termprep/           # 整个 termprep 包
│   ├── __init__.py
│   ├── ...
│   └── web/
│       ├── __init__.py
│       └── server.py
├── app.py              # HF Space 入口（见下方）
├── requirements.txt  # 依赖
└── .gitignore
```

### `app.py`（HF Space 入口文件）

```python
"""Hugging Face Space entry point."""
import sys
import os

# HF Space uses port 7860
from termprep.web.server import start_server

if __name__ == "__main__":
    start_server(host="0.0.0.0", port=7860)
```

### `requirements.txt`

```
fastapi
uvicorn
pydantic
click
rich
requests
jieba
openpyxl
python-docx
```

---

## 4. 验证部署

部署后访问：`https://<your-username>-<your-space>.hf.space`

打开前端 → 粘贴文本 → 开启 AI 增强 → 运行流水线

---

## 5. 多人协作 / 公开 Space 的安全建议

| 场景 | 做法 |
|------|------|
| **公开 Space** | 只通过 HF Secrets 注入 key，前端不暴露 key |
| **团队共享** | 在 HF Space 中配置 Secrets，团队成员无需本地 .env |
| **本地开发** | 使用 `.env` 文件，已被 `.gitignore` 排除 |
| **泄露应急** | 在 Kimi 控制台撤销 API key，重新生成 |

---

## 6. 检查清单（部署前必读）

- [ ] `.env` 不在 `git status` 中
- [ ] `.gitignore` 已包含 `.env`
- [ ] HF Space Secrets 已配置所有环境变量
- [ ] 测试流水线成功运行
- [ ] 确认 API key 仅存在于 HF Secrets，不泄露到 Git

