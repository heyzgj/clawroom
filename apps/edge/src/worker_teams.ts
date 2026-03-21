import { badRequest, json, normalizePlatformError } from "./worker_util";

type Env = {
  MONITOR_ADMIN_TOKEN?: string;
  CLAWROOM_MONITOR_ADMIN_TOKEN?: string;
};

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
        return this.handleGetTeam(decodeURIComponent(teamMatch[1]));
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
      // GET /internal/agents/:agent_id — internal lookup used by edge coordination paths
      const internalAgentMatch = url.pathname.match(/^\/internal\/agents\/([^/]+)$/);
      if (request.method === "GET" && internalAgentMatch) {
        return this.handleInternalGetAgent(decodeURIComponent(internalAgentMatch[1]));
      }
      // POST /internal/agents/:agent_id/verify_inbox_token — internal token verification
      const verifyInboxMatch = url.pathname.match(/^\/internal\/agents\/([^/]+)\/verify_inbox_token$/);
      if (request.method === "POST" && verifyInboxMatch) {
        return this.handleVerifyInboxToken(decodeURIComponent(verifyInboxMatch[1]), request);
      }
      // POST /agents/:agent_id/heartbeat — agent heartbeat
      const hbMatch = url.pathname.match(/^\/agents\/([^/]+)\/heartbeat$/);
      if (request.method === "POST" && hbMatch) {
        return this.handleHeartbeat(decodeURIComponent(hbMatch[1]));
      }
      // GET /agents/:agent_id/assignments — agent's assigned tasks (legacy inbox path moved)
      const assignmentsMatch = url.pathname.match(/^\/agents\/([^/]+)\/assignments$/);
      if (request.method === "GET" && assignmentsMatch) {
        return this.handleAssignments(decodeURIComponent(assignmentsMatch[1]));
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

  private monitorToken(request: Request): string {
    const url = new URL(request.url);
    return (
      request.headers.get("x-monitor-token")
      || request.headers.get("X-Monitor-Token")
      || url.searchParams.get("admin_token")
      || ""
    ).trim();
  }

  private hasValidMonitorToken(request: Request): boolean {
    const expected = String(this.env.MONITOR_ADMIN_TOKEN || this.env.CLAWROOM_MONITOR_ADMIN_TOKEN || "").trim();
    if (!expected) return false;
    return this.monitorToken(request) === expected;
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
    const managedRunnerdUrl = String(body.managed_runnerd_url || "").trim();
    const providedInboxToken = String(body.inbox_token || "").trim();
    const issueInboxToken = body.issue_inbox_token === true;

    if (!name) throw badRequest("name is required");

    const existing = this.sql.exec(
      "SELECT inbox_token_digest FROM agents WHERE agent_id=? LIMIT 1",
      agentId,
    ).toArray()[0] as { inbox_token_digest?: string } | null;
    const hasAdmin = this.hasValidMonitorToken(request);
    let inboxToken = "";
    let inboxTokenDigest = "";
    if (providedInboxToken) {
      const providedDigest = await sha256Hex(providedInboxToken);
      if (existing?.inbox_token_digest) {
        if (String(existing.inbox_token_digest) === providedDigest) {
          inboxToken = providedInboxToken;
          inboxTokenDigest = providedDigest;
        } else if (hasAdmin) {
          inboxToken = providedInboxToken;
          inboxTokenDigest = providedDigest;
        } else {
          return json(
            { error: "unauthorized", message: "monitor admin token required to rotate inbox token" },
            { status: 401 },
          );
        }
      } else if (hasAdmin) {
        inboxToken = providedInboxToken;
        inboxTokenDigest = providedDigest;
      } else {
        return json(
          { error: "unauthorized", message: "monitor admin token required to bootstrap inbox token" },
          { status: 401 },
        );
      }
    } else if (existing?.inbox_token_digest) {
      inboxTokenDigest = String(existing.inbox_token_digest);
    } else if (issueInboxToken) {
      if (!hasAdmin) {
        return json(
          { error: "unauthorized", message: "monitor admin token required to issue inbox token" },
          { status: 401 },
        );
      }
      inboxToken = `agtok_${crypto.randomUUID().replace(/-/g, "")}`;
      inboxTokenDigest = await sha256Hex(inboxToken);
    }

    const now = new Date().toISOString();
    this.sql.exec(
      `INSERT INTO agents (agent_id, name, team_id, capabilities, runtime, managed_runnerd_url, status, registered_at, last_heartbeat_at, inbox_token_digest)
       VALUES (?, ?, ?, ?, ?, ?, 'online', ?, ?, ?)
       ON CONFLICT(agent_id) DO UPDATE SET
         name=excluded.name, team_id=excluded.team_id, capabilities=excluded.capabilities,
         runtime=excluded.runtime, managed_runnerd_url=excluded.managed_runnerd_url,
         status='online', last_heartbeat_at=excluded.last_heartbeat_at,
         inbox_token_digest=CASE
           WHEN excluded.inbox_token_digest != '' THEN excluded.inbox_token_digest
           ELSE agents.inbox_token_digest
         END`,
      agentId, name, teamId, capabilities, runtime, managedRunnerdUrl, now, now, inboxTokenDigest
    );

    return json({
      agent_id: agentId,
      status: "online",
      registered_at: now,
      ...(inboxToken ? { inbox_token: inboxToken } : {}),
    }, { status: 201 });
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

  private handleAssignments(agentId: string): Response {
    const assignments = this.sql.exec(
      "SELECT * FROM assignments WHERE agent_id=? AND status NOT IN ('completed','canceled') ORDER BY assigned_at DESC",
      agentId
    ).toArray();

    return json({ agent_id: agentId, assignments });
  }

  private handleInternalGetAgent(agentId: string): Response {
    const agent = this.sql.exec(
      "SELECT agent_id, name, team_id, capabilities, runtime, managed_runnerd_url, status, registered_at, last_heartbeat_at, inbox_token_digest FROM agents WHERE agent_id=? LIMIT 1",
      agentId,
    ).toArray()[0] as Record<string, unknown> | null;
    if (!agent) return json({ error: "agent_not_found" }, { status: 404 });

    return json({
      agent: {
        agent_id: String(agent.agent_id || ""),
        name: String(agent.name || ""),
        team_id: String(agent.team_id || ""),
        capabilities: JSON.parse(String(agent.capabilities || "[]")),
        runtime: String(agent.runtime || ""),
        managed_runnerd_url: String(agent.managed_runnerd_url || ""),
        status: String(agent.status || ""),
        registered_at: String(agent.registered_at || ""),
        last_heartbeat_at: String(agent.last_heartbeat_at || ""),
        has_inbox_token: Boolean(String(agent.inbox_token_digest || "")),
      },
    });
  }

  private async handleVerifyInboxToken(agentId: string, request: Request): Promise<Response> {
    const agent = this.sql.exec(
      "SELECT inbox_token_digest FROM agents WHERE agent_id=? LIMIT 1",
      agentId,
    ).toArray()[0] as { inbox_token_digest?: string } | null;
    if (!agent) return json({ error: "agent_not_found" }, { status: 404 });
    const body = await request.json() as Record<string, unknown>;
    const token = String(body.token || "").trim();
    if (!token) throw badRequest("token is required");
    const digest = await sha256Hex(token);
    if (!agent.inbox_token_digest || String(agent.inbox_token_digest) !== digest) {
      return json({ error: "unauthorized", message: "invalid inbox token" }, { status: 401 });
    }
    return json({ ok: true, agent_id: agentId });
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
        managed_runnerd_url TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'offline',
        registered_at TEXT NOT NULL,
        last_heartbeat_at TEXT NOT NULL,
        inbox_token_digest TEXT NOT NULL DEFAULT ''
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

    const agentCols = this.sql.exec("PRAGMA table_info(agents)").toArray().map((row: any) => String(row.name || ""));
    if (!agentCols.includes("inbox_token_digest")) {
      this.sql.exec("ALTER TABLE agents ADD COLUMN inbox_token_digest TEXT NOT NULL DEFAULT ''");
    }
    if (!agentCols.includes("managed_runnerd_url")) {
      this.sql.exec("ALTER TABLE agents ADD COLUMN managed_runnerd_url TEXT NOT NULL DEFAULT ''");
    }

    this.schemaReady = true;
  }
}

async function sha256Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", data);
  const bytes = new Uint8Array(digest);
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
}
