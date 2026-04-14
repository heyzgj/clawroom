# ClawRoom Landing Page Design Context

Saved for future sessions to pick up where we left off.

> **Migration note (2026-04-15):** originally lived in the pre-v3 `agent-chat` repo at `docs/clawroom_design.md`. Paths below referring to `apps/monitor/...` are historical — the mockup was moved alongside this doc as `landing-mockup.html` in the same directory. Install command `npx skills add heyzgj/clawroom` is also historical (it installed the v2.2.x thick-protocol skill); the v3.1 install path is via Telegram-triggered bridge self-launch, TBD once v3 is ready to ship.

## Current State

- **Latest mockup**: `landing-mockup.html` (this directory)
- **Structure**: Hero (headline + install CTA) → Use cases (4, Zoom-style with photos) → Proof strip (4 metrics) → End CTA → Footer
- **Install command**: `npx skills add heyzgj/clawroom` (pre-v3; to be revised for v3.1)
- **Assets**: 5 Pexels photos, all grayscaled (`filter: grayscale(1) contrast(1.05) brightness(0.78-0.85)`) to maintain duo-tone

## Visual Identity (Locked)

| Property | Value | Notes |
|----------|-------|-------|
| Background | `#000000` | Pure black |
| Text | `#ffffff` | Pure white, with opacity layers for hierarchy |
| Border-radius | `0px` | Zero everywhere. Non-negotiable. |
| Display font | Space Mono 700 | Monospace display. Tight letter-spacing (-0.04em) |
| Body font | IBM Plex Sans 300-500 | Clean, not Inter (banned) |
| Code font | JetBrains Mono 400-600 | For install commands, JSON examples |
| Shadows | None | Flat. Depth from light/dark contrast only |
| Photos | Grayscale-treated | `grayscale(1)` to stay in duo-tone |

## User Preferences (Observed)

**What they want:**
- Confident positioning — reads like a launched product, not an experiment
- Minimal — text and information deliver value, no decoration
- Zoom-style use case showcase — each use case with real photo + heading + description
- Hero with prominent, copyable install command as visual focal point
- Real content in examples (JSON, room IDs, agent names)
- "How it works" section with substance
- End CTA echoing the install command

**What they explicitly rejected:**
- `substrate v0.1` / version meta → reads as "we're not ready"
- `1 room active · 14 sealed today` → fake activity counter is cringe
- `what it's not` section → defensive disclaimer
- Any "experimental / zero users" honesty on the homepage
- Small noise text that doesn't deliver value
- Decorative gradients, background video, ambient wallpaper

**Inference**: They want the homepage to project confidence even though the product is experimental. Self-deprecation belongs in CLAUDE.md and docs, not on the marketing surface. Zoom doesn't write "v0.1" on their homepage.

## Positioning (From CLAUDE.md / README)

**One-liner**: "Bounded collaboration rooms where two AI agents from different owners exchange structured outcomes."

**Differentiator**: bounded cross-owner task rooms with structured outcomes, owner-in-the-loop, and real reliability instrumentation. Nothing else covers all four.

**Competitive map**:
- vs Google A2A: spec only, no execution
- vs Anthropic MCP: agent→tool, not agent↔agent
- vs OpenAI Agents SDK: single-owner
- vs LangGraph: general state-machine, not room-shaped
- vs Agent Relay: no structured outcomes

**Real metrics** (validated, can use confidently):
- S2 scenario suite: 9-10 / 10
- Avg room close time: 55-63s
- Cross-machine: validated
- Owner-in-the-loop: validated

## Open Questions (Need User Input)

1. **Target user**: Engineers already using Claude Code/Cursor/Codex? Or broader? Determines voice level (Linear-craft vs Zoom-mainstream).
2. **Use cases**: Current 4 are my guesses (coordinate plans / negotiate terms / hand off project / decide asynchronously). User may have specific verticals in mind.
3. **Video**: Currently excluded (user said minimal, text delivers value). May want to revisit.
4. **Brand voice level**: Currently Linear-craft (mono fonts, zero radius, dev aesthetic). User hasn't explicitly confirmed this vs moving toward Zoom-mainstream.

## Previous Mockup History

8 mockups existed before this session (cleaned up in repo reset):
1-resend-editorial, 2-terminal-native, 3-stripe-docs, 4-linear-precision, 5-mono-grid, 6-mono-ink, 7-warm-instrument, 8-duo-tone (chosen, became current homepage)

This session created 5 structural variants (9-manifesto, 10-transcript, 11-spec-sheet, 12-comparison, 13-room-index) + the synthesis (14-resonance) + the current Zoom-style landing (landing-v2/index.html). All in `apps/monitor/public/` but 9-13 may be gone if mockups/ was cleaned.

## Next Steps

- User reviews landing-v2 in browser (needs `npm run dev` in apps/monitor)
- Refine use cases based on user's real product priorities
- Once approved, merge into `apps/monitor/index.html` (replace existing homepage)
- Consider adding scroll animations (currently static — no IntersectionObserver)
