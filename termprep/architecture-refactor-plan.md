# TermPrep 后端架构分析与优化方案

## 一、现有架构全景

```
termprep/
├── __init__.py, __main__.py          # 包入口
├── cli.py                             # CLI (Click)
├── web/server.py                      # FastAPI Web 后端
├── pipeline.py                        # 端到端流水线（硬编码 7 步）
├── analyzer.py                        # 文本分析：语言检测、领域识别、难度评估
├── extractor.py                       # 术语提取：jieba/YAKE/TextRank/C-Value/POS
├── translator.py                      # 全文翻译：Google Translate + Youdao fallback
├── searcher.py                        # 术语搜索：本地 DB + 在线词典
├── associator.py                      # 术语关联：Wikipedia + Datamuse + Youdao
├── termbase.py                        # 本地领域词典 + Wikidata 查询
├── db.py                              # SQLite 术语库（多库、CRUD、关联）
├── tm.py                              # 翻译记忆（TM）
├── exporter.py                        # 多格式导出：CSV/XLSX/TBX/JSON
├── report.py                          # Markdown 报告生成
├── sources/                           # 数据源抽象层
│   ├── base.py                        # DictSource 抽象基类
│   ├── youdao.py                      # 有道词典 API
│   ├── webster.py                     # 韦氏词典
│   └── wikipedia.py                   # 维基百科
```

## 二、现有架构问题诊断

### 1. 翻译层缺乏抽象（最严重，直接影响 AI 接入）
- **症状**：Google Translate 和 Youdao 的调用逻辑散落在 `pipeline.py` 和 `translator.py` 两处，各有重复实现。
- **后果**：新增一个 AI 翻译引擎需要修改 3+ 文件，容易遗漏、难以测试。

### 2. Pipeline 是过程式硬编码
- **症状**：`run_pipeline()` 用 7 个 `try/except` 块顺序执行，步骤间通过 `result` 对象传递数据。
- **后果**：插入新步骤（如 AI 术语对齐、质量评估）必须修改函数体，违背开闭原则。

### 3. 没有 Service 层，CLI 与 Web 直接调用底层模块
- **症状**：`cli.py` 和 `web/server.py` 都直接 `from termprep.extractor import extract`。
- **后果**：业务逻辑变更时需要同时修改两个入口，维护成本高。

### 4. 配置管理薄弱
- **症状**：API key 通过 `os.environ.get` 分散在各模块；没有 `.env` 自动加载；没有配置验证。
- **后果**：部署到不同环境（本地/Hugging Face/服务器）时容易出错。

### 5. 错误处理粗放
- **症状**：大量使用 `except Exception:` catch-all，没有分级降级策略。
- **后果**：Google 翻译失败时直接 fallback 到 Youdao，但 Youdao 失败时没有进一步 fallback；网络波动导致整步失败。

### 6. 模块间循环依赖风险
- **症状**：`extractor.py` 的 `_extract_cvalue` 通过 `from termprep.extractor import _extract_phrases` 自身导入；`pipeline.py` 直接导入几乎所有模块。
- **后果**：随着功能增加，循环依赖和启动时崩溃风险上升。

### 7. 扩展点不明确
- **症状**：没有“插件注册”或“引擎发现”机制，新增功能靠修改已有代码。
- **后果**：与论文中提到的“多 Agent 工作流”理念不匹配，难以将 TermPrep 作为工作流中的一个 Agent 节点。

## 三、优化目标

1. **引入翻译引擎抽象层**：任何翻译引擎（Google、Youdao、OpenAI、Kimi、Claude、本地模型）都实现统一接口，即插即用。
2. **Pipeline 插件化**：每个步骤是一个可注册、可替换的组件，支持声明式配置。
3. **建立 Service 层**：CLI 和 Web 只调用 Service，不直接依赖底层模块。
4. **统一配置**：Pydantic Settings 管理所有 API key、模型参数、功能开关。
5. **消除循环依赖**：通过接口隔离和延迟导入解决。

## 四、优化后的架构设计

```
termprep/
├── config.py                          # 统一配置（Pydantic Settings）
├── interfaces/                        # 抽象接口层（零外部依赖）
│   ├── __init__.py
│   ├── translator.py                  # Translator 抽象基类
│   ├── extractor.py                   # Extractor 抽象基类（可选）
│   └── pipeline_step.py              # PipelineStep 抽象基类
├── engines/                           # 具体引擎实现（翻译、提取）
│   ├── __init__.py
│   ├── base.py                        # 引擎注册表（Registry / 发现机制）
│   ├── google.py                      # Google Translate 引擎
│   ├── youdao.py                      # 有道翻译引擎
│   ├── ai_translator.py               # AI 大模型翻译引擎（统一封装）
│   ├── kimi_engine.py                 # Kimi (Moonshot) 专用引擎（可选）
│   └── ollama_engine.py               # 本地 Ollama 引擎（可选）
├── services/                          # 业务服务层
│   ├── __init__.py
│   ├── translation_service.py         # 翻译服务：引擎选择、降级、缓存
│   ├── pipeline_service.py            # 流水线服务：步骤编排、执行、回调
│   └── termbase_service.py            # 术语库服务：CRUD、导入导出、验证
├── core/                              # 保留并精简现有核心模块
│   ├── analyzer.py                    # 文本分析（独立，无依赖）
│   ├── extractor.py                   # 术语提取（独立，无依赖）
│   ├── db.py                          # 数据库（独立）
│   ├── tm.py                          # 翻译记忆（独立）
│   ├── termbase.py                    # 本地词典 + Wikidata（独立）
│   ├── exporter.py                    # 导出（独立）
│   ├── report.py                      # 报告（独立）
│   ├── searcher.py                    # 搜索（独立）
│   └── associator.py                  # 关联（独立）
├── cli.py                             # CLI → 调用 services/
├── web/server.py                      # FastAPI → 调用 services/
└── sources/                           # 数据源（保持现有）
```

### 核心设计模式

- **Strategy Pattern**：`Translator` 接口 + 多引擎实现
- **Registry Pattern**：引擎自注册，运行时自动发现
- **Chain of Responsibility**：翻译失败时按优先级自动降级
- **Plugin Pattern**：Pipeline 步骤可注册、可扩展

## 五、AI 翻译接入方案

### 方案概述

创建一个 `AITranslator` 引擎，封装所有大模型 API 调用，支持以下后端：

| 后端 | 接入方式 | 适用场景 |
|------|--------|--------|
| **OpenAI 兼容 API** | `openai` SDK / `requests` 直接调用 | GPT-4、Azure OpenAI、第三方代理 |
| **Kimi (Moonshot)** | `openai` SDK（兼容模式） | 国内稳定、长文本、中英翻译质量高 |
| **Claude (Anthropic)** | `anthropic` SDK | 高质量文学/学术翻译 |
| **Ollama (本地)** | HTTP `POST /api/generate` | 隐私敏感、离线环境、成本控制 |
| **阿里云百炼** | `dashscope` SDK / OpenAI 兼容 | 国内合规、Qwen 系列 |

### 统一 Prompt 模板

采用 "system + user" 两段式提示，支持 domain/style 注入：

```python
SYSTEM_PROMPT = """You are a professional translation engine. Translate the given text accurately while preserving the original meaning, tone, and structure. Use domain-specific terminology consistently. Output ONLY the translated text, without explanations, summaries, or markdown formatting."""

DOMAIN_PROMPTS = {
    "legal":    "Use formal legal language. Preserve the precise meaning of clauses, obligations, and rights. Keep the structure of legal provisions.",
    "medical":  "Use standard medical terminology. Translate drug names, anatomical terms, and clinical procedures according to internationally accepted nomenclature.",
    "finance":  "Use formal financial reporting language. Translate accounting terms, financial instruments, and regulatory concepts accurately.",
    "it":       "Use standard IT and software engineering terminology. Keep API names, technical parameters, and code-related terms in English where appropriate.",
    "academic": "Use academic writing style. Preserve citations, technical terms, and methodological descriptions. Maintain formal register.",
    "general":  "Translate naturally and fluently. Adapt idioms to target-language equivalents.",
}
```

### 关键特性

1. **流式翻译（Streaming）**：长文本分段翻译，支持 SSE 输出到前端。
2. **术语注入（Term Injection）**：在 prompt 中传入术语库（glossary），要求模型强制使用指定译法。
3. **回译验证（Back-translation Check）**：可选，将译文回译成原文语言，对比语义一致性。
4. **自适应分块**：根据模型上下文窗口自动分块（Kimi 128K、GPT-4 8K/32K/128K）。
5. **多轮术语对齐**：先提取术语，用 AI 翻译术语，再翻译全文，保证一致性。

### 术语对齐工作流（AI 增强）

```
Source Text
    │
    ▼
┌─────────────────┐
│ 1. Term Extraction │  ← jieba + YAKE + C-Value
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ 2. AI Term Translation │  ← AI 引擎：批量翻译术语，支持 context
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ 3. Build Glossary │  ← 术语 → 双语对照表
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ 4. AI Full-text Translation │  ← 注入 glossary 到 prompt
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ 5. Quality Check (可选) │  ← AI 评估流畅度、术语一致性
└─────────────────┘
```

## 六、迁移路线图

| 阶段 | 工作内容 | 风险 | 建议 |
|------|--------|------|------|
| **Phase 1** | 创建 `interfaces/` 和 `engines/`，把 Google/Youdao 翻译抽成 `Translator` 实现 | 低 | 先保留原文件，新接口通过 adapter 复用旧逻辑 |
| **Phase 2** | 实现 `AITranslator` + `TranslationService`，在 Web 端新增 `/api/translate/ai` 路由 | 低 | 用 feature flag 控制，默认关闭 |
| **Phase 3** | 重构 `PipelineService`，将步骤改为插件注册 | 中 | 保留 `run_pipeline` 作为兼容包装器 |
| **Phase 4** | 提取 `config.py`，统一环境变量管理 | 低 | 用 `pydantic-settings` |
| **Phase 5** | 删除旧 `translator.py` 中的重复代码，CLI/Web 全面迁移到 `services/` | 中 | 充分测试后再删除 |

