# rag-design

**编码实现指导**：教你在代码里把 RAG（检索增强生成）管线搭出来——文档分块、稠密与稀疏检索、ANN 索引选型、混合融合与神经重排序、检索质量度量与调优。

RAG 的核心思想是**把 LLM 的思考与生成能力，和外部知识库的广度与时效性结合**：模型训练数据有截止日期，知识库可以随时更新。核心流程是**检索 → 增强 → 生成**。

使用本 skill 时：**先自己把需求调研清楚**——项目代码/配置、文档检索、线上查询日志与实测，能自己确认的就自己确认；只有知识范围、更新频率、成本与延迟权重、权限边界这类**必须由人拍板**的决策才问用户，且一次不超过 3 个问题、每项带选项与推荐项。

## 解决什么问题

| 症状 | 入口 |
| --- | --- |
| 整篇文档嵌进去，检索不准 | `references/chunking.md`（三类策略 + 块大小重叠权衡） |
| 结构化文档被切碎 | `references/chunking.md`（递归/结构感知切分）+ `chunk_inspector.py` |
| 技术代码、人名查不到 | `references/sparse-embedding.md`（BM25）+ 补稀疏路 |
| 同义表达查不到 | `references/dense-embedding.md` + 补稠密路 |
| 两路结果不知怎么合并 | `references/hybrid-retrieval.md`（RRF，k=60） |
| 命中但排得很靠后 | `references/hybrid-retrieval.md`（跨编码器重排序） |
| 新文档检索不到 | `references/dense-embedding.md`（ANNOY vs HNSW 选型） |
| 不知道该调什么 | `references/hybrid-retrieval.md`（recall@k / MRR / nDCG）+ `retrieval_eval.py` |
| 单文档霸屏，其他文档进不来 | `references/multi-document-quota.md` + `quota_allocator.py` |
| 「单文档最多 N 条」导致信息不足 | 同上（配额动态计算，单文档场景自动拿满 K） |
| 同一文档多条高度相似 | MMR / xQuAD（见 `references/multi-document-quota.md` 第 7 节） |

## 安装

```bash
npx skills add JFeng2048/agent-design-skills
```

手动安装：把本目录复制到技能目录（建议目录名与 frontmatter 的 `name` 一致，即 `rag-design`）。

- 用户级：`~/.codebuddy/skills/rag-design/`
- 项目级：`.codebuddy/skills/rag-design/`

## 目录结构

```
rag-design/
├── SKILL.md                       主流程（7 步）、自检清单、反模式表
├── references/
│   ├── information-gathering.md   自主调研：项目勘察、文档检索、线上数据、什么才必须问用户
│   ├── chunking.md                三类分块策略、块大小与重叠、切断上下文的缺陷
│   ├── dense-embedding.md         嵌入与余弦相似度、模型演进、ANN 索引（ANNOY vs HNSW）
│   ├── sparse-embedding.md        词袋、TF-IDF、BM25 公式与参数、倒排索引
│   ├── hybrid-retrieval.md        三阶段流水线、RRF、跨编码器重排序、四个度量指标
│   └── multi-document-quota.md    多文档配额分配：比例代表制、最大余额法、α 旋钮
├── assets/
│   ├── requirements_intake.md     设计输入表：调研事实、默认方案、待拍板决策
│   ├── chunking_config_template.md分块配置与调优记录
│   ├── retrieval_pipeline_spec.md 检索管线规格（模型、索引、融合、重排参数）
│   └── evaluation_plan_template.md 标注集与指标基线表
└── scripts/
    ├── discover_rag_context.py    项目勘察：向量库、嵌入模型、分块、重排序
    ├── chunk_inspector.py         分块体检：大小分布、重叠率、结构截断
    ├── bm25_calc.py               BM25 手算与参数校验
    ├── retrieval_eval.py          recall@k / MRR / nDCG / 失败率 + RRF 融合
    └── quota_allocator.py         多文档配额分配 + α 敏感性扫描
```

## 脚本用法

### discover_rag_context.py：项目调研

```bash
python scripts/discover_rag_context.py <项目目录>
python scripts/discover_rag_context.py <项目目录> --json
```

输出带 `文件:行` 证据的事实（区分**代码命中**与**仅在文档中提及**），覆盖向量库、嵌入模型、分块配置、稀疏检索、重排序、检索入口、相似度度量，并给出默认方案。

### chunk_inspector.py：分块体检

```bash
python scripts/chunk_inspector.py chunks.json --target 512 --overlap 0.15
python scripts/chunk_inspector.py chunks.json --format json
```

检查块大小分布（中位/P95）、实际重叠率、过小块与超大块占比，以及结构截断：代码块围栏未闭合、XML 标签未闭合、表格缺表头、以标题结尾等。

### bm25_calc.py：BM25 手算

```bash
python scripts/bm25_calc.py corpus.json --query "模型 蒸馏" --show-hits
python scripts/bm25_calc.py corpus.json --query "退款流程" --k1 1.5 --b 0.75
```

输出倒排索引命中、每个词的 DF 与 IDF、每个文档的 TF 与贡献值、最终排序。中文按二元组分词。**`--idf-floor` 默认为 0.0**——DF > N/2 时 IDF 会变负，必须设下限。

### retrieval_eval.py：检索质量评估

```bash
python scripts/retrieval_eval.py annotations.json --k 10
python scripts/retrieval_eval.py annotations.json --k 10 --rrf-k 60 --format json
```

标注集给出 `relevant`（相关文档）与 `dense` / `sparse` / `fused` 三路结果之一；只给两路时自动用 RRF 融合。输出 recall@k（主指标）、MRR、nDCG@k、失败率@20，并按查询类型拆解，用于定位短板。

### quota_allocator.py：多文档配额分配

```bash
python scripts/quota_allocator.py candidates.json --k 8
python scripts/quota_allocator.py candidates.json --k 8 --alpha 0.5 --max-quota 3
python scripts/quota_allocator.py candidates.json --k 8 --alpha-sweep
```

按文档相关性比例分配 Top-K 名额：代表分取 max、权重按 `rel^α` 归一化、最大余额法分配、钳制后缺口回填。输出每个文档的代表分/权重/配额/可用数，以及最终入选清单、文档覆盖率与单文档最大占比。`--alpha-sweep` 扫描 α ∈ {0, 0.25, 0.5, 1, 1.5, 2, 4} 的分配变化，用于定 α。分数可能为负时自动平移为正。

## 四条关键纪律

1. **先有指标再调参**：没有带标注的查询集，改任何参数都说不清是变好还是变坏。以 **recall@k** 为主。
2. **稠密与稀疏缺一不可**：稠密懂语义但漏关键词（搜 `HTTP-403` 返回泛泛讨论），稀疏精确但读不懂同义词（搜 `kitty` 找不到 `cat`）。融合用 **RRF**（k=60），不要直接相加。
3. **重排序不替代融合**：融合产生统一候选池，跨编码器在候选池上精排 Top-N；候选池本身有偏时，精排救不回来。
4. **Top-K 不是直接截断**：重排后要按文档相关性比例分配名额（配额分配器），避免单文档霸屏；硬性的「单文档最多 N 条」会在只有该文档相关时造成信息不足。文档内冗余则叠加 MMR / xQuAD——两者职责不同。
