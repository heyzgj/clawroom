# The Silent Killer of Background AI Agents: Concurrent Tool-Call Contamination

There's a class of bug in agent backends that doesn't show up in any framework's documentation, doesn't trigger any error handler, and doesn't change a single exit code. We spent weeks chasing it. This is what we found.

## The setup

We're building [ClawRoom](https://clawroom.cc), a system where AI agents from different owners collaborate inside bounded "rooms" — bring your own agent, agree on a goal, exchange a few turns, close with a structured outcome. Two agents, two owners, two runtimes, one shared room.

On each side, the agent runs a small Python process called a **room poller**. Its job is dead simple:

1. Poll the room for new messages
2. When a relay arrives, call the agent's LLM via the host's CLI: `openclaw agent --message "..."`
3. POST the response back to the room
4. Repeat until the room closes

The poller is a background process. It doesn't talk to the user. It's not interactive. It just shuttles messages between the room and the local LLM. About the simplest possible worker you could imagine.

## The symptom

For weeks, our end-to-end success rate hovered around 75%.

Sometimes a room would complete perfectly — both sides exchange turns, fields get filled, mutual DONE, clean close. Beautiful.

Sometimes the same room template, same prompts, same agents, would just... drift. Replies would come back wrong. Context would be missing. One side would respond as if it had never seen the other side's message. Or the response would be empty. Or it would be a fragment of something that didn't belong to this room at all.

The CLI returned exit code 0. Every time. No stderr. No stack trace. The poller logs said "called LLM, got response, posted to room." From the poller's perspective, everything was fine.

It just wasn't.

## The false leads

Here's everything we blamed before we found the actual bug:

- **LLM cold-starts.** First call to a fresh runtime is slow, sometimes weird. Plausible. Wasn't it.
- **Network flakiness.** Railway and our local boxes were both involved. We tightened retries. Didn't help.
- **SSL handshake quirks.** We had a real SSL bug at one point on one runtime, fixed it, problem persisted.
- **Token exhaustion.** Maybe the agent was running out of context mid-turn? No — token counts were nowhere near limits.
- **Cron timing.** Maybe two pollers were waking up too close together and stomping each other? We staggered them. Didn't matter.
- **Race conditions in our own code.** We added locks. We added serialization. We rewrote the polling loop twice. The bug was unmoved.

Every fix made the success rate marginally better and then it would regress again. Classic "the bug is somewhere you're not looking" pattern. We were so deep into our own code we couldn't see the layer below us.

## The isolation experiments

On April 2 we stopped trying fixes and started running experiments. The goal: stop debugging and start measuring. Strip everything down to the smallest possible reproduction, then add one variable at a time.

| Experiment | Setup | Result |
|---|---|---|
| Exp 1 | Single background exec, main session idle | 5/5 (100%) |
| Exp 2 | Two concurrent background execs, main session idle | **0/6 (0%)** |
| Exp 3 | Single background exec + active main session | ~83% (first 2 calls polluted) |
| Exp 4 | Cron-driven turns + active main session | 3/4 (75%) |

Exp 1 was our baseline. A single background process calling `openclaw agent --message "say hi"` five times in a row. Five clean responses. Whatever was wrong, it wasn't the call itself.

Exp 2 was the smoking gun, and honestly the moment everything snapped into place. Two background processes. Same machine. Same agent. Same prompt. Run them concurrently. **Zero out of six runs returned correct content.** Not one. Sometimes both processes got empty completions. Sometimes one got a fragment of the other's response. Sometimes they both got something that looked like a response but had nothing to do with their actual prompt.

Every single failed call returned exit code 0.

Exp 3 added back our actual operating condition: the user's main session was active in the foreground while a background worker tried to call the same CLI. The first two calls were almost always contaminated. Subsequent calls usually recovered. We'd been seeing this for weeks and thinking it was warm-up.

Exp 4 used a cron-style scheduler to trigger turns. Slightly better, but still 25% failure rate, still silent, still exit-code-zero garbage.

## The root cause

The CLI opens a fresh WebSocket connection to a local gateway process, sends a request, waits for a streamed response, and exits. The gateway is the actual long-lived daemon — it holds the LLM connection, manages sessions, routes responses back to whichever client is waiting.

Each CLI invocation looks to the gateway like a brand new client. New WebSocket. New session handshake. New request ID negotiation.

When two CLI calls land at the gateway concurrently, the gateway's internal session routing gets crossed. Somewhere in the routing layer — we never fully reverse-engineered which layer — request A's response goes to client B's socket, or vice versa, or the response gets dropped on the floor and the client times out into an empty completion.

The CLI process doesn't know any of this happened. It got bytes back over its socket. It exited cleanly. From the OS's perspective, nothing went wrong.

This is why it's invisible. There's no error to catch. There's no exception to log. The whole failure mode lives in the gap between "the CLI returned successfully" and "the content the CLI returned is actually yours."

## The fix

We bypassed the CLI entirely.

The gateway speaks a documented WebSocket protocol. Instead of spawning a CLI subprocess, we built a minimal WebSocket client inside the poller that talks to the gateway directly. The client is maybe 200 lines of Python.

The important parts of what it does differently:

1. **Each call manages its own session key.** No shared session state across processes. No assumptions about which session belongs to whom.
2. **Each call generates its own request ID and only accepts responses tagged with that exact ID.** If a frame comes back with a different ID, we drop it on the floor instead of treating it as our response.
3. **No process spawning.** The connection lives in-process. There's no subprocess fork, no stdout pipe, no exit code shell game.
4. **No connection pooling or reuse across calls.** Each LLM call is a complete session: open, handshake, request, stream-until-done, close. Boring, deterministic, ours.

That's the whole fix. The "magic" is just that we're now the ones correlating requests to responses, instead of trusting an upstream router to do it for us.

After the change:
- 4/4 concurrent calls returned correct content in our reproduction harness
- 10/10 reliability across our scenario suite
- Zero contamination observed since the switch

## A diagnostic protocol

If your agent backend has any of these patterns, you might be sitting on this same bug:

- Background workers that call a CLI or SDK to invoke an LLM
- Multiple workers running on one machine
- A central daemon, gateway, or router that mediates LLM calls
- "Mostly fine" reliability that mysteriously degrades when you scale up

Here's the protocol we'd use if we were starting over:

**Step 1 — Establish a baseline.** Five sequential calls from one process with a trivial prompt ("say hi"). You should get 100%. If you don't, you have a different problem; fix that first.

**Step 2 — Run two concurrent processes calling the same prompt.** Same machine, same CLI, same gateway, but two processes started simultaneously. Repeat ten times. If your success rate drops below your baseline **without any exit code changing**, you have contamination.

**Step 3 — Look at what came back, not whether anything came back.** This is the trap. Most reliability monitoring checks whether the process exited 0 and whether some bytes came back. Contamination passes both of those. You need to compare the *content* of the response against the prompt that was supposed to generate it. Even just "does the response mention any keyword from the prompt?" catches most of it.

**Step 4 — If contamination is real, replace the CLI/SDK call with a direct connection** — HTTP, gRPC, WebSocket, whatever the underlying gateway speaks — where your code owns the request/response correlation. Stop trusting the layer below to keep your messages straight.

## What we don't know

This is one CLI on one runtime. We have **not** tested whether the same failure mode exists in:

- Other CLI-mediated LLM tools (the OpenAI CLI, Gemini CLI, llm, Claude Code itself, etc.)
- SDK-based clients that share an HTTP connection pool
- MCP servers under concurrent load
- Other gateway architectures

It's entirely possible that some of these are bulletproof. It's entirely possible that some of them have the exact same bug and nobody's noticed because nobody's running concurrent background workers against them. We don't know. We didn't have time to audit the world; we had a product to ship.

What we *can* say is that the failure mode is real, it's silent, it's reproducible in under five minutes once you know what to look for, and we couldn't find a single popular agent framework that documents it. We checked LangGraph, AutoGen, CrewAI, OpenAI's Agents SDK, and Anthropic's MCP. None of them mention what happens when two concurrent tool calls quietly contaminate each other. The frameworks document tool errors, hallucinations, network timeouts. Not this.

That's not a criticism of any of those projects — the bug is below the framework layer, in process spawning and gateway routing. But it does mean if you build on top of any of them and run background workers, you're inheriting an assumption that nobody seems to have tested.

## The lesson

The narrow lesson is: when you're running concurrent background workers that call into a shared local daemon via a CLI, make sure something in your stack is correlating requests to responses. If it's not your code, find out whose code it is, and find out whether that code has actually been tested under concurrency.

The wider lesson, the one that actually changed how we think about reliability: **agent reliability problems often come from layers below the agent.** They come from process spawning, from session routing, from CLIs that were never designed for the access pattern you're using them for. The framework you build on trusts those layers. You probably do too. We did, for weeks, and it cost us.

Your tool-use loop can be perfect. Your prompts can be perfect. Your retries, your timeouts, your structured outputs can all be perfect. And you can still sit at 75% reliability because somewhere in a CLI subprocess two WebSocket frames got crossed and nobody threw an exception about it.

Probably worth checking.

---

*This is what we saw on our system, on our runtime, during our investigation. Your stack is not our stack. Your gateway might handle concurrency fine. Your CLI might be bulletproof. The diagnostic protocol above is cheap to run — if it comes back clean, congratulations, you don't have this bug. If it doesn't, at least now you know what you're looking at.*
