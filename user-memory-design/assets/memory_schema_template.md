# 记忆条目 Schema 模板

使用说明：本文件定义**记忆条目的数据结构**，是代码里落库与检索的契约，**不是提示词模板**。
按数据重要性分级选格式（关键少量用高表达力格式，大量非关键用低成本格式），多数场景为混合模式。

---

## 1. 通用字段（所有格式都要有）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 稳定唯一标识，供更新与引用 |
| `user_id` | string | 是 | 记忆与用户绑定 |
| `type` | enum | 是 | `episodic` / `semantic` / `procedural`（见 `../references/memory-types.md`） |
| `content` | string 或 object | 是 | 事实内容，格式按第 2 节选择 |
| `created_at` | timestamp | 是 | 提取时间 |
| `occurred_at` | timestamp | 否 | 事件发生时间（情景记忆必填） |
| `source` | string | 是 | 来源会话 ID / 消息 ID，便于回溯 |
| `confidence` | float | 否 | 提取置信度 |
| `access_count` | int | 否 | 被检索命中次数（供重要性评分） |
| `protected` | bool | 否 | 为 true 时不参与压缩淘汰（证件、过敏、禁忌必设） |

## 2. 四种格式的条目示例

### 2.1 Simple Notes（原子事实，低成本）

```json
{
  "id": "m_001",
  "user_id": "u_123",
  "type": "semantic",
  "content": "用户邮箱：john@example.com",
  "created_at": "2025-09-14T10:30:00Z",
  "source": "session_88#msg_12",
  "protected": false
}
```

适用：大量且非关键的对话事实。开销极低、O(1)，但关联性丢失。

### 2.2 Enhanced Notes（整段叙事，语义完整）

```json
{
  "id": "m_002",
  "user_id": "u_123",
  "type": "semantic",
  "content": "用户在 TechCorp 担任高级软件工程师，专注于机器学习已有三年，目前领导一个推荐系统项目，团队 5 人。",
  "created_at": "2025-09-14T10:31:00Z",
  "source": "session_88#msg_15"
}
```

适用：需保留叙事完整性、更新不频繁的记忆。缺点是冗余、难更新、长文难检索。

### 2.3 JSON Cards（类别 → 子类别 → 键值）

```json
{
  "id": "m_003",
  "user_id": "u_123",
  "type": "semantic",
  "content": {
    "work": {
      "company": { "name": "TechCorp" },
      "position": { "title": "高级软件工程师", "team_size": 5 }
    }
  },
  "created_at": "2025-09-14T10:31:00Z",
  "source": "session_88#msg_15"
}
```

适用：分类清晰、需部分更新的记忆。改 `work.position.title` 不影响 `work.company.name`。

### 2.4 Advanced JSON Cards（带消歧与来源）

```json
{
  "id": "m_004",
  "user_id": "u_123",
  "type": "semantic",
  "content": { "fact": "心脏科医生：张医生" },
  "person": "父亲",
  "relationship": "家庭成员",
  "backstory": "用户提到父亲去年做了心脏支架手术，主治医生姓张",
  "created_at": "2025-03-12T09:00:00Z",
  "source": "session_41#msg_7",
  "protected": true
}
```

适用：关键且少量、需要消歧的数据（多个家人、多辆车、多位同名联系人）。

关键字段语义：
- `backstory`：**为什么**存这条信息（获取背景）
- `person`：**为谁**存（主体身份）
- `relationship`：与用户的关系

## 3. 情景记忆的元数据（多维检索用）

```json
{
  "id": "m_005",
  "user_id": "u_123",
  "type": "episodic",
  "content": "用户订了下周五去东京的 ANA 航班",
  "occurred_at": "2025-01-15T00:00:00Z",
  "created_at": "2025-01-10T14:20:00Z",
  "metadata": {
    "task_id": "booking_tokyo",
    "topic": "travel",
    "sentiment": "positive",
    "entities": ["ANA", "Tokyo"]
  },
  "source": "session_12#msg_3"
}
```

支持按时间、主题、实体、任务标识**组合检索**（如「我们上次讨论预算是什么时候」）。

## 4. 程序记忆（可复用流程）

```json
{
  "id": "m_006",
  "user_id": "u_123",
  "type": "procedural",
  "content": {
    "name": "订票流程",
    "steps": ["搜索直飞航班", "确认座位偏好", "使用常旅客号码", "订特殊餐食"],
    "applicable_when": "用户要求预订机票"
  },
  "backstory": "从用户最近 5 次订票会话中抽象出的稳定流程",
  "confidence": 0.82,
  "created_at": "2025-09-14T11:00:00Z"
}
```

## 5. 类型化状态（User as Code，需要「算」的场景）

```python
state = {
    "passport": PassportInfo(number="AB1234567", country="US", expiry_date=date(2025, 2, 18)),
    "trips": [
        Trip(destination="Tokyo", departure_date=date(2025, 1, 15), is_international=True),
    ],
    "allergies": [...],
    "current_medications": [...],
}
```

配套规则函数（聚合 / 冲突检测 / 约束执行）：

```python
def check(state):
    for trip in state.trips:
        if trip.is_international:
            days = date_difference(state.passport.expiry_date, trip.departure_date)
            if days < 180:
                alert("passport expires too soon", trip, days)
```

适用：需要聚合统计、冲突检测、主动预警的字段。其余字段仍用文本格式——**混合使用成本最低**。

## 6. 混合模式的分级建议

| 数据 | 格式 | 理由 |
| --- | --- | --- |
| 偏好、关键人物关系 | Advanced JSON Cards | 消歧 + 可检索 |
| 稳定属性（邮箱、会员号） | JSON Cards / Simple Notes | 精确命中 |
| 事件经历 | 情景记忆 + 元数据 | 按时间线检索 |
| 可复用流程 | 程序记忆 | 能被执行 |
| 大量对话事实 | Simple Notes | 成本低 |
| 需计算的字段（证件有效期、过敏冲突） | 类型化状态 + 规则函数 | 确定性执行 |

## 7. 质量检查

落库后用 `../scripts/memory_lint.py` 体检：裸 PII、缺来源/时间戳、重复条目、原子性。
