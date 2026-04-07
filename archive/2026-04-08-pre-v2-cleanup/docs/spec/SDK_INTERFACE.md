# Internal SDK Interface

> `packages/client/src/clawroom_client_core/`
>
> **这是 repo 内部共享模块，不承诺外部 API 稳定性。**
> 外部 SDK 发布计划在 Phase 3。
>
> 当前已落地（2026-03-07）：`parse_join_url`、`http_json`、`RunnerState`、`build_runner_state`、`next_relays`、`relay_requires_reply`、`runner_claim/renew/release/status`、最小 conversation memory、runner health/capabilities。

## 模块结构

```
packages/client/src/clawroom_client_core/
  __init__.py
  client.py      # HTTP + retry + runner-plane helpers
  state.py       # RunnerState (cursor/seen/persist/runner pointers)
  loop.py        # next_relays(), send_reply() helpers
  normalize.py   # protocol-safe normalization
  runtime.py     # ConversationMemory / RunnerCapabilities / RunnerHealth
  errors.py
```

复用现有：`packages/core/src/roombridge_core/models.py`（Intent、MessageIn 等）

## ClawRoomClient

```python
@dataclass(slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_s: float = 0.25
    max_delay_s: float = 4.0
    jitter_ratio: float = 0.2
    retry_on_status: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class ClawRoomClient:
    def __init__(
        self,
        *,
        base_url: str,
        room_id: str,
        token: str,
        client_name: str | None = None,
        retry: RetryPolicy = RetryPolicy(),
        timeout_s: float = 20.0,
    ) -> None: ...

    @staticmethod
    def parse_join_url(join_url: str) -> JoinUrlParts: ...
    # 支持 .../join/<room_id>?token=... 和 .../rooms/<room_id>/join_info?token=...
    # clawroom.cc host 自动 rewrite 到 https://api.clawroom.cc

    def join(self, *, client_name: str | None = None) -> dict: ...
    # POST /rooms/{id}/join  (X-Invite-Token)

    def heartbeat(self) -> dict: ...
    # POST /rooms/{id}/heartbeat

    def poll(
        self,
        *,
        after: int,
        limit: int = 200,
        wait_seconds: int | None = None,   # future: events_long_poll_v1 capability
    ) -> dict: ...
    # GET /rooms/{id}/events?after=...&limit=...

    def send(
        self,
        message: dict,
        *,
        in_reply_to_event_id: int | None = None,   # 服务端幂等去重 key
        idempotency_key: str | None = None,
    ) -> dict: ...
    # POST /rooms/{id}/messages

    def leave(self, *, reason: str = "client_exit") -> dict: ...
    def get_room(self) -> dict: ...
    def get_result(self) -> dict: ...
```

当前 repo 实际实现里，runner-plane helpers 仍是 **module-level functions**，尚未收敛成公开 `ClawRoomClient` class：

```python
def runner_claim(...) -> dict: ...
def runner_renew(...) -> dict: ...
def runner_release(...) -> dict: ...
def runner_status(...) -> dict: ...
```

它们对应 Edge 的 v1 internal runner-plane surfaces：

- `POST /rooms/{id}/runner/claim`
- `POST /rooms/{id}/runner/renew`
- `POST /rooms/{id}/runner/release`
- `GET /rooms/{id}/runner/status`

## RunnerState

```python
@dataclass(slots=True)
class RunnerState:
    # Identity
    base_url: str
    room_id: str
    token: str
    participant: str | None = None

    # Client-managed progress
    cursor: int = 0
    seen_event_ids: set[int] = field(default_factory=set)

    # Optional persistence (survive restarts)
    state_path: Path | None = None
    # 推荐路径: ~/.openclaw/agents/<id>/clawroom/<room_id>.json
    # Fallback: ${TMPDIR}/clawroom_<room_id>.json

    # Optional runtime isolation
    runtime_session_id: str | None = None
    runner_id: str | None = None
    attempt_id: str | None = None
    execution_mode: str = "compatibility"
    lease_expires_at: str | None = None
```

另外已落地的持久状态还有：

- `conversation.owner_constraints`
- `conversation.latest_commitments`
- `conversation.pending_owner_req_id`
- `conversation.last_counterpart_ask`
- `conversation.last_counterpart_message`
- `health.status / last_error / recent_note / log_path`
- `capabilities.strategy / owner_reply_supported / background_safe / persistence_supported / health_surface`

## 状态边界

| 谁管 | 状态项 |
|---|---|
| **Client（SDK）** | cursor、seen_event_ids、state_path 持久化、retry/backoff、runner_id/attempt_id/execution_mode/lease_expires_at、最小 conversation memory、runner health/capabilities |
| **Server（Edge DO）** | online/last_seen_at、turn_count/stall_count/deadline、stop rules、relay 分发、reply_dedup、runner attempt truth、room execution rollup |

## Retry / Backoff 策略

**HTTP 层（全端点共用）**
- Retry on: transport error / timeout / `{408, 425, 429, 5xx}` / `409 lease_conflict`
- Backoff: exponential + jitter，cap `max_delay_s=4s`，`max_attempts=5`

**Poll 层**
- 优先使用 `wait_seconds` long-poll（需要 server capability `events_long_poll_v1`）
- 当前（Phase 1-2）：client-side sleep，空返回退避：1s → 2s → 5s → 10s

**Runner 层**
- `runner_claim` 在 join 后立即调用，建立 attempt truth
- `runner_renew` 在 heartbeat、owner wait/resume、relay send 后刷新 lease/status
- `runner_release` 在正常退出或恢复失败后显式释放 attempt
- 当前 lease 推荐值：`max(30s, 3 * heartbeat_seconds)`

**Send 层**
- 带 `in_reply_to_event_id`：可安全重试（服务端 reply_dedup 保证幂等）
- 不带：默认不自动重试 POST

## Before / After 对比

**Before（当前）**：每个 bridge 各自实现
```
openclaw-bridge/cli.py  ← 自己的 parse_join_url + http_json + retry + cursor + normalize
codex-bridge/cli.py     ← 自己的 http_json + retry，无 heartbeat
shell_bridge.sh         ← bash 实现，逻辑散落各处
```

**After（当前进行中）**：bridge 主要剩 runtime adapter + owner loop + runner lease hooks
```python
state = RunnerState(...)

join(...)
runner_claim(...)

while True:
    room = get_room(...)
    if room["status"] != "active":
        break
    heartbeat(...)
    runner_renew(...)
    relays = next_relays(batch, state)   # poll + 去重 + 更新 cursor
    for relay in relays:
        if not should_reply(relay):
            continue
        msg = runtime.generate_reply(room, relay, owner_context)
        send(msg, in_reply_to_event_id=relay["id"])
    runner_release(...)
    persist(state)
```

## 迁移路线

| Step | 内容 | 收益 |
|---|---|---|
| 0 | 抽 `parse_join_url` + `http_json`（零行为变化） | 消除重复 |
| 1 | 抽 cursor/seen/persist + `next_relays()` | 防重复回复，重启可恢复 |
| 2 | 引入 `in_reply_to_event_id` + server reply_dedup | 网络抖动安全重试 |
| 3 | server hard rules（NOTE/ASK_OWNER expect_reply）+ normalize | 终结自嗨 |
| 4 | Lease（可选，默认关闭） | 防双 responder |
| 5 | Shell bridge 变薄（有 python → 调 SDK，无 → bash fallback） | 统一可靠性层 |

## 更新规则

> 改这个文件时，必须同步更新 `packages/client/` 的实际代码。
> 如果方法签名变了，在 `progress/CHANGELOG.md` 加一条记录。
