# ClawRoom v3.1 Next Chat Handoff - 2026-04-24

This is the starting context for the next chat session. Read this first, then
read `AGENTS.md`, `SKILL.md`, and
`docs/runbooks/CLAWROOM_V3_E2E_AND_DEBUG.md`.

## Current State

- Repo: `/Users/supergeorge/Desktop/project/clawroom`
- GitHub: `https://github.com/heyzgj/clawroom`
- Current commit: `7c3c75f feat: harden owner decision URL product path`
- Local branch status when this handoff was written: `main == origin/main`
- Main public-core ASK_OWNER path: relay-owned owner decision URL,
  `owner_reply.source: owner_url`
- Optional adapter path: Telegram inbound / non-reply recovery. Useful for the
  tested clawd/Link deployment, but not a ClawRoom public-core dependency.

## What Was Just Clarified

The owner asked why the E2E install instructions did not simply use:

```sh
npx skills add heyzgj/clawroom
```

Answer: `npx skills add` is the right target install path, but it has not yet
been promoted to release-candidate status for v3.1.

We verified:

```sh
npx skills add heyzgj/clawroom --list
```

It cloned the GitHub repo and found exactly one available skill:

```text
clawroom-v3
```

So the next install-path experiment should use:

```sh
npx skills add heyzgj/clawroom --skill clawroom-v3
```

Remaining unknown: whether the installed payload is clean. If the CLI installs
the root directory as a whole, the user's runtime may see maintainer-only files
such as docs, relay source, scripts, screenshots, and artifacts. That is the
reason the previous instruction used "install only the four product runtime
files" for release-candidate E2E.

The install design doc has been updated:

- `docs/design/install-path-v3-DRAFT.md`

## User's Intended Manual E2E Journey

The owner wants to run a real "average user" flow in Telegram, roughly:

1. Give one OpenClaw the ClawRoom repo / skill install instruction.
2. After install, tell it: "开个房间".
3. It should ask one or more useful clarification questions if the room goal is
   unclear.
4. Once it understands the goal and owner constraints, it should create a room
   and return a public invite link.
5. The owner forwards that invite link to the guest OpenClaw.
6. The guest OpenClaw should ask for guest-side owner context if the forwarded
   invite has no usable constraints.
7. After owner context is clear, the guest should join and let the bridge run.
8. If the negotiation crosses a mandate, ASK_OWNER should route through the
   ClawRoom owner decision URL.
9. Both sides should mutually close and report a useful owner-facing summary.

Current expected behavior:

- Host clarification is intentionally light: `SKILL.md` says ask one short
  question only if a critical goal or constraint is missing, then act.
- Guest clarification is also light: if invite text has no usable guest-side
  context, ask one short question before joining.
- Multi-question onboarding is not yet a hard protocol. If the user expects a
  full pre-room interview, that is a product gap to design, not a relay bug.
- Artifact generation is currently evaluator/harness-facing. Normal owners
  should expect a summary and, when ASK_OWNER fires, a decision URL.

## Exact First Instruction To Give An OpenClaw

Use this in a fresh `/new` Telegram session for each OpenClaw:

```text
请从 GitHub 安装最新版 ClawRoom v3 skill：

https://github.com/heyzgj/clawroom

优先尝试：
npx skills add heyzgj/clawroom --skill clawroom-v3

要求：
1. 如果本机已有 clawroom-v3 skill，先清理旧的 visible skill bundle。
2. 安装后确认 skill 目录里至少有：
   - SKILL.md
   - clawroomctl.mjs
   - launcher.mjs
   - bridge.mjs
3. 不要现在创建房间，不要加入房间。
4. 安装后验证：
   - node --check clawroomctl.mjs
   - node --check launcher.mjs
   - node --check bridge.mjs
   - openclaw skills info clawroom-v3 显示 Ready
5. 如果支持创建/检查 agent，请确认 dedicated agent clawroom-relay 可用。
6. 不要把 token、PID、路径、hash、raw JSON、日志贴给我。
7. 最后只用普通话回复我：ClawRoom v3 已准备好。若失败，只说人类可读的失败原因。
```

If `npx skills add` installs too much or cannot put the skill in the actual
OpenClaw-visible path, fall back to installing only the four runtime files into:

- local clawd: `~/clawd/skills/clawroom-v3`
- Railway Link: `/data/workspace/skills/clawroom-v3`

Then verify with `openclaw skills info clawroom-v3`.

## Suggested First Manual E2E Script

Host owner message:

```text
开个房间
```

If it asks what the room is for, reply:

```text
帮我和对方 agent 谈一个短视频合作。我的预算上限是 6.5 万日元；如果对方超过这个价格，必须先问我。目标是确定价格、交付内容、付款、使用权、修改轮次和下一步。
```

Expected host result:

- It creates a room.
- It gives only a public invite link.
- It does not print launcher JSON, PID, raw tokens, paths, hashes, or logs.

Guest owner message:

```text
这个 ClawRoom invite 帮我处理一下：
<PUBLIC_INVITE_URL>

你代表我。我的底价是 7.5 万日元；低于这个不要同意。如果对方要压价，先问我。
```

Expected guest result:

- It joins the room and reports that it will handle it.
- It does not print raw launcher JSON or machine details.
- If the counterpart proposes below/above the mandate, owner decision uses the
  ClawRoom decision URL.
- Final report includes agreed terms and next step.

## What To Watch For In Screenshots

Pass signals:

- Public invite URL only, not raw `/join?token=...`.
- No launcher JSON.
- No PID, log path, runtime-state path, SHA, session key, create key, or bot
  token.
- ASK_OWNER message contains a human decision link, not internal endpoint
  labels like `Room`, `Role`, or `owner_reply_token`.
- Final summary is owner-ready and includes next step.

Fail signals:

- OpenClaw installs old v2 skill or a non-visible copy.
- Guest invite falls into ordinary browser/tool chat instead of `clawroomctl`.
- It creates/join rooms without asking a necessary owner-context question.
- It asks owner through Telegram free-text only and no `owner_url` appears.
- Link or clawd outputs raw technical JSON.
- Both roles accidentally run on the same machine in what was meant to be a
  cross-machine run.

## Preflight For Codex Or A Future Agent

Before helping with the manual E2E:

```sh
git -C /Users/supergeorge/Desktop/project/clawroom status --short --branch
git -C /Users/supergeorge/Desktop/project/clawroom log -1 --oneline --decorate
pgrep -f '^node .*bridge\.mjs --thread' || true
railway ssh sh -lc "ps -eo pid,args | grep 'bridge.mjs --thread' | grep -v grep | awk '{print \$1}'"
```

Do not run or paste raw `railway ssh env`. It can expose secrets.

## Current Known Gaps

- `npx skills add heyzgj/clawroom --skill clawroom-v3` is discovered but not
  yet validated as a clean install payload.
- First-use install may still need manual agent setup for `clawroom-relay`.
- Bridge still relies on the owning OpenClaw runtime to provide Telegram chat
  context/token correctly.
- Full multi-question pre-room interview is not implemented as a hard product
  behavior.
- Normal owner-facing "artifact" output is not yet a polished product feature;
  committed artifacts are evaluator/harness evidence.
- Public hosted relay should remain gated or paid-capacity. BYO relay remains
  the safer public path.

## Recommended Next Session Goal

Run an install-path release-candidate test:

1. In a disposable or clean OpenClaw-visible skill area, run
   `npx skills add heyzgj/clawroom --skill clawroom-v3`.
2. Inspect what files were installed.
3. Verify `node --check` for the three runtime scripts.
4. Verify `openclaw skills info clawroom-v3`.
5. Repeat on Railway Link or use the owner-driven Telegram install flow.
6. Run one average-user host create plus guest join using natural Telegram
   language.
7. Record screenshots and any raw-output leakage.
