# TermPrep 优化交付报告

对应 `HARNESS_程序优化.md`。**全程遵守第 1 节安全红线**：所有验证均在本地 TestClient / 本地副本进行，
从未在线上实例（`https://dzha0118-termprep.hf.space`）调用任何写入、破坏、上传或调试端点；
未读取、打印或提交任何 key 明文；未写压测脚本。

---

## A. 变更说明（任务书 §9.2）

| 编号 | 文件 | 改动 | 验证方式 |
|---|---|---|---|
| §5.1 | `termprep/web/security.py`(新)、`termprep/web/server.py` | 新增 `require_admin` 依赖（`X-Admin-Token`）；给 `/api/db/init`、`/api/term/add`、`/api/rag/upload`、`/api/translate/upload`、`/api/translate/upload-test`、`/api/debug/env` 挂上；未配置令牌时 **fail-closed 503**。前端「AI 配置」新增令牌输入（sessionStorage） | 匿名调用 6 个端点全部 401 |
| §5.2 | `security.py`、`server.py` | 扩展名白名单 `{.txt,.md,.csv,.json,.docx,.pdf,.doc,.xlsx}`；单文件 ≤10MB；请求体 ≤12MB 中间件；`os.path.basename` 清洗文件名；回显改用安全名 | `.exe`→400、11MB→413、`../../etc/passwd`→`passwd` |
| §5.3 | `server.py` | `/api/debug/env` 收敛为 `{ai_configured, provider}`，**移除 `base_url`**，并加鉴权 | keys 恰为 `{ai_configured, provider}`，响应无 `base_url` / `api_key` |
| §5.4.1 + §5.11(3) | `termprep/decision_log.py`(新)、`server.py`、`index.html` | 裁决日志落盘（时间戳／会话／步骤／输入摘要／术语／系统建议／译员裁决／评分／备注）；`/api/decisions/{record,list,summary,export}`；前端提取表新增「裁决」列 + 会话统计 + **一键导出 CSV** | 写入 3 条 → summary 计数正确 → CSV 4 行含全部必需列 |
| §5.5 | `termprep/searcher.py`、`sources/youdao.py`、`server.py` | `search_multi()` **并发聚合** 替代优先级短路；合并去重 + `also_from` 跨源印证 + 词典源优先 AI 兜底；AI 改为请求字典式条目（译名+词性+释义+例句）；有道 translation 条目补音标与释义 | 离线桩测试：3 条结果、含非 `ai` 源、`also_from` 生效、`definition ≠ word` |
| §5.6 | `termprep/translator.py`、`agents/translation_agent.py`、`index.html` | 高亮必须能在译文中**原样找到**（`_match_in_target`），找不到即丢弃；新增 `target_span`；AI 路径改为**源侧定位 + 译文侧校验**（原先误把目标句位置当源位置） | 真实译文下 0 错位；`编排→Arrange` 被拦截；合成用例全通过 |
| §5.7 | `termprep/extractor.py`、`server.py` | `explainable_score()`：`0.4·频次 + 0.3·词表命中 + 0.2·长度 + 0.1·形态`，响应含 `score_breakdown` | 8 个术语出现 3 个不同取值（原为恒定 0.9） |
| §5.8 | `termprep/analyzer.py`、`index.html` | `academic` 词表扩充；新增 `translation`、`humanities`；改用**词边界匹配** + **饱和归一** `hits/(hits+3)` | 翻译研究文本 → `academic`（0.857，原为 0.0） |
| §5.9a | `extractor.guess_pos()` | `pos` 填充（中文 jieba 词性 / 英文形态学启发式） | 全部术语 `pos` 非空 |
| §5.9b | `analyzer._count_chinese_words()` | 去掉 `max(1, …)` 下限 | 纯英文输入 `words_cn == 0` |
| §5.9c | `termprep/tm.py`、`server.py` | 写入显式 float 时间戳 + `search(exclude_after=…)` | 本次请求写入的条目不作为命中返回 |
| §5.9d | `analyzer.get_summary()`、`server.py` | `summary` 改为自然语言摘要；结构字段平铺到顶层 | 响应不再出现 `AnalysisResult(` |
| §5.9e | `tm.stats()` | 不再把 `source`/`target` 截断到 60 字符 | 104 字符译文完整返回 |
| §5.10 | `server.py` | 新增 `GET /api/health` | 返回 `version=0.5` |
| 额外 | `termprep/storage.py`(新)、`tm.py`、`decision_log.py` | **TM 数据库路径 bug**：原 `../../data` 解析到应用目录之外的 `/data`（容器重建即丢失）。改为项目内 `data/`，应用目录只读时自动回退临时目录 | 解析正确；部署副本内验收同样全绿 |

### A2. 本轮追加修复：流水线两个功能缺陷（用户实测反馈）

| 现象 | 根因（含证据） | 改动 | 验证 |
|---|---|---|---|
| **全自动流水线看不到第四步「全文翻译」** | `/api/pipeline/v2` **确实**计算并返回了 `full_translation`（复现脚本里译文原样打印出来了），但 `index.html` 中 `full_translation` 出现次数为 **0** —— 前端把这个字段整个丢弃，页面上根本没有展示位。此外步骤条只是一个 **1.2 秒定时器**，无论成功失败都全部打勾；`errors` 也从不渲染，所以"没翻译"和"翻译成功"在界面上完全一样 | `web/static/index.html`：新增「全文翻译」结果区块并渲染 `full_translation`；改用后端返回的**真实** `step_status`（✓/✗/– + 原因 + 耗时）；新增错误条展示 `errors`；显示术语表完成度 `已译/总数`。`pipeline.py`：新增 `step_status` 逐步记实；**空译文直接判 failed 并给出可读原因**（不再静默当成功）；新增 `steps` 参数按模板裁剪执行。`server.py`：按所选模板解析步骤并下发（原先 `template` 只是回显，**快速分析与完整流水线跑的是同一套流程**）；`export` 步骤真正产出文件；返回 `steps`/`step_status`/`glossary_total`/`glossary_untranslated`/`exports` | 29 项断言全通过：无译文时 `full_translate` 状态为 `failed` 且 errors 可读；有译文时为 `ok` 并报告字符数；`quick-analysis` 正确跳过全文翻译与术语库（证明模板真的生效）；前端含渲染区块 |
| **术语提取翻译有的没翻译全** | 30 个术语只译出 **10 个**。根因：`translate_terms` 把整份术语表塞进**一次** AI 请求，而解析器**只认 `term \|\| translation`** 一种格式 —— AI 回复一旦格式漂移（`1. term - translation`）或漏输出，这些术语就**静默留空**（显示 `[未找到翻译]`），既无重试也无兜底；旧解析里的松散子串"模糊匹配"还会把**别的术语的译文错配**过来 | `services/translation_service.py`：四通道补齐 —— ①按 15 条分批 ②对漏项用小批量重试 ③逐条走免费引擎兜底 ④单条 AI 精补（上限 15 条）；`_parse_batch_output` 重写为**格式宽容**（`\|\|`/`→`/`->`/`:`/`-`/制表符/JSON 对象/JSON 数组/markdown 表格/代码块/列表编号全部支持）；**删除会错配的子串模糊匹配**（宁可缺失不可错配） | 模拟"AI 只答 60% 且格式漂移"：**30/30 全部译出**，通道贡献 `ai:18 / ai-retry:7 / ai-single:5`；8 种格式解析全部通过；错配用例返回空（不错配） |

### A3. 本轮追加修复：术语提取/识别准确率（用户实测反馈）

**现象**：流水线提取出的多是「词组」甚至句子片段，而非术语。

**关键前提更正**：本机 `python` 会在 3.10.11（未装 jieba）与 `C:\Program Files\Python312`（已装 jieba/yake）之间**不确定解析**，
第一次诊断跑在错误解释器上、结论无效。改用显式解释器后重测，问题确实存在但表现不同。

**实测证据（翻译学语料，改前）**

| 提取结果 | 问题 |
|---|---|
| `考察译员` / `保证术语` / `借助术语库` / `依赖人工判断` | 动词+名词（动宾片段） |
| `语料来自` / `研究发现` / `机器翻译输出` / `智能体编排改变` | 名词+动词 |
| `被动修改转向主动规划` | 连动结构 |
| `study examines post-editing` / `research adopts` / `ensure consistency` | 英文子句片段 |
| `术语管理过程` 吃掉 `术语管理`、`翻译记忆工具` 吃掉 `翻译记忆` | 真术语被长串吞并 |

**根因**

1. **注释写了约束、代码没实现**（`extractor.py` 的 `_MOD_POS`）：动词 `'v','vd'` 被列为合法修饰语，注释明写
   *"verb (allowed only when followed by noun)"*，但**代码从未检查"后面是否跟名词"**。
2. **合格判定太弱**：只要求短语**含**一个名词，不约束首尾。`考察译员`（译员是名词）因此过关。
3. **长度反向激励**：`length_bonus = min(2.0, 1.0 + (len-4)*0.1)` —— 越长分越高，句子片段天然占优；
   4–5 token 的窗口还会跨越动宾边界，且允许「的」作连接词，造出 `联合化疗组的无进展`。
4. **英文词性通道完全失效**：jieba 把英文一律标 `eng`，`_extract_phrases` 对英文返回 **0**（实测确认），
   英文只能靠 YAKE/CValue，而这两者**没有动词过滤**。
5. **合并排序偏长**：`(score, -len(term))` 排序 + “被包含的短词删掉、保留更长者” → 长片段系统性胜出。
6. **`pke` 未在 `pyproject.toml` 声明** → `_extract_pke_zh` 在生产环境恒为空（死代码）。

**⚠️ 设计陷阱（差点做错）**：单靠词性**无法**区分真术语与片段——

| 真术语 | 词性 | 垃圾 | 词性 |
|---|---|---|---|
| 联合化疗 | v+n | 考察译员 | v+n |
| 术语提取 | n+v | 语料来自 | n+v |
| 翻译研究 | v+vn | 研究发现 | vn+v |

因此**没有**采用“首尾必须是名词、内部禁动词”（那会误杀 `术语提取`/`人工判断`/`多智能体编排`/`联合化疗`），
改用**封闭的谓词框架词表** + 边界/词性兜底 + 英文规则分块。

**改动**（`termprep/extractor.py`）

| 措施 | 内容 |
|---|---|
| P0 片段判别 | `_FRAME_VERBS`（约 90 个“做谓语时构成片段”的通用动词：考察/保证/借助/依赖/来自/输出/改变/转向/发现/表明/采用/接受/纳入…）+ `_FRAME_SUBSTRINGS`；`_is_fragment()` 在**所有通道汇合处**过滤 |
| P0 边界约束 | `_BAD_LEAD_POS` / `_BAD_TAIL_POS` / `_BAD_INNER_POS`：首尾禁功能词、**内部禁虚词**（杀掉 `联合化疗组的无进展`）；首字单字 `ng` 判为半截词（杀掉 `性肺炎`）、末位单字 `ng` 判为跨边界碎片（杀掉 `低时`） |
| P0 术语形状 | 分块通道改为 span 判定（含名词 + 两端非功能词）；**移除长度加成**；移除「的」连接词；允许嵌入数字（`程序性死亡受体1抑制剂`）与一个尾随形容/副词性中心（`甲状腺功能异常`） |
| P0 英文分块 | 新增 `_extract_en_noun_phrases()`：以封闭词表（限定词/介词/连词 + 约 90 个谓语动词）切分内容串，只保留 2–4 词 NP 形态 + 缩写词 |
| P0 构词前缀 | 允许单字数词/区别词作 span 起始（`多智能体编排` 多/m、`非小细胞肺癌` 非/b），此前它们被当边界而把术语切断 |
| P0 词性兜底 | `_pos_ok()` 把约束应用到关键词/C-Value 通道（抓 `本文`、`常见`、`低时`）；新增 `_GENERIC_SINGLE` 通用单字词表 |
| P1 中心词剥离 | `_strip_generic_head()`：`术语管理过程` 与 `术语管理` 同时是候选时保留**更精确**的前者（旧逻辑相反） |
| P1 排序 | 并列分数时按长度**升序**取更精确者，替换原来的 `-len` 降序 |

**效果（内部评测集 `eval_termset.json`，4 篇文本 · 57 个金标准术语）**

| 指标 | 改前 | 改后 |
|---|---|---|
| 严格精确率 strict P@20 | 35.2% | **63.8%** |
| 宽松精确率 lenient P@20 | 62.0% | **87.9%** |
| 召回率 R@20 | 47.2% | **64.9%** |
| 噪声条数（既非金标准也非有效短语） | 27 | **0** |
| 动宾/子句片段泄漏 | **23** | **0** |

论文核心术语 `多智能体编排` 改前**完全提取不到**，改后已能正确提取。

**评测口径说明（避免自欺）**：金标准集由 AI 起草，属**内部回归基准**，不是人工双盲标注，不能当论文级金标准。
评测区分三类：`gold`（必测术语，计入召回）、`ok_extra`（有效但非必测的名词短语，既不计噪声也不计召回，
避免用“标注不全”冒充“提取错误”）、`must_not`（真正的动宾/子句片段，出现即计噪声）。
`eval_term_extraction.py --check` 已作为回归门禁（strict ≥55%、recall ≥58%、junk ≤2、泄漏 =0）。

**尚未解决（诚实记录）**：召回 64.9% 仍有提升空间；偏长形式（`非小细胞肺癌治疗`、`回顾性队列研究设计`）
被判为 lenient 而非 strict；`n+v` 类同构判别依赖词表覆盖度。见下方 A4（P4 AI 复核）处理其中可语义判定的部分。

---

### A4. 本轮追加：P4 — AI 术语复核

**动机**：P0/P1 把片段问题解决了（严格精确率 63.8%），但有两类错误**规则层判不了**，需要语义知识：

1. **形态合法但不是术语**：`高校翻译硕士课程` 是标准名词短语，却不是术语；
2. **冗长形式 vs 精确形式**：`术语管理过程` 与 `术语管理` 都合法，只有语义能判断该保留短的。

一次批量 AI 调用即可判定，成本可控（每次提取 1 次请求，≤40 条候选）。

**三条硬约束（设计上刻意保守）**

| 约束 | 含义 |
|---|---|
| **可选 / 优雅降级** | 无 AI key、调用失败、回复不可解析 → 候选**原封不动**。复核永远不能变成故障点，更不能把结果清空 |
| **保守判定** | 仅在「模型明确判为 `is_term: false`」**且**「判定术语与候选原样匹配」时才丢弃；其余一律保留（宁可漏删，不可错删） |
| **单次调用** | 整份候选一次批量请求，控制延迟与付费额度 |

**改动**

| 文件 | 内容 |
|---|---|
| `termprep/services/term_verifier.py`（新） | `verify_terms()` 返回 `(decisions, meta)`；格式宽容解析（`decisions` 包装 / 裸数组 / 布尔字典 / 行式 `term \|\| yes` / 代码块 / 字符串布尔）；`meta` 只含布尔与计数，**绝不含 key** |
| `termprep/web/server.py` | `/api/extract` 规则路径接入复核；新增 `rejected`（被丢候选 + 类型 + 原因）与 `verification`（是否执行 / 原因 / 检查数 / 丢弃数）；AI 抽取路径补齐同名空字段以保持响应结构一致 |
| `termprep/pipeline.py` | 提取步骤接入复核（`term_verify_ai` 设置项可关闭）；步骤 detail 显示 `N 项候选 → AI 复核保留 M 项` |

**⚠️ 效果界定（必须说清，避免过度期待）**

- P4 **只能删、不能补**：它提高**精确率**，对**召回率零帮助**（召回 47.2%→64.9% 全部来自 P0/P1）。
- 当前 58 条输出 = 精确 37 + 冗长 14 + 有效非术语 7，其中 **21 条落在 P4 可处理范围**，
  因此严格精确率**理论上限 ≈ 100%**。
- **该 100% 是理论上限，不是 AI 实测精度。** 本机无外网、无 AI key，**真实精度未验证**。
  上线后请跑一次 `POST /api/extract` 检查 `verification.dropped` 与 `rejected` 是否合理；
  若 AI 判断过激进，把设置项 `term_verify_ai` 设为 `false` 即可退回纯规则结果。

**验证（`verify_term_verifier.py`，24 项断言，全部离线可跑）**

| 分组 | 覆盖 |
|---|---|
| 解析容错 | 6 种回复格式（含代码块、布尔字典、字符串布尔、行式） |
| 优雅降级 | 无 key / 抛异常 / 乱回复 / 空候选 → 均返回空决策且不抛给上层 |
| 保守匹配 | 未请求过的词被忽略；`meta.checked/dropped` 计数正确 |
| 接口面 | `rejected`/`verification` 存在；无 key 时候选不删减；有 key 时精确剔除并记录 |
| 天花板 | 统计 P4 可处理项 = 21 条 |

---

### A5. 本轮追加：§5.4.1 数据留存 —— 可复现的「个案数据包」

**问题（线上实测确认）**：线上体检拿到 `tm/stats total: 0`、`db total_terms: 0`、裁决日志 0 条 ——
容器本地 SQLite 每次重建即清空，论文个案数据**依然不可复现**。

**为什么「配个持久目录」不够**：

- HF 的持久存储是**付费功能**，必须作者自己开启；
- 更糟的是：若 `TERMPREP_DATA_DIR` 指向不可写路径，原实现会**静默回退**到容器内目录 ——
  作者以为配好了，数据照样丢。**这是本轮实测踩到的真 bug**（沙箱不允许写系统临时目录，回退无声发生）。

**解决办法：产出「作者自己保存的」自描述数据包**

| 改动 | 内容 |
|---|---|
| `termprep/export_bundle.py`（新） | `build_case_bundle()` 组装：`manifest`（格式／时间／版本／**确定性说明**／隐私说明）、`input`（**完整原文 + sha256**）、`parameters`（top_n／domain／template／translate_mode／**ai provider+model，绝无 key**）、`terms_recomputed`（用存储的输入+参数**重跑**规则层）、`decisions`、`decision_summary`、`termbases`（全量）、`tm`、`engines`、`pipeline`；配 `to_json_bytes`／`to_zip_bytes`（内含 decisions.csv、terms.csv、README.txt 复现说明）／`save_snapshot`／`list_snapshots`／`read_snapshot`（路径穿越安全） |
| `termprep/decision_log.py` | 新增 `session_inputs` 表存**完整原文 + 运行参数**（原来只有 80 字摘要，根本不可能复现）；`remember_input`／`get_input`／`list_sessions`；含 `meta` 列的迁移 shim |
| `termprep/tm.py` | 新增 `dump_all()` 整库导出 |
| `termprep/storage.py` | **修静默回退**：`TERMPREP_DATA_DIR` 不可写时记录 `resolution_note` 并告警；新增 `is_persistent()`／`resolution_note()` |
| `termprep/web/server.py` | 新端点 `/api/cases/{sessions,bundle,snapshot,snapshots,snapshots/{name}}`；`/api/pipeline/v2` **每次运行自动落快照**并返回 `case.download_url`；`/api/health` 新增 `storage` 区块（是否真持久 + 不可持久时的警告） |
| `web/static/index.html` | 流水线结果新增「个案数据包」区块 + 一键导出 ZIP 按钮 |

**可复现性不是口号，是被测出来的**

`verify_case_bundle.py` 会**中途把本地数据库全部删掉**，再**仅凭数据包里的 `input.text` + `parameters` 重跑**提取，要求术语列表完全一致：

```
[PASS] 规则层确定性：重复运行结果一致
[PASS] 本地数据库已被清空（模拟容器重建）
[PASS] 清库后仅凭数据包即可复现术语列表      ← 核心证明
[PASS] 数据包不含 ai_api_key 字段 / 不含 sk- 明文
```

**诚实边界**

- 规则层确定性 → 可复现；
- **AI 阶段不确定**（sampling temperature > 0）→ 数据包记录的是**当次观测值**，manifest 明确写明，不假装可复现；
- 数据包**不含任何 key**（已断言）。

**上线后的三种留存策略（任选）**

1. **临时／公开演示**：跑完流水线点「导出个案数据包 (ZIP)」留存 —— 数据随容器消失，证据在你手里。
2. **要长期在线留存**：开 HF Persistent Storage，设 secret `TERMPREP_DATA_DIR=/data`，
   然后访问 `/api/health` 确认 `storage.persistent: true`（**这正是本轮修掉的静默 bug 的验证点**）。
3. **要正式论文附录**：直接用 ZIP 里的 `decisions.csv` ＋ `terms.csv` ＋ `README.txt`（已写明复现步骤）。

---

### A6. 本轮追加：Human-in-the-loop 流水线（检查点模式）+ 死功能修复

**为什么**：原流水线「一键跑到底」，译员只能事后看结果 —— 论文主张的「编排者」在流程中无处介入。
现在改为**两个检查点**：`start`（分析+提取）→ 译员裁决 → `resume`（继续后续各步）。

**1. 检查点机制**

| 端点 | 行为 |
|---|---|
| `POST /api/pipeline/start` | 只跑 analyze + extract；返回 `session_id` + 候选（含 `score` / `score_breakdown` / `pos` / `freq`）+ analysis |
| `POST /api/pipeline/{session_id}/resume` | 按裁决过滤/修正/合并后，继续 glossary → full_translate → termbase → report → export |

会话存**内存 dict + TTL 1 小时**，结构与既有 `_pipeline_events` 一致。

裁决语义：

| verdict | 行为 |
|---|---|
| `rejected` | 丢弃，**绝不入库** |
| `edited` | 以 `edited_word` / `edited_translation` 作为最终值入库 |
| `added` | 追加（走裁决日志而非 `/api/term/add`，**不需要 admin token**） |
| `merge_into` | 源条目并入目标条目：只保留一条，频次相加，保留 `merged_from` 溯源 |

译文优先级：`edited_translation` → 前端显式采用 → **裁决日志里「采用」的检索结果** → 会话原值 → 引擎补齐。

**2. 前端改造**

- 裁决按钮 3 → 4：**采纳 / 剔除 / 修正 / 合并**
  - **修正**：行内编辑框（术语 + 译文），保存即写入裁决日志
  - **合并**：先点源行的「合并」（进入合并模式），再点目标行完成，行内显示「→ 并入 X」
- **新增术语**行内输入（术语 + 译文 + 类型）→ 追加为 `verdict=added`
- **「✓ 确认裁决并继续流水线」** → 调 resume → 渲染真实步骤状态 + 全文翻译 + 入库术语表（带 `[edited]`/`[added]` 标记与 `← merged_from` 溯源）
- 词典搜索每行加**「采用 / 弃用」**：采用结果写入裁决日志（`step=search`），resume 时优先用作该术语译文
- 流水线页标注「**全自动模式**」并给出跳转到检查点模式的入口

**3. 顺带修掉一个潜伏 bug（重要）**

原裁决按钮的 HTML 是 `onclick="markVerdict(0,"term",'accepted')"` —— **`JSON.stringify` 产生的双引号会提前截断属性**，
裁决按钮实际上点了没反应。已改为 `data-*` 属性 + `this` 传参，并新增 `escAttr()` 做 HTML 属性转义。

**4. 死功能修复（采用方案 a）**

| 死功能 | 根因 | 修复 |
|---|---|---|
| SSE `/api/pipeline/events/{id}` | `_push_pipeline_event()` 存在但**从未被调用**，连接后只收到 keepalive | `run_pipeline` 新增 `on_event` 回调，每步完成即推送；`/api/pipeline/v2` 与 `/resume` 都接入。实测收到 **8 个 step 事件 + pipeline_done** |
| `/api/agents/task/{id}` `submit→501` | orchestrator 本就有 `submit_task/get_task/execute_task`，但 server 端点写死 501；且 orchestrator 是 **per-request** 的，实例内 `_tasks` 拿不到上次提交的任务 | 改为**模块级 `_AGENT_TASKS` 存储（TTL 1h）+ 同步实现**：submit → status → execute 全线打通（实测 execute 返回 `done`） |

**5. 兼容性**

- `/api/pipeline`、`/api/pipeline/v2` **保留**（标注「全自动模式」），与检查点模式并存
- `/api/decisions/*` 格式**未变**；resume 把整批复决以 `step=adjudicate` 写入日志（CSV 可完整复算）
- 裁决日志 / 个案数据包的 `session_id` = HITL `session_id`，证据链闭合

**验收（`verify_hitl.py`，42 项断言全通过）**

200 词英文摘要 → `start` → 剔除 2 / 修正 1 / 新增 1 / 合并 1 → `resume`，然后**查真实术语库**：

| 检查 | 结果 |
|---|---|
| 被剔除项 A/B 不在术语库 | ✅ |
| 修正项以**新值**入库、译文为译员指定值、旧值未以旧名入库 | ✅ |
| 合并目标存在、合并源不再单独存在 | ✅ |
| 新增项入库且译文正确 | ✅ |
| `/api/decisions/export` CSV 记录 rejected×2、edited、merged、added，可完整复算 | ✅ |
| SSE 收到 step 事件 + pipeline_done | ✅ |
| agent task：submit → status(200) → execute(done)，不再 501 | ✅ |
| 全自动模式 `/api/pipeline`、`/api/pipeline/v2` 仍可用 | ✅ |
| `/openapi.json` 正常生成（44 paths，含两个新端点） | ✅ |
| 未知 session → 404 | ✅ |

**附带收获**：验收用的英文摘要暴露出英文断词表缺 `how` 等词（`how collaboration`、`promote lexical` 这类片段漏出）。
补齐约 40 个高频学术谓语/疑问词后，同一摘要的候选变为 `post-editing effort` / `terminology management` /
`translation memory` / `multi-agent orchestration` / `workflow orchestrator` 等**真术语**，且评测门禁无回归。

---

### A7. 修复「继续失败：session not found」+ 流水线页重做

#### 1. 会话丢失的真根因：多 worker 进程

`Dockerfile` 的启动命令是 `gunicorn -w 2` —— **两个 worker 进程**。
HITL 会话原本存在**进程内 dict**，于是：`/api/pipeline/start` 落在 worker A、
`/api/pipeline/{sid}/resume` 落在 worker B，就必然报
`session not found or expired` —— **即使刚创建、根本没过期**。容器重启也是同样结果。

**修复**：会话落 **SQLite**（新增 `termprep/hitl_store.py` → `data/hitl_sessions.db`）

| 好处 | 说明 |
|---|---|
| 多 worker 共享 | 任何进程都能读到同一会话 |
| 跨重启存活 | 配了 `TERMPREP_DATA_DIR` 持久目录后，重建容器也不丢 |
| 有界 | TTL 1 小时 + 行数上限 500，防匿名灌爆磁盘 |

配套：
- 新增 **`GET /api/pipeline/{session_id}`**：页面刷新后可恢复会话，不必重新提取
- `/api/health` 新增 `hitl` 区块（`backend` / `sessions` / `ttl_seconds` / `shared_across_workers`）
- 错误文案改成可操作的：「该检查点会话已过期或不存在（TTL 1 小时）。请在流水线页重新点「开始：分析与提取」…」
- 回归断言：用**另一个 app 实例**读取同一会话（模拟另一个 worker）→ 通过

#### 2. 流水线页重做（之前的方案确实不对）

**之前的问题**：检查点模式藏在**提取页**，流水线页只有一行小字链接 ——
用户想到「流水线」，看到的却是不可干预的全自动模式。这正是"没有更自然的方案"的根源。

**现在的设计**：把「会停在哪里」直接画出来，并让检查点成为流水线页的一等公民。

| 改动 | 内容 |
|---|---|
| 模式切换 | 页面顶部 segmented：`检查点模式 · 可暂停裁决（推荐）`（默认）/ `全自动模式 · 一键跑到底` |
| **可见阶段条** | ① 分析 → ② 提取 → **③ 译员裁决 ⏸** → ④ 术语表 → ⑤ 全文翻译 → ⑥ 术语库 → ⑦ 报告 → ⑧ 导出；停在 ③ 时高亮 ⏸，继续后逐步打 ✓ / ✗ |
| 检查点就地展开 | 提取完成后面板自动出现并 `scrollIntoView`，内含裁决表 + 新增术语 + 两个出口 |
| 两个出口 | 「✓ 按我的裁决继续（④→⑧）」；「全部采纳，直接继续」（常用路径零摩擦）；另有「放弃本次检查点」 |
| 刷新自动恢复 | 会话 id 存 `sessionStorage`，页面加载时用 `GET /api/pipeline/{sid}` 拉回候选并提示「已恢复上次的检查点」 |

顺带修掉两个前端 bug：

1. **重复 id**：流水线页与提取页都有 `id="hitl-result"` 和 `add-term-*` ——
   `getElementById` 只返回第一个，导致「继续」的结果渲染到看不见的容器、
   「添加术语」读到另一个页面的空输入框。改为**双容器渲染** + `data-role` 作用域查找。
2. 裁决按钮 `onclick` 用 `JSON.stringify` 生成双引号导致属性被截断（A6 已修，此处一并回归覆盖）。

**验收**：`verify_hitl.py` 增至 **44 项**（新增跨 worker 断言）；
全部 5 套 + 评测门禁在 `hf_deploy/` 部署副本内全绿。

---

### A8. 线上实测 Bug：「流水线没法一步步进行 / 继续失败 session not found」

**这是我在线上环境亲自复现并定位的 bug**，不是推测。

#### 现象与实测证据

线上已是最新版本（`hitl` 端点 200、新 UI 全部就位）。但把前端同一套请求序列打到线上：

```
POST /api/pipeline/start        → HTTP 200 (3.1s)，session_id = hitl-945ad38fdcd4
POST /api/pipeline/{sid}/resume → HTTP 404  "该检查点会话已过期或不存在"
```

紧接着对**同一个 session id** 连打 8 次只读查询：

```
[404, 200, 200, 404, 404, 404, 404, 200]     ← 交替出现
```

而 `/api/health` 连打 6 次显示 `sessions` 计数**恒为 3**、`backend` 路径**完全一致**。
⇒ **数据库是共享的**（所以不只是"多 worker 各存各的"），问题出在**读取路径**。

#### 根因（两层缺陷叠加）

| # | 缺陷 | 后果 |
|---|---|---|
| 1 | `Dockerfile` 用 `gunicorn -w 2` | 两个 worker 进程；会话一旦只在内存里，A 建的 B 读不到 |
| 2 | 迁到 SQLite 后，`get()` **在读取路径上做 `DELETE`**（先调 `clean()` 清理过期行），且**每次开连接都执行 `PRAGMA journal_mode=WAL`**（需要短暂排他锁） | 多进程并发下偶发 `sqlite3.OperationalError: database is locked` |
| 3 | `_hitl_get()` **把异常静默吞掉**，返回 `None` | 存储故障被误报成「会话过期/不存在」，用户被误导去重新提取 |

这也解释了线上为何会出现「有时成功、有时失败」：`/api/db/list` 显示 `adjudicated` 库已有 **24 条**、裁决日志 7 条 —— 你确实成功跑通过，之后才间歇性失败。

#### 修复

| 措施 | 内容 |
|---|---|
| **单 worker** | `Dockerfile` 改 `-w 1`。本应用并发极小，单 worker 足够，且彻底消除跨进程状态不一致；配合 SQLite 落盘**仍可跨重启恢复** |
| **长超时** | 追加 `--timeout 300 --graceful-timeout 30`。gunicorn 默认 30s 会杀掉长 AI 调用，表现为前端「网络错误」 |
| **读取路径纯读** | `hitl_store.get()` 不再做任何写操作；过期清理只在 `save()` 时顺带做 |
| **不再逐连接切 journal_mode** | 新增 `storage.connect_db()`：不碰 journal_mode，统一 `busy_timeout=15000` + 驱动级 `timeout=15`，让并发写**排队**而不是报错。`tm.py` / `decision_log.py` 同步改掉 |
| **错误可见** | 新增 `hitl_store.last_error()`；`/api/health` 的 `hitl` 区块加入 **`self_test`（写→读→删自检）**。线上看不到日志也能一眼判断存储是否健康 |
| **不再谎报过期** | 存储异常 → **503**（附真实原因）；进程内兜底缓存让同一 worker 即使读库瞬时失败也能拿到自己刚建的会话 |

#### 验证

| 测试 | 结果 |
|---|---|
| **`verify_hitl_storage.py`**（新增）：4 个真实进程 × 60 次读 + 并发写 | **误读 0 / 异常 0**，自检 write→read→drop OK |
| **跨进程端到端**：两个独立服务进程共享同一数据目录（等价 `-w 2`）：A 建会话 → B 读 **10/10 全 200** → B 执行 resume 入库 7 条 → A 读到 B 写的裁决日志 | **PASS** |
| 其余 5 套回归 + 提取门禁 | 44 / 40 / 29 / 24 / 45 全绿 + 门禁 OK |

> ⚠️ 本次修复含 **`Dockerfile` 变更**，必须**重新上传并等镜像重建**才生效（只改 Python 文件不会触发重建）。

---

## A9. 检查点可用性修复：看不到译文、导不出报告、没有「下一步」

> 现象（用户实测）：「裁决完后仍然没法导出报告，没法进行下一步，下一步调不出来，
> 顶部状态栏全部绿了但是仍然没有下一步出来，并且在裁决状态下看不到术语的翻译结果。」

### 逐条定位到代码

| 现象 | 真实原因 |
|---|---|
| **裁决表看不到译文** | `/api/pipeline/start` 里硬写着 `"translation": ""`（注释是"供译员填写/修正"）。**20 条候选 20 条全空**，那一列永远是「—」，等于让译员盲裁 |
| **导不出报告** | 报告与 csv/json 是**写出来了**（`report` 4.8k 字、`exports: {csv, json}`），但**一个下载端点都没有**——文件躺在服务端，前端只显示了几个文件名 |
| **没有「下一步」** | 裁决后 6 个步骤的结果被堆成一大坨文字，**没有任何推进控件**；④→⑧ 只是阶段条上的装饰 |
| **顶部状态栏全绿** | `resumePipeline()` 里还留着旧调用 `renderStepperFromResume(data)`，它把 `step_status` 里所有完成项**一次性刷绿**——所以"全绿"恰恰发生在"什么都点不了"的那一刻 |

### 修法

**后端（`termprep/web/server.py`）**

| 改动 | 说明 |
|---|---|
| 新增 `_fill_candidate_translations()` | 检查点阶段就复用 `translate_terms` 的四通道补齐（AI 批量 → 小批量重试 → 免费引擎 → 单条 AI）把候选译文填好；任何失败只让该行留空，不阻断检查点。可用 `prefill_translations: false` 关闭 |
| resume 结果**存回会话** | `sess["result"] = payload` 后 `_hitl_save()`：刷新页面后报告、导出清单、裁决统计都还在（此前一刷新就"报告消失了"） |
| 新增 `GET /api/pipeline/{sid}/report` | 返回 Markdown，带 `Content-Disposition: attachment`（含 RFC 5987 中文名回退） |
| 新增 `GET /api/pipeline/{sid}/export/{fmt}` | csv / json / glossary_json 白名单校验，非法格式 400，文件缺失 404 |
| `GET /api/pipeline/{sid}` 返回 `result` + `has_result` | 让"刷新后恢复"能恢复**裁决后**的状态，而不仅是待裁决状态 |
| 新增 `_pipeline_session_or_404()` / `_download_name()` / `_attachment_headers()` | 统一「503 存储故障 vs 404 会话不存在」的区分，并生成安全文件名 |

**前端（`web/static/index.html`）**

| 改动 | 说明 |
|---|---|
| 裁决表译文列 | 有译文直接显示（带来源 tooltip）；没译文的行给出 **`＋ 补译`** 按钮，点击进入内联编辑 |
| 裁决后改为 **5 张阶段卡片** | ④ 术语表 / ⑤ 全文翻译 / ⑥ 入库术语库 / ⑦ 报告 / ⑧ 导出，一次只展开一张<br>底部 **`← 上一步`｜`第 n / 5 步`｜`下一步：⑤ 全文翻译 →`**，顶部还有 5 个标签可跳转<br>到最后一步按钮置灰为「✓ 已完成全部步骤」，不再误导 |
| 每张卡片都带**真实下载** | ④ 术语表 CSV/JSON；⑦ 报告 `.md`（正文也直接渲染出来）；⑧ 报告 + CSV + JSON + 个案数据包 ZIP |
| 阶段条按**当前步**画 | 走到第几张卡片，阶段条就停在那一格（`⏸`）；已看过的变绿，没到的保持灰色 |
| **删掉** `renderStepperFromResume()` 的调用 | 这就是"全绿却没下一步"的元凶；函数保留但加了注释说明检查点页面不能用它 |
| 失败步骤仍显示失败 | 翻到某一步不会因为"翻过"就把它刷绿 |
| 刷新恢复 | `restoreCheckpoint()` 读到 `has_result` 就直接还原整套分步结果（含下载链接） |
| **顶层数据表前置** | `PIPELINE_STAGES` / `_RESUME_STAGE` / `HITL_STAGES` 移到脚本最前面；主题检测包进 `try`。<br>原因：顶层语句抛错会让后面所有 `var` 赋值不执行 → 函数都在、数据表是 `undefined`，界面表现为**静默失效**（正是本次排查中真实踩到的坑） |

### 实测（本地真服务 + jsdom 真页面）

| 测试 | 结果 |
|---|---|
| 后端端到端（start → 混合裁决 → resume → 下载 → 刷新恢复） | **30 / 30 通过**；译文预填 **20/20**；报告 6 594 字节；CSV 2 167；JSON 8 710；ZIP 合法 |
| **`verify_ui_e2e.js`**（新增，jsdom 加载真实页面打真实服务） | **40 / 40 通过**：面板出现 → 12 条候选 12/12 预填译文 → 混合裁决 → 5 张卡片 → 点「下一步」走完 → **真的 GET 四个下载链接全 200** → 刷新仍可取回报告 |
| **`verify_frontend_ui.py`**（新增，零依赖静态一致性） | **34 / 34 通过**：id 唯一、104 个 `getElementById` 目标全存在、33 个内联处理函数全已定义、后端 8 个关键字段都被渲染、下载按钮指向真实接口 |
| 既有 6 套回归 + 提取门禁 | 44 / 40 / 29 / 24 / 45 / storage PASS + 门禁 OK（**无回归**） |

> ⚠️ 本轮同时改了 **前端 + 后端**，必须重新上传部署（见 D 节）。

---

## B. 验收报告（任务书 §8，本地实测）

### §8.1 安全 — 全部通过

| 验收项 | 结果 |
|---|---|
| 匿名 `POST /api/db/init` → 401/503 | ✅ **401** |
| 匿名 `POST /api/term/add` → 401 | ✅ **401** |
| 匿名 `POST /api/rag/upload` → 401 | ✅ **401** |
| 匿名 `POST /api/translate/upload` → 401 | ✅ **401** |
| 匿名 `GET /api/debug/env` → 401，且不含 `base_url` | ✅ **401**；带令牌后仅 `{ai_configured, provider}` |
| 非白名单扩展名 → 4xx | ✅ **400**（`.exe`） |
| \>10MB → 4xx | ✅ **413** |
| 文件名含 `../` 被清洗 | ✅ `../../etc/passwd` → `passwd`；`..\..\evil.doc` → `evil.doc` |
| 代码库检索不到 key 明文 | ✅ 全库无明文 key/token |

### §8.2 功能 — 本地可测项全部通过

| 验收项 | 结果 |
|---|---|
| `search` 返回 >1 条且出现非 `ai` 源 | ✅ **离线桩测试**通过（`youdao`+`wikipedia-en`+`ai` 三源合并）。真实有道/Wikipedia 需联网，**待线上验证** |
| `definition` 与 `word` 不同且含词汇级信息 | ✅ 聚合后 `definition ≠ word` |
| 每个 highlight 的 `target_span` 都能在 `translated` 中原样找到 | ✅ 全量断言通过（真实译文 + 合成用例） |
| `extract` 的 `score` 不再全部相同（≥3 个取值） | ✅ 3 个不同取值 + `score_breakdown` |
| `analyze` 对翻译研究文本给出 `academic`/`translation`，`academic > 0` | ✅ `domain=academic`，`academic=0.8571` |
| `pos` 有值或已移除 | ✅ 有值 |
| 纯英文输入 `words_cn == 0` | ✅ `0` |
| 首次翻译 `tm_matches` 为空（不自我命中） | ✅ 本次请求写入的条目不返回 |

### §8.3 论文支撑 — 通过

| 验收项 | 结果 |
|---|---|
| 导出 CSV 含时间戳／输入摘要／逐条判定／系统建议-译员裁决标签 | ✅ 见 `termprep-decisions-sample.csv`（真实导出） |
| `/api/health` 返回版本号 | ✅ `0.5` |

**汇总**

| 验收套件 | 结果 |
|---|---|
| `verify_acceptance.py`（任务书 §8，源码树） | **40 PASS / 0 FAIL** |
| `verify_acceptance.py`（在 `hf_deploy/` 部署副本内） | **40 PASS / 0 FAIL** |
| `verify_pipeline_fixes.py`（本轮两处流水线缺陷） | **29 PASS / 0 FAIL** |
| `repro_pipeline_bugs.py`（缺陷复现，改前改后对比） | 改前 10/30 术语译出、全文翻译不可见；改后 30/30、步骤状态可信 |

> 唯一无法离线验证的是「真实有道/Wikipedia 双源返回」与「真实 AI/Google 全文翻译」——本机外网受限
> （Google 返回 429，AI 无 key）。已用等价的严格桩测试覆盖同一不变式；联网后请用线上实例
> `POST /api/search {"term":"post-editing effort","limit":3}` 复核（只读端点，可安全调用）。

---

## C. 论文侧同步清单（任务书 §9.4）

| 论文表述 | 现状 | 建议 |
|---|---|---|
| 版本 **0.4** | 线上自报 **0.5**（`/api/health`） | 论文改为 0.5（版本号已收敛为单一常量 `APP_VERSION`） |
| **Streamlit** | **FastAPI** + 单文件原生 JS SPA | 论文改为 FastAPI；若早期确用 Streamlit，补一句版本演进 |
| 有道 + Wikipedia「双源检索释义与例句」 | 已修为**并发聚合**（词典源优先、AI 兜底），释义/音标/例句回归 | 可保留原表述，并可补「多源印证」细节 |
| 「五个功能模块」 | 另有 TM、RAG、Agent 任务队列、8 步流水线、裁决日志 | 补「已扩展至含 TM/RAG/Agent 调度」——**反而强化论点** |
| 个案 **28 项有效术语**、数据未留存 | 新增**裁决日志 + CSV/JSON 导出**，可复现、可审计 | 用 `/api/decisions/export` 产出附录表；个案数据请由作者补齐真实记录 |
| 译员按质量阈值筛选（§5.1） | `score` 为可解释多因子，返回 `score_breakdown` | 可写「阈值筛选有据可查，误检可归因到具体因子」 |
| （可新增）流水线可信度 | 每步返回真实状态与耗时，失败一并可见 | 论文若描述工作流，可直接引用 `step_status` 作为「编排可审计」的佐证 |

**安全提示（需作者操作）**：本版把写/上传/调试端点锁在管理员令牌之后。请在 Space 的
**Settings → Variables and secrets** 添加 **secret** 类型的 `TERMPREP_ADMIN_TOKEN`，
否则相关端点返回 503（安全锁定，预期的 fail-closed 行为）。前端在「AI 配置」面板填入同一令牌即可解锁。

---

## D. 部署：把 `hf_deploy/` 里的内容拖进 Space

`hf_deploy/` 是**干净、完整、可直接拖拽**的部署集（60 个文件，无 `__pycache__`、无 `.pyc`、无文档、无数据库）。

1. 打开 Space 的 **Files** 页。
2. 进入本地 `hf_deploy/`，`Ctrl+A` 全选 → 拖进 Files 页（**拖里面的内容，不要拖文件夹本身**）。
3. HF 自动重建镜像（含 torch 等重依赖，10~20 分钟属正常）。
4. 构建变绿后硬刷新，确认新版界面：提取页有「词性」「裁决」两列与导出按钮；流水线页有「全文翻译」区块与真实步骤状态。
5. 在 Space 的 Secrets 里配置 `TERMPREP_ADMIN_TOKEN`，并在页面「AI 配置」填入同一令牌以解锁上传/写库。

**验证清单（上线后）**

- `GET /api/health` → `version=0.5`、engines 与 db 有值
- 匿名 `POST /api/db/init` → 401/503（不再能清库）
- `POST /api/search {"term":"post-editing effort","limit":3}` → **>1 条且含非 `ai` 源**（联网后待复核）
- 跑一次完整流水线 → 步骤条显示 **8 步真实状态**，第 4 步「全文翻译」下方出现译文（或明确报错原因）
- DevTools → Network：`index.html` 带 `Content-Encoding: gzip`

---

## E. 可复跑的验证脚本

| 脚本 | 用途 |
|---|---|
| `verify_hitl.py` | **检查点（HITL）全链路验收（44 项）**：start → 剔除/修正/合并/新增 → resume，逐项核对真实术语库；含跨 worker 会话共享断言 |
| `verify_frontend_ui.py` | **前端静态一致性（34 项）**：id 唯一、`getElementById` 目标存在、内联处理函数已定义、后端字段被渲染、下载按钮指向真实接口（零依赖） |
| `verify_ui_e2e.js` | **UI 端到端（40 项，可选）**：jsdom 加载真实页面 → 真实 `startPipeline`/`resumePipeline` → 点「下一步」→ 真去下载报告/CSV/JSON/ZIP。需 `npm i --no-save jsdom` + 本地服务；缺前置条件时自动 SKIP |
| `verify_acceptance.py` | 任务书 §8 全部验收项（安全 / 功能 / 论文支撑，40 项） |
| `verify_pipeline_fixes.py` | 流水线两处缺陷的回归测试（29 项） |
| `verify_term_verifier.py` | P4 AI 复核的回归测试（24 项，含降级与天花板测量） |
| `verify_case_bundle.py` | 数据留存与可复现性测试（45 项，含**清库后仅凭数据包复现**证明） |
| `eval_term_extraction.py` | 术语提取质量评测；`--check` 为回归门禁，`--show` 逐条标注 gold/lenient/ok-extra/JUNK |
| `eval_termset.json` | 内部金标准集（4 篇文本 · 57 个金标准术语） |
| `live_test_space.py` | 线上 Space **只读**体检（42 项，绝不触碰写入/破坏性端点） |

前 5 套只使用本地 `TestClient` 与桩引擎，不访问线上实例、不消耗任何付费额度；
`live_test_space.py` 是唯一打线上的脚本，且只调用只读端点 + 3 次无副作用计算端点。

> 开发期的一次性诊断/演示脚本（`diag_*` / `demo_*` / `repro_pipeline_bugs.py` / `live_diag.py`）
> 已在收尾时删除；它们对应的结论已固化进本报告 A2–A7 各节。
