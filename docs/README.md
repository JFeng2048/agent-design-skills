# docs：Skill 开发文档

这里的文档是**写给本仓库作者（以及接手维护的人）看的**，不是给 skill 使用者看的。

使用者读的是每个 skill 目录下的 `SKILL.md` 与 `README.md`；本目录回答的是另一类问题：

- 为什么要做成 skill，而不是写成一篇教程或一份 checklist？
- 为什么每个 skill 都长成「SKILL.md + references + assets + scripts」这个样子？
- 为什么流程里要放「自主调研」这一步，而不是直接开干或反过来问一堆问题？
- 每个 skill 内部，步骤是怎么切分的、reference 为什么拆成这些篇、脚本为什么选这几个？

## 文档索引

| 文档 | 回答什么 |
| --- | --- |
| [skill-authoring.md](skill-authoring.md) | **通用方法论**：写这类 skill 的 7 条设计原则，每条都给出「为什么」 |
| [decisions.md](decisions.md) | **决策记录**：仓库层面的关键取舍与当时的理由（命名、语言、定位、合规、语言统计等） |
| [skills/system-prompt-design.md](skills/system-prompt-design.md) | 系统提示词 skill 的设计说明 |
| [skills/agent-status-design.md](skills/agent-status-design.md) | 状态栏 skill 的设计说明 |
| [skills/user-memory-design.md](skills/user-memory-design.md) | 用户记忆 skill 的设计说明 |
| [skills/rag-design.md](skills/rag-design.md) | RAG 管线 skill 的设计说明 |
| `images/` | 仓库 logo 与头像（`logo.png`、`logo-icon.png`） |

## 阅读顺序建议

1. 先读 `skill-authoring.md`——四个 skill 共享同一套方法论，读懂一次即可。
2. 再读 `decisions.md`——了解仓库层面的约束从哪来。
3. 需要改某个 skill 时，读 `skills/` 下对应的那篇。

## 一个总纲

四个 skill 覆盖的是 Agent 系统的四个层次，彼此不重叠、但会互相引用：

| Skill | 管的是 | 时间尺度 | 载体 |
| --- | --- | --- | --- |
| system-prompt-design | 静态指令：身份、规则、SOP、工具定义 | 不变（静态前缀） | 提示词 + 工具 schema |
| agent-status-design | 运行时状态：计数、TODO、环境、错误 | 单次会话内每轮更新 | 代码维护的状态栏 |
| user-memory-design | 跨会话用户事实：偏好、关系、事件 | 跨会话长期演化 | 记忆库 + 检索 |
| rag-design | 共享知识：文档、分块、检索、重排 | 随知识库更新 | 索引 + 检索管线 |

一句话：**提示词管「规则」，状态栏管「此刻」，记忆管「这个人」，RAG 管「这个世界」。**
