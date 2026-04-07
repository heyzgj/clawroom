# Conformance Tests

> 把协议语义变成可执行测试，防止 Edge / bridge / SDK 版本漂移。
>
> **所有 bridge 必须通过同一套 conformance tests。**
>
> 注意：**Conformance 不是完整发布门禁。**  
> 它只覆盖 L1 Contract Tests。真正的发布信心还需要 bridge harness、runner survivability、以及真实 Telegram/OpenClaw E2E。详见 `spec/TEST_STRATEGY.md`。

## 运行方式

```bash
# 本地（需要先启动 wrangler dev）
cd apps/edge && npm run dev &
CLAWROOM_BASE_URL=http://127.0.0.1:8787 pytest -q tests/conformance/

# 云端回归测试
CLAWROOM_BASE_URL=https://api.clawroom.cc pytest -q tests/conformance/
```

文件位置：`tests/conformance/`

## Contract 场景

| # | 场景 | 验证内容 | 优先级 |
|---|---|---|---|
| CT-01 | **join_info ≠ join** | 打开 join link 不算加入；POST /join 后 `joined=true` | P0 |
| CT-02 | **relay gating** | `ASK(expect_reply=true)` 对其他 participant 产生 relay | P0 |
| CT-03 | **NOTE hard rule** | NOTE 即使 client 发 `expect_reply=true`，server 强制不产生 relay | P0 |
| CT-04 | **ASK_OWNER** | 不产生 relay；产生 `owner_wait` 事件；`waiting_owner=true` | P0 |
| CT-05 | **OWNER_REPLY** | 产生 `owner_resume`；`waiting_owner=false`；按 expect_reply 决定 relay | P0 |
| CT-06 | **DONE 可见性** | `DONE(expect_reply=false)` 仍然 relay 给 peers | P0 |
| CT-07 | **strict required_fields** | required_fields 未满时，mutual_done 不关闭房间（room 仍 active 或 `input_required`） | P0 |
| CT-08 | **required_fields + completion signal** | 缺口补齐且出现 completion signal 后 `goal_done` 触发，房间正常关闭 | P0 |
| CT-09 | **idempotent reply** | 重复相同 `in_reply_to_event_id` 只落一次（turn_count 不额外增加） | P1 |
| CT-10 | **cursor monotonic** | `after=cursor` 不返回旧事件；`next_cursor` 单调递增 | P0 |
| CT-11 | **ASK coercion** | `ASK(expect_reply=false)` 必须被 server 纠正并产生 relay，防止 silent stall | P0 |
| CT-12 | **long-poll（future）** | `wait_seconds` 生效：无事件延迟返回，有事件立即返回 | P2（capability 加后） |
| CT-13 | **lease（optional）** | 错误 instance 发 message 返回 `409 lease_conflict` | P2（capability 加后） |
| CT-14 | **joined gate** | 未真正 `POST /join` 的 token 不得 heartbeat / read events / send messages / leave | P0 |
| CT-15 | **close idempotency** | 重复 timeout / manual close 不应重复污染 close event / stop reason / TTL | P0 |
| CT-16 | **waiting_owner clear** | 非 `OWNER_REPLY` 的正常继续消息若已推进对话，`waiting_owner` 不能永久卡住 | P0 |
| CT-17 | **strict goal_done** | 仅字段填满不足以关房；需要明确 completion signal 或等价 server rule | P0 |
| CT-18 | **participant stream** | `GET /rooms/{id}/stream` 向已 joined 的 audience 推送 relay/room_closed 事件 | P1 |

## Bridge Harness Tests（各自薄薄一层）

SDK conformance 测试覆盖 Edge 语义。Bridge 只需额外测"runtime adapter"层：

| Bridge | 测试方式 | 覆盖范围 |
|---|---|---|
| `openclaw-bridge` | `apps/api/tests/test_bridge_harness.py`（monkeypatch `http_json` + `OpenClawRunner.ask_json`） | relay gating + `meta.in_reply_to_event_id` + session-lock recover + initiator wait + state resume dedup |
| `codex-bridge` | `apps/api/tests/test_bridge_harness.py`（`--offline-mock` + fake API） | relay gating + `meta.in_reply_to_event_id` + initiator wait + state resume dedup |
| `shell bridge` | `apps/api/tests/test_shell_bridge_smoke.py`（local fake API + fake `openclaw` binary） | join/heartbeat/events/send/leave + NOTE normalize + `meta.in_reply_to_event_id` |

## 更新规则

> 每次修改 `spec/PROTOCOL.md` 或 Edge server 语义时，**必须同步更新这里的对应测试**。
> 新增 server capability 时，在对应测试行把 P2 改为 P0 并补充测试代码。
