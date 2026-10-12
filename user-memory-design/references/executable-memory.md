# 进阶形态：User as Code（可执行记忆）

前面四种格式本质上都是**文本**：擅长召回单条事实，却要把**聚合、冲突检测和约束执行**交给 LLM「心算」。

User as Code 把用户状态改成**带类型的可执行对象**，并把规则写成普通函数，让「表示」和「推理」使用同一种可验证介质。

## 1. 核心机制：预写日志 + 检查点

借鉴数据库的「预写日志 + 检查点」机制：

1. 会话结束后，先把**事实追加到只增日志**（append-only log）；
2. 再**定期从完整日志重建带类型的状态**（checkpoint）。

这样既保留原始证据，也能得到**可查询、可执行**的派生状态。

## 2. 类型化状态示例

```python
state = {
    "passport": PassportInfo(
        number="AB1234567",
        country="US",
        expiry_date=date(2025, 2, 18),
    ),
    "trips": [
        Trip(destination="Tokyo", departure_date=date(2025, 1, 15), is_international=True),
        ...
    ],
}
```

带类型的状态把原本需要 LLM「读一遍再心算」的操作，交给**确定性函数**。

## 3. 三类操作从 LLM 转移到代码

### 3.1 聚合统计

```python
count(
    trip for trip in state.trips
    if trip.is_international and year(trip.departure_date) == 2025
)
# => 2
```

### 3.2 冲突发现

把当前用药与过敏史交叉比对：

```python
def check_drug_allergy(profile):
    for medication in profile.current_medications:
        for allergy in profile.allergies:
            if medication.drug_class == allergy.drug_class:
                emit_conflict(medication, allergy)
```

### 3.3 约束执行

状态更新后自动检查护照有效期，**不必等用户再次检索**——这正是第三层「主动服务」的实现方式：

```python
def check():
    for trip in state.trips:
        if trip.is_international:
            days = date_difference(state.passport.expiry_date, trip.departure_date)
            if days < 180:
                alert("passport expires too soon", trip, days)
```

## 4. 什么时候值得用

| 场景 | 是否值得 | 理由 |
| --- | --- | --- |
| 需要聚合统计（多少次国际行程） | 是 | 文本格式要靠 LLM 心算，易错 |
| 需要冲突检测（用药 vs 过敏） | 是 | 规则确定，代码执行可靠 |
| 需要主动预警（护照即将过期） | 是 | 状态更新即触发，不必等检索 |
| 只需要召回单条事实 | 否 | 文本格式成本更低 |
| 记忆类型不稳定、频繁新增 | 谨慎 | 类型定义会成为维护负担 |

**判断口径**：凡是「每次都要 LLM 读一遍再算」的操作，且规则可以确定化，就该下沉到代码。

## 5. 与状态栏的分工

| 维度 | User as Code | Agent 状态栏（`agent-status-design`） |
| --- | --- | --- |
| 作用域 | 跨会话的用户状态 | 单会话内的运行时状态 |
| 典型内容 | 护照、行程、用药、过敏 | 工具调用计数、TODO、当前目录 |
| 更新时机 | 会话结束后提取 + 定期重建 | 每轮或每事件 |
| 共同点 | **都由代码计算，不交给 LLM 统计** | 同左 |

两者可以衔接：状态栏展示「当前会话进度」，User as Code 提供「跨会话的用户事实」，约束检查可复用同一套规则函数。

## 6. 落地建议

1. 先用文本格式跑通提取与检索，观察哪些查询需要「算」才能回答。
2. 把这类查询对应的状态**类型化**（定义 dataclass / struct）。
3. 保留只增日志作为原始证据，状态从日志重建。
4. 把冲突检测与约束写成函数，在状态更新后自动执行。
5. 只把需要「算」的字段类型化，其余留给文本格式——**混合使用成本最低**。

## 7. 自检

- [ ] 需要聚合、冲突检测、约束执行的场景已下沉到代码
- [ ] 状态有类型定义，字段可枚举
- [ ] 保留只增日志，状态可重建
- [ ] 约束在状态更新后自动触发，不依赖用户检索
- [ ] 未把稳定的文本召回场景过度工程化
