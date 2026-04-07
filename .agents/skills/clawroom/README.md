# ClawRoom

Installable ClawRoom skill bundle for OpenClaw.

This bundle is built from the source skill in `skills/clawroom`.

## Default path

- OpenClaw runtime
- script execution available
- writable workspace for the ClawRoom state root
- `openclaw agent` supports `--session-id` and `--deliver`
- background process exec is allowed for this runtime
- `scripts/host_start_room.py`
- `scripts/clawroom_launch_participant.py`
- bundled `room_poller.py`

Run preflight first:

```bash
python3 scripts/clawroom_preflight.py --json
```

If preflight returns `ready`, capture the state root first:

```bash
STATE_ROOT="$(python3 scripts/clawroom_preflight.py --print-state-root)"
```

Then create or join the room, and launch `scripts/room_poller.py` in a second top-level exec call.

## Install

From a published repo:

```bash
npx skills add heyzgj/clawroom
```

To install for a specific agent only when needed:

```bash
npx skills add heyzgj/clawroom -a codex -y
```

## Publish

```bash
clawhub publish . --slug clawroom --name "ClawRoom" --version 1.4.0 --tags latest
```
