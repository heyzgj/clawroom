# ClawRoom v3.1 E2E And Debug Runbook

Last updated: 2026-04-20

This is the handoff for future agents joining ClawRoom v3.1 work. It covers how to run real Telegram E2E, how to inspect local clawd vs Railway Link, how to update installed skills, and which traps already cost us time.

## Current Mental Model

ClawRoom v3.1 has three moving parts:

- Hosted or BYO relay: Cloudflare Worker + SQLite Durable Object.
- Bridge runtime: `bridge.mjs` launched by an OpenClaw skill, then long-polling the relay.
- Owner UX: Telegram messages from local clawd and Railway Link.

The product path is not "operator runs a command". The product path is:

1. George sends ordinary Telegram language to local clawd.
2. Local clawd uses the installed `clawroom-v3` skill to create a room and launch the host bridge.
3. George forwards the public invite URL to Tom's Railway Link bot.
4. Railway Link uses its installed `clawroom-v3` skill to join and launch the guest bridge inside the Railway container.
5. Both bridges negotiate, mutually close, notify their owners, and exit.

Never count `railway run node ...` as cross-machine proof. It runs locally with Railway env injected. For the remote Link container, use `railway ssh`, `railway logs`, Telegram, and `/data/...` runtime files.

## Hard Gate Before Every E2E

Start with a clean process picture. Stale bridges burn Durable Object requests and can write misleading events.

Local:

```sh
pgrep -f '^node .*bridge\.mjs --thread' || true
```

Kill only live stale bridges you understand:

```sh
kill <pid>
```

Railway Link:

```sh
railway status
railway ssh sh -lc "ps -eo pid,args | grep 'bridge.mjs --thread' | grep -v grep | awk '{print \$1}'"
```

The Railway command should print only live bridge PIDs. If a broader process
inspection shows `[node] <defunct>` rows, those are zombies, not live bridge
pollers. Record the process-reaping issue if it is growing, but do not treat a
zombie PID as an active room bridge.

Do not paste full local or Railway process command lines into notes, artifacts,
or chat. Bridge argv can contain room tokens and owner chat ids. Use PID-only
checks, then inspect runtime-state and bridge log files by room id when you need
context.

Do not run raw `railway ssh env` in notes or artifacts; it can print Telegram bot tokens, gateway tokens, passwords, and API keys. If you need one value, query only that key, for example `railway ssh printenv OPENCLAW_STATE_DIR`.

## Skill Update Gate

If any runtime file changes (`SKILL.md`, `bridge.mjs`, `launcher.mjs`, or
`clawroomctl.mjs`), clean and reinstall the visible skill bundle before the next
E2E. Do this for both runtimes; otherwise the test may silently run stale code.

Local clawd visible path:

```text
~/clawd/skills/clawroom-v3
```

Railway Link visible path:

```text
/data/workspace/skills/clawroom-v3
```

After syncing, verify readiness:

```sh
openclaw skills info clawroom-v3
railway ssh "sh -lc 'OPENCLAW_STATE_DIR=/data/.openclaw openclaw skills info clawroom-v3'"
```

Also compare SHA-256 for the four runtime files if this is a release gate. Do
not start Telegram E2E until the installed files match the repo files.

For a release-candidate clean-reinstall gate, archive old ClawRoom state and
remove stale visible skills before reinstalling:

- local: archive `~/.clawroom-v3` room/runtime artifacts, remove
  `~/clawd/skills/clawroom-v3`, then copy the current repo files back;
- Railway: archive `/data/.openclaw/clawroom-v3` room/runtime artifacts, remove
  `/data/workspace/skills/clawroom-v3`, then install/copy the current repo
  files back;
- verify `node --check` for `bridge.mjs`, `launcher.mjs`, and
  `clawroomctl.mjs` before install;
- verify the installed SHA-256 hashes match the repo hashes on both sides;
- only then start the Telegram product path.

## Product UX Boundary

ClawRoom-owned Telegram output must hide technical details by default:

- no launcher JSON;
- no PID;
- no runtime/log/state file paths;
- no `bridge_sha256`;
- no room bearer token or create key;
- no debug labels such as `Room`, `Role`, or owner-reply endpoint unless an
  explicit debug flag is enabled.

OpenClaw's own persona/greeting chatter is not a ClawRoom blocker unless it
prevents the skill from launching or leaks ClawRoom internals. Record it if
useful, but do not fail the ClawRoom gate for it.

Do not hard-code language policy in ClawRoom. OpenClaw should follow the
owner's language naturally; ClawRoom only gates protocol correctness and
technical leakage.

## Local Clawd Preflight

Run these from this repo unless noted.

```sh
openclaw --version
which openclaw
openclaw gateway status
openclaw skills info clawroom-v3
openclaw agent --agent clawroom-relay --message 'REPLY: PONG only'
```

Expected:

- Gateway RPC probe is OK.
- Gateway is loopback-bound at `127.0.0.1:18789`.
- `clawroom-v3` is `Ready`.
- `clawroom-relay` responds through the same OpenClaw path the bridge will use.

Useful local files:

```text
~/.clawroom-v3/<thread>-host.machine.json
~/.clawroom-v3/<thread>-host.launch.json
~/.clawroom-v3/<thread>-host.runtime-state.json
~/.clawroom-v3/<thread>-host.bridge.log
~/.clawroom-v3/<thread>-host.state.json
~/.openclaw/logs/gateway.log
~/.openclaw/logs/gateway.err.log
/tmp/openclaw/openclaw-YYYY-MM-DD.log
~/Library/LaunchAgents/ai.openclaw.gateway.plist
```

Useful local checks:

```sh
launchctl list | rg -i 'openclaw|clawd'
plutil -p ~/Library/LaunchAgents/ai.openclaw.gateway.plist
tail -n 100 ~/.clawroom-v3/<thread>-host.bridge.log
cat ~/.clawroom-v3/<thread>-host.runtime-state.json
tail -n 100 /tmp/openclaw/openclaw-$(date +%F).log
```

If `openclaw gateway status` says the LaunchAgent points at an old Node or old npm install, repair that before Telegram E2E. A healthy CLI and a stale LaunchAgent can coexist and create false confidence.

## Railway Link Preflight

Railway Link is the remote Linux container. Its persistent OpenClaw state is normally `/data/.openclaw`; its visible workspace skills live under `/data/workspace/skills`.

```sh
railway status
railway ssh printenv OPENCLAW_STATE_DIR
railway ssh openclaw gateway status
railway ssh openclaw skills info clawroom-v3
railway ssh openclaw agent --agent clawroom-relay -m REPLY:PONG
```

Useful remote files:

```text
/data/workspace/skills/clawroom-v3/SKILL.md
/data/workspace/skills/clawroom-v3/clawroomctl.mjs
/data/workspace/skills/clawroom-v3/launcher.mjs
/data/workspace/skills/clawroom-v3/bridge.mjs
/data/.openclaw/clawroom-v3/<thread>-guest.machine.json
/data/.openclaw/clawroom-v3/<thread>-guest.launch.json
/data/.openclaw/clawroom-v3/<thread>-guest.runtime-state.json
/data/.openclaw/clawroom-v3/<thread>-guest.bridge.log
/data/.openclaw/clawroom-v3/<thread>-guest.state.json
/data/.openclaw/workspaces/clawroom-relay
```

Useful remote checks:

```sh
railway ssh ls -la /data/workspace/skills
railway ssh ls -la /data/.openclaw/clawroom-v3
railway ssh tail -n 100 /data/.openclaw/clawroom-v3/<thread>-guest.bridge.log
railway ssh cat /data/.openclaw/clawroom-v3/<thread>-guest.runtime-state.json
railway logs --lines 500 --filter <thread>
railway logs --lines 500 --filter clawroom
railway logs --lines 500 --filter canvas
```

For Telegram inbound behavior, source tests are not enough. Verify the running
OpenClaw package that Railway actually loads. On the current Link deployment,
the entrypoint has been observed at `/openclaw/dist/entry.js`, with the package
bundle under `/usr/local/lib/node_modules/openclaw/dist`.

Do not treat a manual bundle hotpatch as shippable. It is acceptable for
debugging a root cause for a deployment-specific adapter, but ClawRoom public
readiness must not depend on patching OpenClaw or Clawdbot source. The portable
ASK_OWNER path is the ClawRoom-owned owner decision URL.

Railway SSH has no SCP/SFTP. If you must manually sync a file for a one-off debug session, prefer a proper install/deploy path. If forced to stream file content, handle the final base64 chunk even when it has no trailing newline:

```sh
while IFS= read -r chunk || [ -n "$chunk" ]; do
  # append chunk
done
```

## Skill Update Workflow

Update source files in this repo first:

```sh
node --check bridge.mjs
node --check launcher.mjs
node --check clawroomctl.mjs
shasum -a 256 SKILL.md clawroomctl.mjs launcher.mjs bridge.mjs
```

Then sync visible local clawd skill:

```sh
mkdir -p ~/clawd/skills/clawroom-v3
cp SKILL.md clawroomctl.mjs launcher.mjs bridge.mjs ~/clawd/skills/clawroom-v3/
openclaw skills info clawroom-v3
```

Then sync or install into Railway Link's visible OpenClaw workspace:

```sh
railway ssh mkdir -p /data/workspace/skills/clawroom-v3
# use the current approved install/sync path, then verify:
railway ssh sha256sum \
  /data/workspace/skills/clawroom-v3/SKILL.md \
  /data/workspace/skills/clawroom-v3/clawroomctl.mjs \
  /data/workspace/skills/clawroom-v3/launcher.mjs \
  /data/workspace/skills/clawroom-v3/bridge.mjs
railway ssh openclaw skills info clawroom-v3
```

Do not count legacy copies under `/data/workspace/.codebuddy/skills` or `/data/workspace/.continue/skills` as OpenClaw-visible. Those paths caused the `t_423bc8e2-d37` product-path failure.

Owner-facing launch output must stay non-technical. Use `launcher.mjs --owner-facing` where the agent might paste stdout back into Telegram. It must not show JSON, PID, paths, hashes, tokens, or logs.

## Product-Path Telegram E2E

Use this for average-user readiness.

Send a natural-language request to local clawd. Example:

```text
帮我创建一个 ClawRoom，让我的 agent 和对方 agent 自己协调一个 30 分钟会议时间。我的限制：2026-04-23（周四）下午 3:00-3:30 上海时间可以；如果对方不行，请找一个同一天 2:00-5:00 之间的替代时间。最终请用中文报告确认时间。创建后只给我可转发给对方 agent 的邀请链接和一句简短说明，不要显示命令、JSON、PID、文件路径、hash、token 或日志。
```

Forward the public invite URL to Railway Link with ordinary user language:

```text
这是 ClawRoom 邀请，请让你的 agent 加入并代表 Tom 协调会议时间：<public invite URL>

Tom 这边：2026-04-23（周四）下午 3:00-3:30 上海时间可以，4 点以后不行。请加入房间让 agent 自己协商，完成后用中文告诉我确认结果。不要显示命令、JSON、PID、文件路径、hash、token 或日志。
```

Before sending the invite, verify the Telegram target by sight. Telegram
Desktop `tg://resolve` has opened a wrong/fresh bot view during testing. Use
Telegram search if needed, select the existing `Link_🦀` chat, and confirm the
visible title before pasting the public invite.

Do not rely on keyboard search or `tg://resolve` alone. A failed H4 attempt sent
the guest invite back into the `clawd` chat, causing local clawd to launch both
host and guest bridges. The relay transcript then looked healthy, but Railway
had no guest runtime file. Always keep the title bar visible in screenshots and
verify the target chat name before paste/send.

Capture screenshots at three moments:

1. Host after invite creation.
2. Guest after receiving the public invite.
3. Both sides after final owner summary.

Screenshot review is part of the pass criteria. Look for:

- no raw launcher JSON;
- no PID;
- no runtime/log/state file paths;
- no `bridge_sha256`;
- no room bearer token or create key;
- invite preview is a human `ClawRoom Invite`, not a `.json` download card;
- no ClawRoom-owned technical leakage while the skill is launching.

OpenClaw persona chatter is not a ClawRoom failure by itself.

Monitor the relay without printing tokens:

```sh
node - <<'NODE'
const fs = require('fs');
const room = process.env.ROOM_ID;
const machine = JSON.parse(fs.readFileSync(`${process.env.HOME}/.clawroom-v3/${room}-host.machine.json`, 'utf8'));
const relay = 'https://clawroom-v3-relay.heyzgj.workers.dev';
const token = machine.thread.host_token;
(async () => {
  const snapshot = await fetch(`${relay}/threads/${room}/join?token=${encodeURIComponent(token)}`).then(r => r.json());
  const rows = await fetch(`${relay}/threads/${room}/msgs?token=${encodeURIComponent(token)}&after=-1`).then(r => r.json());
  console.log(JSON.stringify({
    room,
    closed: snapshot.closed,
    close_state: snapshot.close_state,
    runtime_heartbeats: snapshot.runtime_heartbeats,
    events: rows.map(r => ({ id: r.id, from: r.from, kind: r.kind, ts: r.ts, text: String(r.text || '').slice(0, 140) }))
  }, null, 2));
})();
NODE
```

Validate the redacted artifact:

```sh
node scripts/validate_e2e_artifact.mjs --artifact docs/progress/v3_1_<thread>.<scenario>.redacted.json
```

A product-path smoke pass must have:

- embedded redacted transcript;
- `closed: true`;
- `close_state.host_closed: true`;
- `close_state.guest_closed: true`;
- host and guest runtime heartbeats `status: stopped`;
- local host PID and Railway guest PID in runtime evidence;
- validator `ok: true`;
- screenshot-backed UX review.

## ASK_OWNER Owner Decision URL Gate

This is the portable product gate. It must pass before public use because it
does not depend on OpenClaw source patches or Telegram inbound interception.

Use a product-path room, not a raw technical harness prompt:

1. Start from a natural-language local clawd room creation.
2. Forward the public invite URL to Railway Link with normal owner language.
3. Wait until Telegram displays an ASK_OWNER message.
4. Tap the Telegram "Open Decision Page" button.
5. Approve, reject, or enter a counter-instruction on the ClawRoom page.
6. The page should confirm that the decision was recorded.
7. The relay transcript must contain `owner_reply.source: owner_url`.

Screenshot evidence should show the ASK_OWNER prompt, the decision page or
confirmation, and the final owner summary. Crop out old chat history if it
contains tokens or command blocks.

## Optional Telegram Inbound Adapter Gate

ForceReply/non-reply Telegram interception is a convenience adapter for a
specific OpenClaw deployment. It is useful for our own runtime, but it is not a
portable ClawRoom requirement because other OpenClaw installs will not
automatically receive our source changes.

Only run this gate when explicitly validating that adapter:

- verify the running OpenClaw package, not only a source checkout;
- clear stale ASK_OWNER binding files first;
- prove unrelated messages and ClawRoom launch/invite prompts are not consumed
  as owner decisions;
- mark artifacts as adapter evidence, not public ClawRoom core evidence.

## Direct Harness E2E

Use `scripts/telegram_e2e.mjs` for hard protocol regression, not for average-user UX claims. It may intentionally send a technical command block as Telegram user input.

Before allowing the harness to paste or send, run the target guard:

```sh
node scripts/telegram_e2e.mjs \
  --target-check-only \
  --host-title 'clawd|singularitygz_bot' \
  --guest-title 'Link|Link_|link_clawd_bot' \
  --screenshot-dir docs/progress/screenshots/harness-target-check
```

The guard opens each Telegram target, captures a screenshot, crops the active
chat title area, OCRs the crop, and fails closed if the expected title is not
present. This is intentionally stricter than a normal API validator: if it
cannot prove the chat target, it must not paste anything. It currently expects
macOS `screencapture`, `sips`, AppleScript UI access, and `tesseract`.

Examples:

```sh
node scripts/telegram_e2e.mjs --send --monitor --min-messages 2
node scripts/telegram_e2e.mjs --send --monitor --min-messages 8
node scripts/validate_e2e_artifact.mjs --artifact docs/progress/v3_1_<thread>.<scenario>.redacted.json --require-ask-owner --require-owner-reply-source owner_url
```

The send path uses the same target guard by default. `--no-confirm-targets`
exists only for local debugging when a human is watching the screen; do not use
it for release evidence.

Count these as transport/protocol evidence:

- multi-turn term-sheet negotiation;
- ASK_OWNER/owner_reply;
- retry/idempotency;
- marker injection and parser safety;
- bridge clean shutdown.

Do not count direct harness screenshots as public UX evidence unless the user input itself is natural language.

## Artifact Discipline

Every E2E, pass or fail, should leave a committed redacted artifact under `docs/progress/`.

Required fields:

- `phase`;
- `scenario`;
- `relay`;
- redacted `thread`;
- `owner_contexts`;
- `transcript`;
- `finalSnapshot`;
- `runtime_evidence`;
- `screenshots`;
- `validator`;
- `_redaction_notice`;
- `coverage_note`.

If `thread.host_token` is redacted, the artifact must embed the transcript. Otherwise `validate_e2e_artifact.mjs` cannot revalidate it without live credentials.

Never commit:

- Telegram bot tokens;
- full `chat_id`;
- relay create key;
- unredacted `host_token` or `guest_token`;
- token-bearing private invite URL.

## Debug Decision Tree

If no room was created:

- Check local `openclaw skills info clawroom-v3`.
- Check local Telegram/OpenClaw logs.
- Check hosted relay create-key and quota state.
- Confirm the host machine wrote `~/.clawroom-v3/<thread>-host.machine.json`.

If guest did not launch:

- Check Railway `openclaw skills info clawroom-v3`.
- Confirm `/data/workspace/skills/clawroom-v3` exists.
- Search Railway logs for the thread id and for `canvas`. A `canvas navigate` attempt means the public invite fell into normal tools/main-agent chat instead of the ClawRoom skill.
- Check for `/data/.openclaw/clawroom-v3/<thread>-guest.runtime-state.json`.

If bridge launched but no opening message:

- Check `openclaw agent --agent clawroom-relay`.
- Check bridge log for `OpenClaw accepted run`.
- Check local or remote gateway logs for agent responses and timeouts.
- Confirm `CLAWROOM_AGENT_TIMEOUT_MS` is long enough for the current model.

If ASK_OWNER failed:

- For portable product-path testing, first verify the ASK_OWNER Telegram message
  includes the ClawRoom decision link and that the owner used that page.
- Confirm the relay transcript contains `owner_reply.source: owner_url`.
- Check `ask-owner-bindings` under the relevant `OPENCLAW_STATE_DIR` only when
  you are explicitly testing the optional Telegram inbound adapter.
- Verify the Telegram reply was intercepted before normal main-agent handling
  only when testing that optional adapter.
- If you are testing the optional inbound adapter and source tests pass but
  Railway still fails, inspect the running OpenClaw package bundle rather than
  assuming the source tree is deployed.
- Confirm transcript contains `ask_owner` and `owner_reply`.
- For portable strict T3, validator should be run with
  `--require-owner-reply-source owner_url`.

If costs/quota spike:

- Stop E2E.
- Sweep live bridges locally and on Railway.
- Check relay logs and Cloudflare usage.
- Prefer BYO relay or local tunnel E2E until hosted quota recovers.

## Known Sharp Edges

- OpenClaw gateway is loopback-only; external webhook push is not the v3 path. Bridges use long-poll.
- Dedicated agent is `clawroom-relay`; do not use `main` for bridge traffic.
- Session key format is `agent:clawroom-relay:clawroom:<thread>:<role>`.
- Railway state dir is `/data/.openclaw`, not `/root/.openclaw` and not `$HOME/.openclaw`.
- Public invite URLs are bootstrap protocol messages. If they fall through to main-agent chat, the room can look alive without verified bridge runtime.
- Telegram Desktop deep links can target the wrong bot or a new Start screen.
  For E2E, select `Link_🦀` from visible search and verify the chat title before
  sending any invite. The harness now OCR-checks the active title crop before
  paste/send; a failure is a useful guardrail, not a flaky test to bypass.
- Telegram shortcut state is fragile. Avoid extra navigation shortcuts such as
  `Cmd+Down` immediately before paste/send unless you can see the intended chat
  is still selected.
- Telegram ForceReply is an affordance, not a guarantee. Treat reply-to-message
  interception as an optional adapter, not the portable core path.
- The running OpenClaw package can differ from a source checkout. Verify the
  deployed `dist` bundle when debugging optional Telegram inbound behavior.
- Prompt markers such as `REPLY:` and `CLAWROOM_CLOSE:` are a protocol boundary. Parser tests must cover quoted/adversarial marker text.
- Direct Bot API notifications need `chat_id`, but full chat ids and bot tokens must stay out of artifacts.
- Durable Objects free-tier limits are real. Stale pollers can exhaust daily request volume.
- Detached child process reliability depends on detached stdio and `unref`; use the launcher and runtime-state files rather than shell backgrounding.

## External References Checked

- Railway SSH docs: <https://docs.railway.com/cli/ssh>
- Railway logs docs: <https://docs.railway.com/observability/logs>
- Cloudflare Durable Objects pricing: <https://developers.cloudflare.com/durable-objects/platform/pricing/>
- Cloudflare Durable Objects limits: <https://developers.cloudflare.com/durable-objects/platform/limits/>
- Cloudflare Durable Objects rules: <https://developers.cloudflare.com/durable-objects/best-practices/rules-of-durable-objects/>
- Telegram Bot API: <https://core.telegram.org/bots/api>
- Apple UI scripting guide: <https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/AutomatetheUserInterface.html>
- Node.js child process docs: <https://nodejs.org/api/child_process.html>
