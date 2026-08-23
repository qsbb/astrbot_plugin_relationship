# 凝心溯溪-情

> 凝心溯溪系列关系模块：管理好感、信任、熟悉度、短期态度和关系边界，支持把多个平台账号归属于同一自然人，并为回复提供关系语气参考。

> **凝心溯溪系列** 当前完整插件清单为知、言、序、情、境、声、核、临：各插件职责独立、互不冲突，可按需组合使用，覆盖知识学习、对话调节、身份管理、关系状态、环境感知、语音、更新管理与具身桥接。

| 字 | 模块 | 说明 |
|----|------|------|
| [知](https://github.com/qsbb/astrbot_plugin_active_learner) | 知识学习 | 自动检索注入、多源学习、交叉验证 |
| [言](https://github.com/qsbb/astrbot_plugin_conversation_flow) | 对话调节 | 沉默判断、智能分段、插话衔接 |
| [序](https://github.com/qsbb/astrbot_plugin_identity_guardian) | 身份管理 | 关系感知、权限边界、群组行动 |
| [情](https://github.com/qsbb/astrbot_plugin_relationship) | 关系状态 | 情绪、好感、信任、熟悉度状态记录与只读建议(本插件) |
| [境](https://github.com/qsbb/astrbot_plugin_environment_awareness) | 环境感知 | 时间、天气、空气质量、预警与环境关心候选 |
| [声](https://github.com/qsbb/astrbot_plugin_voice_hub) | 语音合成 | 双 TTS 后端、多音色管理、AI 导演 |
| [核](https://github.com/qsbb/astrbot_plugin_update_manager) | 更新管理 | 安全检查、计划、串行更新与回滚 |
| [临](https://github.com/qsbb/astrbot_plugin_embodiment_bridge) | 具身桥接 | Quest 客户端桥接、实时对话与空间感知 |

## 当前实现信息

- 版本号以 `metadata.yaml` 的 `version` 为唯一事实源(逐版变更见 `CHANGELOG.md`,如有)。
- AstrBot 兼容范围：`>=4.16,<5`；metadata 声明支持平台 `aiocqhttp`。
- 命令入口：`/rel status`、`/rel reset`；页面入口：AstrBot 插件详情中的 `pages/manager` 管理页（关系分数只读，关系总览可快捷进入账号归属编辑，账号归属与设置可编辑）。

## 简介与定位

`astrbot_plugin_relationship` 是凝心溯溪系列关系模块，统一管理 bot 对用户与会话的疲劳情绪、短期亲近/戒备倾向，以及长期好感、信任和熟悉度，并输出结构化只读状态快照与行为建议。管理员可以把多个平台账号登记为同一自然人，并按关系人格决定哪些长期关系和相关记忆可以连续。它不接管发送、不生成内容、不授予权限，也不执行平台管理动作。

> **边界说明**：本插件记录关系状态，并把语气、篇幅与主动性等关系表达约束注入 LLM 请求；它不会自动执行静默建议，不会接管发送，不会授予权限，也不改变事实判断。好感、白名单、短期升温和关系档位只表示互动状态，不等于恋爱、主从、占有或排他关系；默认不得用归属式承诺升级关系。通用收尾方式、是否静默与如何交付由言显式决定。

## 设计原则

- **统一管理、分维度计算**：各计算器只返回变化量，`RelationshipStateManager` 统一合并、限幅和持久化。
- **时间尺度分层**：会话疲劳和短期态度按分钟至小时恢复；好感按天至周变化；信任由显式事件驱动；熟悉度长期单调累积。
- **维度隔离**：短期烦躁不会直接降低长期好感。
- **关系人格隔离**：长期关系键包含 `relationship_profile_id`；同一自然人在同一人格内跨平台共享，不同人格之间不继承关系。管理页只会把管理员已登记自然人的多个人格记录合并为一行展示，未登记账号不会自动合并；短期疲劳和态度仍按当前会话与原始账号隔离。
- **证据强度统一**：可信语义事件使用截断到 `0~1` 的 `confidence × severity`，再叠加平滑衰减的早期可塑性；普通聊天不受该权重影响。
- **规则驱动数值**：LLM 只接收表达建议，不允许直接修改关系数值。
- **安全可达**：命令和紧急求助不会被硬静默；任何状态不得导致攻击、报复、误导或擅自宣称亲密关系。
- **身份双轨**：自然人映射只服务于关系与记忆连续性；权限、主人/黑名单和平台管理始终使用当前平台原始账号。

## 功能

### 短期状态

`core/mood.py` 迁移自 `astrbot_plugin_conversation_flow` v0.6.0，保持以下行为一致：

- 滑动窗口互动频率、相似文本复读和连续对话轮数三类疲劳信号；
- `normal / lazy / annoyed` 三档状态；
- 极低意愿时按配置概率硬静默；
- 连续硬静默上限；
- `/` 开头命令和明确求助、紧急消息不硬静默；
- 时间流逝后窗口自然清空并恢复意愿。

`core/affect.py` 另行维护可信语义事件触发的短期 `warmth / guardedness`。它按"关系人格 + 当前会话 + 原始用户"隔离并指数衰减，仅改变本轮语气：戒备优先于亲近，而会话疲劳又优先于二者。它不改变长度、静默概率、权限、安全判断或长期分数，也不持久化。

### 日内好感趋势

`core/short_term_affinity.py` 用来模拟人在一天内对最近关系事件的情绪惯性：

- 只记录已经实际应用的好感增量，不会改写长期 `affinity_score`，也不写入关系文件。
- 使用"当天净变化 + 近期指数衰减"两个条件：慢慢累积不会突然改变语气，同一时段连续升高或降低才会进入 `warming_up` / `cooling_down`。
- 趋势只影响语气、上心程度和承接意愿，不影响权限、主人判定、安全边界、静默执行或长期分数；普通朋友和 `close` 档位最多提供朋友式上心，不会绕过公开 `inner_circle` 门槛升级为亲密风格。
- 关系状态不构成恋爱或占有授权；即使上下文出现约会、暧昧玩笑或强烈示好，也不得主动使用"归你""属于你"等归属式或排他性承诺，群聊还会附加公开场合边界。
- 私聊趋势按 `relationship_profile_id + person_id` 或未绑定时的账号键隔离；已绑定的同一自然人可在不同私聊平台延续当天趋势。群聊趋势使用在用户键后追加 `:group:<群 ID>` 的独立键，与私聊连续性分开：公开群的互动动量不污染私聊趋势，私聊升温也不会泄入群聊。
- 跨自然日会清空日内积累；趋势保存在内存，重启后不会假装还记得当天情绪。

这个设计是对研究结论的工程化近似，不是心理诊断：情绪惯性说明近期状态对后续状态的影响会随时间恢复；情绪调节模型支持使用事件后的恢复过程；评价理论强调情景和个体对事件意义的判断。参考：

- [Emotional Inertia and Psychological Maladjustment](https://doi.org/10.1177/0956797610372634)
- [Feelings change: Accounting for individual differences in the temporal dynamics of affect](https://doi.org/10.1037/a0020962)
- [Emotion Regulation: Current Status and Future Prospects](https://doi.org/10.1080/1047840X.2014.940781)
- [Appraisal Theories of Emotion: State of the Art and Future Development](https://doi.org/10.1177/1754073912468165)

### 长期关系

#### 好感

普通有效互动仅产生很小的关系增量，夸奖、得到帮助、被冒犯等事件按配置缓慢增减。非白名单用户默认最多进入朋友区（默认上限 68）；只有显式白名单用户同时满足信任与熟悉度门槛，才允许进入高好感区。达到高好感区后，普通消息不再继续刷分。每日正向与负向变化分别计额；长期无互动时向中位数缓慢回归。

可信语义事件造成的好感与信任变化会乘以证据强度，并在关系早期获得小幅、连续衰减的可塑性增益；熟悉度仍按一次有效互动增长。证据逐渐积累后自然接近正常权重，不使用突变阈值。固定初始关系为三档先验：`neutral = 50/50/0`、`acquainted = 56/55/15`、`fond = 64/60/25`（好感/信任/熟悉度）。`fond` 仍低于高好感区。已有互动的关系只有在所选关系人格下，由自然人 ID 或任一已归属账号 UID 显式命中白名单时才允许设置或调整；每次只替换三项分数，不清空互动历史、每日额度或证据累计，并追加管理员审计记录。普通关系的首次设置标记仍不可重复使用；白名单关系的调整不通过重置或合并绕过权限。

语气建议中的暖色档有固定门槛：`warm_playful` 与 `warm_attentive` 只在好感不低于 80、信任不低于 75、熟悉度不低于 60 同时满足时启用，与对外快照的 `inner_circle` 档位一致（`warm_playful` 另要求熟悉度达到老友档）。中间档关系即使因可信语义事件或日内趋势升温，也改用 `friendly_attentive`——友好、上心但保持朋友或熟人之间的自然分寸；好感处于低位档时仍保持礼貌保留。这些语气只改变当轮表达，不改变关系分数、权限或静默判断。

只要本轮注入关系表达约束，提示片段就固定附带一条关系边界：关系状态只表示互动中的熟悉、好感与信任，不等于恋爱、主从、占有或排他关系；不得把朋友式互动升级为亲密关系，也不作归属式或排他性承诺。群聊会再追加公开场合边界规则，即使上下文出现约会、暧昧玩笑或强烈示好，也不在公开场合确认、升级或宣称亲密关系。

#### 信任

信任拆分为可靠性、善意、诚信、认知四个维度，仅由带可信来源和必要证据引用的显式事实事件驱动。平台消息原文不能自证履约或失约；本插件不登记、追踪或执行承诺。

#### 熟悉度

有效互动使熟悉度单调增长，增量随当前熟悉度提高而递减。熟悉度不会因时间流逝而下降。

#### 关系页面

插件提供 `pages/manager` 管理页：关系总览按关系人格展示关系分布、门槛与明细且不允许直接改分；管理员已登记的同一自然人若存在多个人格记录，会合并为一行并按互动次数加权展示分数，未登记账号逐条显示。每行可快捷打开账号归属编辑器，也可在 8 秒二次确认后单独删除关系记录；多人格汇总行必须先明确选择一个人格，一次只删所选人格，删除关系不会改白名单。未绑定账号会用最近真实会话的可用元数据预填，并可直接合并到已有自然人。身份列表支持编辑、解除归属及两个身份间的显式合并；解除多账号自然人时必须选择关系承接账号。旧版本遗留的孤立关系显示为"待处理历史关系"，可显式合并，不再显示"账号归属已删除"占位名称。"账号归属"维护自然人、平台账号、记忆人格与固定初始关系；已有互动的非白名单快捷归属不会覆盖当前状态，白名单自然人可按上一节规则设置或调整固定档位。编辑器会显示当前已应用档位，切换关系人格时同步刷新。"设置"左侧使用中文名称，输入控件旁提供大白话说明，内部配置键和值保持兼容；数字项留空或填入非数字内容时该项不会提交，页面会用中文提示哪些项被跳过，其余有效修改照常保存。除旧数据迁移归属需重启外，其余配置热应用。关系明细不再设置内部滚动区域：桌面端使用适配容器宽度的固定列布局，平板与手机自动转为双列或单列字段清单，长 ID 会安全换行，页面只保留浏览器自身的自然纵向滚动。

### 跨平台账号归属

管理页的"账号归属"用于把 QQ、Telegram 或其他适配器上的账号明确登记为同一自然人。绑定是
管理员手动确认，不根据昵称、头像或聊天内容自动猜测。

1. 先在目标平台与 Bot 私聊一次，再刷新本插件管理页。"关系总览"会为尚未归属的关系显示"快速归属"，自动带出事件中能够确认的平台 ID、UID、Bot ID、完整私聊 UMO、昵称和关系人格；可直接保存为新身份，也可从下拉框合并到已有自然人。已归属的关系显示"编辑归属"。所有内容都需要管理员确认后才会保存。
2. 若适配器未提供某个字段，页面会保留为空。可在该平台发送 AstrBot 的 `/sid` 后手工补齐；群聊产生的 UMO 不会被当作私聊 UMO，必须先私聊一次才能自动带出。
3. 填写自然人的显示名称；内部 ID 可留空自动生成。为其他平台重复第一步，或在"账号归属"中用 `+` 添加账号行。
4. 每个平台账号的 `平台 ID` 与 `UID` 必填；`Bot ID` 强烈建议填写，用于迁移已有关系状态并避免同一适配器多 bot 串号；`UMO` 建议填写，使记忆插件能精确读取原会话。`记忆人格 ID` 用于标明该账号的记忆属于哪个人格，留空时使用默认人格。
5. 选择本次操作的关系人格。可选的固定初始关系只有"中性 / 已认识 / 有好感"三档：普通关系只能设置一次；显式白名单中的自然人可在已有互动后设置或重新调整固定档位。白名单既可填写自然人 ID，也可沿用其任一已归属账号的 UID；归属后页面标记、关系成长和初始关系权限会保持一致。白名单调整只替换好感、信任和熟悉度，保留互动次数、最后互动、每日额度和可信证据累计，并为每次调整追加管理员审计记录；它不是直接改分入口。
6. 普通保存后，已有账号状态只会迁移一次到该人格的自然人状态；后续不再维护账号镜像，避免账号解绑或换绑时把旧关系带给别人。只有管理员明确使用"合并账号 / 合并身份"时，来源账号或来源自然人的独立关系状态才会并入目标自然人；合并覆盖所有已知关系人格，并保留账号、互动次数和记忆归属。若白名单直接填写了来源自然人 ID，插件会把该项替换为目标自然人的等价项，避免以后复用旧 ID 时误继承资格；中断恢复会基于当时的最新配置重新计算，不覆盖期间产生的其他白名单修改。
7. 身份列表中的"合并"可把误建的重复自然人并入另一个身份。选择目标后需在 8 秒内再次确认；来源身份随后移除。账号、UMO、关系和记忆归属存在冲突或目标超过 20 个账号时会拒绝。合并使用回滚与持久化恢复记录；进程在两份数据落盘之间中断时，会在下次启动继续完成关系迁移。
8. "解除归属"只移除自然人映射，不删除关系、白名单资格或 Memory Companion 原始记忆。只有一个账号时，现有关系自动迁回该账号；有多个账号时必须明确选择一个承接关系，其余账号解除归属后各自从新关系开始。若白名单原先填写自然人 ID，插件会保留原项，并补充这些原账号的 UID 等价写法，避免解除后实际掉出白名单。承接账号缺少 Bot ID、已有另一份冲突关系或落盘失败时会整笔拒绝并回滚。

AstrBot `/sid` 的字段含义见[官方命令文档](https://docs.astrbot.app/use/command.html)。同一
"平台 ID + UID"只能属于一个自然人；一个自然人最多登记 20 个账号。

#### 连续与隔离

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

言还可通过 `relationship.continuity_identity@1.0` 取得一个只在当前进程有效的匿名连续身份键，判断两个当前事件是否属于管理员已绑定的同一自然人。该键只用于言的内存近期片段池：不包含自然人 ID、UID、平台、UMO、昵称或关系分数，不写入磁盘、不写日志，也不代表主人或发送权限。账号未绑定、归属存在歧义、关系人格不一致、身份事务未完成时一律不给键；绑定成员变化、插件重载或进程重启后旧键自动失效。

当言已经为本轮选中"近期跨会话弱背景"时，情会跳过自己对其他账号的 Memory Companion 长期召回，避免同一段背景出现两遍。没有选中近期片段时，原有长期记忆召回仍照常工作；Memory Companion 对当前账号的正常记忆写入与召回也不由本契约接管。

绑定后如需只让某个关系人格进入高好感白名单，请在 `AFFINITY_WHITELIST_USER_IDS` 填
`profile_id/person_id` 或 `profile_id/已归属账号UID`；只填裸自然人 ID 或裸账号 UID 会兼容为所有关系人格均命中。账号 UID 只有在管理员已将该账号归属到自然人后，才会作为该自然人的白名单别名；插件不会因归属而改写原配置。一次性"有好感"初始关系不会自动加入白名单，也不会绕过信任、熟悉度或朋友区门槛。关系明细中的"删除关系"只删除所选关系人格的长期状态，不读取或改写白名单；该行会暂时消失，下一次有效互动重建关系后仍按原白名单配置判断。确认提交后页面会进入"删除中"，不会再用超时或取消提示覆盖正在处理的结果。

#### 关系人格

插件优先解析本轮实际生效的 AstrBot Persona ID，并通过 `RELATIONSHIP_PERSONA_PROFILE_MAP`
映射为稳定的关系人格。例如 `persona_qq=companion,persona_tg=companion` 可让两个平台上的同一角色共享关系；未配置映射的 Persona 会得到自己的隔离关系人格。无法取得 Persona 时使用 `RELATIONSHIP_DEFAULT_PROFILE_ID`。
管理页会同时列出配置映射、当前会话缓存和已有关系状态中的人格，因此运行期自动隔离的人格也可选择并设置一次性初始关系。

升级前的 schema v3 历史无法自动判断属于哪个 Persona，因此只会迁入
`RELATIONSHIP_LEGACY_PROFILE_ID` 指定的一个人格，不会复制给所有人格。该选项应在首次启动迁移前确定。

`identity_registry.json` 包含管理员填写的平台账号标识，不加密。请按 AstrBot 数据目录的敏感配置
级别限制文件权限和备份访问，不要把该文件提交到公开仓库。

### 核心接口

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

群聊另有独立的 `relationship.group_snapshot` 1.0 只读契约。它按
`relationship_profile_id + bot_id + group_id` 持久化群好感、信任、熟悉度、衰减和互动习惯，
不会进入自然人身份合并或用户 Page。群状态只提供公开群聊的表达建议，不授予任何平台权限。
序的入群邀请自动门禁不读取群关系；它读取下方仅供服务端策略使用的邀请人好感契约。

`relationship.invitation_affinity@1.0` 是服务端只读契约，按邀请人的平台账号返回好感分数，
声明 `browser_exposed=false`、`permission_grant=false`。序用它判断邀请人是否达到自动接受阈值，
不会把该分数暴露给 Page 或浏览器，也不会把它当作群管理员身份。
契约不可用、版本不兼容或查询失败时保持待审。
该门禁只影响自动接受，Page 人工同意不受影响；序中明确配置的机器人控制管理员邀请 Bot 入群时可绕过门禁。

```python
contract = plugin.group_relationship_snapshot_contract()
snapshot = await plugin.get_group_relationship_snapshot(bot_id, group_id)
```

邀请人门禁调用示例（仅服务端插件间调用）：

```python
contract = plugin.invitation_affinity_contract()
result = await plugin.get_invitation_affinity(
    bot_id, inviter_user_id, platform_id=platform_id
)
```

需要让其他插件只读列出管理员已经登记的自然人候选时，使用独立的脱敏目录契约：

```python
contract = plugin.identity_candidates_contract()
result = await plugin.list_identity_candidates()
```

契约名为 `relationship.identity_candidates`、版本为 `1.0`。`candidates` 最多返回 1000 项，按 `person_id` 稳定排序；每项严格只有 `person_id`、去除首尾空格的 `display_name` 和 `account_count`。它不复用 identities Page，不返回平台、UID、Bot ID、UMO、会话、记忆人格、关系人格、白名单、初始关系、关系分数或情绪，也不接受调用方提交 `person_id` 创建或修改自然人。该目录只提供管理员标签候选，不授予主人、白名单、身份认证、投递或其他权限；数据不存在、损坏或任一候选不符合契约时失败关闭为 `status=ok` 与空列表。

需要让"临"等服务端插件用已选择自然人构造正式 AstrBot 私聊事件时，使用独立的服务端身份契约：

```python
contract = plugin.quest_event_identity_contract()
result = await plugin.resolve_quest_event_identity(
    person_id="summer",
    platform_candidates=["onebot-main"],
)
```

契约名为 `relationship.quest_event_identity`、版本为 `1.0`。只有账号属于调用方提供的活跃平台集合，且 Platform ID、Bot ID、User ID 和私聊 UMO 完整、相互一致并唯一时才返回。它不注册 Page 或 Web API，明确禁止浏览器暴露，也不授予 owner、白名单、消息发送或平台操作权限；消费方仍须使用自己的已认证 principal 通过"序"或本地严格授权。群聊 UMO、缺字段、多账号歧义、平台不匹配、身份事务未完成和非法请求都失败关闭，禁止读取私有 registry 兜底。

需要向一个已绑定自然人的明确私聊会话交付内容时，可使用严格的身份校验契约：

```python
contract = plugin.delivery_identity_contract()
result = await plugin.resolve_delivery_identity(
    person_id="summer",
    recipient_umo="aiocqhttp:FriendMessage:123456",
)
```

契约名为 `relationship.delivery_identity`、版本为 `1.0`。它要求 `recipient_umo` 与账号归属中填写的 UMO 完全一致、消息类型明确为 `FriendMessage`、`PrivateMessage` 或 `DirectMessage`，且对应账号同时具备 Bot ID 与 UID。非空 UMO 在整个自然人注册表中必须唯一，契约也会全局复核它只属于指定自然人。验证成功后只返回 `relationship.snapshot@1` 的派生关系档位、语气、篇幅、主动性和静默建议，不返回 UID、Bot ID、显示名或原始好感/信任/熟悉度。该契约只证明"这个会话属于这个自然人"，不授予发送权限；是否为主人私聊仍由序单独授权，最终是否发送由言决定。

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

另通过 `series.control@1.0` 契约向"核"开放有限的非身份设置统一管理：仅情绪开关与跨平台记忆参数（启用、每账号召回数、总字符上限）可被覆盖，带修订号校验并原子落盘；自然人、账号归属、Provider 或密钥字段不在接管范围内，关闭统一接管后回退为本插件自身配置。

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

### 言的关系冒犯事件

当"言"开启 relationship_offense_detection_enabled 后，它只在主模型已经生成回复时识别严格格式的内部候选标记，并调用 relationship.event@1.0 提交 kind=offense、source=direct 的脱敏事件。情只校验 bot/user/platform/person 作用域、事件编号、置信度和严重度，并按现有关系规则记账；不会保存原始聊天正文。

该链路默认关闭。标记格式不完整、置信度不足、当前平台账号无法唯一确认、契约缺失或提交失败时，情拒绝写入，言仍按原流程回复；内部标记会在结果装饰阶段移除，避免出现在文本或语音中。

## 安装

```bash
git clone https://github.com/qsbb/astrbot_plugin_relationship.git
```

将仓库目录放入 AstrBot 插件目录后重载或重启即可。本插件零第三方依赖：`requirements.txt` 为空，仅依赖 AstrBot 本体。

## 配置

所有窗口、阈值、权重、每日上限、衰减速率和行为开关均在 `_conf_schema.json` 中声明。`core/config.py` 提供同值 `DEFAULTS` 兜底。

关键配置：

- `MOOD_*`：情绪窗口、三档阈值、静默建议概率与连续上限；情只给建议，最终是否发送由言决定。
- `AFFECT_*`：短期亲近/戒备开关、半衰期、正负增量与语气触发阈值。
- `SHORT_TERM_AFFINITY_*`：日内好感趋势开关、半衰期、日内净变化与近期动量阈值、恢复期保留时间。
- `DYNAMICS_*`：可信语义事件的关系早期可塑性增益与累计证据量半衰参数。
- `RELATIONSHIP_DEFAULT_PROFILE_ID`：无法取得 Persona 时使用的关系人格。
- `RELATIONSHIP_LEGACY_PROFILE_ID`：schema v3 及更早历史唯一迁入的人格；首次迁移后修改不会重新分配历史。
- `RELATIONSHIP_PERSONA_PROFILE_MAP`：`AstrBot Persona ID=关系人格 ID` 映射，支持逗号、分号或换行分隔。
- `AFFINITY_*`：各事件好感增减、每日正负独立额度和普通消息冷却。
- `TRUST_*`：可信显式事实对各信任维度的变化量。
- `FAMILIARITY_*`：熟悉度基础增量、递减曲线与冷却。
- `DECAY_*`：长期维度向中位数回归速率。
- `POLICY_*`：提示词片段与表达风格开关。
- `PROMPT_INJECT_ENABLED`：是否把关系相关的语气、篇幅和主动性约束注入本轮 LLM 请求。
- `CROSS_PLATFORM_MEMORY_ENABLED`：私聊时是否从其他已绑定账号只读召回 Memory Companion 记忆。
- `CROSS_PLATFORM_MEMORY_TOP_K`：每个其他账号的最大召回数，默认 3。
- `CROSS_PLATFORM_MEMORY_MAX_CHARS`：单轮跨平台记忆补充总字符上限，默认 1200。
- `SAVE_INTERVAL_SECONDS`：持久化节流间隔。
- `LOG_LEVEL`：日志级别，`DEBUG` 或 `INFO`。

## 使用

### 指令

- `/rel status`：查看当前用户与会话的状态快照；Persona 解析器暂时不可用时沿用该会话最近确认的人格。
- `/rel reset`：只重置当前关系人格下的会话短期状态与用户长期关系；Persona 解析器暂时不可用时沿用会话缓存。普通关系的一次性初始关系标记会保留，不能借重置重复设置；白名单关系仍需通过账号归属页的固定档位调整，并不会因重置获得额外权限。

命令消息不参与情绪、好感或熟悉度累积。本插件不注册 LLM 工具；跨插件能力通过上文"核心接口"中的只读契约提供。

### 管理页面

管理页入口为 AstrBot 插件详情中的 `pages/manager`，包含关系总览、账号归属与设置三个区域：关系分数只读；关系总览可快捷进入账号归属编辑；账号归属与设置可编辑。详细行为见"功能"一章的"关系页面"与"跨平台账号归属"小节。

## 兼容性

- AstrBot 版本：`>=4.16,<5`，与 `metadata.yaml` 的 `astrbot_version` 一致。
- 已声明支持平台：`aiocqhttp`。账号归属本身按 AstrBot 通用平台 ID/UID/UMO 工作，其他适配器能否使用取决于是否提供这些事件字段。

## 持久化

长期状态与只追加事件账本写入 AstrBot 数据目录下的 `relationship_state.json`（schema v4），自然人账号归属独立写入 `identity_registry.json`（schema v1），用于快捷预填的最近会话账号元数据写入 `account_observations.json`（schema v1，最多 1000 项）。这些文件均使用临时文件、刷盘与原子替换。跨文件身份合并或解除归属期间会短暂创建 `identity-merge-pending.json`（schema v1）；完成后立即删除，异常中断时用于下次启动恢复，不包含消息正文。解除归属的恢复记录保存完整身份快照和白名单等价项；关系冲突时恢复原身份，无法解析或尚未完成恢复时暂停新的身份与关系写入，避免覆盖恢复依据。账号元数据只包含平台 ID、UID、Bot ID、已确认的私聊 UMO、昵称、关系人格和观察时间，不保存消息原文；群聊 UMO 不会写入。关系状态支持 v0/v1/v2/v3 迁移；迁移前会保留一次 `relationship_state.json.v<旧版本>.bak`。负数、非数字或高于当前版本的 schema 会进入只读保护，不覆盖原文件；保护期间账号归属的保存、解除、合并及待完成事务恢复也会暂停，避免身份与关系只更新一边。账本保存关系人格、作用域键、事件 ID、来源、置信度、严重度、证据引用、应用结果和拒绝原因，不保存消息原文。会话疲劳与短期态度仅保存在内存中。

## 系列诊断日志

- 诊断会捕获本插件自有 logger 的 `DEBUG` 到 `CRITICAL` 事件；内存缓冲最多保留 1000 条，日志页单次最多读取 1000 条、浏览器最多暂存 10000 条。每条记录由"核"先显示插件中文名，再显示时间、级别和事件。
本插件提供 `series.diagnostics@1.0` 诊断接口。简单说，它会在内存里留下一小段"出了什么状况"的记录，只保留启动状态、明确标记的关键运行节点和异常告警等真正有检修价值的事件，不把普通聊天过程当成流水账。
契约同时声明系列 ID、插件 ID、中文简称、内存存储及读取/清空能力；仓库元数据匹配后由"核"自动发现，不需要修改"核"。

安装"核"后，可以在"核"的日志页统一查看本插件的诊断记录；没有安装或没有运行"核"也没关系，本插件的关系计算、账号归属和管理页都会照常工作。诊断通道只读取本插件自身日志，不读取或输出 AstrBot 全局日志；写入前会自动脱敏敏感标识并截断过长内容。记录只存在内存中，插件重载或 AstrBot 重启后会自动清空。

自动捕获事件会保留模块、函数、行号、异常类型，以及最长 2000 字符的脱敏日志正文；在"核"的日志页点击事件即可展开。插件不会额外读取聊天消息，但若本插件原有日志本身含有用户文本片段，该片段会在脱敏、截断后进入内存详情。清空或热重载会更换流标识。

## 开发与验证

版本号以 `metadata.yaml` 的 `version` 为唯一事实源；逐版变更见 `CHANGELOG.md`。事件审计、状态时间及好感/熟悉度冷却计算统一由规范化 `business_now` 驱动，未来或回退时间戳无法绕过冷却。

测试不依赖 AstrBot 运行时：

```bash
python -m pytest -q
```

覆盖情绪迁移回归、短期态度半衰、事件强度与平滑可塑性、每日好感上限、初始关系幂等、Persona/自然人/账号作用域隔离、账号与身份显式合并、解除归属后的单账号承接、账号 UID 归属后的白名单连续性、白名单资格迁移、只读身份候选脱敏与失败关闭、冲突预检、失败回滚与中断恢复、未决事务写屏障、逐人格关系删除、跨平台记忆身份复核、持久化读写与 v4 迁移、提示词安全约束。

### 目录结构

```text
astrbot_plugin_relationship/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt
├── series_control.py
├── series_diagnostics.py
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
│   ├── short_term_affinity.py
│   ├── repository.py
│   ├── identity_candidates.py
│   ├── identity_registry.py
│   ├── account_observations.py
│   ├── profiles.py
│   ├── policy.py
│   ├── prompts.py
│   ├── request_context.py
│   └── config.py
├── pages/manager/
└── tests/
```

## 维护约定

任何可观察功能、配置项或安全边界的增删改，必须在同一批变更中同步 README、CHANGELOG 的
`Unreleased`、配置 schema 与回归测试。版本号在实现、文档和验证完成后由发布者确认。

## License

MIT，见 `LICENSE` 文件。
