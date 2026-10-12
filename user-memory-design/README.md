# user-memory-design

**编码实现指导**：教你在代码里把用户记忆系统做出来——层次划分、存储格式、写入与检索策略、压缩淘汰、PII 脱敏。

用户记忆不保存每句对话，而是用额外的 LLM 调用**提取、压缩并审查**对未来有用的事实；这与只在当前窗口生效的上下文学习不同。本 skill 产出的是数据结构与管线实现，不是一份提示词模板。

使用本 skill 时：**先自己把需求调研清楚**——项目代码/配置、文档检索、线上失效案例与实测，能自己确认的就自己确认；只有记忆范围、保留期限、合规边界、成本权重这类**必须由人拍板**的决策才问用户，且一次不超过 3 个问题、每项带选项与推荐项。

## 三套正交的分类体系

| 分类体系 | 回答 | 类别 |
| --- | --- | --- |
| 记忆层次 | 存在哪里 | 轨迹（当前会话）/ 用户长期记忆（跨会话）/ 业务状态（任务阶段） |
| 存储格式 | 怎么存 | Simple Notes / Enhanced Notes / JSON Cards / Advanced JSON Cards（进阶：User as Code） |
| 认知类型 | 存什么 | 情景（事件）/ 语义（一般知识）/ 程序（行为流程） |

三者可自由组合：一条「偏好靠窗座位」的语义记忆可用 Simple Notes 存；一段「先搜直飞 → 确认座位 → 用常旅客号」的程序记忆可用 Advanced JSON Cards 存。

## 解决什么问题

| 症状 | 入口 |
| --- | --- |
| 跨会话记不住用户偏好 | `references/memory-types.md`（提取三规则）+ `references/storage-formats.md` |
| 同名实体混淆（张医生是谁的医生） | `references/storage-formats.md` 的 Advanced JSON Cards（backstory / person / relationship） |
| 矛盾信息不知如何处理 | `references/frameworks.md`（写入时消歧 vs 追加 + 检索时排序） |
| 旧信息被当成当前事实 | 检索融合时间信号（见 `references/frameworks.md` v3 管线） |
| 记忆库膨胀、检索变慢 | `references/compression-privacy.md` + `scripts/importance_scoring.py` |
| 记忆里出现明文身份证/密码 | `references/compression-privacy.md` + `scripts/memory_lint.py` |
| 不知道记忆系统算不算好 | `references/evaluation-framework.md`（三层次框架） |
| 需要聚合、冲突检测、主动预警 | `references/executable-memory.md`（User as Code） |

## 安装

```bash
npx skills add JFeng2048/agent-design-skills
```

手动安装：把本目录复制到技能目录（建议目录名与 frontmatter 的 `name` 一致，即 `user-memory-design`）。

- 用户级：`~/.codebuddy/skills/user-memory-design/`
- 项目级：`.codebuddy/skills/user-memory-design/`

## 目录结构

```
user-memory-design/
├── SKILL.md                       主流程、三套分类体系、自检清单、反模式表
├── references/
│   ├── information-gathering.md   自主调研：项目勘察、文档检索、线上数据、什么才必须问用户
│   ├── evaluation-framework.md    三层次评估框架、八项能力、LLM-as-a-judge 评测
│   ├── memory-hierarchy.md        轨迹 / 长期记忆 / 业务状态，工作记忆与轨迹的关系
│   ├── storage-formats.md         四种存储格式对比与混合模式选择
│   ├── executable-memory.md       User as Code：类型化状态、冲突检测、约束执行
│   ├── memory-types.md            情景 / 语义 / 程序记忆与三套体系对照
│   ├── frameworks.md              Mem0 v2/v3、Memobase 与参考架构
│   └── compression-privacy.md     三层压缩与 PII 脱敏
├── assets/
│   ├── requirements_intake.md     设计输入表：调研事实、默认方案、待拍板决策
│   ├── memory_schema_template.md  记忆条目 schema 与四种格式示例
│   ├── evaluation_plan_template.md 三层次评测集设计表
│   └── privacy_policy_template.md PII 类型与脱敏策略表
└── scripts/
    ├── discover_memory_context.py 项目勘察：事实 + 证据 + 默认方案
    ├── memory_lint.py             记忆库体检：PII、来源、时间戳、重复
    └── importance_scoring.py      重要性评分与压缩淘汰建议
```

## 脚本用法

### discover_memory_context.py：项目调研

```bash
python scripts/discover_memory_context.py <项目目录>
python scripts/discover_memory_context.py <项目目录> --json
```

输出带 `文件:行` 证据的事实（区分**代码命中**与**仅在文档中提及**），覆盖已有记忆实现、存储后端、提取与检索方式、PII 处理、schema、用户绑定，并给出默认方案。

### memory_lint.py：记忆库体检

```bash
python scripts/memory_lint.py memories.json
python scripts/memory_lint.py memories.json --check        # 存在 ERROR 退出码 1
python scripts/memory_lint.py memories.json --similarity 0.7 --max-length 200
```

检查项：身份证/银行卡/密码/密钥类明文（ERROR）、手机号/邮箱（WARN）、缺 source 与 created_at、认知类型未标注、情景记忆缺 occurred_at、疑似关键记忆未设 protected、单条过长、重复条目（中文按二元组比对）。

### importance_scoring.py：重要性评分

```bash
python scripts/importance_scoring.py memories.json --threshold 0.3 --half-life 60
python scripts/importance_scoring.py memories.json --top 20 --format csv
```

四因素加权：访问频率 + 时间衰减（指数半衰期）+ 情感强度 + 信息独特性；`protected` 的记忆永不淘汰。输出按得分升序排列，标注「可删除 / 可压缩 / 保留」。

## 两条关键纪律

1. **提取结果必须满足选择性、抽象化、结构化**——丢弃「搜索返回 3 个选项」这类短期细节，把「本次靠窗」抽象为「偏好靠窗座位」，用可检索字段保存。
2. **需要「算」的操作下沉到代码**——聚合统计、冲突检测、约束执行（如护照到期预警）交给确定性函数，不要让 LLM 心算。
