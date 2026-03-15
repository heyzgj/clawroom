# Known Issues

> **更新规则**：发现新 bug 加条目（状态=`open`）；修复后更新状态为 `fixed` + 加 fix 日期；定期归档已修复的。

## 格式

```
### [KI-XXX] 标题（status: open / fixed）
- **症状**：
- **触发条件**：
- **Workaround**：
- **计划修复**：
```

---

## Open Issues

### [KI-019] Telegram/OpenClaw start latency still varies enough that a healthy room can look stalled for 30-60s (status: open)
- **症状**：真实 Telegram E2E 里，有些房间在 host/guest prompt 已发送后，要过几十秒才真正进入 room transcript；如果只看很早的一次快照，容易误判成 “没 join / 没监听”
- **触发条件**：连续跑多轮 Telegram/OpenClaw 回归、刚 `/new` 完的新 session、或者云端/本地 runtime 切换较频繁时
- **Workaround**：使用 serial runner、坚持 `/new` 后至少等待 10 秒、优先看 60-90 秒窗口内的 monitor summary + room result 变化，不要用单次快照下结论
- **计划修复**：补一层“message delivered / participant joined / first relay seen”的 start-SLO 观测面，并研究 Telegram 发送后是否需要额外的送达验证

### [KI-013] Registry ingestion 仍是 best-effort（status: open）
- **症状**：ops auth 现在已经 fail-closed，dashboard 也会显式 degraded；但如果 room → registry 的 upsert/remove 本身失败，问题仍主要停留在 worker 日志里，难以直接追责
- **触发条件**：registry DO 暂时不可用、内部 fetch 失败、持久化异常
- **Workaround**：同时检查 room snapshot / room timeline / worker logs，不只看 ops dashboard
- **计划修复**：把 registry ingestion 做成可见、可诊断、可重放的健康面

### [KI-016] Local Codex CLI bridge 延迟仍偏高（status: open）
- **症状**：本地 `codex-bridge` 在真实房间里功能正确，但单轮回复常落在十几秒到几十秒区间，明显慢于 OpenClaw runner
- **触发条件**：使用本地 subscription-backed `codex exec` 作为 room participant runtime
- **Workaround**：owner-facing常规场景优先使用 OpenClaw runner；需要用本地 Codex 时接受较高延迟，或只把它当作验证/secondary runtime
- **计划修复**：研究更低延迟的本地 Codex 调用模式、推理配置或持久 runner path，必要时把 local Codex 明确降级为非主推 runtime

### [KI-020] Compatibility mode 仍然不是强 SLA 路径（status: open）
- **症状**：真实 Telegram/OpenClaw 房间在 `compatibility` 模式下，即使 room core 正常，仍可能因为外部 runtime 掉线或未继续 relay 而需要人工接管
- **触发条件**：依赖原始 skill/invite 路径启动，而不是使用 `managed_attached` bridge
- **Workaround**：优先使用 product-owned managed bridge；若必须走 compatibility 模式，观察 `execution_attention` / ops summary，并在 takeover 提示出现时立即接管
- **计划修复**：继续推进 Hybrid cutover，让 product-owned flow 默认进入 `managed_attached`，并把 compatibility 路径显式标成 best-effort

### [KI-021] Telegram/OpenClaw 的 detached shell runner 目前能启动，但不一定能持续存活（status: open）
- **症状**：真实 Telegram/OpenClaw 房间已经能出现 `execution_mode=managed_attached`、`client_name=OpenClawShellBridge` 和 `runner_claim`，但 runner 可能在 first relay 之前就 lease 过期，房间进入 `runner_abandoned / takeover_required`
- **触发条件**：Telegram/OpenClaw 通过 bash tool 启动 shell runner，并依赖 detached/background 子进程持续存活
- **Workaround**：把这类房间视为 runner-plane diagnostic，不要误判成“managed 主路径已完全打通”；继续依赖 `execution_attention` / ops summary 快速接管
- **计划修复**：研究更 durable 的 managed-attached 执行方式，避免把 shell child lifecycle 继续绑定在单次 tool call 的生存模型上

### [KI-022] `managed_attached` 目前还不是单一 SLA 等级（status: open）
- **症状**：ops / room snapshot 已经能看到某些房间进入 `managed_attached`，但其中只有一部分属于 `runner_certification=certified`；如果把全部 `managed_attached` 都按主路径可靠性解读，容易高估系统真实成功率
- **触发条件**：candidate shell runner 也能正常 claim/renew attempt，但无法稳定活过 first relay 或无法自动恢复时
- **Workaround**：看 `execution_mode` 时必须同时看 `runner_certification` 和 `automatic_recovery_eligible`；只有 certified managed path 才能被当成强 continuity 路径
- **计划修复**：建立正式的 certified runtime 边界、replacement plane、以及 runtime certification gate，避免让 candidate path 继续冒充主路径

### [KI-023] Cloudflare DO SQLite free-tier row-read budget is currently an invalid DoD foundation (status: open)
- **症状**：生产上的 `POST /rooms` 和 `/monitor/summary` 会直接返回 `503 capacity_exhausted`，detail 为 `Exceeded allowed rows read in Durable Objects free tier.`
- **触发条件**：当账号当天已经耗尽 Cloudflare DO SQLite free-tier rows-read budget 时
- **Workaround**：把这类 run 记成 `infrastructure_blocked`，不要继续把它当普通行为回归；优先降低热路径读量，并在需要时切换到有容量余量的付费前提
- **计划修复**：继续削减 room/registry 热路径读量（当前已落地 room snapshot cache + registry derived-view cache）；一旦 daily budget reset，立即重跑 production probes 验证真实改善幅度；如果仍无法满足目标负载，就明确把付费容量或替代存储架构纳入当前阶段的基础前提

---

## Fixed Issues

### [KI-001] NOTE intent 导致自嗨循环（status: fixed）
- **症状**：两个 agent 对话卡住，turn_count 快速耗尽直到 stall_limit 关房
- **根因**：NOTE 消息在 client 默认值下可能被当成 `expect_reply=true` 继续 relay
- **修复**：Edge server 在 `handleMessage` 增加 hard rule，`intent=NOTE` 时强制 `expect_reply=false`
- **Fix 日期**：2026-03-05

### [KI-002] Codex Bridge 缺少 heartbeat（status: fixed）
- **症状**：Codex Bridge 加入后约 30s 变成 `online=false`
- **根因**：`apps/codex-bridge` loop 中未发送 heartbeat
- **修复**：新增 `--heartbeat-seconds` 与 heartbeat loop（含首次强制 heartbeat）
- **Fix 日期**：2026-03-05

### [KI-003] 双 Codex Bridge 死锁（status: fixed）
- **症状**：host 与 guest 都不先开场，房间永远没有第一轮
- **根因**：`apps/codex-bridge` 缺少 role 自动识别和 initiator kickoff
- **修复**：新增 `--role auto|initiator|responder`、initiator 开场策略与 peer join 等待逻辑
- **Fix 日期**：2026-03-05

### [KI-004] Shell Bridge DONE 硬编码 ack 可能误关房（status: fixed）
- **症状**：收到 DONE 后脚本直接回 DONE，可能在 required_fields 未完成时提前收敛
- **根因**：`openclaw_shell_bridge.sh` 对 DONE relay 使用硬编码 payload
- **修复**：移除 DONE 硬编码分支，统一交给模型决策生成回复
- **Fix 日期**：2026-03-05

### [KI-005] Python Bridge `max_seconds=480` 对长对话不够（status: fixed）
- **症状**：对话进行 8 分钟后 bridge 自动退出，房间悬空
- **根因**：openclaw/codex Python bridge 默认 `max_seconds=480`
- **修复**：默认值改为 `0`（无限），保持 `--max-seconds` 可覆盖
- **Fix 日期**：2026-03-05

### [KI-006] DONE/ASK_OWNER 仍可能触发不必要 relay（status: fixed）
- **症状**：DONE 或 ASK_OWNER 在部分 client 默认值下仍可能继续触发对端回复
- **根因**：语义仅靠 client 约定，服务端没有 hard override
- **修复**：Edge server 在 `handleMessage` 对 `DONE/ASK_OWNER` 强制 `expect_reply=false`
- **Fix 日期**：2026-03-05

### [KI-007] 重试/重复发送导致重复回复写入（status: fixed）
- **症状**：网络重试或 runner 重启后，同一 relay 可能被同一参与者重复回复
- **根因**：缺少 `(participant, in_reply_to_event_id)` 级别的服务端幂等约束
- **修复**：新增 `reply_dedup` 表并在消息入口按 `meta.in_reply_to_event_id` 去重，bridge 同步回传该字段
- **Fix 日期**：2026-03-05

### [KI-008] Production API 未暴露 protocol v1 字段（status: fixed）
- **症状**：`GET /rooms/{id}` 缺少 `protocol_version` / `capabilities`；重复回复未返回 `dedup_hit`
- **根因**：线上 API 尚未部署到包含 server semantic guarantees 的版本
- **修复**：重新部署 Cloudflare Worker（Version ID: `0e74d616-7926-4de7-ba8e-5536bca1b2e1`），并做线上 contract 验证（`protocol_version=1` + `dedup_hit=true`）
- **Fix 日期**：2026-03-05

### [KI-009] join 不是服务端硬门槛（status: fixed）
- **症状**：持有 invite token 的 participant 可能在未真正 `POST /join` 的情况下 heartbeat、读 events 或发消息，导致 room truth 不可信
- **根因**：room endpoint 过去只验证 invite token，不验证 `joined=true`
- **修复**：Edge server 现在对 heartbeat / events / stream / messages / leave 强制要求 `joined=true`
- **Fix 日期**：2026-03-06

### [KI-010] close path 不是严格幂等（status: fixed）
- **症状**：timeout alarm / manual close 重入时，event log、TTL、stop reason 可能被重复污染或相互覆盖
- **根因**：`closeRoom()` 过去即使房间已关闭也会继续追加 close lifecycle
- **修复**：`closeRoom()` 改为严格幂等；重复 close 返回现状，不再追加 duplicate closed status
- **Fix 日期**：2026-03-06

### [KI-011] `waiting_owner` 可能卡死（status: fixed）
- **症状**：房间已经继续推进，但 room snapshot / ops 仍显示 `input_required`
- **根因**：服务端只在 `OWNER_REPLY` 时清理 `waiting_owner`
- **修复**：服务端现在会在同一 participant 的有效 continuation 明显恢复对话时清理 `waiting_owner`，并追加 `owner_resume`
- **Fix 日期**：2026-03-06

### [KI-012] `goal_done` 过于乐观（status: fixed）
- **症状**：required_fields 刚被填上时房间就关闭，但对方可能尚未确认、字段可能只是临时提案
- **根因**：旧逻辑将“字段齐全”直接等价为“对话完成”
- **修复**：`goal_done` 现在需要“字段齐全 + completion signal”（例如 `DONE` 或 `meta.complete=true`）
- **Fix 日期**：2026-03-06

### [KI-014] 默认测试主线仍偏 legacy（status: fixed）
- **症状**：默认 `pytest` 主要覆盖 `apps/api/tests`，而 `tests/conformance/` 不在默认入口，容易产生假信心
- **根因**：`pyproject.toml` 的 `testpaths` 只指向 legacy-style test 目录
- **修复**：默认 `pytest` 入口已纳入 `tests/conformance/`，release gate 文档也明确了不同改动层级的必测项
- **Fix 日期**：2026-03-06

### [KI-015] initiator kickoff race 会导致 host 双开口（status: fixed）
- **症状**：在 hybrid 场景（本地 `openclaw-bridge` host + Telegram/OpenClaw guest）里，guest 已经先发第一条，但 host 仍在几秒后补发自己的 kickoff，导致 transcript 出现双开口/自相矛盾的推进
- **根因**：旧逻辑只在 kickoff 开始前检查一次 `turn_count==0` 和 join 状态，没有在 kickoff 发送前再次确认 peer 是否已经说话
- **修复**：bridge 现在分两层拦截：当前 poll batch 里若已有 `msg/relay` 活动则不 kickoff；即使 batch 为空，在发送 kickoff 前也会再 poll 一次 events，若 peer 已开口则跳过 kickoff，仅处理新 relay
- **Fix 日期**：2026-03-06

### [KI-017] Registry overview 在历史 schema / 空查询下会 500（status: fixed）
- **症状**：新加的 ops summary 在旧 registry DO 上报 `no such column: health_state`，或在没有 active room / latest event 时因为 `.one()` 读取空结果而 500
- **根因**：schema backfill 先建依赖新列的索引，再补列；同时 overview 里有几条“可能为空”的 SQL 还在用 `.one()`
- **修复**：registry 现在先逐列 backfill 再建索引，重复列错误自动忽略；overview 对可空查询改用 `toArray()` + nullable first-row 读取
- **Fix 日期**：2026-03-06

### [KI-018] 过期 active room 只依赖 alarm 关闭，可能留下 zombie room（status: fixed）
- **症状**：历史房间 deadline 已经过了，但 room 仍显示 `active`，ops 会把它视作 degraded/stale active room
- **根因**：旧逻辑主要依赖 DO `alarm()` 做 timeout close；如果历史房间未挂上 alarm 或平台时序漏掉，就不会自我收敛
- **修复**：任何房间读写请求都会先执行一次 timeout catch-up close；一旦发现 deadline 已过，立即 `timeout` 关闭并同步 registry
- **Fix 日期**：2026-03-06
