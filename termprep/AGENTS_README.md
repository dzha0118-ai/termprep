# TermPrep Multi-Agent 翻译工作流

## 快速开始

### 1. 配置 AI 翻译引擎

创建 `.env` 文件或在环境变量中设置：

```bash
# Kimi (Moonshot) — 推荐，国内稳定
TERMPREP_AI_PROVIDER=kimi
TERMPREP_AI_API_KEY=sk-your-key-here
TERMPREP_AI_MODEL=moonshot-v1-128k

# 或 OpenAI
TERMPREP_AI_PROVIDER=openai
TERMPREP_AI_API_KEY=sk-your-key-here
TERMPREP_AI_MODEL=gpt-4o-mini

# 或本地 Ollama
TERMPREP_AI_PROVIDER=ollama
TERMPREP_AI_API_KEY=ollama
TERMPREP_AI_BASE_URL=http://localhost:11434/v1
TERMPREP_AI_MODEL=llama3.1
```

### 2. 启动服务

```bash
cd termprep
python -m termprep.web.server
# 访问 http://127.0.0.1:8672
```

## API 使用

### 术语 Agent — 提取并标准化术语

```bash
curl -X POST http://127.0.0.1:8672/api/agents/term \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Machine learning has revolutionized NLP...",
    "project_name": "Demo",
    "top_n": 20
  }'
```

返回标准化 Glossary JSON：
```json
{
  "glossary": {
    "meta": {
      "version": "1.0.0",
      "project_name": "Demo",
      "domain": "it",
      "lang_pair": {"source": "en", "target": "zh"}
    },
    "terms": [
      {
        "term_id": "t-001",
        "source_term": "machine learning",
        "target_translation": "机器学习",
        "term_type": "phrase",
        "confidence": "high",
        "status": "confirmed",
        "source_context": "...machine learning has revolutionized...",
        "frequency": 3
      }
    ]
  }
}
```

### 翻译 Agent — 消费 Glossary 进行翻译

```bash
curl -X POST http://127.0.0.1:8672/api/agents/translate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Machine learning has revolutionized NLP...",
    "glossary": { /* 上一步返回的 glossary JSON */ },
    "domain": "it",
    "engine": "ai"
  }'
```

返回结构化翻译结果：
```json
{
  "translated": "机器学习彻底改变了自然语言处理...",
  "engine": "ai",
  "model": "moonshot-v1-128k",
  "glossary_used": true,
  "term_consistency": 0.85,
  "segments": [
    {
      "index": 0,
      "source": "Machine learning has revolutionized NLP.",
      "target": "机器学习彻底改变了自然语言处理。",
      "highlights": [{"start": 0, "end": 4, "term": "机器学习"}]
    }
  ]
}
```

### 全量 Pipeline — 一步到位

```bash
curl -X POST http://127.0.0.1:8672/api/agents/pipeline \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Machine learning has revolutionized NLP...",
    "project_name": "Demo",
    "top_n": 20,
    "translation_engine": "ai"
  }'
```

返回包含 `glossary` + `translation` 的完整结果。

## 架构设计

```
┌─────────────┐      Glossary JSON      ┌─────────────────┐
│  Term Agent  │  ──────────────────→  │ Translation Agent │
│  (提取术语)   │                       │   (全文翻译)        │
└─────────────┘                       └─────────────────┘
       │                                       │
       ▼                                       ▼
  ┌──────────┐                          ┌────────────┐
  │ schemas  │                          │  结构化输出  │
  │Glossary  │                          │TranslationResult│
  └──────────┘                          └────────────┘
```

## 核心特性

1. **术语一致性**：Glossary 通过 system prompt 注入，强制 AI 使用指定译法
2. **批量术语翻译**：一次性翻译所有术语，效率提升 10x+
3. **术语一致性评分**：量化翻译输出中术语保留的完整度
4. **分段对齐**：按句子分段，返回每段的高亮信息
5. **降级链**：AI → Google → Youdao → 本地词典，保证可用性
6. **完全解耦**：Glossary JSON 是 Agent 间唯一契约，支持跨服务、跨时间消费
