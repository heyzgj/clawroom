import { badRequest, json, normalizePlatformError } from "./worker_util";

type Env = {};

type AgentStatus = "online" | "offline" | "busy";

/**
 * TeamRegistryDurableObject: manages agent teams, agent registration,
 * capabilities, heartbeat-based online status, and task assignments (inbox).
 *
 * Single global instance (idFromName("global")).
 */
export class TeamRegistryDurableObject implements DurableObject {
  private sql: SqlStorage;
  private schemaReady = false;

  constructor(private state: DurableObjectState, private env: Env) {
    this.sql = state.storage.sql;
  }

  async fetch(request: Request): Promise<Response> {
    this.ensureSchema();
    const url = new URL(request.url);

    try {
      // --- Teams ---
      // POST /teams — create team
      if (request.method === "POST" && url.pathname === "/teams") {
        return this.handleCreateTeam(request);
      }
      // GET /teams — list teams
      if (request.method === "GET" && url.pathname === "/teams") {
        return this.handleListTeams();
      }
      // GET /teams/:team_id — team detail with agents
      const teamMatch = url.pathname.match(/^\/teams\/([^/]+)$/);
      if (request.method === "GET" && teamMatch) {
        return this.handleGetTeam(teamMatch[1]);
      }

      // --- Agents ---
      // POST /agents — register agent
      if (request.method === "POST" && url.pathname === "/agents") {
        return this.handleRegisterAgent(request);
      }
      // GET /agents — list agents (optionally filtered by team_id or status)
      if (request.method === "GET" && url.pathname === "/agents") {
        return this.handleListAgents(url);
      }
      // POST /agents/:agent_id/heartbeat — agent heartbeat
      const hbMatch = url.pathname.match(/^\/agents\/([^/]+)\/heartbeat$/);
      if (request.method === "POST" && hbMatch) {
        return this.handleHeartbeat(hbMatch[1]);
      }
      // GET /agents/:agent_id/inbox — agent's assigned tasks
      const inboxMatch = url.pathname.match(/^\/agents\/([^/]+)\/inbox$/);
      if (request.method === "GET" && inboxMatch) {
        return this.handleInbox(inboxMatch[1]);
      }

      // --- Assignments ---
      // POST /assignments — assign a mission task to an agent
      if (request.method === "POST" && url.pathname === "/assignments") {
        return this.handleAssign(request);
      }

      return json({ error: "not_found" }, { status: 404 });
    } catch (error: unknown) {
      if (error instanceof Response) return error;
      const normalized = normalizePlatformError(error);
      if (normalized) return normalized;
      return json({ error: "internal_error", message: String((error as Error)?.message || error) }, { status: 500 });
    }
  }

  // --- Team handlers ---

  private async handleCreateTeam(request: Request): Promise<Response> {
    const body = await request.json() as Record<string, unknown>;
    const teamId = String(body.team_id || `team_${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`);
    const name = String(body.name || "");
    const owner = String(body.owner || "");

    if (!name) throw badRequest("name is required");

    const now = new Date().toISOString();
    this.sql.exec(
      "INSERT INTO teams (team_id, name, owner, created_at) VALUES (?, ?, ?, ?)",
      teamId, name, owner, now
    );

    return json({ team_id: teamId, name, created_at: now }, { status: 201 });
  }

  private handleListTeams(): Response {
    const teams = this.sql.exec("SELECT * FROM teams ORDER BY created_at DESC").toArray();
    return json({ teams });
  }

  private handleGetTeam(teamId: string): Response {
    const team = this.sql.exec("SELECT * FROM teams WHERE team_id=?", teamId).toArray()[0];
    if (!team) return json({ error: "not_found" }, { status: 404 });

    const agents = this.sql.exec(
      "SELECT * FROM agents WHERE team_id=? ORDER BY registered_at DESC", teamId
    ).toArray();

    // Resolve online status based on heartbeat
    const STALE_MS = 5 * 60 * 1000;
    const now = Date.now();
    const resolved = agents.map((a: any) => ({
      ...a,
      resolved_status: this.resolveStatus(a, now, STALE_MS),
    }));

    return json({ team, agents: resolved });
  }

  // --- Agent handlers ---

  private async handleRegisterAgent(request: Request): Promise<Response> {
    const body = await request.json() as Record<string, unknown>;
    const agentId = String(body.agent_id || `agent_${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`);
    const name = String(body.name || "");
    const teamId = String(body.team_id || "");
    const capabilities = JSON.stringify(body.capabilities || []);
    const runtime = String(body.runtime || "");

    if (!name) throw badRequest("name is required");

    const now = new Date().toISOString();
    this.sql.exec(
      `INSERT INTO agents (agent_id, name, team_id, capabilities, runtime, status, registered_at, last_heartbeat_at)
       VALUES (?, ?, ?, ?, ?, 'online', ?, ?)
       ON CONFLICT(agent_id) DO UPDATE SET
         name=excluded.name, team_id=excluded.team_id, capabilities=excluded.capabilities,
         runtime=excluded.runtime, status='online', last_heartbeat_at=excluded.last_heartbeat_at`,
      agentId, name, teamId, capabilities, runtime, now, now
    );

    return json({ agent_id: agentId, status: "online", registered_at: now }, { status: 201 });
  }

  private handleListAgents(url: URL): Response {
    const teamId = url.searchParams.get("team_id");
    const status = url.searchParams.get("status");

    let query = "SELECT * FROM agents WHERE 1=1";
    const params: string[] = [];

    if (teamId) { query += " AND team_id=?"; params.push(teamId); }
    if (status) { query += " AND status=?"; params.push(status); }
    query += " ORDER BY registered_at DESC";

    const agents = this.sql.exec(query, ...params).toArray();

    const STALE_MS = 5 * 60 * 1000;
    const now = Date.now();
    const resolved = agents.map((a: any) => ({
      ...a,
      resolved_status: this.resolveStatus(a, now, STALE_MS),
    }));

    return json({ agents: resolved });
  }

  private handleHeartbeat(agentId: string): Response {
    const existing = this.sql.exec("SELECT agent_id FROM agents WHERE agent_id=?", agentId).toArray();
    if (existing.length === 0) return json({ error: "agent_not_found" }, { status: 404 });

    const now = new Date().toISOString();
    this.sql.exec(
      "UPDATE agents SET last_heartbeat_at=?, status='online' WHERE agent_id=?",
      now, agentId
    );

    return json({ agent_id: agentId, status: "online", last_heartbeat_at: now });
  }

  private handleInbox(agentId: string): Response {
    const assignments = this.sql.exec(
      "SELECT * FROM assignments WHERE agent_id=? AND status NOT IN ('completed','canceled') ORDER BY assigned_at DESC",
      agentId
    ).toArray();

    return json({ agent_id: agentId, assignments });
  }

  // --- Assignment handlers ---

  private async handleAssign(request: Request): Promise<Response> {
    const body = await request.json() as Record<string, unknown>;
    const agentId = String(body.agent_id || "");
    const missionId = String(body.mission_id || "");
    const taskId = String(body.task_id || "");
    const roomId = String(body.room_id || "");

    if (!agentId || !taskId) throw badRequest("agent_id and task_id are required");

    const assignmentId = `asgn_${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
    const now = new Date().toISOString();

    this.sql.exec(
      `INSERT INTO assignments (assignment_id, agent_id, mission_id, task_id, room_id, status, assigned_at)
       VALUES (?, ?, ?, ?, ?, 'assigned', ?)`,
      assignmentId, agentId, missionId, taskId, roomId, now
    );

    return json({ assignment_id: assignmentId, status: "assigned", assigned_at: now }, { status: 201 });
  }

  // --- Helpers ---

  private resolveStatus(agent: any, nowMs: number, staleMs: number): AgentStatus {
    if (agent.status === "busy") return "busy";
    const hb = agent.last_heartbeat_at ? new Date(agent.last_heartbeat_at).getTime() : 0;
    return (nowMs - hb) > staleMs ? "offline" : "online";
  }

  private ensureSchema(): void {
    if (this.schemaReady) return;

    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS teams (
        team_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        owner TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS agents (
        agent_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        team_id TEXT NOT NULL DEFAULT '',
        capabilities TEXT NOT NULL DEFAULT '[]',
        runtime TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'offline',
        registered_at TEXT NOT NULL,
        last_heartbeat_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS assignments (
        assignment_id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        mission_id TEXT NOT NULL DEFAULT '',
        task_id TEXT NOT NULL DEFAULT '',
        room_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'assigned',
        assigned_at TEXT NOT NULL
      );
    `);

    this.schemaReady = true;
  }
}
