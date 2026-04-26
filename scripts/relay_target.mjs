#!/usr/bin/env node
/**
 * Inspect and switch the ClawRoom relay target without editing runtime code.
 *
 * Runtime precedence remains:
 *   --relay flag > CLAWROOM_RELAY env var > built-in DEFAULT_RELAY.
 */

import { execFileSync } from "node:child_process";

const TARGETS = {
  prod: "https://api.clawroom.cc",
  hosted: "https://clawroom-v3-relay.heyzgj.workers.dev",
  local: "http://127.0.0.1:8787",
};

function usage(status = 2) {
  process.stderr.write([
    "Usage:",
    "  node scripts/relay_target.mjs status",
    "  node scripts/relay_target.mjs probe [prod|hosted|local|URL]",
    "  node scripts/relay_target.mjs print-export [prod|hosted|local|URL]",
    "  node scripts/relay_target.mjs railway-set [prod|hosted|local|URL] [--skip-deploys]",
    "  node scripts/relay_target.mjs railway-clear",
    "",
    "Targets:",
    ...Object.entries(TARGETS).map(([name, url]) => `  ${name.padEnd(7)} ${url}`),
  ].join("\n") + "\n");
  process.exit(status);
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg.startsWith("--")) {
      out._.push(arg);
      continue;
    }
    const key = arg.slice(2);
    out[key] = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
  }
  return out;
}

function targetUrl(value) {
  const raw = String(value || "prod").trim();
  const url = TARGETS[raw] || raw;
  if (!/^https?:\/\/[^/\s]+/i.test(url)) usage();
  return url.replace(/\/$/, "");
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\"'\"'`)}'`;
}

function runOptional(cmd, args) {
  try {
    return execFileSync(cmd, args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  } catch (error) {
    return String(error.stderr || error.message || "");
  }
}

async function probe(base) {
  const relay = targetUrl(base);
  const url = `${relay}/threads/new?topic=relay-probe&goal=relay-probe`;
  const started = Date.now();
  try {
    const response = await fetch(url, {
      headers: { accept: "application/json", "user-agent": "clawroom-relay-target/1" },
      signal: AbortSignal.timeout(15_000),
    });
    const text = await response.text();
    let body = {};
    try {
      body = JSON.parse(text || "{}");
    } catch {
      body = { raw: text.slice(0, 120) };
    }
    const error = String(body.error || "");
    const looksLikeV3 =
      Boolean(body.thread_id) ||
      [
        "create_key_required",
        "invalid_create_key",
        "create_disabled",
        "relay_disabled",
        "create_rate_limited",
      ].includes(error);
    return {
      relay,
      status: response.status,
      ok: looksLikeV3 && error !== "not_found",
      error: error || null,
      looks_like_v3: looksLikeV3,
      latency_ms: Date.now() - started,
    };
  } catch (error) {
    return {
      relay,
      status: null,
      ok: false,
      error: String(error?.message || error),
      looks_like_v3: false,
      latency_ms: Date.now() - started,
    };
  }
}

function railwayRelayVar() {
  const out = runOptional("railway", ["variable", "list", "--json"]);
  try {
    const json = JSON.parse(out || "{}");
    return typeof json.CLAWROOM_RELAY === "string" ? json.CLAWROOM_RELAY : "";
  } catch {
    return "";
  }
}

async function status() {
  const probes = [];
  for (const target of ["prod", "hosted"]) probes.push(await probe(target));
  const localOverride = String(process.env.CLAWROOM_RELAY || "").trim();
  const railwayOverride = railwayRelayVar();
  console.log(JSON.stringify({
    default_relay: TARGETS.prod,
    local_env_clawroom_relay: localOverride || null,
    railway_clawroom_relay: railwayOverride || null,
    probes,
    recommendation: probes.find((item) => item.relay === TARGETS.prod)?.ok
      ? "prod-ready"
      : "prod-domain-not-ready-use-hosted-or-local-for-e2e",
  }, null, 2));
}

function railwaySet(url, skipDeploys) {
  const args = ["variable", "set", `CLAWROOM_RELAY=${targetUrl(url)}`];
  if (skipDeploys) args.push("--skip-deploys");
  execFileSync("railway", args, { stdio: "inherit" });
}

function railwayClear() {
  execFileSync("railway", ["variable", "delete", "CLAWROOM_RELAY"], { stdio: "inherit" });
}

const args = parseArgs(process.argv.slice(2));
const command = args._[0] || "status";
if (args.help || command === "help") usage(0);

if (command === "status") {
  await status();
} else if (command === "probe") {
  console.log(JSON.stringify(await probe(args._[1] || "prod"), null, 2));
} else if (command === "print-export") {
  console.log(`export CLAWROOM_RELAY=${shellQuote(targetUrl(args._[1] || "prod"))}`);
} else if (command === "railway-set") {
  railwaySet(args._[1] || "prod", Boolean(args["skip-deploys"]));
} else if (command === "railway-clear") {
  railwayClear();
} else {
  usage();
}
