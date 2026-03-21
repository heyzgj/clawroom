import { WorkflowEntrypoint, WorkflowStep } from "cloudflare:workers";
import type { WorkflowEvent } from "cloudflare:workers";

type AiBinding = {
  run(
    model: string,
    input: {
      messages: Array<{ role: "system" | "user"; content: string }>;
      response_format?: unknown;
    },
  ): Promise<Record<string, unknown>>;
};

type Env = {
  ROOMS: DurableObjectNamespace;
  AI?: AiBinding;
};

export type RoomParticipantWorkflowParams = {
  room_id: string;
  participant: string;
  participant_token: string;
  mode?: "conversation";
  room_url?: string;
  model?: string;
};

export type RoomParticipantWorkflowPayload = {
  payload_json?: string | null;
};

type WorkflowCheckpoint = {
  room_id: string;
  participant: string;
  participant_token: string;
  mode: "conversation";
  room_url: string | null;
  model: string;
  last_seen_event_id: number;
  replies_sent: number;
  final_state: "running" | "done_sent" | "room_closed" | "step_cap_reached";
};

type RoomFieldMap = Record<string, { value?: string | null } | string | null | undefined>;

type RoomParticipantSnapshot = {
  name: string;
  joined?: boolean;
};

type RoomSnapshot = {
  id: string;
  topic: string;
  goal: string;
  required_fields: string[];
  expected_outcomes?: string[];
  fields: RoomFieldMap;
  participants: RoomParticipantSnapshot[];
  status: string;
  turn_count: number;
};

type RoomEventRow = {
  id: number;
  type: string;
  audience: string;
  payload?: Record<string, unknown>;
};

type RoomEventsResponse = {
  room: RoomSnapshot;
  events: RoomEventRow[];
  next_cursor: number;
};

type WorkflowActionableEvent =
  | {
    kind: "counterpart_relay";
    event: RoomEventRow;
  }
  | {
    kind: "owner_resume";
    event: RoomEventRow;
  };

type WorkflowModelReply = {
  intent: "ASK" | "ANSWER" | "NOTE" | "DONE" | "ASK_OWNER" | "OWNER_REPLY";
  text: string;
  fills: Record<string, string>;
  facts: string[];
  questions: string[];
  expect_reply: boolean;
};

const DEFAULT_WORKFLOW_MODEL = "@cf/meta/llama-3.1-8b-instruct-fast";
const MAX_WORKFLOW_CYCLES = 12;

function trimmedString(value: unknown, max = 500): string {
  return String(value || "").trim().slice(0, max);
}

function asFieldValues(fields: RoomFieldMap): Record<string, string> {
  const values: Record<string, string> = {};
  for (const [key, raw] of Object.entries(fields || {})) {
    const value = typeof raw === "object" && raw !== null ? trimmedString(raw.value, 2000) : trimmedString(raw, 2000);
    if (value) values[key] = value;
  }
  return values;
}

function normalizeReply(raw: Record<string, unknown>, relayEventId: number): WorkflowModelReply & { meta: Record<string, unknown> } {
  const intentRaw = trimmedString(raw.intent, 24).toUpperCase();
  const intent = (
    ["ASK", "ANSWER", "NOTE", "DONE", "ASK_OWNER", "OWNER_REPLY"].includes(intentRaw)
      ? intentRaw
      : "ANSWER"
  ) as WorkflowModelReply["intent"];
  const fillsInput = raw.fills && typeof raw.fills === "object" ? raw.fills as Record<string, unknown> : {};
  const fills: Record<string, string> = {};
  for (const [key, value] of Object.entries(fillsInput)) {
    const cleanKey = trimmedString(key, 120);
    const cleanValue = trimmedString(value, 2000);
    if (cleanKey && cleanValue) fills[cleanKey] = cleanValue;
  }
  const facts = Array.isArray(raw.facts) ? raw.facts.map((item) => trimmedString(item, 500)).filter(Boolean) : [];
  const questions = Array.isArray(raw.questions) ? raw.questions.map((item) => trimmedString(item, 500)).filter(Boolean) : [];
  const expectReplyDefault = intent === "ASK" || intent === "ANSWER" || intent === "OWNER_REPLY";
  const expectReply = typeof raw.expect_reply === "boolean" ? raw.expect_reply : expectReplyDefault;
  return {
    intent,
    text: trimmedString(raw.text, 1000) || "(no text)",
    fills,
    facts,
    questions,
    expect_reply: intent === "NOTE" || intent === "DONE" || intent === "ASK_OWNER" ? false : expectReply,
    meta: {
      workflow_auto: true,
      in_reply_to_event_id: relayEventId,
    },
  };
}

function roomOutcomesComplete(room: RoomSnapshot, reply: WorkflowModelReply): boolean {
  const expected = Array.isArray(room.expected_outcomes) && room.expected_outcomes.length > 0
    ? room.expected_outcomes
    : room.required_fields;
  if (!expected.length) return false;
  const known = asFieldValues(room.fields || {});
  for (const [key, value] of Object.entries(reply.fills || {})) {
    if (trimmedString(value, 2000)) known[key] = trimmedString(value, 2000);
  }
  return expected.every((field) => Boolean(trimmedString(known[field], 2000)));
}

function coerceTerminalReply(room: RoomSnapshot, reply: WorkflowModelReply & { meta: Record<string, unknown> }): WorkflowModelReply & { meta: Record<string, unknown> } {
  if (!["ANSWER", "NOTE"].includes(reply.intent)) return reply;
  if (reply.expect_reply) return reply;
  if ((reply.questions || []).some((item) => trimmedString(item, 300))) return reply;
  if (!roomOutcomesComplete(room, reply)) return reply;
  return {
    ...reply,
    intent: "DONE",
    expect_reply: false,
    meta: {
      ...(reply.meta || {}),
      terminal_coercion: ["intent->DONE"],
    },
  };
}

function buildReplyPrompt(room: RoomSnapshot, participant: string, actionable: WorkflowActionableEvent): string {
  const requiredFields = room.required_fields || room.expected_outcomes || [];
  const knownFields = asFieldValues(room.fields || {});
  const eventPayload = actionable.event.payload || {};
  const message = eventPayload.message && typeof eventPayload.message === "object"
    ? eventPayload.message as Record<string, unknown>
    : {};
  const incomingSection = actionable.kind === "owner_resume"
    ? [
      "Owner guidance:",
      `- participant: ${trimmedString(eventPayload.participant, 120) || participant}`,
      `- text: ${trimmedString(eventPayload.text, 1200)}`,
      `- meta: ${JSON.stringify(eventPayload.meta && typeof eventPayload.meta === "object" ? eventPayload.meta : {})}`,
      "",
      "Use the owner guidance to continue the room. Do not repeat the owner message verbatim.",
    ]
    : [
      "Incoming relay:",
      `- from: ${trimmedString(eventPayload.from, 120)}`,
      `- intent: ${trimmedString(message.intent, 40)}`,
      `- text: ${trimmedString(message.text, 1200)}`,
      `- fills: ${JSON.stringify(message.fills && typeof message.fills === "object" ? message.fills : {})}`,
    ];
  return [
    "You are writing one in-room reply for ClawRoom.",
    "Return only one JSON object.",
    `Participant: ${participant}`,
    `Topic: ${room.topic}`,
    `Goal: ${room.goal}`,
    `Required fields: ${JSON.stringify(requiredFields)}`,
    `Known fields: ${JSON.stringify(knownFields)}`,
    ...incomingSection,
    "",
    "Rules:",
    "- Keep the reply short and concrete.",
    "- Add new information, one proposal, a decision, or one useful question.",
    "- Use fills for required fields when you know them.",
    "- Use ASK_OWNER only when a real owner-only decision is still missing.",
    "- If owner guidance resolves the remaining decision and you can fill the required fields now, prefer DONE.",
    "- If no further reply is needed and the outcome is clear, use DONE.",
    "- Do not mention APIs, JSON, relays, workflows, or room mechanics.",
    "",
    "Output schema:",
    JSON.stringify({
      intent: "ANSWER",
      text: "short reply",
      fills: { optional_field: "value" },
      facts: ["optional fact"],
      questions: ["optional question"],
      expect_reply: true,
    }),
  ].join("\n");
}

export class RoomParticipantWorkflow extends WorkflowEntrypoint<Env, RoomParticipantWorkflowParams> {
  private roomStub(roomId: string): DurableObjectStub {
    const id = this.env.ROOMS.idFromName(roomId);
    return this.env.ROOMS.get(id);
  }

  private async roomFetch(roomId: string, path: string, init?: RequestInit): Promise<Response> {
    const stub = this.roomStub(roomId);
    return await stub.fetch(new Request(`https://room/rooms/${roomId}${path}`, init));
  }

  private async fetchEvents(checkpoint: WorkflowCheckpoint): Promise<RoomEventsResponse> {
    const response = await this.roomFetch(
      checkpoint.room_id,
      `/events?after=${checkpoint.last_seen_event_id}&limit=100`,
      {
        method: "GET",
        headers: {
          "X-Participant-Token": checkpoint.participant_token,
        },
      },
    );
    if (!response.ok) {
      throw new Error(`room events fetch failed status=${response.status}`);
    }
    return await response.json() as RoomEventsResponse;
  }

  private async postMessage(
    checkpoint: WorkflowCheckpoint,
    message: WorkflowModelReply & { meta: Record<string, unknown> },
  ): Promise<void> {
    const response = await this.roomFetch(checkpoint.room_id, "/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-Participant-Token": checkpoint.participant_token,
      },
      body: JSON.stringify(message),
    });
    if (!response.ok) {
      throw new Error(`room message post failed status=${response.status}`);
    }
  }

  private async callModel(
    checkpoint: WorkflowCheckpoint,
    room: RoomSnapshot,
    actionable: WorkflowActionableEvent,
  ): Promise<WorkflowModelReply & { meta: Record<string, unknown> }> {
    if (!this.env.AI) {
      throw new Error("AI binding is not configured");
    }
    const prompt = buildReplyPrompt(room, checkpoint.participant, actionable);
    const result = await this.env.AI.run(checkpoint.model, {
      messages: [
        {
          role: "system",
          content: "Return only valid JSON matching the requested schema.",
        },
        {
          role: "user",
          content: prompt,
        },
      ],
      response_format: {
        type: "json_schema",
        json_schema: {
          type: "object",
          properties: {
            intent: {
              type: "string",
              enum: ["ASK", "ANSWER", "NOTE", "DONE", "ASK_OWNER", "OWNER_REPLY"],
            },
            text: { type: "string" },
            fills: {
              type: "object",
              additionalProperties: { type: "string" },
            },
            facts: {
              type: "array",
              items: { type: "string" },
            },
            questions: {
              type: "array",
              items: { type: "string" },
            },
            expect_reply: { type: "boolean" },
          },
          required: ["intent", "text", "fills", "facts", "questions", "expect_reply"],
          additionalProperties: false,
        },
      },
    });
    const structured = result.response && typeof result.response === "object"
      ? result.response as Record<string, unknown>
      : {};
    return normalizeReply(structured, actionable.event.id);
  }

  private latestActionableEvent(events: RoomEventRow[], participant: string): WorkflowActionableEvent | null {
    const actionable: WorkflowActionableEvent[] = [];
    for (const event of events) {
      if (event.type === "relay") {
        const payload = event.payload || {};
        const from = trimmedString(payload.from, 120);
        if (from && from !== participant) {
          actionable.push({ kind: "counterpart_relay", event });
        }
        continue;
      }
      if (event.type === "owner_resume") {
        const payload = event.payload || {};
        const resumedParticipant = trimmedString(payload.participant, 120);
        if (resumedParticipant && resumedParticipant === participant) {
          actionable.push({ kind: "owner_resume", event });
        }
      }
    }
    return actionable.length ? actionable[actionable.length - 1] : null;
  }

  async run(event: WorkflowEvent<RoomParticipantWorkflowParams>, step: WorkflowStep): Promise<Record<string, string | number | null>> {
    let checkpoint = await step.do("capture workflow parameters", async (): Promise<WorkflowCheckpoint> => {
      return {
        room_id: trimmedString(event.payload.room_id, 120),
        participant: trimmedString(event.payload.participant, 120),
        participant_token: trimmedString(event.payload.participant_token, 240),
        mode: event.payload.mode === "conversation" ? "conversation" : "conversation",
        room_url: typeof event.payload.room_url === "string" && event.payload.room_url.trim()
          ? event.payload.room_url.trim()
          : null,
        model: trimmedString(event.payload.model, 120) || DEFAULT_WORKFLOW_MODEL,
        last_seen_event_id: 0,
        replies_sent: 0,
        final_state: "running",
      };
    });

    if (!checkpoint.room_id) throw new Error("room_id is required");
    if (!checkpoint.participant) throw new Error("participant is required");
    if (!checkpoint.participant_token) throw new Error("participant_token is required");

    for (let cycle = 1; cycle <= MAX_WORKFLOW_CYCLES; cycle += 1) {
      await step.waitForEvent<RoomParticipantWorkflowPayload>(
        `wait for room event ${cycle}`,
        { type: "room-event", timeout: "24 hours" },
      );

      checkpoint = await step.do(`process room event ${cycle}`, async (): Promise<WorkflowCheckpoint> => {
        const payload = await this.fetchEvents(checkpoint);
        const nextCursor = Number(payload.next_cursor || checkpoint.last_seen_event_id);
        const room = payload.room;
        const actionable = this.latestActionableEvent(payload.events || [], checkpoint.participant);

        const nextCheckpoint: WorkflowCheckpoint = {
          ...checkpoint,
          last_seen_event_id: Number.isFinite(nextCursor) ? nextCursor : checkpoint.last_seen_event_id,
        };

        if (!room || room.status !== "active") {
          return { ...nextCheckpoint, final_state: "room_closed" };
        }

        if (!actionable) {
          return nextCheckpoint;
        }

        const reply = coerceTerminalReply(room, await this.callModel(nextCheckpoint, room, actionable));
        await this.postMessage(nextCheckpoint, reply);
        return {
          ...nextCheckpoint,
          replies_sent: nextCheckpoint.replies_sent + 1,
          final_state: reply.intent === "DONE" ? "done_sent" : "running",
        };
      });

      if (checkpoint.final_state === "done_sent" || checkpoint.final_state === "room_closed") {
        return {
          status: checkpoint.final_state,
          room_id: checkpoint.room_id,
          participant: checkpoint.participant,
          model: checkpoint.model,
          last_seen_event_id: checkpoint.last_seen_event_id,
          replies_sent: checkpoint.replies_sent,
        };
      }
    }

    return {
      status: "step_cap_reached",
      room_id: checkpoint.room_id,
      participant: checkpoint.participant,
      model: checkpoint.model,
      last_seen_event_id: checkpoint.last_seen_event_id,
      replies_sent: checkpoint.replies_sent,
    };
  }
}
