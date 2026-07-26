# 凝心溯溪-情

`astrbot_plugin_relationship` v0.3.1 是凝心溯溪系列关系模块，统一管理 bot 对用户与会话的短期情绪、长期好感、信任和熟悉度，并输出结构化只读状态快照与行为建议。它不接管发送、不生成内容、不授予权限，也不执行跨插件动作。

## 设计原则

- **统一管理、分维度计算**：各计算器只返回变化量，`RelationshipStateManager` 统一合并、限幅和持久化。
- **时间尺度分层**：情绪按分钟至小时恢复；好感按天至周变化；信任由显式事件驱动；熟悉度长期单调累积。
- **维度隔离**：短期烦躁不会直接降低长期好感。
- **作用域分离**：长期关系使用 `bot_id + user_id`，短期情绪使用 `bot_id + group_id/private user_id`。
- **规则驱动数值**：LLM 只接收表达建议，不允许直接修改关系数值。
- **安全可达**：命令和紧急求助不会被硬静默；任何状态不得导致攻击、报复或误导。

## 核心接口

其他插件只应调用以下三个入口：

```python
from astrbot_plugin_relationship.core.manager import RelationshipStateManager
from astrbot_plugin_relationship.core.models import InteractionEvent, RelationshipScope

snapshot = await manager.record(event)
snapshot = await manager.get_snapshot(bot_id, user_id, group_id)
await manager.reset(RelationshipScope(bot_id, user_id, group_id))
```

`RelationshipSnapshot` 包含：

- `mood`：`normal / lazy / annoyed`
- `willingness`：回复意愿，范围 `0~100`
- `affinity / trust / familiarity`：长期关系维度，范围 `0~100`
- `trust_dimensions`：可靠性、善意、诚信、认知四个信任维度
- `behavior`：包含语气、长度、主动性、边界与静默建议的结构化只读对象
- `response_style / should_silence / prompt_fragment`：0.1.0 兼容字段；插件入口不会执行或注入这些建议

各计算器属于内部实现，不应被外部插件直接调用。

## 情绪迁移

`core/mood.py` 迁移自 `astrbot_plugin_conversation_flow` v0.6.0，保持以下行为一致：

- 滑动窗口互动频率、相似文本复读和连续对话轮数三类疲劳信号；
- `normal / lazy / annoyed` 三档状态；
- 极低意愿时按配置概率硬静默；
- 连续硬静默上限；
- `/` 开头命令和明确求助、紧急消息不硬静默；
- 时间流逝后窗口自然清空并恢复意愿。

## 长期关系

### 好感

普通有效互动仅产生很小的关系增量，夸奖、得到帮助、被冒犯等事件按配置缓慢增减。非白名单用户默认最多进入朋友区（默认上限 68）；只有显式白名单用户同时满足信任与熟悉度门槛，才允许进入高好感区。达到高好感区后，普通消息不再继续刷分。每日正向与负向变化分别计额；长期无互动时向中位数缓慢回归。

### 关系页面

插件提供 `pages/manager` 管理页，样式与凝心溯溪-核更新管理器保持一致：使用深色青绿色控制台界面，展示关系总数、高好感/朋友/谨慎分布、白名单门槛、好感柱状分布与用户关系明细。页面只读，不直接修改状态或聊天行为。

### 信任

信任拆分为可靠性、善意、诚信、认知四个维度，仅由带可信来源和必要证据引用的显式事实事件驱动。平台消息原文不能自证履约或失约；本插件不登记、追踪或执行承诺。

### 熟悉度

有效互动使熟悉度单调增长，增量随当前熟悉度提高而递减。熟悉度不会因时间流逝而下降。

## 命令

- `/rel status`：查看当前用户与会话的状态快照。
- `/rel reset`：重置当前会话情绪与当前用户长期关系。

命令消息不参与情绪、好感或熟悉度累积。

## 持久化

长期状态与只追加事件账本写入 AstrBot 数据目录下的 `relationship_state.json`（schema v2），并支持旧版本迁移。账本保存事件 ID、来源、置信度、证据引用、应用结果和拒绝原因，不保存消息原文；事件 ID/去重键保证重试幂等。写入采用临时文件与原子替换；短期双层情绪仅保存在内存中。

## 配置

所有窗口、阈值、权重、每日上限、衰减速率和行为开关均在 `_conf_schema.json` 中声明。`core/config.py` 提供同值 `DEFAULTS` 兜底。

关键配置：

- `MOOD_*`：情绪窗口、三档阈值、硬静默概率与连续上限。
- `AFFINITY_*`：各事件好感增减、每日正负独立额度和普通消息冷却。
- `TRUST_*`：可信显式事实对各信任维度的变化量。
- `FAMILIARITY_*`：熟悉度基础增量、递减曲线与冷却。
- `DECAY_*`：长期维度向中位数回归速率。
- `POLICY_*`：提示词片段与表达风格开关。
- `SAVE_INTERVAL_SECONDS`：持久化节流间隔。

## 测试

测试不依赖 AstrBot 运行时：

```bash
python -m pytest -q
```

覆盖情绪迁移回归、每日好感上限、衰减恢复、群聊/私聊和用户作用域隔离、维度隔离、持久化读写与版本迁移、提示词安全约束。

## 目录

```text
astrbot_plugin_relationship/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── core/
│   ├── manager.py
│   ├── models.py
│   ├── mood.py
│   ├── affinity.py
│   ├── trust.py
│   ├── familiarity.py
│   ├── decay.py
│   ├── repository.py
│   ├── policy.py
│   └── config.py
└── tests/
    └── test_core.py
```
