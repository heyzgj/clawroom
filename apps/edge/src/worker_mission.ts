import { badRequest, json, normalizePlatformError } from "./worker_util";

type Env = {
  ROOMS: DurableObjectNamespace;
  TEAM_REGISTRY?: DurableObjectNamespace;
};

type MissionStatus = "planning" | "active" | "completed" | "failed" | "canceled";
type TaskStatus = "pending" | "assigned" | "active" | "completed" | "failed" | "canceled";

/**
 * MissionDurableObject: coordinates a group of bounded task rooms
 * spawned by a lead agent. Each mission has tasks, each task maps to
 * a room assigned to a specific worker agent.
 *
 * Keyed by mission_id (idFromName).
 */
export class MissionDurableObject implements DurableObject {
  private sql: SqlStorage;
  private schemaReady = false;

  constructor(private state: DurableObjectState, private env: Env) {
    this.sql = state.storage.sql;
  }

  async fetch(request: Request): Promise<Response> {
    this.ensureSchema();
    const url = new URL(request.url);

    try {
      // POST /init — create mission
      if (request.method === "POST" && url.pathname === "/init") {
        return this.handleInit(request);
      }
      // GET /status — mission overview + tasks
      if (request.method === "GET" && url.pathname === "/status") {
        return this.handleStatus();
      }
      // POST /tasks — add a task to the mission
      if (request.method === "POST" && url.pathname === "/tasks") {
        return this.handleAddTask(request);
      }
      // POST /tasks/:task_id/status — update task status
      const taskStatusMatch = url.pathname.match(/^\/tasks\/([^/]+)\/status$/);
      if (request.method === "POST" && taskStatusMatch) {
        return this.handleUpdateTaskStatus(taskStatusMatch[1], request);
      }
      // POST /complete — mark mission complete
      if (request.method === "POST" && url.pathname === "/complete") {
        return this.handleComplete(request);
      }
      // POST /cancel — cancel mission
      if (request.method === "POST" && url.pathname === "/cancel") {
        return this.handleCancel();
      }

      return json({ error: "not_found" }, { status: 404 });
    } catch (error: unknown) {
      if (error instanceof Response) return error;
      const normalized = normalizePlatformError(error);
      if (normalized) return normalized;
      return json({ error: "internal_error", message: String((error as Error)?.message || error) }, { status: 500 });
    }
  }

  private async handleInit(request: Request): Promise<Response> {
    const body = await request.json() as Record<string, unknown>;
    const missionId = String(body.mission_id || "");
    const title = String(body.title || "");
    const goal = String(body.goal || "");
    const leadAgent = String(body.lead_agent || "");

    if (!missionId || !title || !leadAgent) {
      throw badRequest("mission_id, title, and lead_agent are required");
    }

    const existing = this.sql.exec("SELECT mission_id FROM missions LIMIT 1").toArray();
    if (existing.length > 0) {
      return json({ error: "already_exists", mission_id: missionId }, { status: 409 });
    }

    const now = new Date().toISOString();
    this.sql.exec(
      `INSERT INTO missions (mission_id, title, goal, lead_agent, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'planning', ?, ?)`,
      missionId, title, goal, leadAgent, now, now
    );

    return json({ mission_id: missionId, status: "planning", created_at: now }, { status: 201 });
  }

  private handleStatus(): Response {
    const mission = this.sql.exec("SELECT * FROM missions LIMIT 1").toArray()[0];
    if (!mission) return json({ error: "not_found" }, { status: 404 });

    const tasks = this.sql.exec("SELECT * FROM tasks ORDER BY created_at ASC").toArray();
    const summary = {
      total: tasks.length,
      pending: tasks.filter((t: any) => t.status === "pending").length,
      assigned: tasks.filter((t: any) => t.status === "assigned").length,
      active: tasks.filter((t: any) => t.status === "active").length,
      completed: tasks.filter((t: any) => t.status === "completed").length,
      failed: tasks.filter((t: any) => t.status === "failed").length,
    };

    return json({ mission, tasks, summary });
  }

  private async handleAddTask(request: Request): Promise<Response> {
    const body = await request.json() as Record<string, unknown>;
    const taskId = String(body.task_id || `task_${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`);
    const title = String(body.title || "");
    const description = String(body.description || "");
    const assignedAgent = String(body.assigned_agent || "");
    const roomId = String(body.room_id || "");

    if (!title) throw badRequest("title is required");

    const now = new Date().toISOString();
    this.sql.exec(
      `INSERT INTO tasks (task_id, title, description, assigned_agent, room_id, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)`,
      taskId, title, description, assignedAgent, roomId, now, now
    );

    // Auto-transition mission to active if still planning
    const mission = this.sql.exec("SELECT status FROM missions LIMIT 1").one() as any;
    if (mission?.status === "planning") {
      this.sql.exec("UPDATE missions SET status='active', updated_at=?", now);
    }

    return json({ task_id: taskId, status: "pending", created_at: now }, { status: 201 });
  }

  private async handleUpdateTaskStatus(taskId: string, request: Request): Promise<Response> {
    const body = await request.json() as Record<string, unknown>;
    const newStatus = String(body.status || "");
    const result = String(body.result || "");

    const validStatuses: TaskStatus[] = ["pending", "assigned", "active", "completed", "failed", "canceled"];
    if (!validStatuses.includes(newStatus as TaskStatus)) {
      throw badRequest(`invalid status, must be one of: ${validStatuses.join(", ")}`);
    }

    const existing = this.sql.exec("SELECT task_id FROM tasks WHERE task_id=?", taskId).toArray();
    if (existing.length === 0) return json({ error: "task_not_found" }, { status: 404 });

    const now = new Date().toISOString();
    if (result) {
      this.sql.exec("UPDATE tasks SET status=?, result=?, updated_at=? WHERE task_id=?", newStatus, result, now, taskId);
    } else {
      this.sql.exec("UPDATE tasks SET status=?, updated_at=? WHERE task_id=?", newStatus, now, taskId);
    }

    // Check if all tasks are terminal → auto-complete mission
    const tasks = this.sql.exec("SELECT status FROM tasks").toArray() as any[];
    const allDone = tasks.every((t: any) => ["completed", "failed", "canceled"].includes(t.status));
    if (allDone && tasks.length > 0) {
      const anyFailed = tasks.some((t: any) => t.status === "failed");
      const missionStatus = anyFailed ? "failed" : "completed";
      this.sql.exec("UPDATE missions SET status=?, updated_at=?", missionStatus, now);
    }

    return json({ task_id: taskId, status: newStatus, updated_at: now });
  }

  private async handleComplete(request: Request): Promise<Response> {
    const body = await request.json() as Record<string, unknown>;
    const summary = String(body.summary || "");
    const now = new Date().toISOString();

    this.sql.exec("UPDATE missions SET status='completed', updated_at=?", now);
    if (summary) {
      this.sql.exec(
        "INSERT INTO mission_meta (key, value) VALUES ('completion_summary', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        summary
      );
    }

    return json({ status: "completed", updated_at: now });
  }

  private handleCancel(): Response {
    const now = new Date().toISOString();
    this.sql.exec("UPDATE missions SET status='canceled', updated_at=?", now);
    this.sql.exec("UPDATE tasks SET status='canceled', updated_at=? WHERE status NOT IN ('completed','failed','canceled')", now);
    return json({ status: "canceled", updated_at: now });
  }

  private ensureSchema(): void {
    if (this.schemaReady) return;

    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS missions (
        mission_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        goal TEXT NOT NULL DEFAULT '',
        lead_agent TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'planning',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        assigned_agent TEXT NOT NULL DEFAULT '',
        room_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        result TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS mission_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
      );
    `);

    this.schemaReady = true;
  }
}
