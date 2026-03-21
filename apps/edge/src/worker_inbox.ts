import { badRequest, json, normalizePlatformError } from "./worker_util";

type Env = {};

type InboxEventType = "room_invite" | "owner_gate_notification";

type InboxEventRow = {
  id: number;
  type: InboxEventType;
  payload_json: string;
  created_at_ms: number;
};

const MAX_WAIT_SECONDS = 30;
const MAX_EVENTS_PER_POLL = 50;
const RETENTION_MS = 7 * 24 * 60 * 60 * 1000;

export class AgentInboxDurableObject implements DurableObject {
  private sql: SqlStorage;
  private schemaReady = false;
  private waitingResolvers = new Set<() => void>();

  constructor(private state: DurableObjectState, private env: Env) {
    this.sql = state.storage.sql;
  }

  async fetch(request: Request): Promise<Response> {
    this.ensureSchema();
    const url = new URL(request.url);

    try {
      if (request.method === "POST" && url.pathname === "/events") {
        return await this.handleWriteEvent(request);
      }

      if (request.method === "GET" && url.pathname === "/events") {
        return await this.handlePollEvents(url);
      }

      return json({ error: "not_found" }, { status: 404 });
    } catch (error: unknown) {
      if (error instanceof Response) return error;
      const normalized = normalizePlatformError(error);
      if (normalized) return normalized;
      return json(
        { error: "internal_error", message: String((error as Error)?.message || error) },
        { status: 500 },
      );
    }
  }

  private async handleWriteEvent(request: Request): Promise<Response> {
    const body = await request.json() as Record<string, unknown>;
    const type = String(body.type || "").trim() as InboxEventType;
    if (!type || !["room_invite", "owner_gate_notification"].includes(type)) {
      return badRequest("type must be 'room_invite' or 'owner_gate_notification'");
    }
    const payload = body.payload;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return badRequest("payload must be an object");
    }

    const createdAtMs = Date.now();
    this.sql.exec(
      "INSERT INTO inbox_events (type, payload_json, created_at_ms) VALUES (?, ?, ?)",
      type,
      JSON.stringify(payload),
      createdAtMs,
    );
    const inserted = this.sql.exec(
      "SELECT last_insert_rowid() AS id",
    ).one() as { id: number | bigint } | null;

    for (const resolve of this.waitingResolvers) {
      resolve();
    }
    this.waitingResolvers.clear();

    return json({
      id: Number(inserted?.id || 0),
      type,
      created_at_ms: createdAtMs,
    }, { status: 201 });
  }

  private async handlePollEvents(url: URL): Promise<Response> {
    const after = Math.max(0, Number(url.searchParams.get("after") || "0"));
    const waitSeconds = Math.min(
      MAX_WAIT_SECONDS,
      Math.max(0, Number(url.searchParams.get("wait") || String(MAX_WAIT_SECONDS))),
    );

    this.pruneExpired();

    let events = this.getEventsAfter(after);
    if (events.length > 0 || waitSeconds === 0) {
      return this.eventsResponse(events, after);
    }

    let wake: (() => void) | null = null;
    try {
      await Promise.race([
        new Promise<void>((resolve) => {
          wake = () => resolve();
          this.waitingResolvers.add(wake);
        }),
        new Promise<void>((resolve) => setTimeout(resolve, waitSeconds * 1000)),
      ]);
    } finally {
      if (wake) this.waitingResolvers.delete(wake);
    }

    events = this.getEventsAfter(after);
    return this.eventsResponse(events, after);
  }

  private getEventsAfter(after: number): Array<Record<string, unknown>> {
    const rows = this.sql.exec(
      "SELECT id, type, payload_json, created_at_ms FROM inbox_events WHERE id > ? ORDER BY id ASC LIMIT ?",
      after,
      MAX_EVENTS_PER_POLL,
    ).toArray() as InboxEventRow[];

    return rows.map((row) => ({
      id: Number(row.id),
      type: String(row.type),
      payload: JSON.parse(String(row.payload_json || "{}")),
      created_at_ms: Number(row.created_at_ms || 0),
    }));
  }

  private eventsResponse(events: Array<Record<string, unknown>>, after: number): Response {
    const nextCursor = events.length > 0
      ? Number(events[events.length - 1].id)
      : after;
    return json({ events, next_cursor: nextCursor });
  }

  private pruneExpired(): void {
    const cutoffMs = Date.now() - RETENTION_MS;
    this.sql.exec("DELETE FROM inbox_events WHERE created_at_ms < ?", cutoffMs);
  }

  private ensureSchema(): void {
    if (this.schemaReady) return;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS inbox_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at_ms INTEGER NOT NULL
      );
    `);
    this.schemaReady = true;
  }
}
