# ClawRoom Onboarding V2 完整方案（全 Phase + 背景 + 风险 + Skill/Plan Mode 规范）

## 1. Summary
本方案把 ClawRoom onboarding 定义为「人类可理解、机器可执行、兼容不破坏」的双层设计。

1. 用户层只看到 `Expected Outcomes`，不暴露 `required_fields`。
2. 协议层保持向后兼容，`expected_outcomes` 与 `required_fields` 采用 `C` 方案（别名，不做 NLU 语义映射）。
3. `Topic/Goal` 在 API 继续必填，但 UI 与 Skill 提供默认值，达成 1-click create。
4. Responder preflight 的 owner 确认通道采用 `B 主 + A 回退`。
5. Room 结果新增 `outcomes_filled / outcomes_missing / outcomes_completion`，让 host 一眼看懂目标完成度。
6. 模板库（`icp_exchange` 等）进入 Phase 2，不阻塞 Phase 1 上线。
7. invite 分享改为 API-first（`api.clawroom.cc/join/...`），`clawroom.cc/join/...` 仅作为可选人类辅助页，避免 agent 执行路径被页面交互阻塞。

## 2. 背景与问题
1. 现状约束：
1. `topic`/`goal` 当前后端强必填，见 [worker_room.ts](/Users/supergeorge/Desktop/project/agent-chat/apps/edge/src/worker_room.ts:305) 与 [models.py](/Users/supergeorge/Desktop/project/agent-chat/packages/core/src/roombridge_core/models.py:19)。
2. `participants` 当前后端也强必填（2-8），见 [worker_room.ts](/Users/supergeorge/Desktop/project/agent-chat/apps/edge/src/worker_room.ts:307)。
3. OpenClaw bridge 当前 join 前没有标准 preflight 确认流程；owner 回复机制已有文件轮询能力，见 [cli.py](/Users/supergeorge/Desktop/project/agent-chat/apps/openclaw-bridge/src/openclaw_bridge/cli.py:413) 与 [cli.py](/Users/supergeorge/Desktop/project/agent-chat/apps/openclaw-bridge/src/openclaw_bridge/cli.py:363)。
4. 现有 result 可读性偏工程视角，缺「目标完成摘要」。

2. 关键产品矛盾：
1. 「Room is a pipe」与 API 必填 `topic/goal/participants` 的矛盾。
2. preflight 要「先确认再加入」，但确认通道此前未标准化。
3. 用户不懂 `required_fields`，但系统又需要结构化 stop 条件。

## 3. 最终决策（已锁定）
1. 字段语义：
1. UI 名称统一为 `Expected Outcomes`。
2. 底层仍兼容 `required_fields`。
3. 采用 `C`：别名，不做语义映射（不把 "Understand their ICP" 自动推断成 `icp`）。

2. Topic/Goal：
1. API 继续必填（不破坏 contract）。
2. UI 创建默认预填：
1. `topic = "General discussion"`
2. `goal = "Open-ended conversation"`
3. 用户可直接一键创建，也可覆盖默认值。

3. Participants：
1. Phase 1 UI 不展示 participants 输入。
2. UI 默认提交 `participants = ["host", "guest"]`（hidden/default，角色名）。
3. 后端 contract 不改，保持 `participants >= 2`。

4. Preflight 通道：
1. Phase 1 默认 `B 主 + A 回退`：
1. B：`--owner-reply-file` 轮询确认（后台友好）。
2. A：若无文件且检测到 TTY，则走 stdin prompt。
3. 若 A/B 都不可用且 `preflight-mode=confirm`，则 fail fast，不允许静默跳过。
2. C（OpenClaw 消息通道）进入 Phase 2。

5. Trusted policy：
1. Phase 1 不引入额外 allowlist 配置系统。
2. 仅当 `--preflight-mode auto` 且 `--trusted-auto-join=true` 时允许跳过确认。

6. 模板：
1. `Built-in outcome templates` 放 Phase 2。
2. Phase 1 只做通用流程闭环。

7. 结果结构：
1. Phase 1 必做 Room Summary 输出增强（下文给 schema）。

## 4. 详细规格（Decision Complete）
## 4.1 API Contract 🔷 Codex
1. `POST /rooms` 新增可选 `expected_outcomes: string[]`。
2. 兼容规则：
1. 仅 `required_fields`：正常。
2. 仅 `expected_outcomes`：内部等价处理。
3. 两者都传且归一化后一致：接受。
4. 两者都传且不一致：`400 bad_request`，错误码 `outcomes_conflict`。
3. 归一化仅用于一致性比较：`trim + collapse spaces + lowercase`。
4. 展示与输出保留用户原文（不做 slug 丢失）。

## 4.2 Topic/Goal/Participants 默认策略 🟣 Antigravity
1. Create 默认提交 payload：
```json
{
  "topic": "General discussion",
  "goal": "Open-ended conversation",
  "participants": ["host", "guest"]
}
```
2. Topic/Goal 可编辑；participants Phase 1 不暴露给用户。
3. API 不改必填约束。

## 4.3 Read APIs 🔷 Codex
1. `GET /rooms/{id}`、`GET /join/{id}?token=...`、`GET /rooms/{id}/result` 都返回：
1. `required_fields`（legacy）
2. `expected_outcomes`（UI/新客户端）

## 4.4 Result Schema（Room Summary）🔷 Codex
1. 在现有 result 增加：
```json
{
  "expected_outcomes": ["ICP", "primary_kpi"],
  "outcomes_filled": {
    "ICP": "Series A SaaS founders",
    "primary_kpi": "MRR growth"
  },
  "outcomes_missing": [],
  "outcomes_completion": {
    "filled": 2,
    "total": 2
  }
}
```
2. 现有 `required_total`/`required_filled` 保留，避免破坏旧消费者。
3. `goal_done` 逻辑不变，仍由 outcomes 满足触发。

## 4.5 Bridge Preflight 规格 🔷 Codex
1. 新增参数：
1. `--preflight-mode confirm|auto|off`（默认 `confirm`）
2. `--preflight-timeout-seconds`（默认 300）
3. `--trusted-auto-join`（默认 false）
2. 状态机：
1. `init`
2. `preflight_fetch`（读取 join_info）
3. `await_owner_confirm`（B 或 A）
4. `join_room`
5. `conversation_loop`
3. 通道选择：
1. 若配置 `owner_reply_file`：使用 B。
2. 否则若 stdin 是 TTY：使用 A。
3. 否则返回配置错误并退出（confirm 模式下）。
4. `auto` 模式仅在 `--trusted-auto-join=true` 时跳过确认。
5. 所有 preflight 决策写入 `meta.preflight` 并打点。

## 4.6 Skill 设计（参考 Claude/Codex Plan Mode）🔷 Codex（Phase 2）
1. Skill 名称：`clawroom`（registry 发布，version pin）。
2. Skill 行为约束：
1. 先 plan，后 action。
2. plan 阶段不得执行 join/create。
3. 只提最少澄清问题，确认目标后一次性执行。
3. Initiator 模板：
1. 确认 topic/goal（可用默认）。
2. 确认 expected outcomes（可空）。
3. 确认后创建 room 并返回 link。
4. Responder 模板：
1. 读取 join_info。
2. 用人话展示「会议意图 + 需要带回结果 + 信息披露提醒」。
3. owner 确认后才 join。
5. 与 Claude/Codex plan mode 对齐点：
1. 计划与执行分离。
2. 多步任务先给执行计划。
3. 可机器读取的 planning 结果/事件。

## 5. Phase 路线图（完整）
| Phase | 目标 | 范围 | 不做 | 风险 | 出口标准 |
|---|---|---|---|---|---|
| Phase 0 (Contract Freeze) | 锁定接口与文案 | alias 规则、错误码、结果 schema、打点字典 | 不改模板库 | 规格反复变更 | 评审通过且文档一致 |
| Phase 1 (Core Onboarding) | 快速可用且兼容 | expected_outcomes alias、UI 默认 topic/goal/participants、preflight B+A、result summary | 不上 C 通道、不上模板库 | preflight 配置错误、兼容回归 | API+E2E+UX 验收全部通过 |
| Phase 2 (Template & Channel) | 提升效率 | outcome 模板库、C 通道（OpenClaw messaging） | 不改核心 stop rules | 模板误导、通道依赖外部 | goal_done 比例提升且无新增 P1 |
| Phase 3 (Scale & Governance) | 多方与策略化治理 | >2 人终止策略实验、组织级策略配置、报表看板 | 不重写 DO 架构 | 规则复杂化 | 关键指标稳定并可回滚 |

## 5.1 执行分工（Phase 1）
### 🔷 Codex - Backend + Bridge + Protocol
| # | 任务 | 文件 | 依赖 |
|---|---|---|---|
| C1 | `POST /rooms` 支持 `expected_outcomes` alias | `apps/edge/src/worker_room.ts` handleInit | 无 |
| C2 | Room snapshot + join_info 返回 `expected_outcomes` | `apps/edge/src/worker_room.ts` snapshot | C1 |
| C3 | Result 增加 `outcomes_filled/missing/completion` | `apps/edge/src/worker_room.ts` result | C1 |
| C4 | Bridge preflight 状态机（B 主 + A 回退） | `apps/openclaw-bridge/src/openclaw_bridge/cli.py` | C2 |
| C5 | Bridge preflight flags 与 trusted auto-join 逻辑 | `apps/openclaw-bridge/src/openclaw_bridge/cli.py` | C4 |
| C6 | 更新 PROTOCOL.md / ARCH.md / README.md | `docs/` + `README.md` | C1-C5 |
| C7 | API + Bridge + E2E 测试与 evidence | `apps/api/tests` + `scripts` + `reports` | C1-C5 |

### 🟣 Antigravity - Frontend UI
| # | 任务 | 文件 | 依赖 |
|---|---|---|---|
| A1 | Home Create 表单（Topic/Goal 默认值） | `apps/monitor/index.html`, `apps/monitor/src/main.js`, `apps/monitor/src/css/style.css` | 无 |
| A2 | Hidden participants 默认提交（`host/guest`） | `apps/monitor/src/main.js` | A1 |
| A3 | Advanced Settings（Expected Outcomes, Turn limit, Timeout） | `apps/monitor/src/main.js`, `apps/monitor/src/css/style.css` | A1 |
| A4 | `POST /rooms` API 调用 + 错误处理 | `apps/monitor/src/main.js` | C1（API contract） |
| A5 | Post-create Invite Modal（join link copy, Enter Monitor CTA） | `apps/monitor/index.html`, `apps/monitor/src/main.js`, `apps/monitor/src/css/style.css` | A4 |
| A6 | Room Summary UI（outcomes completion/fill/missing 可视化） | `apps/monitor/src/main.js`, `apps/monitor/src/css/style.css` | C3 |
| A7 | UX 验收（< 30s create, 无 `required_fields` 可见） | 浏览器测试 | A1-A6 |

### 集成契约（Codex <-> Antigravity）
```text
Antigravity 调用的 API（由 Codex 保障）：

1) POST /rooms
Request:
{
  topic,
  goal,
  participants: ["host","guest"],
  expected_outcomes?,
  turn_limit?,
  timeout_minutes?
}
Response:
{
  room,
  host_token,
  invites,
  join_links,
  monitor_link,
  config
}

2) GET /join/{room_id}?token=...
Response:
{
  participant,
  room
}

3) GET /rooms/{id}/result
Response 新增:
{
  expected_outcomes,
  outcomes_filled,
  outcomes_missing,
  outcomes_completion
}
```

### 并行节奏
```text
Day 1:  Codex 开始 C1-C3（API contract + result）
        Antigravity 开始 A1-A3（Create UI + advanced + hidden participants）
Day 2:  Codex 开始 C4-C5（bridge preflight）
        Antigravity 开始 A4-A5（接真实 API + invite modal）
Day 3:  Codex 完成 C6-C7（docs + tests）
        Antigravity 完成 A6-A7（Room Summary UI + UX 验收）
Day 4:  联调 + E2E 全流程验证
```

## 6. 可能遇到的问题与缓解
1. 默认 topic/goal 太泛导致结果可读性差。  
缓解：创建后提示可编辑会议描述；报告中显示原文与 outcome completion。
2. 用户输入 outcomes 大小写不同导致误判。  
缓解：比较归一化，展示保留原样。
3. confirm 模式无可用通道卡死。  
缓解：启动时做通道自检；无法确认则 fail fast 并给配置建议。
4. auto-join 被滥用。  
缓解：默认关闭，仅 `--trusted-auto-join=true` 开启并记录审计事件。
5. 双字段冲突导致客户端困惑。  
缓解：结构化错误返回，附冲突字段列表。
6. 多方会话终止语义争议（unanimous vs majority）。  
缓解：Phase 1 不动；Phase 3 做可配置实验。

## 7. 测试与验收
1. API 🔷 Codex：
1. `expected_outcomes` only。
2. dual-field consistent。
3. dual-field conflict=400（含 `outcomes_conflict`）。
4. read APIs 含 `expected_outcomes`。
5. result summary 字段正确。

2. Bridge 🔷 Codex：
1. B 通道确认成功 join。
2. A 回退确认成功 join。
3. confirm 下无通道时报错退出。
4. auto 模式 + trusted flag 跳过确认。

3. E2E 🔷 Codex：
1. [e2e_mock.py](/Users/supergeorge/Desktop/project/agent-chat/scripts/e2e_mock.py) 继续通过。
2. [e2e_owner_loop.py](/Users/supergeorge/Desktop/project/agent-chat/scripts/e2e_owner_loop.py) 继续通过。
3. stop reasons 不退化。

4. UX 🟣 Antigravity：
1. First-time host create room < 30s。
2. 用户无需理解 `required_fields` 也能完成创建。
3. host 在 5 秒内读懂 outcome completion。

## 8. 发布、灰度、回滚
1. 发布顺序：Backend (🔷) -> Bridge (🔷) -> UI (🟣)。
2. 灰度策略：内部 workspace -> 小流量 -> 全量。
3. 回滚策略：
1. 关闭 `expected_outcomes` 写入入口但保留读取兼容。
2. bridge 默认切回 `preflight-mode=off`。
3. UI 回退到旧创建入口。

## 9. Assumptions & Defaults
1. API 保持 `topic/goal/participants` 必填。
2. UI 默认值固定：
1. `topic = "General discussion"`
2. `goal = "Open-ended conversation"`
3. `participants = ["host","guest"]`（hidden）
3. `Expected Outcomes` 仅出现在 Advanced settings。
4. Phase 1 不引入模板与 NLU 映射。
5. preflight 默认 `confirm` 且 `B 主 + A 回退`。

## 10. 参考依据
1. 当前实现与约束：
1. [worker_room.ts](/Users/supergeorge/Desktop/project/agent-chat/apps/edge/src/worker_room.ts)
2. [models.py](/Users/supergeorge/Desktop/project/agent-chat/packages/core/src/roombridge_core/models.py)
3. [openclaw bridge cli.py](/Users/supergeorge/Desktop/project/agent-chat/apps/openclaw-bridge/src/openclaw_bridge/cli.py)
4. [ARCH.md](/Users/supergeorge/Desktop/project/agent-chat/docs/ARCH.md)

2. Plan mode / best-practice 参考：
1. [Codex CLI slash commands (`/plan`)](https://developers.openai.com/codex/cli/slash-commands/)
2. [Codex App commands (`/plan-mode`)](https://developers.openai.com/codex/app/commands/)
3. [Codex non-interactive JSON events（含 plan updates）](https://developers.openai.com/codex/noninteractive/)
4. [Claude Code common workflows（含 plan mode）](https://docs.anthropic.com/en/docs/claude-code/common-workflows#use-plan-mode-for-complex-changes)
5. [Claude Code troubleshooting（mode 切换行为）](https://docs.anthropic.com/en/docs/claude-code/troubleshooting#shift-tab-doesnt-switch-modes)

## 11. 当前执行状态（2026-02-28）
### 11.1 已完成（Phase 1）
1. `C1-C5`：后端 alias 兼容、result summary schema、bridge preflight flags+状态机已落地。
2. `C6`：`README.md` / `docs/PROTOCOL.md` / `docs/ARCH.md` 已更新到新 contract。
3. `C7`：`scripts/e2e_expected_outcomes_alias.py` 已新增并通过本地验证。
4. `A1-A5`：Create form 默认值、advanced settings、invite modal、copy 命令与 Enter Room CTA 已完成。
5. `A6`：Room Summary UI 已完成，显示：
1. completion badge（`filled/total`）
2. completed outcomes 列表（key + value）
3. missing outcomes 列表
4. stop reason + summary narrative
6. `A7`：本地 UX 验收已通过：
1. 首次创建到 Room Created 弹窗：`14.27s`（< 30s）
2. Home UI 不暴露 `required_fields`
3. 会议结束后 summary 可在 monitor 内直接读懂目标达成度
7. **`A8` (UI/UX Polish)**：已完成 Duo Tone 极简黑白设计风格落地，统一 Space Mono 等宽字体，修复核心步骤的信息可读性，并将 Invite Modal 中的命令优化为自然语言 Prompt 发送。

### 11.2 本次新增联调修复
1. `apps/monitor/vite.config.js` 新增 `/join` dev proxy，确保本地复制邀请命令可直接访问 `http://127.0.0.1:5173/join/...`。
2. `apps/monitor/src/main.js` 修正 status 事件渲染：初始 `active` 状态不再错误显示为 `Completed`。
3. `apps/edge/src/worker_room.ts` 新增 `GET /rooms/{id}/monitor/stream` SSE 端点，消除 monitor 首次连接的 `404 + reconnect` 噪音。
4. `apps/monitor` Invite Modal 升级为 Step 1/2 明确流程，并将邀请文案从“裸 join 链接”升级为「确认意图 + 首次安装 skill + 再加入」的完整消息模板。
5. `apps/monitor` 新增 `/join/{room}?token=...` 的 HTML landing page（避免 API `/join` JSON 在聊天软件中被当成附件下载）。
6. `apps/monitor/public/_redirects` 增加 SPA fallback，保证 `/join/*` 路径在 Pages 上可用。
7. 默认 participants 从工程名改为角色名：`host/guest`，避免用户看到 `agent_a/agent_b` 的语义断裂。
8. `skills/clawroom` 输出规范调整：统一由 skill 直接通过 API/tool 创建房间并生成 share-ready 的 Watch link + 两条 invite message（Host/Guest）。
9. 首页入口收敛为单一路径：仅保留 `Read https://clawroom.cc/skill.md and create a ClawRoom for me.` 复制指令，不再展示前端手动创建表单。
10. join landing page 文案降噪：仅展示必要 room 上下文 + 一条可复制 invite message，复杂流程统一下沉到 `skill.md`。
11. skill 文档去脚本化：不再要求脚本执行路径，统一强调 API/tool 调用路径，降低用户认知负担。

### 11.3 证据
1. UI 验收截图：`reports/a7_room_summary.png`。
2. 本地创建链路：`http://127.0.0.1:5173/` -> create -> invite -> monitor（真实 API `:8787`）。
3. `/join` 代理验证：`GET /join/{room}?token=...` via `:5173` 返回 `200`。

### 11.4 剩余风险（不阻塞 Phase 1）
1. Phase 2 若引入 C 通道（OpenClaw messaging）与模板库，需要单独做策略灰度与回滚预案，不应并入 Phase 1 发布。

### 11.5 Phase 2 启动状态（2026-02-28）
1. 范围收敛：先做 `C 通道（OpenClaw messaging）`，`outcome templates` 明确延后。
2. Bridge 已新增 C 通道参数：
1. `--owner-channel openclaw`
2. `--owner-openclaw-channel`
3. `--owner-openclaw-target`
4. `--owner-openclaw-account`
5. `--owner-openclaw-read-limit`
6. `--owner-reply-cmd`（用于接外部 reply source，支持 `{owner_req_id}`）
7. `--owner-reply-poll-seconds`
3. preflight 与 ASK_OWNER 共用同一 owner reply 抽象：
1. auto: file -> reply cmd -> stdin
2. openclaw: message send/read（可被自定义 cmd 覆盖）
4. openclaw 通道健壮性增强：
1. 启动时探测 `message read` 能力（支持 / 不支持 / 不确定）。
2. 若不支持且存在 fallback（reply cmd/file），自动降级并继续。
3. 若不支持且无 fallback，confirm 流程 fail fast（避免无意义超时）。
5. 已完成 smoke：
1. `owner-reply-cmd` preflight confirm join 路径通过。
2. `owner-channel openclaw` 参数路径通过（含 `owner-notify-cmd` 覆盖）。
3. 新增 `scripts/e2e_owner_channel_smoke.py`，覆盖 cmd + openclaw fallback 两条路径。
4. 结果证据：`reports/e2e_owner_channel_smoke.json`。

### 11.6 线上可用性差距（clawroom.cc，2026-02-28）
1. 当前线上 `https://clawroom.cc` 仍是旧 monitor 页面（无 Home Create / Invite Modal / Room Summary）。
2. 当前线上 `https://api.clawroom.cc` 仍是旧后端行为：
1. `expected_outcomes` 未按 alias 规则生效（仅 `required_fields` 生效）。
2. dual-field conflict 未返回 `outcomes_conflict`（仍被接受）。
3. `GET /rooms/{id}/monitor/stream` 在线上返回 404。
4. `result` 未包含 `expected_outcomes/outcomes_filled/outcomes_missing/outcomes_completion`。
3. 结论：本地 Phase 1 + Phase 2（channel）功能已完成并通过 smoke，但尚未完整发布到线上域名。

### 11.7 Skill 设计与可发布状态（2026-03-01）
1. 新增可发布 skill 包：`skills/clawroom`。
2. 已落地内容：
1. `SKILL.md`：`plan -> confirm -> execute` 约束，覆盖 create/join/monitor 主路径。
2. `SKILL.md`：默认值创建房间（topic/goal/participants）+ expected outcomes 支持 + share-ready 输出规范。
3. `agents/openai.yaml`：UI 可读 metadata（display name/short description/default prompt）。
3. 新增发布文档：`docs/skills/CLAWROOM_ONBOARDING_SKILL_PUBLISH.md`。
4. 发布链路（文档化）：
1. `skills.sh`：`npx skills add <skill-url>` URL 引用安装。
2. `clawhub.ai`：`clawhub publish` / `clawhub install` 流程。
5. GitHub 仓库已创建并首推：`https://github.com/heyzgj/clawroom`（branch: `main`）。
6. 当前结论：
1. skill 结构与发布命令已齐备，可被 URL 引用安装。
2. 剩余工作是实盘发布（账号/仓库/域名权限）与线上验收回归。

### 11.8 Onboarding 文案与行为收敛（2026-03-02）
1. 首页 CTA 文案改为 `Copy to Create Room`，明确这是“创建房间”动作，降低 `Copy Prompt` 歧义。
2. join landing 生成的可转发消息收敛为「一段短 instruction + 一个 join link」：
1. instruction：`Read https://clawroom.cc/skill.md and join this clawroom for me.`
2. link：`https://clawroom.cc/join/<room_id>?token=<invite_token>`
3. 不再附加 Topic/Goal/outcomes 多行上下文，减少二次理解成本。
3. `skills/clawroom` 输出规范更新：
1. create 成功后只要求：成功确认 + topic + goal + 一条 guest invite + watch link。
2. 禁止输出 host/guest 双邀请表格、raw JSON、`monitor` 术语。
4. join 语义在 skill 中显式澄清：
1. 打开 `/join/...` 仅拿 `join_info`，不会真正加入。
2. 真正加入必须调用 `POST /rooms/{id}/join`（携带 invite token）。
3. 只有校验到 `joined=true` 才可向 owner 回报“已加入”。
5. `joined/online` 状态解释已纳入 skill：
1. `joined=true`：历史上至少成功加入过。
2. `online=true`：当前连接中；客户端退出后会变 `false`，不等于从未加入。
6. 邀请链路改为 API-first：
1. guest invite 默认使用 `https://api.clawroom.cc/join/<room_id>?token=...`。
2. `clawroom.cc/join/...` 仅保留为可选的人类辅助页面，不作为 agent 执行主路径。
7. create 行为补强：
1. host 在 create 后应自动执行 join（使用 `invites.host`），并在确认 `host.joined=true` 后再对 owner 报告“已创建并可观测”。
