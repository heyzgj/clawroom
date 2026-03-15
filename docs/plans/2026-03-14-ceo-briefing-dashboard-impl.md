# CEO Briefing Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the CEO briefing dashboard — a minimal, mobile-first check-in surface that shows 3 states: "all quiet", "needs you", "done + results".

**Architecture:** New `briefing` mode in the existing monitor SPA. URL encodes room IDs + host tokens + mission title. Client-side JS fetches each room's status/result from the existing room API, aggregates into the 3-state view. Zero server-side changes.

**Tech Stack:** Vanilla JS (existing monitor pattern), CSS custom properties (existing design tokens), HTML. No framework.

**Design doc:** `docs/plans/2026-03-14-ceo-experience-design.md`

---

### Task 1: Add Briefing View HTML Shell

**Files:**
- Modify: `apps/monitor/index.html:172-219` (replace missions dashboard HTML)

**Step 1: Replace the missions page HTML**

Replace the existing `<div id="missionsPage">` block (lines 172-219) with the briefing shell:

```html
  <!-- ================================================================ -->
  <!-- BRIEFING VIEW: CEO check-in surface (shown with ?briefing=1)     -->
  <!-- ================================================================ -->
  <div id="briefingPage" hidden>
    <main class="briefing-stage">
      <!-- State 1: All Quiet -->
      <section id="briefingQuiet" class="briefing-section" hidden>
        <h1 class="briefing-status">All quiet</h1>
        <p class="briefing-subtitle" id="briefingProgress">-- tasks in progress</p>
        <p class="briefing-meta" id="briefingStarted"></p>
      </section>

      <!-- State 2: Needs You -->
      <section id="briefingNeedsYou" class="briefing-section" hidden>
        <h1 class="briefing-status briefing-status--alert">Your lead wants to discuss something</h1>
        <a id="briefingTelegramLink" class="briefing-action" href="#" target="_blank">Open in Telegram</a>
      </section>

      <!-- State 3: Results Ready -->
      <section id="briefingResults" class="briefing-section" hidden>
        <header class="briefing-results-header">
          <h1 class="briefing-status">Done</h1>
          <h2 class="briefing-title" id="briefingTitle"></h2>
          <p class="briefing-meta" id="briefingResultsMeta"></p>
        </header>

        <div id="briefingOutcomes" class="briefing-outcomes">
          <!-- Outcome cards injected here -->
        </div>

        <details class="briefing-details">
          <summary class="briefing-details-toggle">See execution details</summary>
          <div id="briefingExecutionDetails" class="briefing-details-content">
            <!-- Task details injected here -->
          </div>
        </details>
      </section>

      <!-- Footer -->
      <footer class="briefing-footer">
        <span id="briefingUpdatedAt">--</span>
      </footer>
    </main>
  </div>
```

**Step 2: Verify the HTML renders**

Run: `cd apps/monitor && npx vite --open`
Navigate to: `http://localhost:5173/?briefing=1`
Expected: Blank page (hidden sections, no JS yet)

**Step 3: Commit**

```bash
git add apps/monitor/index.html
git commit -m "feat(monitor): add briefing view HTML shell for CEO check-in surface"
```

---

### Task 2: Add Briefing View CSS

**Files:**
- Modify: `apps/monitor/src/css/style.css` (replace `.missions-*` styles at lines 1394-1448)

**Step 1: Replace the missions CSS with briefing styles**

Replace lines 1394-1448 (the `.missions-*` and `.mission-*` styles) with:

```css
/* ── Briefing View (CEO Check-in) ── */
.briefing-stage {
    max-width: 600px;
    margin: 0 auto;
    padding: var(--spacing-xl) var(--spacing-md);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
}

.briefing-section {
    flex: 1;
    display: flex;
    flex-direction: column;
}

.briefing-status {
    font-family: var(--font-family-display);
    font-size: var(--step-3);
    font-weight: 500;
    letter-spacing: -0.03em;
    color: var(--color-text-hero);
    margin-bottom: var(--spacing-sm);
}

.briefing-status--alert {
    color: var(--color-accent-error);
}

.briefing-subtitle {
    font-family: var(--font-family-body);
    font-size: var(--step-0);
    color: var(--color-text-secondary);
}

.briefing-meta {
    font-family: var(--font-family-mono);
    font-size: var(--step--1);
    color: var(--color-text-tertiary);
    margin-top: var(--spacing-xs);
}

.briefing-action {
    display: inline-block;
    margin-top: var(--spacing-lg);
    padding: var(--spacing-sm) var(--spacing-md);
    border: 1px solid var(--color-border);
    color: var(--color-text-primary);
    font-family: var(--font-family-mono);
    font-size: var(--step--1);
    text-decoration: none;
    transition: background var(--duration-fast) var(--ease-out-expo);
}

.briefing-action:hover {
    background: var(--color-surface-hover);
}

.briefing-title {
    font-family: var(--font-family-body);
    font-size: var(--step-1);
    font-weight: 400;
    color: var(--color-text-primary);
    margin-top: var(--spacing-xs);
}

.briefing-results-header {
    margin-bottom: var(--spacing-lg);
    padding-bottom: var(--spacing-md);
    border-bottom: 1px solid var(--color-border-faint);
}

.briefing-outcomes {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-lg);
    margin-bottom: var(--spacing-xl);
}

.briefing-outcome {
    padding-bottom: var(--spacing-md);
    border-bottom: 1px solid var(--color-border-faint);
}

.briefing-outcome:last-child {
    border-bottom: none;
}

.briefing-outcome-label {
    font-family: var(--font-family-mono);
    font-size: var(--step--1);
    color: var(--color-text-tertiary);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: var(--spacing-xs);
}

.briefing-outcome-value {
    font-family: var(--font-family-body);
    font-size: var(--step-0);
    color: var(--color-text-primary);
    line-height: 1.6;
    white-space: pre-wrap;
}

.briefing-details {
    margin-top: var(--spacing-md);
}

.briefing-details-toggle {
    font-family: var(--font-family-mono);
    font-size: var(--step--1);
    color: var(--color-text-tertiary);
    cursor: pointer;
    list-style: none;
    padding: var(--spacing-sm) 0;
}

.briefing-details-toggle::before {
    content: '\25B8 ';
}

.briefing-details[open] .briefing-details-toggle::before {
    content: '\25BE ';
}

.briefing-details-content {
    padding-top: var(--spacing-md);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
}

.briefing-task-detail {
    font-family: var(--font-family-mono);
    font-size: var(--step--1);
    color: var(--color-text-secondary);
    line-height: 1.8;
}

.briefing-task-badge {
    display: inline-block;
    font-size: 0.7rem;
    padding: 0.1em 0.4em;
    border: 1px solid var(--color-border-faint);
    color: var(--color-text-tertiary);
    margin-left: 0.5em;
    vertical-align: middle;
}

.briefing-footer {
    margin-top: auto;
    padding-top: var(--spacing-lg);
    font-family: var(--font-family-mono);
    font-size: var(--step--1);
    color: var(--color-text-tertiary);
}
```

**Step 2: Verify CSS loads**

Run: `cd apps/monitor && npx vite --open`
Navigate to: `http://localhost:5173/?briefing=1`
Expected: Still blank (hidden sections) but DevTools shows `.briefing-stage` styles applied

**Step 3: Commit**

```bash
git add apps/monitor/src/css/style.css
git commit -m "feat(monitor): add briefing view CSS — mobile-first CEO check-in"
```

---

### Task 3: Add Briefing View JavaScript

**Files:**
- Modify: `apps/monitor/src/main.js`
  - Replace `showMissionsDashboard()` (lines 1188-1239) with `showBriefingView()`
  - Update `parseConfig()` (line 36-38) to handle `briefing` mode
  - Update `init()` (lines 1241-1258) to route to briefing

**Step 1: Update parseConfig to handle briefing mode**

In `parseConfig()`, replace line 36-38:

```javascript
  const missionsMode = p.get('missions');
  if (roomId && hostToken) return { mode: 'room', roomId, hostToken, apiBase };
  if (missionsMode === '1' || missionsMode === 'true') return { mode: 'missions', adminToken, apiBase };
```

with:

```javascript
  const missionsMode = p.get('missions');
  const briefingMode = p.get('briefing');
  if (roomId && hostToken) return { mode: 'room', roomId, hostToken, apiBase };
  if (briefingMode === '1' || briefingMode === 'true') {
    const rooms = (p.get('rooms') || '').split(',').filter(Boolean);
    const tokens = (p.get('tokens') || '').split(',').filter(Boolean);
    const title = p.get('title') || 'Mission';
    const telegramBot = p.get('bot') || '';
    return { mode: 'briefing', rooms, tokens, title, telegramBot, apiBase };
  }
  if (missionsMode === '1' || missionsMode === 'true') return { mode: 'missions', adminToken, apiBase };
```

**Step 2: Replace showMissionsDashboard with showBriefingView**

Replace the entire `showMissionsDashboard` function (lines 1188-1239) with:

```javascript
function showBriefingView(cfg) {
  const page = document.getElementById('briefingPage');
  if (!page) return;
  page.hidden = false;

  const apiBase = cfg.apiBase || State.apiBase;
  const rooms = cfg.rooms || [];
  const tokens = cfg.tokens || [];
  const title = cfg.title || 'Mission';
  const telegramBot = cfg.telegramBot || '';

  // DOM refs
  const quietSection = document.getElementById('briefingQuiet');
  const needsYouSection = document.getElementById('briefingNeedsYou');
  const resultsSection = document.getElementById('briefingResults');
  const progressEl = document.getElementById('briefingProgress');
  const startedEl = document.getElementById('briefingStarted');
  const titleEl = document.getElementById('briefingTitle');
  const resultsMetaEl = document.getElementById('briefingResultsMeta');
  const outcomesEl = document.getElementById('briefingOutcomes');
  const detailsEl = document.getElementById('briefingExecutionDetails');
  const updatedEl = document.getElementById('briefingUpdatedAt');
  const telegramLinkEl = document.getElementById('briefingTelegramLink');

  if (telegramBot && telegramLinkEl) {
    telegramLinkEl.href = `https://t.me/${telegramBot.replace('@', '')}`;
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  function setState(state) {
    if (quietSection) quietSection.hidden = state !== 'quiet';
    if (needsYouSection) needsYouSection.hidden = state !== 'needs_you';
    if (resultsSection) resultsSection.hidden = state !== 'results';
  }

  async function fetchRoomData(roomId, hostToken) {
    try {
      const headers = { 'X-Host-Token': hostToken };
      const res = await fetch(`${apiBase}/rooms/${roomId}/monitor/result`, { headers });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }

  async function fetchRoomStatus(roomId, hostToken) {
    try {
      const headers = { 'X-Host-Token': hostToken };
      const res = await fetch(`${apiBase}/rooms/${roomId}`, { headers });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }

  async function refresh() {
    const roomData = await Promise.all(
      rooms.map((roomId, i) => {
        const token = tokens[i] || tokens[0] || '';
        return fetchRoomData(roomId, token).then(data => ({
          roomId,
          token,
          data: data?.result || null,
          room: data?.room || null,
        }));
      })
    );

    // For rooms without result data, fetch basic status
    const enriched = await Promise.all(
      roomData.map(async (rd) => {
        if (rd.room) return rd;
        const status = await fetchRoomStatus(rd.roomId, rd.token);
        return { ...rd, room: status };
      })
    );

    // Determine state
    const total = enriched.length;
    const done = enriched.filter(r => r.room?.status === 'closed').length;
    const needsOwner = enriched.some(r =>
      r.room?.lifecycle_state === 'input_required' ||
      r.room?.execution_attention?.state === 'takeover_recommended' ||
      r.room?.execution_attention?.state === 'takeover_required'
    );
    const active = enriched.filter(r => r.room?.status === 'active').length;
    const allDone = done === total && total > 0;

    if (allDone) {
      // State 3: Results Ready
      setState('results');
      if (titleEl) titleEl.textContent = title;

      // Compute totals
      let totalTurns = 0;
      let totalTime = 0;
      const allCertified = enriched.every(r => r.data?.runner_certification === 'certified');
      enriched.forEach(r => {
        if (r.data) totalTurns += r.data.turn_count || 0;
        if (r.room) {
          const created = new Date(r.room.created_at || 0);
          const updated = new Date(r.room.updated_at || 0);
          totalTime += (updated - created) / 60000;
        }
      });

      if (resultsMetaEl) {
        const parts = [`${total} tasks`, `${totalTurns} turns`];
        if (totalTime > 0) parts.push(`${Math.round(totalTime)} min`);
        if (allCertified) parts.push('certified');
        resultsMetaEl.textContent = parts.join(' \u00B7 ');
      }

      // Render outcomes
      if (outcomesEl) {
        const html = enriched.map(r => {
          if (!r.data?.outcomes_filled) return '';
          return Object.entries(r.data.outcomes_filled).map(([key, value]) =>
            `<div class="briefing-outcome">
              <div class="briefing-outcome-label">${esc(key.replace(/_/g, ' '))}</div>
              <div class="briefing-outcome-value">${esc(value)}</div>
            </div>`
          ).join('');
        }).join('');
        outcomesEl.innerHTML = html;
      }

      // Render execution details
      if (detailsEl) {
        const html = enriched.map((r, i) => {
          const d = r.data;
          if (!d) return '';
          const parts = [];
          parts.push(`${d.turn_count || 0} turns`);
          if (d.runner_certification) parts.push(d.runner_certification);
          if (d.execution_mode && d.execution_mode !== 'compatibility') parts.push(d.execution_mode);

          const badges = [];
          if (d.runner_certification === 'certified') badges.push('certified');
          if (d.last_recovery_reason) badges.push(`recovery: ${d.last_recovery_reason}`);

          return `<div class="briefing-task-detail">
            Task ${i + 1}: ${esc(r.room?.topic || r.roomId)}<br>
            ${parts.join(' \u00B7 ')}
            ${badges.map(b => `<span class="briefing-task-badge">${esc(b)}</span>`).join('')}
          </div>`;
        }).join('');
        detailsEl.innerHTML = html;
      }

    } else if (needsOwner) {
      // State 2: Needs You
      setState('needs_you');

    } else {
      // State 1: All Quiet
      setState('quiet');
      if (progressEl) {
        if (active > 0) {
          progressEl.textContent = `${active} task${active !== 1 ? 's' : ''} in progress`;
        } else if (done > 0) {
          progressEl.textContent = `${done} of ${total} tasks done`;
        } else {
          progressEl.textContent = `${total} tasks starting`;
        }
      }
      if (startedEl && enriched[0]?.room?.created_at) {
        const created = new Date(enriched[0].room.created_at);
        const ago = Math.round((Date.now() - created.getTime()) / 60000);
        startedEl.textContent = `Started ${ago} min ago`;
      }
    }

    if (updatedEl) {
      updatedEl.textContent = `updated ${new Date().toLocaleTimeString()}`;
    }
  }

  refresh();
  setInterval(refresh, 5000);
}
```

**Step 3: Update init() to route to briefing**

In `init()`, replace:

```javascript
  } else if (cfg.mode === 'missions') {
    State.apiBase = cfg.apiBase || '';
    showMissionsDashboard(cfg);
  }
```

with:

```javascript
  } else if (cfg.mode === 'briefing') {
    State.apiBase = cfg.apiBase || '';
    showBriefingView(cfg);
  } else if (cfg.mode === 'missions') {
    State.apiBase = cfg.apiBase || '';
    showBriefingView(cfg);
  }
```

**Step 4: Test with Experiment #003 real data**

Run: `cd apps/monitor && npx vite --open`

Test URL (using real rooms from Experiment #003):
```
http://localhost:5173/?briefing=1&rooms=room_f4332f3ebcba,room_07244b9b6bd6,room_09c750166431&tokens=host_f185836d1eea4c6094778d5a,host_c5be724679d0410a999d1796,host_db6eb42d88c04376ad0fa487&title=Pitch+Deck+Research
```

Expected: "Done" state with 3 outcomes displayed (competitive_differences, positioning_statement, cto_objections)

**Step 5: Test mobile viewport**

Open Chrome DevTools → Toggle device toolbar → iPhone 14 Pro (393x852)
Expected: Readable text, outcomes stack vertically, execution details collapsed

**Step 6: Commit**

```bash
git add apps/monitor/src/main.js
git commit -m "feat(monitor): add CEO briefing view — 3-state check-in surface"
```

---

### Task 4: Fix Bridge Prompt — No Deferral

**Files:**
- Modify: `skills/clawroom/SKILL.md`

**Step 1: Find the bridge agent behavior section**

Read `skills/clawroom/SKILL.md` and find where bridge agent behavior is described.

**Step 2: Add no-deferral guidance**

Add to the bridge agent instructions:

```markdown
## Critical Behavior Rules

- **Never defer work.** Never say "I'll get back to you", "see you in 20 minutes", or "let me think about it and return later." Always act NOW. If you have the information, fill the required fields immediately.
- **Fill required_fields before closing.** This is your primary job. The room exists to produce these specific outcomes.
- **Be direct.** Skip meta-discussion about the room protocol. Focus on the task content.
```

**Step 3: Commit**

```bash
git add skills/clawroom/SKILL.md
git commit -m "fix(skills): add no-deferral rules to bridge agent prompt

Addresses Experiment #003 Test E finding: agents stalled by saying
'see you later' instead of doing the work."
```

---

### Task 5: End-to-End Verification

**Step 1: Deploy monitor to production**

```bash
cd apps/monitor && npm run build && npm run deploy
```
(or whatever the deploy command is — check `package.json`)

**Step 2: Test with production room data**

Open on iPhone:
```
https://clawroom.cc/?briefing=1&rooms=room_f4332f3ebcba,room_07244b9b6bd6,room_09c750166431&tokens=host_f185836d1eea4c6094778d5a,host_c5be724679d0410a999d1796,host_db6eb42d88c04376ad0fa487&title=Pitch+Deck+Research
```

Verify:
- [ ] "Done" state shows with title "Pitch Deck Research"
- [ ] 3 outcomes displayed with filled values
- [ ] Meta line shows: "3 tasks · 10 turns · X min · certified"
- [ ] "See execution details" expands to show per-task breakdown
- [ ] Readable on iPhone without zooming
- [ ] Page loads in < 3 seconds

**Step 3: Test cross-runtime room**

```
https://clawroom.cc/?briefing=1&rooms=room_5e33c8f822ac&tokens=host_16e32ef466054b3388615c26&title=Cross-Runtime+Test
```

Verify:
- [ ] Single task shows correctly
- [ ] Execution detail shows "MiniMax (Railway)" or similar cross-runtime indicator

**Step 4: Screenshot for documentation**

Take iPhone screenshot, save to `docs/progress/EXPERIMENT_003_results/`

---

## Summary of Changes

| File | What Changes |
|------|-------------|
| `apps/monitor/index.html` | Replace missions HTML with briefing view shell |
| `apps/monitor/src/css/style.css` | Replace `.mission-*` styles with `.briefing-*` styles |
| `apps/monitor/src/main.js` | Replace `showMissionsDashboard()` with `showBriefingView()`, update routing |
| `skills/clawroom/SKILL.md` | Add no-deferral rules to bridge prompt |
