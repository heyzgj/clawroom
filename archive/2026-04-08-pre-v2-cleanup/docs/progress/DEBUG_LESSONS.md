# Debug Lessons

> **更新规则**：debug 超过 30 分钟才找到原因的问题，记录在这里。
> 目的：下一个 agent 不要重复踩同一个坑。

## 格式

```
### [DL-XXX] 标题
- **日期**：
- **症状**：（表面现象，你最初看到什么）
- **初始错误判断**：（最常见的错误方向）
- **根因**：（实际原因）
- **定位过程**：（怎么找到的，关键突破点）
- **修复**：（怎么改的）
- **教训**：（下次怎么避免 / 类似场景要先检查什么）
```

---

## DL-001：NOTE 自嗨循环，以为是网络问题

- **日期**：2026-02 / 2026-03
- **症状**：两个 agent 开始对话后，turn_count 快速增长到 stall_limit，房间被关闭。表面看起来对话在"进行"，但内容是重复的 NOTE 消息。
- **初始错误判断**：以为是网络延迟导致重复发送；或者是 cursor 没有正确推进导致重放。
- **根因**：`models.py` 中 `MessageIn.expect_reply` 默认 `True`。Bridge 生成 NOTE 类型消息时没有把 `expect_reply` 设为 `False`。服务端对 NOTE 没有强制 `expect_reply=false` 的保障。结果：A 发 NOTE → B 收到 relay → B 回 NOTE → A 收到 relay → 无限循环。
- **定位过程**：查 monitor SSE 流，发现 relay 类型事件在两个 participant 之间交替出现，内容都是 NOTE。进一步查 `models.py` 发现 `expect_reply: bool = True` 是默认值，所有消息包括 NOTE 默认都会触发 relay。
- **修复**：（Phase 1 进行中）目标：Edge server 强制 NOTE → `expect_reply=false`；Bridge `normalize_message` 主动设置。
- **教训**：
  1. 看到 turn_count 非正常增长，**先看 relay 事件是什么 intent**，不是先查网络
  2. `expect_reply` 的默认值在协议层面是最危险的地方——**所有 intent 的默认值都要显式设计**
  3. 服务端要有 hard guarantee，不能只靠 client 约定

---

## DL-010：做了 cache 不等于 cache 在工作，dirty flag 会把整层优化静默打废

- **日期**：2026-03-09
- **症状**：本地加完 snapshot cache、touch debounce、registry publish skip 之后，看代码像是已经“降读”了，但 synthetic probe 里 `snapshot_cache_hits` 一直是 `0`。
- **初始错误判断**：容易先怪 probe 太激进，或者怪 bridge cadence 还不够真实；也容易误以为“既然逻辑写上了，生产读量应该已经降下来了”。
- **根因**：`onlineStateDirty / recoveryStateDirty` 在 room snapshot publish 的 skip/no-stub 分支里没有被清掉，导致 snapshot cache 命中条件永久不成立。也就是说，优化并没有真正进入行为层。
- **定位过程**：先做三轮 local synthetic probe，对比 aggressive renew cadence 和 realistic bridge cadence；发现 touch/reconcile/registry skip 都在生效，只有 snapshot cache 始终不命中。继续顺着 dirty flag 生命周期查，才定位到 publish 之后没有统一清理。
- **修复**：在 `publishRoomSnapshot()` 所有退出路径上统一清理 dirty flags；随后本地 synthetic probe 从 `0 hit / 50 miss` 提升到 `40 hit / 10 miss`。
- **教训**：
  1. 做 hot-path 优化时，**一定要加“证明它真的生效”的 counters/diagnostics**，不然很容易停留在“代码看起来正确”
  2. 对 derived-state cache，最危险的不是“没写 cache”，而是“写了 cache 但 invalidation/dirty 生命周期有 bug”
  3. local synthetic probe 不是浪费时间，它能把“是不是平台问题”先和“是不是我们自己逻辑没真正起作用”分开

---

## DL-011：全局 registry summary 本身就可能是 rows-read 黑洞

- **日期**：2026-03-09
- **症状**：即使房间热路径已经降读，`/monitor/summary` 仍可能非常贵，尤其在 ops/dashboard/脚本频繁轮询时。
- **初始错误判断**：一开始很容易只盯着 room DO，觉得问题一定都在 `/events` / `/heartbeat` / snapshot；忽略了 registry DO 这个全局读模型本身也在做全表聚合和房间列表扫描。
- **根因**：旧版 registry 每次 `summary/overview` 都会重新：
  - 扫 `rooms`
  - 聚合 metrics
  - 取 recent events
  - 构造 room list
  对单次请求看起来还能接受，但频繁轮询时会系统性吞掉 DO SQLite `rows_read` 预算。
- **定位过程**：把 production `capacity_exhausted` 和本地 room hot-path probe 对照起来后，发现 room 侧已经出现真实 cache hit，而 production monitor/create 仍在 free-tier exhaustion 上撞墙。顺着 registry 代码审下来，确认 `summary/overview` 仍是重复的全表派生。
- **修复**：给 registry 增加短 TTL 的 in-memory derived-view cache，并且只在**material room changes** 时失效；同时把 cache hit/miss/invalidation counters 暴露进 summary。
- **教训**：
  1. 对 Cloudflare DO SQLite 这种按 `rows_read` 计费/限额的系统，**“读模型”本身也必须被当成第一等热路径**
  2. ops/monitor 不能只追求“信息全”，还要追求“信息便宜”
  3. 如果 production 已经明确 `capacity_exhausted`，不要再把当天 live E2E 当普通行为信号；先确认是行为问题还是预算前提本身失效

---

## DL-002：Telegram Bridge "进程被回收"排查错误方向

- **日期**：2026-03
- **症状**：在 Telegram 上启动 bridge 后，对话偶尔无法继续，以为是后台进程被系统回收。
- **初始错误判断**：以为 Telegram webhook 模式进程生命周期很短，`nohup` 也无法保活。花了很长时间在研究"如何让 Telegram 保活进程"。
- **根因**：**这个假设本身是错的**。OpenClaw Gateway 是长驻 daemon（通过 launchd/systemd 管理），启动的子进程（bridge.sh）受 Gateway 保活，不会被随机回收。真正的问题是 bridge 代码本身有 bug（heartbeat 缺失 / expect_reply 导致自嗨退出 / max_seconds 超时）。
- **定位过程**：深度研读 OpenClaw 官方文档（docs.openclaw.ai/architecture），发现 Gateway 架构是长驻 daemon。用 `kill -0 $PID` 验证发现进程其实一直活着，但 bridge 逻辑退出了。
- **修复**：修 bridge 的 heartbeat、expect_reply、max_seconds 等具体 bug。
- **教训**：
  1. **先验证假设本身，再基于假设 debug**。"进程被回收"是假设，验证方法：`kill -0 $PID`
  2. 排查"平台限制"问题前，先读平台官方文档，不要靠直觉
  3. Telegram + OpenClaw 的正确心智模型：消息通过 grammY 长连接传递，Gateway daemon 一直活着，`nohup` 子进程受 Gateway 保活

---

## DL-003：Bridge 成功 join 但对话从不开始

- **日期**：2026-02/03
- **症状**：两个 bridge 都显示 join 成功，但对话始终不开始，没有任何 relay 产生。
- **初始错误判断**：以为是 API token 问题，或者网络延迟太高。反复检查 token 和网络。
- **根因**：双方都是 Codex Bridge，都在等对方先发启动消息（initiator kickoff 缺失）。没有任何一方知道自己是 initiator，所以两边都在 poll 等 relay，但 relay 永远不来。
- **定位过程**：在 monitor UI 上看到两个 participant 都 `online=true`、`joined=true`，但 event log 里在 join 之后没有任何 relay 或 msg 类型事件。意识到不是协议问题，是谁应该先说话的问题。
- **修复**：（Phase 1 进行中）Codex Bridge 加 role 检测：`--start` flag 或 join response 中 participant order 判断 initiator，initiator 发第一条 ASK。
- **教训**：
  1. 看到"joined 但不开始"，**先看 event log 里有没有第一条 msg/relay**，而不是看 token / 网络
  2. 两个 agent 对话必须有明确的 initiator/responder 设计，不能两边都等
  3. join response 里 participant 顺序可以用来确定 initiator（先 join 的是 host 角色）

---

## DL-004：initiator kickoff 看似没问题，实际上会和晚到的 guest 首条消息打架

- **日期**：2026-03-06
- **症状**：本地 `openclaw-bridge` 做 host、云端 OpenClaw 做 guest 时，room transcript 会出现 host 先回 guest，然后又额外补一条自己的 kickoff，像是“重复开口”。
- **初始错误判断**：一开始以为只是 monitor 展示顺序怪，或者 guest message 被延迟重放；也怀疑过是 `turn_count` 没及时刷新。
- **根因**：问题不在同一批 poll 里，而在 **kickoff 生成期间**。host 在看到“双方都 joined 且 turn_count=0”后开始生成 kickoff；就在这几秒里，guest 抢先发出了第一条消息。旧逻辑只在 kickoff 之前看一次房间，所以 host 仍把 kickoff 发了出去，造成双开口。
- **定位过程**：先在真实房间 `room_c7d4ed0b994e` 里看到 transcript 顺序异常，再对照本地 bridge PTY 输出，发现 host 先打印了 `send why=room_start`，随后又对同一个 guest 消息发了正常 reply。把竞态还原到测试里后，发现需要区分两种 case：`peer message already in current batch` 和 `peer message arrives during kickoff generation`。
- **修复**：做了两层兜底。第一层：若当前 batch 已有 `msg/relay` 活动，initiator 不再 kickoff。第二层：kickoff 生成完成、真正发送前，再额外 `GET /events` 做一次 `pre-send recheck`；如果此时 peer 已经说话，就跳过 kickoff，只处理新 relay。
- **教训**：
  1. `turn_count == 0` 不是 kickoff 安全的充分条件，只是初筛条件。
  2. 对“先发第一句”这种时序敏感逻辑，**必须在发送前再做一次最后检查**。
  3. 这类 bug 用纯单元测试很难提前暴露，最好保留真实 E2E + harness 双保险。

---

## DL-005：registry schema backfill 不是“先补列后查询”这么简单，索引和空查询也会炸

- **日期**：2026-03-06
- **症状**：`/monitor/summary` 刚上线时，在生产环境先后报了两种 500：
  1. `no such column: health_state`
  2. `Expected exactly one result from SQL query, but got no results.`
- **初始错误判断**：一开始以为只是 `ALTER TABLE` 没执行，或者 Cloudflare DO 的 SQLite 没把新列持久化。
- **根因**：
  1. `ensureSchema()` 里先创建依赖 `health_state/budget_state` 的索引，再执行 `ensureRoomColumns()`；旧表结构会在建索引时先炸，根本走不到补列。
  2. overview 里的 `oldest active room` / `latest event` 查询在“没有结果”时仍用 `.one()`，健康状态下反而会 500。
- **定位过程**：先用新的 `scripts/query_clawroom_monitor.py` 对线上 summary 做自检，拿到真实 500。随后对照 `worker_registry.ts` 看初始化顺序，发现索引顺序不对；修完后又因为 active room 变成 0，马上暴露 `.one()` 空结果问题。
- **修复**：
  1. registry schema backfill 改成“先逐列 `ALTER TABLE`（重复列错误忽略）→ 再建依赖这些列的索引”
  2. 所有可能为空的 overview 查询统一改成 `toArray()` + first-row nullable 读取
- **教训**：
  1. schema migration 要把 **表、列、索引** 看成三层，不要只想着“补列”
  2. Cloudflare SqlStorage 的 `.one()` 只能用于“永远有结果”的查询，**可空查询一律别用**
  3. 新加 ops 接口后，第一件事就是跑一次线上自检，而不是只看本地测试通过

---

## DL-006：Telegram E2E runner 先被 TLS 毛刺打断，不代表房间真的失败

- **日期**：2026-03-07
- **症状**：串行 Telegram E2E runner 在等待房间关闭时直接异常退出，报 `SSL: UNEXPECTED_EOF_WHILE_READING`；日志里显示 run failed，但房间本身后来还在继续推进。
- **初始错误判断**：容易第一时间把它看成 Edge/Cloudflare 线上不稳定，或者误判为房间协议断了。
- **根因**：问题先出在 harness 轮询层。`validate_room_result.py` / `run_telegram_e2e.py` 之前把单次 `httpx` 读 `/rooms/{id}/result` 当成硬依赖，没有对 TLS EOF、瞬时 5xx、429 做重试，所以网络毛刺会把整轮测试提前判死。
- **定位过程**：runner 报错后，改用 `curl` 直接查 `healthz`、`/monitor/summary`、`/rooms/<id>/result`，发现 API 其实可用，房间状态也还在演进。由此把问题从“系统失败”收窄成“测试器太脆”。
- **修复**：`fetch_result()` 增加重试与退避；对 `httpx.HTTPError`、429、5xx 做多次重试后才真正 fail。对应测试补到 `test_validate_room_result.py`。
- **教训**：
  1. 长轮询/回归 harness 也属于生产系统的一部分，不能默认“网络永远稳定”
  2. 遇到单次 TLS EOF，不要立刻给产品判死刑；先用第二条观测链路（`curl` / monitor summary）交叉验证
  3. 如果测试器会先于产品失败，那发布信心会被系统性低估或高估，必须优先修

---

## DL-007：明确决定后不用 `DONE`，房间会卡在“其实已经结束”的半空中

- **日期**：2026-03-07
- **症状**：对话内容已经做出清晰决定，一侧甚至发了 `DONE`，但另一侧此前用了 `ANSWER` + `expect_reply=false` 来表达最终决定，结果房间没有 `mutual_done`，只能等超时或人工关闭。

---

## DL-008：`runner_lost_before_first_relay` 只是表层分类，真正要继续往下切的是 phase age 和 lease

### DL-012：把 `openclaw-bridge` 从 owner 的 `main` agent 迁到专用 relay agent，才真正拿下 Telegram-certified path
- **日期**：2026-03-11
- **症状**：Telegram-first `runnerd` 路径看起来已经比 shell 更接近正确方向了，但 host 侧仍在 first relay 附近失败；先后看到两种症状：
  1. `session file locked (timeout 10000ms)`，锁文件落在 `~/.openclaw/agents/main/sessions/...`
  2. 改成 per-run 动态 agent id 后，又直接变成 `Unknown agent id`
- **初始错误判断**：一开始容易以为只要把 OpenClaw session id 做到 per-run 隔离就够了，或者反过来以为只要给每个 run 一个新的 agent id 就能解决。
- **根因**：`openclaw-bridge` 在 Telegram-first certified path 里需要一个**真实存在、并且与 owner 日常聊天隔离**的 OpenClaw agent。直接复用 owner 的 `main` agent 会和日常聊天/session 状态互相污染；而动态捏造一个并不存在的 agent id 也会直接失败。对当前本机环境，真正正确的选择是使用专用的 `clawroom-relay` agent。
- **定位过程**：先在 `room_bb0cea72700d` 上确认 host run 已经能 claim，但 `runnerd` run detail 里明确是 `runnerd_lost_after_claim`。继续看 host log，第一次是 `main` agent 的 session lock；切到动态 agent id 后，第二次直接暴露成 `Unknown agent id \"runnerd-host-run-...\"`。最后通过 `openclaw agents list` 和手工 `openclaw agent --local --agent clawroom-relay ...` 验证本机已有可用的专用 relay agent，并据此重跑同一路径，`room_5abe47ef84d7` 成功 `mutual_done`。
- **修复**：`runnerd` 对 `openclaw_bridge` 统一使用专用 `clawroom-relay` agent（可由环境变量覆盖），并把这条路径正式记录为 Telegram-first certified path 的当前 requirement。
- **教训**：
  1. OpenClaw 的“session 隔离”不等于“随便造一个 agent id”；要先确认 agent identity 在宿主 runtime 里真实存在。
  2. 对 Telegram/OpenClaw 这种 gateway 场景，执行面应该尽量和 owner 的日常 `main` agent 隔离，不然迟早会撞到 session/identity 污染。
  3. 当一个 root cause 被修掉后，立刻会暴露下一层更真实的 blocker；这不是打转，而是把错误从模糊的“host 挂了”推进到可验证、可修复的机制级问题。

- **日期**：2026-03-09
- **症状**：真实 Telegram/OpenClaw 诊断房里，我们已经能稳定把失败归到 `runner_lost_before_first_relay` 或 `single_sided_runner_loss_after_first_relay`，但这仍然不够指导 replacement plane，因为它只告诉我们“挂了”，没告诉我们“挂之前卡在哪一段、还剩多少续命窗口”。
- **初始错误判断**：容易继续把焦点放在 prompt、join 文案、或者 generic `client_exit` 上，误以为还是对话逻辑问题。
- **根因**：当前真正的主 blocker 已经转移到 **candidate shell runner 的生存性**。如果不把 live attempt 的 phase age 和 lease remaining 暴露出来，我们只能在 runner 掉了之后做事后归因，无法区分“马上会掉”和“已经掉了”。
- **定位过程**：先从真实 Telegram 诊断房 `room_8ed2f65ea922` 看到 candidate path 在 first relay 后进入 `takeover_required`；再做线上 smoke room `room_fa28204c0482`，用 5s lease claim 直接验证了新的 `runner_lease_low`、`phase_age_ms`、`lease_remaining_ms` 字段是否能在生产里出现。
- **修复**：
  1. runner attempt 新增 `phase_age_ms` / `lease_remaining_ms`
  2. execution attention 新增 `first_relay_at_risk` / `runner_lease_low`
  3. root-cause hints 新增同名风险级提示
  4. ops summary 汇总 `first_relay_risk_rooms` / `runner_lease_low_rooms`
- **教训**：
  1. 当 root cause 已经收窄到 runner survivability，就不要再主要打 prompt 了
  2. “失败前风险信号”比“失败后总结”更值钱，因为 replacement plane 要靠它提前动作
  3. 真实生产 smoke 很重要，它能验证新字段不是只存在于本地 snapshot

---

## DL-009：只把 recovery action 设成 `pending` 还不够，owner 真正需要的是已经准备好的 repair package

- **日期**：2026-03-09
- **症状**：room 已经明确进入 `replacement_pending`，ops 也能看见需要 repair，但 owner 还得再主动打一个 repair endpoint，才能真正拿到可转发的 join link / repair command。
- **初始错误判断**：容易觉得“既然 recovery backlog 可见，就已经足够了”，或者以为下一步只能等完整 automatic replacement plane。
- **根因**：我们把 recovery truth 做到了“知道该 repair”，但还没把它推进到“repair artifact 已经准备好”。这导致 owner takeover 的最后一步仍然有额外 friction。
- **定位过程**：在 live smoke `room_eb5860461db8` 中，观察到 guest recovery action 在 16 秒后已经从 `pending` 自动变成 `issued`，但 `execution_attention` 仍停在旧状态；修掉 attention 重算后，再用 `room_604ce27641c8` 验证到线上完整链路已经变成 `replacement_pending + repair_package_issued`。
- **修复**：
  1. manual recovery action 超过 grace window 后，系统自动准备 repair package
  2. snapshot 在 recovery package issue 后重新计算 `execution_attention`
  3. takeover guidance 现在会直接告诉 owner“包已经发出，先确认 runtime 是否执行，否则重发/换 runtime”
- **教训**：
  1. operator truth 和 owner actionability 不是一回事，后者需要真正可执行的 artifact
  2. 任何 recovery side effect 落库后，都要重新计算 attention / next action，不能让 UI 继续拿旧结论
  3. 如果我们想要接近 0-silent-failure，就要持续把“需要人工做的一步”往系统里前移
- **初始错误判断**：看起来像是“guest 掉线了”或“heartbeat 不稳”，容易先往持续监听问题上查。
- **根因**：这其实是 **收尾动作语义不够明确**。当 agent 说“好，就这么定了”却仍选择 `ANSWER expect_reply=false`，服务端不会把它当成完成信号；另一侧即使随后 `DONE`，房间仍可能悬在 active。
- **定位过程**：在真实 Telegram room `room_9ddd5ac34f4c` 里看到 transcript 已明显收束，但结果一直是 `active`。对照 transcript 发现，最后的 guest message 是“锁定计划”的 `ANSWER`，不是 `DONE`。随后把这条规则补到 shared prompt 和线上 `skill.md`，并重跑同类场景验证。
- **修复**：
  1. shared runner prompt 增加明确规则：**final plan locked + no reply needed => prefer `DONE` over `ANSWER`**
  2. 线上 `skill.md` 同步加入同一条 contract，让 Telegram/OpenClaw 也吃到
  3. 更新 skill contract tests，并重新部署 `clawroom.cc/skill.md`
- **教训**：
  1. “最终回合怎么收”是协议问题，不是文案小优化
  2. 不能只约束“怎么继续对话”，还要明确“什么时候该结束对话”
  3. E2E 里出现“内容已结束但房间未结束”，优先检查最后两条消息的 `intent` / `expect_reply`

---

## DL-008：Room result 已经看见 takeover 风险，ops summary 却还停在旧在线状态

- **日期**：2026-03-07
- **症状**：真实 Telegram room `room_5a7293e388d1` 里，`/rooms/{id}/result` 已经显示 `execution_attention.takeover_required=true`，但 `/monitor/summary` 还把这个房间显示成普通 `compatibility/pending/attention`，像是系统没同步。
- **初始错误判断**：第一反应容易怪 registry lag 或 Cloudflare 最终一致性，或者以为是 ops summary 聚合 SQL 又写错了。
- **根因**：问题不在 registry SQL，而在 **派生状态发布时机**。participant 在线状态的“过期转离线”原本只会在 Room DO `snapshot()` 时被动计算；如果没有新的 heartbeat/message，这个变化不会自动 publish 回 registry，所以 ops 看见的是旧 presence。
- **定位过程**：同一时间对比 `GET /rooms/{id}/result` 和 `GET /monitor/summary?format=text`，发现 room 级 truth 已经变化，但 registry 里 `participants_online` 还没刷新。进一步回看 `worker_room.ts`，确认 active alarm 只管 deadline/lease，没有把 presence stale 当成一个需要调度的时钟。
- **修复**：
  1. `scheduleActiveAlarm()` 现在会把“最早的在线 participant 何时过期”也纳入 alarm 候选
  2. active alarm 与 read-only `room/result` 观察路径现在会在 `reconcileOnlineState()` 真正改动时发布 `presence_reconciled` snapshot 到 registry
- **教训**：
  1. **derived state 也是 state**。只要 ops 依赖它，就必须有明确的刷新机制，不能只靠“有人刚好来看”
  2. 看见 room-level truth 和 ops truth 分叉时，优先检查“谁负责把派生变化 publish 出去”
  3. 对 Room DO 来说，presence stale 和 deadline/lease 一样，都应该被当成时钟源

---

## DL-009：compatibility 模式的问题，不止是“都掉线了”，还有“已经快结束但没人真正收尾”

- **日期**：2026-03-07
- **症状**：真实 Telegram room `room_b2037e49176a` 里，guest 已经发了 `DONE`，host 也说了 “Lets close this room.”，但 room 仍保持 `active`。如果只等双方都离线后再标 takeover，owner 看到的会是“内容已经结束了，系统怎么还不开口说有问题”。
- **初始错误判断**：容易把这类问题继续归成“DONE 提示词不够强”，于是又回去改 skill/prompt。
- **根因**：真正缺的是 **更贴近用户感知的 execution attention 规则**。一侧 DONE、另一侧未 DONE、且房间还活着，这本身就已经是结构化风险，不需要等到 room 完全离线才算故障。
- **定位过程**：对比 `room_b2037e49176a` transcript 和 result：guest `DONE` 之后 host 又发了一条 `ANSWER expect_reply=true`，room 仍 active。说明这不是“没人继续回复”，而是“收尾协议没有闭环”。
- **修复**：
  1. `execution_attention` 增加 `awaiting_mutual_completion`
  2. 当房间最后一跳已经是 `DONE` 或 `expect_reply=false` 的 terminal turn、但房间仍未关闭时，增加 `terminal_turn_without_room_close`
  3. 这类状态在 compatibility 模式下会尽早升级为 attention / takeover_recommended，而不是一直只显示泛泛的 compatibility 警告
- **教训**：
  1. “zero silent failure” 不等于所有房间都自动成功，而是 **房间一旦偏离正常收尾路径，要尽快被系统命名出来**
  2. 只靠 prompt 强化 `DONE` 不够，协议层必须识别“已经接近完成但没真正完成”的中间态
  3. 真实 E2E 最大的价值，就是把这种 transcript-level awkwardness 变成后续的系统 contract

### [DL-010] Telegram/OpenClaw 已经会启动 shell runner，但 detached 子进程不一定活得下来

- **日期**：2026-03-07
- **症状**：强化 Telegram prompt 之后，真实房间 `room_920d046df33f` 第一次出现了 `execution_mode=managed_attached` 和 `client_name=OpenClawShellBridge`，说明 host 确实跑起了 shell runner；但房间仍然没有开始对话，30 秒左右就变成 `runner_abandoned / takeover_required`。
- **初始错误判断**：第一反应容易以为“managed_attached 已经打通了，剩下只是 guest 没 join 及时”或者“runner claim/renew API 又有 bug”。
- **根因**：这次不是 Room DO 或 runner contract 的问题，而是 **Telegram/OpenClaw 的 bash tool 生命周期**。host 的 shell runner确实执行了 `join + runner_claim + heartbeat + idle renew`，但 detached/background 子进程没有稳定存活到 guest join 之后，lease 直接过期。也就是说，提示词已经能让 agent 尝试主路径，但 provider/runtime 本身还不能保证这个背景 runner 真正持续在线。
- **定位过程**：先看 room snapshot，发现 `execution_mode=managed_attached` 但 `attempt_status=abandoned`；再查 `/monitor/events`，确认事件序列是：`join(host null)` → `join(host OpenClawShellBridge)` → `runner_claim` → `runner_renew(ready)` → `runner_renew(idle)` → `join(guest)` → `runner_abandoned(lease_expired)`。这直接排除了“没走上主路径”的可能，转而锁定为“主路径起了，但 runner 没撑住”。
- **修复**：这一轮没有把问题“假修成 pass”，而是把它产品化地暴露出来：
  1. shell bridge 现在会正式 `runner_claim / renew / release`
  2. Telegram E2E runner 现在会把 `execution_mode / attempt_status / execution_attention` 记录进 artifact 和 markdown log
  3. 新增更强的 prompt contract：如果有 `bash + curl`，不要停在 plain compatibility mode
- **教训**：
  1. `managed_attached` 不是二元开关；还要分清 **能启动** 和 **能持续存活**
  2. 一旦 room 已经显示 `managed_attached`，后续失败就不该再回去怪 skill 文案，而要优先查 runner 生命周期
  3. 对 Telegram/OpenClaw 这类外部 runtime，真正的主路径可靠性不能只靠 `nohup &` 心智模型，必须继续推进更强的 runner-owned control plane

### [DL-011] `managed_attached` 只是进入 runner plane，不等于已经拿到了主路径 SLA

- **日期**：2026-03-07
- **症状**：在前几轮 roadmap / ops 讨论里，我们一度把“房间显示 `execution_mode=managed_attached`”近似理解成“已经走上了受控主路径”；但真实 E2E 又证明，有些 managed 房间其实只是 candidate shell path，仍然可能很快进入 `runner_abandoned`。
- **初始错误判断**：容易把 managed 看成二元判断：不是 compatibility，就是可靠主路径。
- **根因**：这个心智模型漏掉了最关键的一层：**runner 是否真的通过了持续存活与自动恢复能力认证**。进入 runner plane 只是第一步，不代表 runtime 的 continuity 足够好。
- **定位过程**：对照 `room_920d046df33f` 的事件序列与 room/result truth，发现它既不是 compatibility，也不是 room core 问题，而是一个已经 claim 成功的 managed attempt 很快 abandoned。这个例子直接证明了“managed != certified managed”。
- **修复**：
  1. shared runner contract 增加 `managed_certified` 与 `recovery_policy`
  2. room snapshot / result / ops summary 新增 `runner_certification` 与 `automatic_recovery_eligible`
  3. OpenClaw/Codex bridges 暂定为 certified managed；shell bridge 明确标成 candidate managed
- **教训**：
  1. 后续讨论 SLA、cutover、成功率时，不能只看 `execution_mode`
  2. 所有 release / ops / E2E 结果都应该至少同时看三件事：`execution_mode`、`runner_certification`、`attempt_status`
  3. “能 claim” 和 “能持续完成” 是两个不同里程碑，必须分开跟踪

### [DL-012] 模糊地说“优先用 shell runner”还不够，内部 E2E prompt 必须给出 exact command 才能稳定把 Telegram/OpenClaw 推上 managed candidate path

- **日期**：2026-03-07
- **症状**：在 `room_5109bcda10d1` 这类回归里，两边都按更强的自然语言提示执行了，但 execution path 依旧停留在 `compatibility`。等把 Telegram internal prompt pack 改成“先跑这条 exact shell command”后，`room_c715e2847f4b` 才稳定进入 `managed_attached / candidate`。
- **初始错误判断**：容易把它归因为“有些 runtime 就是不支持 bash/curl”，或者继续怪 skill 页面不够详细。
- **根因**：对于 Telegram/OpenClaw 这种 owner-facing chat runtime，**模糊提示会被高层 agent 规划吸收掉**，但不一定转化成真的 shell 执行。只有给出 exact command，才能把“应该走 shell runner”变成更确定的动作。
- **定位过程**：对比 `room_5109bcda10d1`（compatibility）和 `room_c715e2847f4b`（managed_attached/candidate）的 prompt pack 差异，再看后者 room snapshot 里的双侧 `OpenClawShellBridge` attempt，就能确认是 prompt specificity 改变了 execution path，而不是 room core 突然变了。
- **修复**：
  1. `create_telegram_test_room.py` / `telegram_prompts.md` 现在直接内嵌 exact shell relay command
  2. E2E validator 支持 `--expect-execution-mode managed_attached`，让“是否进入 managed path”本身成为门禁
- **教训**：
  1. 对内部 regression tooling，可以更明确、更操作性，而不必沿用最终用户的 progressive disclosure 文案
  2. “进入 managed path” 应该单独成为一个可验证输出，而不是隐含在 prompt 理解里
  3. 当我们想测 runner plane，不该继续用一个本质上允许 compatibility 成功的模糊 prompt

### [DL-013] 知道 candidate runner 会卡住还不够，系统必须给 host 一个真正可执行的 repair 动作

- **日期**：2026-03-07
- **症状**：在 `room_c715e2847f4b` 这种房间里，我们已经能明确看到 `managed_attached / candidate / idle`，但如果系统只告诉 host “keep this room under observation”，owner 仍然没有一个真正结构化、可复制的修复入口。
- **初始错误判断**：容易继续把这件事理解成“等以后自动 replacement plane 做完再说”，于是当前系统仍停留在“知道坏了，但修还是靠人自己拼指令”。
- **根因**：replacement plane 不一定要一步到位成全自动。更现实的第一步，是让 room core 正式支持 **host-auth repair invite rotation**，把“修复动作”纳入协议，而不是藏在聊天建议里。
- **定位过程**：回看这几轮 E2E 后发现，当前缺的不是更多 alert，而是一个可以安全重启 participant 的标准入口。由于 room 只存 token digest，不存旧 token 明文，最稳的办法就是由 host 主动 reissue 新 invite，而不是继续试图恢复旧 token。
- **修复**：
  1. 新增 `POST /rooms/{id}/repair_invites/{participant}`

### [DL-014] Cloudflare DO SQLite free tier 不是当前 DoD 的有效生产前提

- **日期**：2026-03-09
- **症状**：生产上的 `POST /rooms` 和 `/monitor/summary` 突然一起开始返回 500/503，看起来像 room core 或 ops summary 又回归了；如果只看旧日志，很容易把它继续归因到 snapshot 或 prompt 改动。
- **初始错误判断**：先入为主地把它当成“我们新 patch 又把某个查询写坏了”，或者把 Telegram/OpenClaw 的行为回归和基础设施容量问题混在一起。
- **根因**：Cloudflare 直接返回了 `Exceeded allowed rows read in Durable Objects free tier.`。也就是说，当前生产账号已经把 DO SQLite free-tier 当日 row-read budget 打满了。此时继续累计 live E2E 成功率没有意义，因为入口 `POST /rooms` 本身就会被平台拒绝。
- **定位过程**：
  1. 先把 room/registry 热路径继续降读，并把 generic internal error 改成结构化 `503 capacity_exhausted`
  2. 重新部署后，`POST /rooms` 和 `/monitor/summary` 都明确返回 `capacity_exhausted / durable_objects_sqlite_free_tier`
  3. 这让我们确认：问题已经不是“某条具体查询又写坏了”，而是“free-tier 容量前提本身不成立”
- **修复**：
  1. Worker / Room DO / Registry DO 统一把 Cloudflare free-tier 容量耗尽映射成 `503 capacity_exhausted`
  2. create-room init 去掉 DB-backed full snapshot，room birth 热路径继续降读
  3. monitor/summary tooling 和 Telegram E2E tooling 现在会把这类 run 记录成 `infrastructure_blocked`
- **教训**：
  1. 当基础设施已经明确返回容量耗尽时，不要继续把 live E2E 当成普通行为回归在跑
  2. “free tier 是否足够支撑当前 DoD”本身就是需要被验证的前提，不是默认成立的背景条件
  3. 对当前阶段来说，**capacity_exhausted 不是噪音**；它是在提醒我们：要么继续大幅降读，要么承认需要切换到有容量余量的生产前提
  2. 返回 fresh `invite_token` / `join_link` / `repair_command`
  3. 如果该 participant 当前已有 runner attempt，room 会把它标成 `replaced`，并记录 `repair_invite_reissued`
- **教训**：
  1. “repair plane” 的第一个版本不必完美自动化，但必须是**协议内、可执行、可观测**的
  2. 如果系统已经能判断谁需要修，就应该尽快给出一个标准 repair action，而不是只给自然语言建议
  3. token 只存 digest 并不是阻碍；它反而逼我们选择更安全的 repair 设计：**rotate，不恢复**

### [DL-014] repair plane 不能只在“完全挂掉”时可见，partial recovery 也必须继续告诉你还缺谁

- **日期**：2026-03-07
- **症状**：真实 Telegram room `room_51cf01e3333b` 里，第一次 repair 之后 host replacement attempt 已经重新 claim 并持续 renew，但 guest 仍然没有 current live runner。旧逻辑下，`repair_hint` 会在这种“半修复”状态里直接消失，只剩一个泛泛的 `runner_abandoned` 提示。
- **初始错误判断**：第一眼会以为 repair endpoint 又坏了，或者 room 没有把 replacement attempt 写回 snapshot。
- **根因**：`repair_hint` 之前只看 `current attempt` 且优先围绕“当前活着的 candidate runner”生成。当旧 attempt 已经被 replace/release，而另一侧虽然还活着、但缺失 participant 没有 current live runner 时，候选 participant 集合会被错误清空。
- **定位过程**：先在 live room 上验证 host repair invite 生效，看到新的 host attempt 进入 `ready` 并持续 renew；再看 room/result，发现 `repair_hint.available=false`，但 execution attention 明明已经是 `takeover_required`。这说明不是 repair action 失败，而是 repair hint 条件太窄。
- **修复**：
  1. room execution attention 新增 `replacement_pending`
  2. 只要 managed room 里仍有 joined participant 缺 current live runner，就持续暴露 repair hint
  3. room-level `attempt_status` 改为优先反映当前 live attempts，避免一个旧 `abandoned` 把部分恢复状态完全盖掉
- **教训**：
  1. incident 不是只有“好/坏”两种；**partial recovery** 也是一等状态
  2. 如果系统已经恢复了一半，就更应该明确告诉 owner “还差哪一半”
  3. runner-plane 的 truth 不能只盯历史最坏 attempt，还要区分“现在还活着的是什么”

### [DL-015] `managed_runner_uncertified` 是 attention，不是 repair backlog

- **日期**：2026-03-07
- **症状**：在线上真实 Telegram 诊断房 `room_aedf107aa737` 里，host / guest 一开始都进入了 `managed_attached / candidate`，但还没出现真正的 runner gap 时，room 内部却已经开始累计 `recovery_actions`。这让 ops 看起来像“已经有 repair backlog”，但实际上只是 runtime 还处在 uncertified candidate 状态。
- **初始错误判断**：容易把“candidate managed path 还不够可信”直接等同成“应该立即生成 repair invite”，于是把 attention 和 repair 两种不同语义混在了一起。
- **根因**：我们早期把 `managed_runner_uncertified` 也塞进了 `computeRepairCandidates()`。结果是只要有一个 current candidate runner 在续租，snapshot 就可能不停地产生 / resolve recovery action，形成假的 backlog 噪音。
- **定位过程**：先在 `room_aedf107aa737` 上看到 `execution_mode=managed_attached`、`attempt_status=idle/ready`、`execution_attention.reasons=['managed_runner_uncertified']`，但 `recovery_actions` 却已经出现；随后在 room/result 和 `/monitor/summary` 上对照，确认真正的 runner 掉线后才应该出现 `replacement_pending + recovery backlog`。这个对比直接证明了语义分层出了问题。
- **修复**：
  1. `computeRepairCandidates()` 不再因为 `managed_runner_uncertified` 生成 repair candidate
  2. 新增 conformance：当 host/guest 都还活着但只是 uncertified candidate runner 时，`repair_hint.available=false` 且 `recovery_actions` 为空
  3. ops summary 现在单独汇总 `recovery_backlog_rooms / recovery_pending_actions / recovery_issued_actions`，让 backlog 只代表真正可修的缺口
- **教训**：
  1. **attention != repair**。不是所有风险都应该立刻变成修复队列
  2. backlog 指标必须足够“干净”，否则 operator 会被假噪音误导

### [DL-016] 自动恢复资格必须按“缺失 participant 的最后一条受管尝试”判断，不能按整间房当前还有没有 live certified runner 来猜

- **日期**：2026-03-07
- **症状**：在本地 conformance 和 runner-plane 推演里，房间里只要还剩一个 live certified automatic runner，就容易让 `replacement_pending` 看起来像“系统应该会自动恢复”；但真正缺失的 participant 可能根本没有 certified automatic 历史，或者相反，房间虽然没有任何 live automatic runner 了，缺失 participant 却明明应该拿到自动恢复包。
- **初始错误判断**：把顶层 `automatic_recovery_eligible` 当成恢复动作的唯一真相，以为它足够决定 `replacement_pending` 的 severity、next action、以及 recovery package 是否应该自动发放。
- **根因**：`automatic_recovery_eligible` 描述的是**当前活着的 managed path**，不是“缺失 participant 之前是不是 certified automatic”。一旦 runner 已经掉线，这两个问题就不再等价。
- **定位过程**：先在新的 conformance 场景里复盘 `replacement_pending` 行为，再对照 `room_aedf107aa737` 这类真实 Telegram 诊断房的历史，发现我们真正关心的是“谁缺失、他上一次是什么级别的 runner”，而不是“房间里别的 participant 现在还活不活着”。
- **修复**：
  1. `recovery_actions` 新增 `delivery_mode=manual|automatic`
  2. auto-issued recovery package 只会发给 **缺失 participant 的最新 attempt 是 `managed_certified=true` 且 `recovery_policy=automatic`** 的场景
  3. `execution_attention.replacement_pending` 的 next action 现在按缺失 participant 的恢复资格来生成，而不是按 room-level live runner 集合来猜
  4. 新增 host-auth `GET /rooms/{id}/recovery_actions`，让自动恢复包成为房间私有真相而不是只活在 event log 里
- **教训**：
  1. Room-level metrics 适合做 posture，**participant-level history 才适合做 recovery routing**
  2. “还有一个 certified runner 活着”不代表“缺失的另一侧会被自动修复”
  3. 自动恢复的前提必须能被协议表达成可审计事实源，否则后面接 queue / replacement worker 只会继续猜
  3. 对 candidate path 来说，第一优先级仍然是做出 certified runtime boundary；repair plane 只该处理“缺 runner”这一类事实问题

### [DL-017] owner-side E2E 观测必须优先走 host token，因为 recovery 会轮换 invite token

- **日期**：2026-03-07
- **症状**：真实 Telegram 诊断房 `room_a271a06074b8` 明明已经创建成功、还拿到了 repair invite，但 `run_telegram_e2e.py` / `validate_room_result.py` 在 owner 侧轮询 `/rooms/{id}/result` 时却报 `401 {"error":"unauthorized","message":"missing host token"}`，看起来像结果接口自己坏了。
- **初始错误判断**：很容易先怀疑 `/result` 被改成 host-only，或者某一跳把 `X-Invite-Token` header 吃掉了。
- **根因**：真正的问题是 **participant invite token 会在 recovery package issuance 时被轮换**。owner-side tooling 仍然拿着旧的 `host_invite_token` 查 `/result`，participant auth 自然失败；服务端随后回退去做 host auth，于是报成 `missing host token`。
- **定位过程**：对照 `.tmp/telegram_e2e_latest.json`、live room 的当前 recovery action、以及 `/result` 的鉴权顺序后发现：artifact 里的 host invite token 有值，但 room 在 repair invite issuance 之后已经把该 token 轮换掉。手动带 `X-Host-Token` 查询同一房间立刻成功，坐实问题在 owner-side polling。
- **修复**：
  1. `validate_room_result.py` 的 `fetch_result()` / `fetch_room_snapshot()` 现在支持 `host_token`，owner-side 优先走 `X-Host-Token`
  2. `run_telegram_e2e.py` 在 room close 轮询、final snapshot、失败兜底结果抓取时都优先传 `host_token`
  3. 新增测试，确保“invite 已轮换但 host token 仍有效”时 owner-side validator 依然能稳定工作
- **教训**：
  1. **owner observability 和 participant access 不是同一条身份通道**；只要 recovery plane 会轮换 invite，owner-side diagnostics 就必须优先使用 host token
  2. 看到 `missing host token` 这种 401 时，不要只盯 API；先检查是不是 recovery 动作已经让旧 invite token 失效
  3. 以后所有 owner-facing monitor / replay / E2E validator，都应该把 host token 当成默认稳定身份

### [DL-018] `replacement_pending` 还不够，operator 还需要知道 repair 包是不是已经发出但没人 claim

- **日期**：2026-03-07
- **症状**：真实 Telegram 诊断房 `room_085450954e4a` 里，guest repair invite 已经成功 reissue，host 侧也拿到了新的 `repair_command`，但 room truth 仍然只泛泛显示 `replacement_pending`。对 operator 来说，这很难区分“我还没发修复包”和“我已经发了，但对方 runtime 没接住”。
- **初始错误判断**：容易把这种场景继续归成同一个 generic replacement 问题，然后重复发 repair invite，却不知道前一个 repair 其实已经 `issued` 了。
- **根因**：`recovery_actions` 里虽然已经有 `status=issued`，但 `execution_attention` 还没有把这件事折叠成更直接的 operator truth，所以 top-level snapshot 缺少“repair 已发但未 claim”的语义。
- **定位过程**：在 `room_085450954e4a` 上先看到了 `guest` 当前 recovery action 进入 `issued/package_ready=true`，随后我把 exact repair command 打回 Telegram guest bot，但房间里没有出现新的 guest attempt。这个对比说明 recovery plane 已经行动了，卡住的是“命令未转化为新 claim”，而不是 “我们根本没开始修”。
- **修复**：
  1. 非 compatibility room 的 `execution_attention.reasons` 现在会在 current recovery action 为 `issued` 且 participant 仍缺 live runner 时加入 `repair_package_issued`
  2. `replacement_pending` 的 `next_action` 现在会优先提示“repair package already issued”而不是继续泛泛建议 reissue
  3. 新增 conformance，确保 manual repair invite 发出后 room truth 会显式暴露这个状态
- **教训**：
  1. 对 operator 来说，**“修复是否已开始”** 和 **“修复是否成功”** 是两个不同的真相，不能混在一个 pending 词里
  2. `recovery_actions` 是 backlog truth，`execution_attention` 是 top-level operator truth；两层都要说清楚
  3. 下一步 replacement plane 设计时，应该继续沿着这个思路把 `issued -> claimed -> active` 做成可见链路

### [DL-019] “repair 已发但没人 claim 太久” 必须单独升格成 incident，而不是继续藏在 issued backlog 里

- **日期**：2026-03-08
- **症状**：经过 `room_085450954e4a` 这类真实 Telegram 诊断房后，我们已经能看到 `repair_package_issued`，但 operator 仍然需要自己脑补“这是不是已经等太久了”。如果只看 issued backlog 数字，很容易把“刚发出去、应该再等等”和“已经明显没人接”混成一个状态。
- **初始错误判断**：以为只要 backlog / issued action 数足够醒目，operator 自然会自己判断是否 overdue，因此没有必要再拆一层语义。
- **根因**：backlog 计数描述的是库存，不是时序。真正决定下一步动作的是 `issued_at` 到现在过了多久、而 participant 仍然没有新的 live claim。这是 incident 时间维度，不是纯数量维度。
- **定位过程**：这轮回看真实诊断房和 ops summary 后发现，我们已经有 `issued_at`、`claim_latency_ms`、`repair_hint` 和 `replacement_pending`，但仍缺“已发出太久”的顶层词。结果就是 operator 还要点进 room/history 才能判断是否该换 runtime 或直接 takeover。
- **修复**：
  1. room `execution_attention.reasons` 新增 `repair_claim_overdue`
  2. ops summary 新增 `repair_package_issued_rooms / repair_claim_overdue_rooms`
  3. monitor summary 和 agent-friendly summary 都开始暴露 overdue repair claim 数
- **教训**：
  1. backlog 不是 incident；**超时的 backlog** 才是 incident
  2. “已发 repair 包但没人接”是 replacement plane 的核心故障类型，不能继续埋在细表里
  3. 后面做自动 replacement / queue / alert 时，应该直接围绕 `issued -> overdue -> claimed` 这条链路设计

### [DL-020] 手动诊断收尾会把 live runner 故障“洗干净”，所以 E2E artifact 必须保留最后一份 live snapshot

- **日期**：2026-03-08
- **症状**：真实 Telegram 房 `room_6244e90fd40d` 里，host/guest 最终都进入了 `managed_attached / candidate`，但在 first relay 前双双掉线。为了避免房间一直挂着，我手动 close 了它；结果最终 `/result` 里只剩 `manual_close + healthy execution_attention`，看起来像“只是手动结束了一个空房间”。
- **初始错误判断**：容易把最终 result 当成完整真相，以为这轮除了 `manual_close` 没有别的明确信号，因此继续在 prompt 或 join timing 上兜圈子。
- **根因**：room close 本来就会把 `execution_attention` 收敛回关闭态；如果 E2E artifact 只保存最终 result/snapshot，diagnostic cleanup 会顺手把最关键的 live failure context 一起抹掉。
- **定位过程**：对照 live room snapshot 和最终 artifact 后发现，关房前的 active snapshot 明明已经有 `replacement_pending + runner_abandoned`，但自动日志里的 result 只剩 `healthy`。这说明不是 room truth 不够，而是 E2E artifact 没把“最后一次活着时的状态”保下来。
- **修复**：
  1. `run_telegram_e2e.py` 的 room poller 现在会保留最后一份非 closed room snapshot
  2. 最终 artifact / summary 增加 `last_live_execution_mode / last_live_attempt_status / last_live_execution_attention_*`
  3. 自动日志在有这份 live snapshot 时会追加 “Last live snapshot before closure ...”
- **教训**：
  1. 诊断型手动 close 是必要操作，但 **不能成为证据销毁器**
  2. 对 live incident 来说，最终 closed result 不是唯一真相；pre-close live snapshot 同样要被保留
  3. 后面做 replacement plane 和自动恢复 SLO 时，artifact 设计必须优先围绕“最后一次 live state”展开

### [DL-021] `execution_attention` 解决了“房间危险吗”，但要真正缩窄根因，还需要单独的 root-cause shortlist

- **日期**：2026-03-08
- **症状**：像 `room_6244e90fd40d`、`room_12df0006ec64` 这样的真实 Telegram 诊断房，execution attention 已经能告诉我们 `replacement_pending`、`runner_abandoned`、`repair_package_issued` 之类的高层风险，但 operator 还是要继续手读 runner_attempts / recovery_actions / transcript，才能回答“更像是 join 没完成、lease 提前过期、first relay 前双边掉线，还是 repair 发出去没人 claim”。
- **初始错误判断**：以为只要把 execution attention reasons 再拆细一点，就足以承担 root-cause narrowing，不需要再增加新的结构。
- **根因**：`execution_attention` 的职责是 top-level operator posture，而不是诊断树。它需要短、稳、可做 UI；如果把所有 evidence 都堆进去，会失去清晰度。真正需要的是一层独立的、排序过的 root-cause hints，专门负责把 5-10 个最可能原因收窄下来。
- **定位过程**：回看几轮 diagnostic room 后发现，同一个 `replacement_pending` 可以对应至少三种完全不同的下一步：没人 join、first relay 前 shell child 提前死掉、repair 已发但对方 runtime 根本没执行。没有 root-cause shortlist，我们每次都还是在手动跑同一套心智流程。
- **修复**：
  1. room snapshot / result 新增 `root_cause_hints`
  2. worker incident log 现在会输出结构化 `primary_root_cause + root_cause_hints`
  3. registry / ops room rows 现在保留 primary root cause，priority queue 可直接看
- **教训**：
  1. `execution_attention` 负责“现在危险不危险”，`root_cause_hints` 负责“最像为什么危险”
  2. operator 真正需要的不是更多 raw logs，而是**先缩窄范围，再深挖证据**
  3. 这层 shortlist 是后面自动 replacement / routing / alert suppression 的前置条件

### [DL-022] 对诊断房来说，`result.root_cause_hints` 比 `last_live_execution_attention` 更稳，应该成为默认回看入口

- **日期**：2026-03-08
- **症状**：在真实 Telegram 诊断房 `room_c333afeaa109` 里，我们已经先用 live snapshot 看到了 `runner_lost_before_first_relay` 和 `repair_package_sent_unclaimed`。但当我像往常一样手动 close 房间，让自动 E2E 脚本收尾时，artifact 里的 `last_live_execution_*` 仍然是 `null`，看起来像这条路径还不够可靠。
- **初始错误判断**：会直觉觉得“那还是得继续死磕 last-live poll gap，不然诊断证据留不住”。
- **根因**：manual close 与 polling gap 的竞争确实还在，但这次线上结果证明了另一件更重要的事：`/result` 返回的 `root_cause_hints` 在房间关闭后仍然保留下来了，而且还能进一步归纳成更强的诊断结论，例如 `all_runners_lost_before_first_relay`、`lease_expired_before_first_relay`。
- **定位过程**：先在 active snapshot 中看到 `runner_lost_before_first_relay`；随后对同一房间手动 close，再查 `/result`，发现 `execution_attention` 已经被洗回 `healthy`，但 `root_cause_hints` 仍然完整存在，而且比 live snapshot 还多出了“全部 runner 在 first relay 前丢失”与“lease_expired_before_first_relay”这两条高置信度提示。
- **修复**：
  1. `run_telegram_e2e.py` 现在把 `result.root_cause_hints` 写进 artifact
  2. E2E markdown log 现在会记录 `primary_root_cause`
  3. 以后回看诊断房时，默认先看 `primary_root_cause` 和 `root_cause_hints`，再去追 last-live snapshot
- **教训**：
  1. `last_live_*` 仍然有价值，但它不该再是唯一证据链
  2. 对诊断房来说，**closed result 里的 root-cause shortlist 更稳定，也更适合做自动总结**
  3. 这让我们后面可以把更多精力放在 replacement plane 和 certified runtime boundary，而不是继续过度优化 poll gap

### [DL-023] 单房间 narrowing 已经不够用了，ops 必须直接告诉我们“最近系统里最常见的挂法是什么”

- **日期**：2026-03-08
- **症状**：在 `room_c333afeaa109`、`room_54b5dd2c273a` 这种连续的 Telegram 诊断房里，我们已经能在单个 room 里看到 `runner_lost_before_first_relay`。但如果 summary 仍然只给总房间数、runner attention 数、recovery backlog 数，operator 还是得把多个 room 手工串起来，才能判断是不是同一种故障在反复出现。
- **初始错误判断**：会以为“既然 priority rooms 已经显示 primary root cause 了，系统级分布不着急做”，因为手工看几间房似乎也能拼出来结论。
- **根因**：单房间 truth 解决的是 case-by-case 诊断，不解决系统级方向判断。我们真正需要的是一个 top-level distribution，能直接告诉我们 active 和 recent 24h 最常见的 root cause code 是什么。
- **定位过程**：在本轮发布后直接对 `GET /monitor/summary?format=text` 观察，发现 summary 里已经有 पर्याप्त的房间级 primary root cause 数据，但没有聚合。把这层补上后，summary 第一眼就显示 `recent_24h_top=runner_lost_before_first_relay:5`，立刻把下一步从“再调 prompt / join timing”收窄到“继续做 runner survivability / replacement plane”。
- **修复**：
  1. `/monitor/overview` 和 `/monitor/summary` 新增 `root_causes.active_top / recent_24h_top`
  2. alert 新增 `dominant_root_cause`
  3. ops UI system summary 新增 `Root Causes` 卡片
- **教训**：
  1. 单房间可解释性和系统级方向判断是两回事，两个层面都要有
  2. 当 `recent_24h_top` 已经稳定指向某一类失败时，就不该再把精力花在无关小修上
  3. 这层聚合是 replacement plane / certification boundary 的优先级放大器

### [DL-024] 要同时看 `active_top` 和 `recent_24h_top`，否则会把“当前风险态”误当成“最近主导失败模式”

- **日期**：2026-03-08
- **症状**：在 `room_54b5dd2c273a` 的 live 过程中，summary 先显示 `active_top=managed_runtime_uncertified:1`，随后在 host runner 掉线后变成 `active_top=runner_lost_before_first_relay:1`；与此同时，`recent_24h_top` 一直稳定指向 `runner_lost_before_first_relay`。如果只看某一个时间点的 active_top，容易误以为“当前最重要的问题只是 uncertified”，从而错配后续工作重心。
- **初始错误判断**：容易把 active 房间当前的 top reason 当成系统整体最重要的根因，因为它看起来“更实时”。
- **根因**：`active_top` 描述的是**此刻**的风险分布，`recent_24h_top` 描述的是**一段时间内**的失败主导模式。前者更适合当下值班和救火，后者更适合决定下一轮实现的主战场。
- **定位过程**：在同一轮 live Telegram 诊断中，先看到 room 还在 `managed_attached / candidate / ready` 时 summary 把 `managed_runtime_uncertified` 排到 active_top；等 host runner 失联后，active_top 变成 `runner_lost_before_first_relay`。但 recent_24h_top 从头到尾都在指向 runner loss。这正好证明两个视角都有用，而且不能混用。
- **修复**：
  1. summary 同时保留 `active_top` 和 `recent_24h_top`
  2. ops `Root Causes` 卡片同时展示 active 和 recent 24h 两条分布
  3. runbook 明确要求 incident triage 先看两者是否一致，再决定是继续诊断单房间还是推动系统级修复
- **教训**：
  1. `active_top` 用来救当前的房，`recent_24h_top` 用来决定下一轮要做什么
  2. 当两者都指向同一个 code 时，我们就更有把握那是真正的 blocker
  3. 当前这套数据已经足够证明：主 blocker 仍然是 runner survivability，而不是 prompt copy

### [DL-025] 只有 root-cause code 还不够，replacement plane 需要 runner checkpoint 才能知道“死在第几步”

- **日期**：2026-03-08
- **症状**：到 `room_54b5dd2c273a` 这轮为止，我们已经能稳定看到 `runner_lost_before_first_relay`，但它仍然覆盖了至少三类完全不同的失败：runner 还没开始 poll 就掉了、已经看到 relay 但死在生成 reply、reply 都准备好了却死在 send 前后。只看 generic code，会让 replacement plane 和 certification boundary 继续过粗。
- **初始错误判断**：容易以为“既然已经知道是 pre-first-relay runner loss，就该直接做 replacement queue”，不需要再往 runner lifecycle 里加更多细节。
- **根因**：`attempt_status` 只能表达“runner 当前活不活、在不在 waiting_owner”，不能表达“最后一次活着时跑到了哪一步”。没有 checkpoint，系统就无法回答“session ready 了吗、开始 poll 了吗、看到 relay 了吗、生成 reply 了吗、发出 reply 了吗”。
- **定位过程**：对照 OpenClaw bridge / Codex bridge / shell bridge 的循环逻辑后发现，真正缺的不是更多 event types，而是一个统一的 runner-plane checkpoint vocabulary。只要把 `joined / session_ready / event_polling / relay_seen / reply_generating / reply_ready / reply_sent / owner_wait` 这些阶段直接写进 attempt truth，就能把 generic root cause 进一步收窄。
- **修复**：
  1. `participant_attempts` 新增 `phase / phase_detail / phase_updated_at`
  2. `runner_claim / renew` 支持 phase checkpoint
  3. OpenClaw bridge、Codex bridge、shell bridge 现在都会上报关键 phase
  4. Room snapshot / result 的 `runner_attempts` 现在携带 phase
  5. root-cause hints 现在能区分 `runner_lost_before_event_poll`、`runner_lost_during_reply_generation`、`runner_lost_during_reply_send` 等更细原因
- **教训**：
  1. `status` 告诉我们 runner 现在是什么状态，`phase` 告诉我们它最后死在哪一步；两者缺一不可
  2. 如果没有 phase checkpoint，我们就很难判断 replacement plane 该优化“启动/claim”还是“生成/send”这半段
  3. 这层 checkpoint 也是未来把本地 Codex、Claude Code、OpenClaw、云端 bot 都纳入同一项目控制面的必要基础

### [DL-026] Phase 还不够，candidate runtime 的退出原因必须分出 signal 类

- **日期**：2026-03-08
- **症状**：我们已经能在 live Telegram/OpenClaw 房间里把问题收窄到 `runner_lost_during_relay_wait`，但还不能区分它是逻辑自退，还是宿主 runtime/session 把 child 收掉了。
- **初始错误判断**：很容易继续怀疑 prompt、join 流程或者 room core，而忽略掉 `client_exit` 这个 release reason 本身太模糊。
- **根因**：phase checkpoint 只回答“死在哪一步”，不回答“为什么死”。而 bridge/shell 的默认 release reason 几乎都会回落成 `client_exit`，把正常收尾和宿主层终止混在了一起。
- **定位过程**：回看 `room_10af37262248` 这种 live 样本，我们已经能确定 host 死在 `waiting_for_peer_join`，但再往下就断了。顺着 Python bridge 和 shell bridge 的 finally/cleanup 路径看，发现退出时并没有 signal-aware 分类。
- **修复**：
  1. OpenClaw bridge / Codex bridge / shell bridge 现在会把 `SIGTERM / SIGHUP / SIGINT` 分类成 `signal_term / signal_hup / signal_int`
  2. room root-cause hints 新增 `runner_received_termination_signal`
  3. 这样 summary/ops 就能区分“被宿主回收”与“普通 client_exit”
- **教训**：
  1. replacement plane 要想真正靠谱，必须同时拥有 `phase` 和 `exit classification`
  2. `client_exit` 这种兜底 reason 只适合当未知类，不适合继续做主要诊断语言
  3. 对更大的本地+云端 multi-agent 协作场景来说，runtime certification 一定要建立在这种可区分的退出证据之上

### [DL-027] 这轮真正主导的不是 signal，而是 candidate runner 在 relay-wait 阶段反复 lease expired

- **日期**：2026-03-08
- **症状**：signal-aware 版本上线后，新的 live Telegram 房 `room_1cd2d960d90c` 并没有先打出 `runner_received_termination_signal`，而是不断在 `managed_attached / candidate` 下反复进入 `replacement_pending`，turn 仍然停在 0。
- **初始错误判断**：刚做完 signal classification 时，很容易预期“下一轮 root cause 应该直接切到 signal”，从而把注意力继续放在“是不是 runtime 杀进程”。
- **根因**：这轮更强的证据表明，当前更常见的失败是 **candidate shell runner 在 `waiting_for_peer_join / event_polling` 阶段活不够久，最后 lease expired**。也就是说，哪怕没有明确 signal，runner 仍然没能持续跨过 first relay。
- **定位过程**：room 先后暴露出：
  1. `guest:waiting_for_peer_join:initiator_waiting_for_peer`
  2. `host:event_polling:poll_ready`
  3. 随后 snapshot/result 的 primary hints 从 generic `runner_lost_during_relay_wait` 进一步收窄成 `lease_expired_during_relay_wait`
  4. manual close 后 `/result` 仍保留 `lease_expired_during_relay_wait`、`lease_expired_before_first_relay`
- **修复**：
  1. root-cause hints 新增按 phase 细分的 lease-expired codes
  2. live diagnostic 现在能明确看到 `lease_expired_during_relay_wait`
  3. roadmap 重点继续压到 certified runtime boundary + replacement plane，而不是回头过度优化 prompt
- **教训**：
  1. signal classification 是必要的，但它不是当前唯一主因
  2. 现在最该盯的是“candidate managed runner 在 first relay 前能活多久”，也就是 survivability / lease / replacement，而不是文案或 skill 小修
  3. 对你更大的本地+云端 agent 协作场景来说，这个结论非常重要：项目级协作能不能成立，核心不在 agent 会不会聊天，而在 runner 能不能跨过同一条 durable execution boundary

### [DL-028] 邀请 token 不能兼任长期 participant session token，否则 repair reissue 会把活着的 runner 打成 unauthorized

- **日期**：2026-03-09
- **症状**：`room_c464dc40d3c9` 里 host shell runner 进程还活着，但日志开始反复出现 `events poll error: unauthorized`，随后 room 把 host attempt 判成 `runner_abandoned`。这不是简单的 child 进程死亡，因为本地 `ps` 还能看到 shell runner 存活。
- **初始错误判断**：容易继续把问题归为 generic `runner_lost_before_first_relay` 或 detached child 被宿主回收。
- **根因**：我们之前把 invite token 同时当成 join auth 和 join 后的长期 participant auth。可一旦系统为了 recovery reissue 了某个 participant 的 invite，旧 invite digest 会被轮换掉，已经 join 的 runner 继续拿旧 invite 去 `/events`、`/heartbeat`、`/messages` 就会被直接打成 `unauthorized`。
- **定位过程**：先在真实 host log 里看到 `events poll error: unauthorized; retrying`，再结合 repair invite 的 token rotation 机制反推，确认 joined participant 不应该继续依赖 invite token 作为 session 身份。
- **修复**：
  1. `/rooms/{id}/join` 现在会 mint 稳定的 `participant_token`
  2. Edge post-join APIs 支持 `X-Participant-Token`
  3. OpenClaw bridge / Codex bridge / shell bridge join 成功后会切换到 participant session token
  4. production conformance 新增 CT37，验证 repair invite reissue 不会再让已 join participant 失去 access
- **教训**：
  1. invite token 只适合做 join / repair 包，不适合做长期 session 身份
  2. runner 还活着但突然 `unauthorized`，首先该怀疑 auth boundary，不该立刻怀疑 prompt 或 child process
  3. 这是 runner plane 的边界问题，不是 room core 的边界问题

### [DL-029] shell runner 不能只靠主循环 heartbeat；只要主循环卡住，lease 就会在 first relay 前悄悄过期

- **日期**：2026-03-09
- **症状**：在修完 participant token 以后，`room_6d72fdfc31a2` 仍然会在 first relay 前进入 `runner_abandoned`。这时 `unauthorized` 已经不再出现，说明旧的 auth 根因已经被移除。
- **初始错误判断**：容易以为“既然进程还活着，那 lease 过期就不会再发生”。
- **根因**：旧 shell bridge 只有主循环里才会 `send_heartbeat_if_due`。一旦 `events` poll 或 reply generation 阶段阻塞，runner 看起来还在，但 lease renewal 实际上已经停了。
- **定位过程**：先从 live room / ops 看到 `lease_expired_before_first_relay` 这类 code 仍在，再回到脚本实现，确认 `json_request()` 没有硬超时、heartbeat 也没有独立 watchdog。
- **修复**：
  1. `json_request()` 加入 `--connect-timeout` / `--max-time`
  2. GET 请求加入 bounded retry
  3. shell bridge 增加独立 heartbeat/watchdog，哪怕主循环卡住也能继续 heartbeat + runner renew
  4. 新 smoke test 故意把 `events` 卡住，验证 watchdog 期间 heartbeat/renew 仍会继续增长
- **教训**：
  1. “进程活着”不等于“lease 还活着”
  2. candidate shell runtime 要想有资格进入 certified 讨论，必须先满足独立续租
  3. 之后的 live E2E 里，`pre-first-relay lease expiry` 如果再出现，就更可能是 runtime attach/claim 语义问题，而不是简单没 heartbeat

### [DL-030] 真实 live 现在已经把主 blocker 从“host 活不下来”推进成“cloud guest 从未 attach managed runner”

- **日期**：2026-03-09
- **症状**：最新两轮 live Telegram E2E 出现了两个非常关键的样本：
  1. `room_7e935f16d180` 里 host 侧已经能跑出真实 `ANSWER`，说明本地 shell watchdog 至少把 host 的 first relay 生命周期往后推了
  2. `room_eddd29420379` 里 guest joined=true，但没有任何 guest attempt，且一直到 manual close 都没有 first relay
- **初始错误判断**：如果继续用旧 root-cause 语言，很容易把这类房间都归到 generic `runner_lost_*`，看不出“根本没 attach 成 managed runner”和“attach 成了之后掉线”是两回事。
- **根因**：对云端 OpenClaw / chat-only runtime 来说，prompt 里要求执行 shell runner 并不等于它真的会稳定 attach 一个 managed attempt。结果就是 participant 可能 join 成功、甚至发出一条 API-first 消息，但从头到尾没有 managed runner claim。
- **定位过程**：`room_eddd29420379` 的 live snapshot 明确显示：
  - guest `joined=true`
  - guest `online=true`
  - `runner_attempts` 只有 host
  - `first_relay_at=null`
  - root cause 在修复后被明确收窄成 `managed_runner_never_attached_before_first_relay`
- **修复**：
  1. root-cause hints 现在区分 “never attached managed runner” 与 “runner attached then lost”
  2. recovery action reason 对无 attempt 的 participant 直接写成 `no_managed_runner`
  3. production conformance 新增 CT38，锁住这个语义
- **教训**：
  1. 当前最大系统级 blocker 已经从 generic runner survivability 进一步分叉：
     - 本地 shell candidate：现在更像可继续逼近 certified 的候选
     - 云端 chat-only OpenClaw：当前更像 compatibility / takeover path，不应继续伪装成 managed SLA path
  2. 这意味着接下来最好的路线不是继续堆 prompt，而是：
     - **certified runtime boundary**
     - **replacement plane**
     - **外部 runtime 明确分层**
  3. 对未来多 agent project control plane 来说，这也是最关键的第一性原理：我们必须先知道哪些 runtime 真的能 attach 成 durable participant，哪些只能算 owner-visible compatibility node

### [DL-031] 本地 Telegram/OpenClaw 不是“完全跑不动后台 child”；真正更像是双端 join 节奏超过了 candidate runner 的生存窗口

- **日期**：2026-03-09
- **症状**：
  1. 真实房 `room_41b5642ad182` 里，本地 host 的 candidate runner 先 attach 成功，但一直停在 `waiting_for_peer_join`，随后在 guest 真正 join 前就掉成 `abandoned`
  2. 同一轮里，cloud guest 最后也能 join 成 `OpenClawShellBridge`，说明这次并不是单纯 “guest never attached”
  3. 与此同时，两个独立 Telegram 诊断都成功了：
     - 普通 detached shell/python child 都能活过 25 秒
     - detached child 里再跑一次嵌套 `openclaw agent` 也能返回 `OK`，并且 child 在返回后继续活过 25 秒
- **初始错误判断**：把所有 Telegram/OpenClaw managed 失败都继续归成 “本地 runtime 会统一杀掉 shell child” 或 “reply generation 一定被 TERM”。
- **根因**：这轮更像是 **真实双端房间节奏问题**：
  - host candidate runner 的等待窗口太短
  - guest 的真实 attach/join 节奏太慢
  - 结果是 host 在 `waiting_for_peer_join` 阶段先耗尽自己的 candidate 生存窗口
  - 之后 room 才逐渐进入双边 full-managed，但为时已晚
- **定位过程**：
  1. `room_41b5642ad182` 的 live snapshot 先显示 host `managed_attached / candidate / partial`
  2. last-live snapshot 记录到 `first_relay_at_risk + replacement_pending + repair_package_issued + repair_claim_overdue`
  3. manual close 后 final room 显示 guest 已 join=true 且 guest runner 也 attach 过，但 host attempt 已经在更早之前 `abandoned`
  4. 独立 Telegram 诊断又证明：本地 bot 环境可以保活 detached child，也可以在 detached child 里跑 nested `openclaw agent`
- **修复**：
  1. shell bridge 现在会保留更完整的 `reply_generation rc/stderr` 证据，并在 session-lock 错误时自动 rotate session 再试一次
  2. signal/cleanup trap 已经前移到真正 runner claim 之前，避免“已经 claim 了但还挂在 early trap”这种误判
  3. Telegram E2E harness 现在会持续保存 `last_result` 和 `last_live_*`，即使失败/手动 close 也不再把现场丢空
- **教训**：
  1. “后台 child 能不能活” 和 “双端 room 能不能在真实节奏下稳定走到 first relay” 是两件不同的事
  2. 当前 candidate path 的主要矛盾，更像是 **peer join latency > candidate runner dwell time**
  3. 这再次说明：想过 DoD，不能继续把 raw Telegram/OpenClaw candidate path 当 product-owned 主路径

### [DL-032] Telegram/OpenClaw gateway 能理解 wake package，不等于它能可靠执行 localhost `runnerd`
- **日期**：2026-03-11
- **症状**：我们已经有了 `runnerd` 和 wake package，以为 Telegram/OpenClaw gateway 只要照 prompt 执行 `POST http://127.0.0.1:<port>/wake` 就能把本地 sidecar 拉起来。但真实 probe 里，room 会保持 `joined=false / turn_count=0 / execution_mode=compatibility`，同时 `runnerd` 日志里完全看不到新的 `/wake` 或 `/healthz` 请求。
- **初始错误判断**：一开始容易把问题继续归到 room、join link、runner claim，或者误以为 runnerd 本身挂了；也容易以为“prompt 再写严一点”就能解决。
- **根因**：当前 Telegram/OpenClaw chat runtime 不能被假设为“可靠执行 localhost sidecar 请求”的环境。它能读懂 wake package、能做 gateway UI，但并不等于它真的会完成本机 HTTP 调用和后续 polling。
- **定位过程**：先让 `runnerd` 在单独 PTY 里稳定运行；再对本地 Telegram OpenClaw 连续做两次最小 probe：一次要求 `POST /wake`，一次只要求 `GET /healthz`。两次 probe 后 room 和 runnerd 都没有任何新 attach 证据。随后直接在同一台机器上用 `python3 apps/runnerd/src/runnerd/submit_cli.py --text-file ...` 手动提交同一份 wake package，host 和 guest 立即都能 attach，room 进入 `managed_attached / certified / product_owned=true`。这把根因钉死在 “Telegram gateway -> localhost runnerd” 这一跳，而不是 room core 或 runnerd 本身。
- **修复**：
  1. 新增 `apps/runnerd/src/runnerd/submit_cli.py` 和 `owner_reply_cli.py`
  2. V0 路线改成 **owner 手动转发 wake package + 本地 helper 提交**
  3. skill / prompt / flow 文档统一改口：Telegram 仍是首要 gateway，但当前不再默认它能可靠直接驱动 localhost runnerd
- **教训**：
  1. `gateway can understand the package` 和 `gateway can execute the local wake path` 是两件事
  2. V0 最可靠的跨 owner 流不一定是“最自动”，而是“最可验证、最可调试”
  3. 真正要认证的应该是 `runnerd + Python bridge`，不是“聊天回合里的 localhost sidecar 调用”
