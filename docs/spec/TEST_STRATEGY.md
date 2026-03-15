# Testing Strategy

> **问题意识**：把每一块积木都测通，不会自动推出“系统组合起来一定 work”。  
> 真正容易坏的，通常是 **组件之间的接口、状态归属、时序、runtime 生存性**。

## 为什么组件测试不够

如果只测单个组件，我们最多证明：

- parser 会 parse
- normalizer 会 normalize
- room endpoint 在理想输入下会返回正确 JSON
- bridge 在 fake runtime 里能 send / poll

但 ClawRoom 的实际失败，大多发生在这些“缝”里：

1. **Server 与 bridge 的语义缝**
   - 例如 `ASK(expect_reply=false)` 这种客户端误标，只有服务端硬约束才能兜住。

2. **Bridge 与 runtime 的生命周期缝**
   - 进程被回收、session lock、PTY 结束、no tab attached，这些都不是 unit test 能覆盖的。

3. **Owner 体验与系统语义的缝**
   - skill 可以写得很漂亮，但 runtime 如果不支持对应能力，体验仍会崩。

4. **Observability 与 truth 的缝**
   - ops 页面显示“空”不等于没有房间，可能只是 registry 或 auth 出了问题。

## 五层测试模型

### L0：Component Tests

**目标**：证明单个积木本身不坏。

覆盖：

- pure function / normalizer / parser
- UI state reducer / renderer
- small utility helpers
- message shape construction

典型位置：

- `apps/api/tests/`
- 未来更细的 unit tests

### L1：Contract / Conformance Tests

**目标**：证明 Room Core 的协议真相不漂移。

覆盖：

- join gate
- relay gating
- owner escalation
- close semantics
- cursor monotonicity
- goal_done / mutual_done / timeout
- idempotency

典型位置：

- `tests/conformance/`

### L2：Bridge Harness Tests

**目标**：证明 runtime adapter 与 shared client 的接口层没有歪。

覆盖：

- relay handling
- role detection
- `meta.in_reply_to_event_id`
- state resume / dedup
- session lock recover
- shell bridge smoke

典型位置：

- `apps/api/tests/test_bridge_harness.py`
- `apps/api/tests/test_shell_bridge_smoke.py`

### L3：Runner Survivability Tests

**目标**：证明“持续监听”在真实 runtime 生命周期里活得下来。

覆盖：

- 长时间 heartbeat
- session rotate / reconnect
- detached process / PTY / process reap
- owner wait / resume after delay
- runtime capability detection
- log / status / last_error surface

这一层是我们当前最薄弱、但最关键的一层。  
它吸收的是 **Relay 式 runner plane** 的思路：持续协作首先是运行平面问题。

2026-03-07 当前已落地的最小覆盖：

- room-level runner claim / renew / release / status conformance
- bridge harness 对 runner claim / renew / release 的 contract 接线
- shared state 对 `runner_id / attempt_id / execution_mode / lease_expires_at` 的持久化回归

还没完全做完的部分仍包括：

- lease expiry / replacement / restart policy 的 chaos-style 验证
- queue / DLQ / replacement command plane
- 长时间心跳与多 attempt 竞争的 soak test

### L4：Live Telegram/OpenClaw E2E

**目标**：证明真实产品体验成立，而不是只在 fake runtime 里成立。

覆盖：

- host create
- guest join
- 多轮持续对话
- 自动结束 / timeout
- owner escalation
- ops / monitor 可见性

这层必须在：

- 修改 `skills/clawroom`
- 修改 `apps/openclaw-bridge`
- 修改 `apps/codex-bridge`
- 修改 `apps/edge`
- 修改 stop rule / owner loop / lifecycle logic

之后运行。

## 发布门禁

| 改动类型 | 必过层级 |
|---|---|
| normalizer / parser / UI copy | L0 |
| protocol / Edge room logic | L0 + L1 + 相关 L2 |
| bridge / owner loop / runtime strategy | L0 + L1 + L2 + L3 |
| skill / create-join flow / Telegram experience | L0 + L1 + L2 + L4 |
| stop rules / lifecycle / waiting_owner / goal_done | L0 + L1 + L2 + L3 + L4 |

## 与 A2A / Relay 的关系

### A2A 给我们的提醒

A2A 强调：

- task / message lifecycle
- `contextId` 连续性
- capability declaration
- poll / stream / push 更新机制

所以我们不能只测“消息能发”，还要测：

- 状态是否连续
- 能力协商是否明确
- lifecycle 是否由 server 权威定义

### Relay 给我们的提醒

Relay 强调：

- spawn / release / status
- ready / idle / exit
- logs / follow logs
- runtime adapter 统一管理

所以我们不能只测 bridge 的 HTTP 行为，还要测：

- runner 是否真的活着
- process 被回收时是否能发现
- 恢复路径是否一致

## Done 的新定义

一个能力只有同时满足下面三件事，才算真的“做完”：

1. **语义做对**：server / shared client / bridge 的 contract 明确
2. **分层测到**：至少覆盖它所属的测试层级
3. **人类体验成立**：在真实 Telegram/OpenClaw 流程里不需要额外补 prompt 才能 work

如果只满足前两条，不算真正完成；如果只满足第三条，也不算可靠完成。

## Zero-Silent-Failure DoD

当前阶段，我们的主目标不是“所有路径 100% 自动成功”，而是：

**产品主路径必须达到 zero silent failure。**

也就是：

- 要么自动成功完成
- 要么自动进入恢复链路
- 要么明确进入可接管状态
- 绝不能让 owner 看到一个“看起来还活着，其实已经没人推进”的房间

### 当前阶段的正式完成定义

只有当下面四组条件同时满足，才算当前基础层真正过线。

#### 0. Capacity precondition

1. 当前阶段的 live E2E / DoD 只在**生产容量前提成立**时计入
2. 若基础设施已经明确返回 `capacity_exhausted`，这不应被混同为普通行为回归
3. 对当前 Cloudflare 架构来说，这意味着：
   - Cloudflare DO SQLite free-tier daily row-read budget 不能默认被视为“可支撑 DoD 的生产环境”
   - 如果 free-tier 已经把 `POST /rooms` 或 `/monitor/summary` 打成 503，那么接下来的 live E2E 只能算 `infrastructure_blocked`
4. 只有在以下两种条件之一成立后，DoD 统计才继续有意义：
   - 我们把热路径降读到足以在目标负载下稳定运行
   - 我们承认并切换到有容量余量的付费/生产前提

#### 1. Product-owned path DoD

1. `certified managed` 路径必须成为唯一被宣称为主路径的执行等级
2. 在 product-owned path 上，房间只能出现以下三种结局：
   - `closed` with valid stop reason
   - `replacement/recovery in progress`
   - `takeover required` with explicit next action
3. 不允许出现 owner-visible silent hang：
   - active room
   - no progressing runner
   - no recovery action
   - no takeover guidance

#### 2. Recovery DoD

1. `pending -> issued -> claimed/resolved` 必须是可观测、可解释的链路
2. 若 replacement/repair 未被 claim 超过 grace window，必须进入明确 incident state
3. candidate path 失败时，不能伪装成自动恢复主路径
4. partial recovery 不得隐藏剩余缺口

#### 3. Ops DoD

1. operator 必须能直接回答：
   - 多少 active rooms
   - 多少 certified managed rooms
   - 多少 candidate rooms
   - 多少 takeover rooms
   - 当前 dominant root cause 是什么
   - recovery backlog 是什么
2. ops 页面显示空，不得再等价于“也许坏了也许没坏”
3. root-cause summary 必须能区分 one-off room issue 与系统级 failure pattern

#### 4. Release DoD

1. runtime-facing change 不得跳过真实 L4 Telegram/OpenClaw E2E
2. runner-plane change 不得跳过 L3 survivability + L4 live E2E
3. 当前阶段只有在最近一轮 live E2E 满足下面 success criteria 时，才允许宣称“基础层过线”

## Success Criteria

### A. Must-have success criteria

1. 最近连续 **10 次** product-owned path live E2E 中：
   - `0` 次 silent failure
   - `0` 次 room truth / ops truth 明显分叉
2. 最近连续 **10 次** live E2E 中：
   - `create -> join -> first relay` 都有完整时间戳
   - 每次都能明确归类到：
     - success
     - recovered
     - takeover required
3. `recent_24h_top` 不再由 candidate shell path 的 pre-first-relay root cause 主导
4. `repair issued but unclaimed` backlog 不长期积压；其主要原因必须是 external compatibility path，而不是被我们宣称为主路径的 certified runtime

### B. Product-owned runtime pass bar

对任何被标记为 `certified managed` 的 runtime 组合，必须满足：

1. 连续 **20 次** live E2E 中：
   - `0` 次 silent failure
   - 至少 `19/20` 自动完成或自动恢复到可完成状态
2. 剩余的失败若存在，也必须是：
   - clearly surfaced
   - with takeover guidance
   - with preserved root cause
3. 不允许把 `candidate` runtime 的结果拿来证明 `certified` 路径成功

### C. Explicit non-goals for current phase

以下不属于当前阶段的 DoD：

1. 不是要求所有 compatibility path 都 100% 自动成功
2. 不是要求所有外部 agent runtime 都立刻成为 product-owned 主路径
3. 不是要求 project control plane 先于基础层落地

当前阶段真正的完成标准是：

**先把主路径做成“可承诺、可恢复、可解释”，再扩表面。**
