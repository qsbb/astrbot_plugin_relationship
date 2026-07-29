# 凝心溯溪-情

> 版本号以 `metadata.yaml` 为唯一事实源，逐版变更见 `CHANGELOG.md`。
> AstrBot 兼容范围：`>=4.16,<5`；metadata 当前声明 `aiocqhttp`。账号归属本身按 AstrBot 通用平台 ID/UID/UMO 工作，其他适配器能否使用取决于是否提供这些事件字段。
> 聊天命令：`/rel status`、`/rel reset`；管理页面入口：AstrBot 插件详情中的 `pages/manager`（关系总览只读，账号归属与设置可编辑）。

> **边界说明**：本插件记录关系状态，并把语气、篇幅与主动性等关系表达约束注入 LLM 请求；它不会自动执行静默建议，不会接管发送，不会授予权限，也不改变事实判断。通用收尾方式、是否静默与如何交付由言显式决定。

`astrbot_plugin_relationship` 是凝心溯溪系列关系模块，统一管理 bot 对用户与会话的短期情绪、长期好感、信任和熟悉度，并输出结构化只读状态快照与行为建议。管理员还可以把多个平台账号登记为同一自然人，使长期关系和相关记忆在私聊中连续。它不接管发送、不生成内容、不授予权限，也不执行平台管理动作。

## 系列导航

当前完整系列清单按知、言、序、情、声、核排列：

| 字 | 模块 | 命令/入口 | 说明 |
|---|---|---|---|
| [知](https://github.com/qsbb/astrbot_plugin_active_learner) | 知识学习 | `/memory` | 自动检索注入、多源学习、交叉验证 |
| [言](https://github.com/qsbb/astrbot_plugin_conversation_flow) | 对话调节 | `/convflow` | 沉默判断、智能分段、插话衔接 |
| [序](https://github.com/qsbb/astrbot_plugin_identity_guardian) | 身份管理 | `/idg` | 关系感知、权限边界、群组行动 |
| [情](https://github.com/qsbb/astrbot_plugin_relationship) | 关系状态 | `/rel` | 情绪、好感、信任、熟悉度状态记录与只读建议（本插件） |
| [声](https://github.com/qsbb/astrbot_plugin_voice_hub) | 语音合成 | Pages / LLM 工具 | 双 TTS 后端、多音色管理、AI 导演 |
| [核](https://github.com/qsbb/astrbot_plugin_update_manager) | 更新管理 | `/aup` | 安全检查、计划、串行更新与回滚 |

## 设计原则

- **统一管理、分维度计算**：各计算器只返回变化量，`RelationshipStateManager` 统一合并、限幅和持久化。
- **时间尺度分层**：情绪按分钟至小时恢复；好感按天至周变化；信任由显式事件驱动；熟悉度长期单调累积。
- **维度隔离**：短期烦躁不会直接降低长期好感。
- **作用域分离**：未绑定账号的长期关系使用 `bot_id + user_id`；已绑定账号使用稳定的 `person_id`。短期情绪仍按当前 bot 与群聊/私聊会话隔离。
- **规则驱动数值**：LLM 只接收表达建议，不允许直接修改关系数值。
- **安全可达**：命令和紧急求助不会被硬静默；任何状态不得导致攻击、报复或误导。
- **身份双轨**：自然人映射只服务于关系与记忆连续性；权限、主人/黑名单和平台管理始终使用当前平台原始账号。

## 跨平台账号归属

管理页的“账号归属”用于把 QQ、Telegram 或其他适配器上的账号明确登记为同一自然人。绑定是
管理员手动确认，不根据昵称、头像或聊天内容自动猜测。

1. 在每个平台分别发送 AstrBot 的 `/sid`，取得平台 ID、用户 ID、Bot ID 和完整会话 ID（UMO）。
2. 打开本插件管理页的“账号归属”，新建自然人并填写显示名称；内部 ID 可留空自动生成。
3. 为每个平台添加一行账号。`平台 ID` 与 `UID` 必填；`Bot ID` 强烈建议填写，用于合并已有关系状态并避免同一适配器多 bot 串号，留空时按该平台账号通配当前 bot；`UMO` 建议填写，使记忆插件能精确读取原会话。
4. 保存后，已有各账号关系状态会合并为一个自然人状态；后续任一账号互动都更新同一长期关系。

AstrBot `/sid` 的字段含义见[官方命令文档](https://docs.astrbot.app/use/command.html)。同一
“平台 ID + UID”只能属于一个自然人；一个自然人最多登记 20 个账号。

### 连续与隔离

| 数据 | 绑定后行为 |
|---|---|
| 好感、信任、熟悉度、长期互动次数 | 跨已绑定账号合并并持续同步 |
| 当前群/私聊的短期疲劳和压力 | 继续按当前会话隔离，不跨平台复制 |
| Memory Companion 记忆 | 仅私聊按当前问题只读召回其他已绑定账号的相关片段 |
| 序的主人、友好、保护、黑名单与群权限 | 始终使用原始平台账号，不继承 |
| 记忆与关系原始数据 | 删除账号归属时不删除，避免误伤历史数据 |

当前记忆桥接适配
[`menglimi/astrbot_plugin_memory_companion`](https://github.com/menglimi/astrbot_plugin_memory_companion)
公开的 `get_active_bridge()` / `compose_injection()` 接口。情只读取其他已验证账号的相关记忆，
不改写该插件的身份解析、数据库或当前账号正常写入流程；未安装、未加载或无相关命中时，关系共享
仍正常工作，只跳过跨平台记忆补充。注入内容有总字符上限，并明确标为资料而非指令。

绑定后如需让该自然人进入高好感白名单，请在 `AFFINITY_WHITELIST_USER_IDS` 填内部
`person_id`，不是任一平台 UID。

`identity_registry.json` 包含管理员填写的平台账号标识，不加密。请按 AstrBot 数据目录的敏感配置
级别限制文件权限和备份访问，不要把该文件提交到公开仓库。

## 核心接口

跨插件消费者应通过入口类的版本化只读契约读取派生快照：

```python
contract = plugin.relationship_snapshot_contract()
snapshot = await plugin.get_relationship_snapshot(bot_id, user_id, group_id)
```

契约名为 `relationship.snapshot`、版本为 `1.0`。返回值只包含 `mood`、
`willingness`、`relationship_tier`、`behavior` 和 `silence`，不会暴露好感、信任、
熟悉度原始分数。兼容的言插件会优先从 `ningxin.request_context` 1.0 读取同轮快照，
并把情登记的关系表达片段与序、知的片段稳定排序和去重；未安装言时情仍直接注入表达约束。

以下三个入口属于本插件内部状态层，供入口适配与测试使用，不作为跨插件契约：

```python
from astrbot_plugin_relationship.core.manager import RelationshipStateManager
from astrbot_plugin_relationship.core.models import InteractionEvent, RelationshipScope

snapshot = await manager.record(event)
snapshot = await manager.get_snapshot(bot_id, user_id, group_id)
await manager.reset(RelationshipScope(bot_id, user_id, group_id))
```

内部 `RelationshipSnapshot` 包含：

- `mood`：`normal / lazy / annoyed`
- `willingness`：回复意愿，范围 `0~100`
- `affinity / trust / familiarity`：长期关系维度，范围 `0~100`
- `trust_dimensions`：可靠性、善意、诚信、认知四个信任维度
- `behavior`：包含语气、长度、主动性、边界与静默建议的结构化只读对象
- `response_style / should_silence / prompt_fragment`：兼容字段；入口只注入表达约束，不执行静默或发送

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

插件提供 `pages/manager` 管理页：关系总览展示关系分布、门槛与明细且不允许直接改分；“账号归属”维护自然人与平台账号；“设置”修改配置并热应用。账号归属只改变后续状态解析和合并，不直接编辑关系数值。

### 信任

信任拆分为可靠性、善意、诚信、认知四个维度，仅由带可信来源和必要证据引用的显式事实事件驱动。平台消息原文不能自证履约或失约；本插件不登记、追踪或执行承诺。

### 熟悉度

有效互动使熟悉度单调增长，增量随当前熟悉度提高而递减。熟悉度不会因时间流逝而下降。

## 命令

- `/rel status`：查看当前用户与会话的状态快照。
- `/rel reset`：重置当前会话情绪与当前用户长期关系。

命令消息不参与情绪、好感或熟悉度累积。

## 持久化

长期状态与只追加事件账本写入 AstrBot 数据目录下的 `relationship_state.json`（schema v3），自然人账号归属独立写入 `identity_registry.json`（schema v1）。两者均使用临时文件与原子替换。关系状态支持 v0/v1/v2 旧版本迁移；账本保存事件 ID、来源、置信度、证据引用、应用结果和拒绝原因，不保存消息原文。短期双层情绪仅保存在内存中。

## 配置

所有窗口、阈值、权重、每日上限、衰减速率和行为开关均在 `_conf_schema.json` 中声明。`core/config.py` 提供同值 `DEFAULTS` 兜底。

关键配置：

- `MOOD_*`：情绪窗口、三档阈值、硬静默概率与连续上限。
- `AFFINITY_*`：各事件好感增减、每日正负独立额度和普通消息冷却。
- `TRUST_*`：可信显式事实对各信任维度的变化量。
- `FAMILIARITY_*`：熟悉度基础增量、递减曲线与冷却。
- `DECAY_*`：长期维度向中位数回归速率。
- `POLICY_*`：提示词片段与表达风格开关。
- `CROSS_PLATFORM_MEMORY_ENABLED`：私聊时是否从其他已绑定账号只读召回 Memory Companion 记忆。
- `CROSS_PLATFORM_MEMORY_TOP_K`：每个其他账号的最大召回数，默认 3。
- `CROSS_PLATFORM_MEMORY_MAX_CHARS`：单轮跨平台记忆补充总字符上限，默认 1200。
- `SAVE_INTERVAL_SECONDS`：持久化节流间隔。

## 版本与测试

版本号以 `metadata.yaml` 为唯一事实源，逐版变更见 `CHANGELOG.md`。事件审计、状态时间及好感/熟悉度冷却计算统一由规范化 `business_now` 驱动，未来或回退时间戳无法绕过冷却。

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
│   ├── identity_registry.py
│   ├── policy.py
│   └── config.py
├── pages/manager/
└── tests/
```

## 维护约定

任何可观察功能、配置项或安全边界的增删改，必须在同一批变更中同步 README、CHANGELOG 的
`Unreleased`、配置 schema 与回归测试。版本号在实现、文档和验证完成后由发布者确认。
