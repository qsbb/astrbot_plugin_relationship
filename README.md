# 凝心溯溪-情

> 版本号以 `metadata.yaml` 为唯一事实源，逐版变更见 `CHANGELOG.md`。
> AstrBot 兼容范围：`>=4.16,<5`；metadata 当前声明 `aiocqhttp`。账号归属本身按 AstrBot 通用平台 ID/UID/UMO 工作，其他适配器能否使用取决于是否提供这些事件字段。
> 聊天命令：`/rel status`、`/rel reset`；管理页面入口：AstrBot 插件详情中的 `pages/manager`（关系分数只读，关系总览可快捷进入账号归属编辑，账号归属与设置可编辑）。

> **边界说明**：本插件记录关系状态，并把语气、篇幅与主动性等关系表达约束注入 LLM 请求；它不会自动执行静默建议，不会接管发送，不会授予权限，也不改变事实判断。通用收尾方式、是否静默与如何交付由言显式决定。

`astrbot_plugin_relationship` 是凝心溯溪系列关系模块，统一管理 bot 对用户与会话的疲劳情绪、短期亲近/戒备倾向，以及长期好感、信任和熟悉度，并输出结构化只读状态快照与行为建议。管理员可以把多个平台账号登记为同一自然人，并按关系人格决定哪些长期关系和相关记忆可以连续。它不接管发送、不生成内容、不授予权限，也不执行平台管理动作。

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
- **时间尺度分层**：会话疲劳和短期态度按分钟至小时恢复；好感按天至周变化；信任由显式事件驱动；熟悉度长期单调累积。
- **维度隔离**：短期烦躁不会直接降低长期好感。
- **关系人格隔离**：长期关系键包含 `relationship_profile_id`；同一自然人在同一人格内跨平台共享，不同人格之间不继承关系。管理页只会把管理员已登记自然人的多个人格记录合并为一行展示，未登记账号不会自动合并；短期疲劳和态度仍按当前会话与原始账号隔离。
- **证据强度统一**：可信语义事件使用截断到 `0~1` 的 `confidence × severity`，再叠加平滑衰减的早期可塑性；普通聊天不受该权重影响。
- **规则驱动数值**：LLM 只接收表达建议，不允许直接修改关系数值。
- **安全可达**：命令和紧急求助不会被硬静默；任何状态不得导致攻击、报复或误导。
- **身份双轨**：自然人映射只服务于关系与记忆连续性；权限、主人/黑名单和平台管理始终使用当前平台原始账号。

## 跨平台账号归属

管理页的“账号归属”用于把 QQ、Telegram 或其他适配器上的账号明确登记为同一自然人。绑定是
管理员手动确认，不根据昵称、头像或聊天内容自动猜测。

1. 先在目标平台与 Bot 私聊一次，再刷新本插件管理页。“关系总览”会为尚未归属的关系显示“快速归属”，自动带出事件中能够确认的平台 ID、UID、Bot ID、完整私聊 UMO、昵称和关系人格；可直接保存为新身份，也可从下拉框合并到已有自然人。已归属的关系显示“编辑归属”。所有内容都需要管理员确认后才会保存。
2. 若适配器未提供某个字段，页面会保留为空。可在该平台发送 AstrBot 的 `/sid` 后手工补齐；群聊产生的 UMO 不会被当作私聊 UMO，必须先私聊一次才能自动带出。
3. 填写自然人的显示名称；内部 ID 可留空自动生成。为其他平台重复第一步，或在“账号归属”中用 `+` 添加账号行。
4. 每个平台账号的 `平台 ID` 与 `UID` 必填；`Bot ID` 强烈建议填写，用于迁移已有关系状态并避免同一适配器多 bot 串号；`UMO` 建议填写，使记忆插件能精确读取原会话。`记忆人格 ID` 用于标明该账号的记忆属于哪个人格，留空时使用默认人格。
5. 选择本次操作的关系人格。可选的一次性初始关系只有“中性 / 已认识 / 有好感”三档，仅允许在尚未互动的“关系人格 + 自然人”上应用一次；不接受直接改分。
6. 普通保存后，已有账号状态只会迁移一次到该人格的自然人状态；后续不再维护账号镜像，避免账号解绑或换绑时把旧关系带给别人。只有管理员明确使用“合并账号 / 合并身份”时，来源账号或来源自然人的独立关系状态才会并入目标自然人；合并覆盖所有已知关系人格，并保留账号、互动次数和记忆归属。
7. 身份列表中的“合并”可把误建的重复自然人并入另一个身份。选择目标后需在 8 秒内再次确认；来源身份随后移除。账号、UMO、关系和记忆归属存在冲突或目标超过 20 个账号时会拒绝。合并使用回滚与持久化恢复记录；进程在两份数据落盘之间中断时，会在下次启动继续完成关系迁移。
8. “解除归属”只移除自然人映射，不删除关系、白名单资格或 Memory Companion 原始记忆。只有一个账号时，现有关系自动迁回该账号；有多个账号时必须明确选择一个承接关系，其余账号解除归属后各自从新关系开始。若白名单原先填写自然人 ID，插件会保留原项，并补充这些原账号的 UID 等价写法，避免解除后实际掉出白名单。承接账号缺少 Bot ID、已有另一份冲突关系或落盘失败时会整笔拒绝并回滚。

AstrBot `/sid` 的字段含义见[官方命令文档](https://docs.astrbot.app/use/command.html)。同一
“平台 ID + UID”只能属于一个自然人；一个自然人最多登记 20 个账号。

### 连续与隔离

| 数据 | 绑定后行为 |
|---|---|
| 好感、信任、熟悉度、长期互动次数 | 仅在同一关系人格内跨已绑定账号共享 |
| 当前群/私聊的疲劳、压力、短期亲近与戒备 | 按当前人格、会话和原始账号隔离，不跨平台复制；重启后短期态度清空 |
| Memory Companion 记忆 | 仅私聊从标记为同一关系人格的其他已绑定账号召回相关片段 |
| 序的主人、友好、保护、黑名单与群权限 | 始终使用原始平台账号，不继承 |
| 记忆与关系原始数据 | 解除账号归属时，关系迁回管理员选定的平台账号；Memory Companion 原始记忆不删除，也不会在缺少账号映射时自动重新关联 |

当前记忆桥接适配
[`menglimi/astrbot_plugin_memory_companion`](https://github.com/menglimi/astrbot_plugin_memory_companion)
公开的 `get_active_bridge()` / `compose_injection()` 接口。情只读取其他已验证且 `memory_profile_id` 与当前关系人格一致的账号记忆，
不改写该插件的身份解析、数据库或当前账号正常写入流程；未安装、未加载或无相关命中时，关系共享
仍正常工作，只跳过跨平台记忆补充。注入内容有总字符上限，并明确标为资料而非指令。

绑定后如需只让某个关系人格进入高好感白名单，请在 `AFFINITY_WHITELIST_USER_IDS` 填
`profile_id/person_id`；只填旧式 `person_id` 会兼容为所有关系人格均命中。一次性“有好感”初始关系不会自动加入白名单，也不会绕过信任、熟悉度或朋友区门槛。关系明细中的“删除关系”只删除所选关系人格的长期状态，不读取或改写白名单；该行会暂时消失，下一次有效互动重建关系后仍按原白名单配置判断。确认提交后页面会进入“删除中”，不会再用超时或取消提示覆盖正在处理的结果。

### 关系人格

插件优先解析本轮实际生效的 AstrBot Persona ID，并通过 `RELATIONSHIP_PERSONA_PROFILE_MAP`
映射为稳定的关系人格。例如 `persona_qq=companion,persona_tg=companion` 可让两个平台上的同一角色共享关系；未配置映射的 Persona 会得到自己的隔离关系人格。无法取得 Persona 时使用 `RELATIONSHIP_DEFAULT_PROFILE_ID`。
管理页会同时列出配置映射、当前会话缓存和已有关系状态中的人格，因此运行期自动隔离的人格也可选择并设置一次性初始关系。

升级前的 schema v3 历史无法自动判断属于哪个 Persona，因此只会迁入
`RELATIONSHIP_LEGACY_PROFILE_ID` 指定的一个人格，不会复制给所有人格。该选项应在首次启动迁移前确定。

`identity_registry.json` 包含管理员填写的平台账号标识，不加密。请按 AstrBot 数据目录的敏感配置
级别限制文件权限和备份访问，不要把该文件提交到公开仓库。

## 核心接口

跨插件消费者应通过入口类的版本化只读契约读取派生快照：

```python
contract = plugin.relationship_snapshot_contract()
snapshot = await plugin.get_relationship_snapshot(
    bot_id,
    user_id,
    group_id,
    relationship_profile_id="companion",
    person_id="summer",
)
```

契约名为 `relationship.snapshot`、版本为 `1.0`。返回值只包含 `mood`、
`willingness`、`relationship_tier`、`behavior` 和 `silence`，不会暴露好感、信任、
熟悉度原始分数。兼容的言插件会优先从 `ningxin.request_context` 1.0 读取同轮快照，
并把情登记的关系表达片段与序、知的片段稳定排序和去重；未安装言时情仍直接注入表达约束。

需要向一个已绑定自然人的明确私聊会话交付内容时，可使用严格的身份校验契约：

```python
contract = plugin.delivery_identity_contract()
result = await plugin.resolve_delivery_identity(
    person_id="summer",
    recipient_umo="aiocqhttp:FriendMessage:123456",
)
```

契约名为 `relationship.delivery_identity`、版本为 `1.0`。它要求 `recipient_umo` 与账号归属中填写的 UMO 完全一致、消息类型明确为 `FriendMessage`、`PrivateMessage` 或 `DirectMessage`，且对应账号同时具备 Bot ID 与 UID。非空 UMO 在整个自然人注册表中必须唯一，契约也会全局复核它只属于指定自然人。验证成功后只返回 `relationship.snapshot@1` 的派生关系档位、语气、篇幅、主动性和静默建议，不返回 UID、Bot ID、显示名或原始好感/信任/熟悉度。该契约只证明“这个会话属于这个自然人”，不授予发送权限；是否为主人私聊仍由序单独授权，最终是否发送由言决定。

可信工作流可以通过写入契约提交已经确认的语义事实；平台聊天原文不能直接自证这些事件：

```python
contract = plugin.relationship_event_contract()
result = await plugin.submit_relationship_event({
    "version": "1.0",
    "bot_id": bot_id,
    "user_id": user_id,
    "relationship_profile_id": "companion",
    "person_id": "summer",
    "event_id": "workflow:task-42:completed",
    "kind": "help_received",
    "source": "verified_action",
    "confidence": 0.9,
    "severity": 0.7,
    "evidence_refs": ["task:42"],
})
```

契约名为 `relationship.event`、版本为 `1.0`。允许的事件种类与来源应从契约声明读取；
`event_id` 必填并按关系人格、bot、用户和会话幂等。账本保存来源、强度与证据引用，不保存消息正文。
已登记账号即使未传 `platform_id`，也只会在 `bot_id + user_id` 能唯一定位自然人时写入其主关系状态；跨平台同 UID 存在歧义时必须补充 `platform_id` 或 `person_id`，否则接口明确拒绝。

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

## 短期状态

`core/mood.py` 迁移自 `astrbot_plugin_conversation_flow` v0.6.0，保持以下行为一致：

- 滑动窗口互动频率、相似文本复读和连续对话轮数三类疲劳信号；
- `normal / lazy / annoyed` 三档状态；
- 极低意愿时按配置概率硬静默；
- 连续硬静默上限；
- `/` 开头命令和明确求助、紧急消息不硬静默；
- 时间流逝后窗口自然清空并恢复意愿。

`core/affect.py` 另行维护可信语义事件触发的短期 `warmth / guardedness`。它按“关系人格 + 当前会话 + 原始用户”隔离并指数衰减，仅改变本轮语气：戒备优先于亲近，而会话疲劳又优先于二者。它不改变长度、静默概率、权限、安全判断或长期分数，也不持久化。

## 长期关系

### 好感

普通有效互动仅产生很小的关系增量，夸奖、得到帮助、被冒犯等事件按配置缓慢增减。非白名单用户默认最多进入朋友区（默认上限 68）；只有显式白名单用户同时满足信任与熟悉度门槛，才允许进入高好感区。达到高好感区后，普通消息不再继续刷分。每日正向与负向变化分别计额；长期无互动时向中位数缓慢回归。

可信语义事件造成的好感与信任变化会乘以证据强度，并在关系早期获得小幅、连续衰减的可塑性增益；熟悉度仍按一次有效互动增长。证据逐渐积累后自然接近正常权重，不使用突变阈值。一次性初始关系为固定先验：`neutral = 50/50/0`、`acquainted = 56/55/15`、`fond = 64/60/25`（好感/信任/熟悉度）。`fond` 仍低于高好感区。

### 关系页面

插件提供 `pages/manager` 管理页：关系总览按关系人格展示关系分布、门槛与明细且不允许直接改分；管理员已登记的同一自然人若存在多个人格记录，会合并为一行并按互动次数加权展示分数，未登记账号逐条显示。每行可快捷打开账号归属编辑器，也可在 8 秒二次确认后单独删除关系记录；多人格汇总行必须先明确选择一个人格，一次只删所选人格，删除关系不会改白名单。未绑定账号会用最近真实会话的可用元数据预填，并可直接合并到已有自然人。身份列表支持编辑、解除归属及两个身份间的显式合并；解除多账号自然人时必须选择关系承接账号。旧版本遗留的孤立关系显示为“待处理历史关系”，可显式合并，不再显示“账号归属已删除”占位名称。“账号归属”维护自然人、平台账号、记忆人格与一次性初始关系；已有互动的快捷归属不会用初始关系覆盖当前状态。“设置”左侧使用中文名称，输入控件旁提供大白话说明，内部配置键和值保持不变。除旧数据迁移归属需重启外，其余配置热应用。关系长表在页面内提供明显的横向滚动条和固定表头，长 ID 与账号表单会按屏幕宽度换行或分列，不再撑乱整页。

### 信任

信任拆分为可靠性、善意、诚信、认知四个维度，仅由带可信来源和必要证据引用的显式事实事件驱动。平台消息原文不能自证履约或失约；本插件不登记、追踪或执行承诺。

### 熟悉度

有效互动使熟悉度单调增长，增量随当前熟悉度提高而递减。熟悉度不会因时间流逝而下降。

## 命令

- `/rel status`：查看当前用户与会话的状态快照；Persona 解析器暂时不可用时沿用该会话最近确认的人格。
- `/rel reset`：只重置当前关系人格下的会话短期状态与用户长期关系；Persona 解析器暂时不可用时沿用会话缓存，一次性初始关系的已应用标记会保留，不能借重置重复设置。

命令消息不参与情绪、好感或熟悉度累积。

## 持久化

长期状态与只追加事件账本写入 AstrBot 数据目录下的 `relationship_state.json`（schema v4），自然人账号归属独立写入 `identity_registry.json`（schema v1），用于快捷预填的最近会话账号元数据写入 `account_observations.json`（schema v1，最多 1000 项）。这些文件均使用临时文件、刷盘与原子替换。跨文件身份合并或解除归属期间会短暂创建 `identity-merge-pending.json`（schema v1）；完成后立即删除，异常中断时用于下次启动恢复，不包含消息正文。解除归属的恢复记录保存完整身份快照和白名单等价项；关系冲突时恢复原身份，无法解析或尚未完成恢复时暂停新的身份与关系写入，避免覆盖恢复依据。账号元数据只包含平台 ID、UID、Bot ID、已确认的私聊 UMO、昵称、关系人格和观察时间，不保存消息原文；群聊 UMO 不会写入。关系状态支持 v0/v1/v2/v3 迁移；迁移前会保留一次 `relationship_state.json.v<旧版本>.bak`。负数、非数字或高于当前版本的 schema 会进入只读保护，不覆盖原文件；保护期间账号归属的保存、解除、合并及待完成事务恢复也会暂停，避免身份与关系只更新一边。账本保存关系人格、作用域键、事件 ID、来源、置信度、严重度、证据引用、应用结果和拒绝原因，不保存消息原文。会话疲劳与短期态度仅保存在内存中。

## 配置

所有窗口、阈值、权重、每日上限、衰减速率和行为开关均在 `_conf_schema.json` 中声明。`core/config.py` 提供同值 `DEFAULTS` 兜底。

关键配置：

- `MOOD_*`：情绪窗口、三档阈值、静默建议概率与连续上限；情只给建议，最终是否发送由言决定。
- `AFFECT_*`：短期亲近/戒备开关、半衰期、正负增量与语气触发阈值。
- `DYNAMICS_*`：可信语义事件的关系早期可塑性增益与累计证据量半衰参数。
- `RELATIONSHIP_DEFAULT_PROFILE_ID`：无法取得 Persona 时使用的关系人格。
- `RELATIONSHIP_LEGACY_PROFILE_ID`：schema v3 及更早历史唯一迁入的人格；首次迁移后修改不会重新分配历史。
- `RELATIONSHIP_PERSONA_PROFILE_MAP`：`AstrBot Persona ID=关系人格 ID` 映射，支持逗号、分号或换行分隔。
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

覆盖情绪迁移回归、短期态度半衰、事件强度与平滑可塑性、每日好感上限、初始关系幂等、Persona/自然人/账号作用域隔离、账号与身份显式合并、解除归属后的单账号承接、白名单资格迁移、冲突预检、失败回滚与中断恢复、未决事务写屏障、逐人格关系删除、跨平台记忆身份复核、持久化读写与 v4 迁移、提示词安全约束。

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
│   ├── affect.py
│   ├── affinity.py
│   ├── trust.py
│   ├── familiarity.py
│   ├── dynamics.py
│   ├── decay.py
│   ├── repository.py
│   ├── identity_registry.py
│   ├── profiles.py
│   ├── policy.py
│   └── config.py
├── pages/manager/
└── tests/
```

## 维护约定

任何可观察功能、配置项或安全边界的增删改，必须在同一批变更中同步 README、CHANGELOG 的
`Unreleased`、配置 schema 与回归测试。版本号在实现、文档和验证完成后由发布者确认。
