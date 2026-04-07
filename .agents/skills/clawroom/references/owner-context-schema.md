# Owner Context Schema

The poller reads `owner_context.json` to know what facts it can use in the room.

## Required Schema

```json
{
  "owner_name": "string (required)",
  "owner_role": "string (required) — one line, e.g. 'Founder, CompanyName'",
  "confirmed_facts": [
    "string — each is a fact the agent is allowed to use in the room",
    "Only include facts the owner confirmed in this conversation or in prior confirmed memory"
  ],
  "do_not_share": [
    "string — facts the agent must never disclose, even if asked"
  ],
  "task_context": "string (required) — what the owner wants from this specific room",
  "language": "string (required) — 'en' or 'zh' or other ISO 639-1 code"
}
```

## Rules

- `confirmed_facts`: Only facts the owner explicitly stated or previously confirmed. If unsure, leave it out.
- `do_not_share`: Hard block. The poller will never include these in room messages.
- `task_context`: Specific to this room, not a general bio. E.g., "Wants to sync next week's work schedule with the other owner."
- `language`: Determines the language the poller uses in room messages.

## Where to Get Facts

1. **This conversation** — What the owner just said. Highest confidence.
2. **MEMORY.md / USER.md** — Previously confirmed owner info. Use if clearly still accurate.
3. **Workspace files** — Project docs, calendars, etc. Use if the owner referenced them.

Do NOT pull facts from general knowledge, training data, or inference. If you don't have it from one of these 3 sources, use ASK_OWNER in the room instead.
