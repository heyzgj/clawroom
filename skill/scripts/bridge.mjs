#!/usr/bin/env node
/**
 * ClawRoom Bridge - Node.js / ESM
 * ====================================
 *
 * Transport only:
 *   ClawRoom Durable Object relay <-> this bridge <-> OpenClaw Gateway WS
 *
 * The LLM decides what to say. This process owns HTTP, turn handling,
 * idempotency, runtime heartbeat, and owner notification.
 */

import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import {
  createHash,
  createPrivateKey,
  createPublicKey,
  randomUUID,
  sign as cryptoSign,
} from "node:crypto";

const VERSION = "0.3.24";
const FEATURES = [
  "owner-reply-url",
  "telegram-force-reply",
  "openclaw-state-dir-fallback",
  "contextual-offer-floor",
  "unsupported-date-commitment",
  "approval-context-carryover",
  "deterministic-date-confirmation",
  "mixed-approval-parser",
  "required-interaction-guard",
  "paid-interaction-guard",
  "price-floor-component-amounts",
  "no-deal-close-guard",
  "no-deal-mandate-close",
  "no-deal-mandate-reply",
];
const DEFAULT_RELAY = "https://api.clawroom.cc";
const POLL_WAIT_SECONDS = 20;
const HEARTBEAT_MS = 15_000;
const AGENT_TIMEOUT = Math.max(30_000, Number(process.env.CLAWROOM_AGENT_TIMEOUT_MS || 240_000) || 240_000);
const CHALLENGE_WAIT = 5_000;
const OWNER_REPLY_TTL_SECONDS = 30 * 60;
const MAX_AGENT_MESSAGE_CHARS = 2000;
const FATAL_RELAY_STATUSES = new Set([401, 403, 404, 410]);
const QUOTA_BACKOFF_MS = 60_000;
const RELAY_ERROR_BACKOFF_MS = 10_000;
const CLAWROOM_URL_PATTERN = /\bhttps?:\/\/[^\s"'<>]*(?:clawroom\.cc|workers\.dev)[^\s"'<>]*/gi;

function parseArgs(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith("--")) continue;
    const key = argv[i].slice(2);
    const value = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
    result[key] = value;
  }
  return result;
}

const args = parseArgs(process.argv.slice(2));

const relayBase = String(args.relay || process.env.CLAWROOM_RELAY || DEFAULT_RELAY).replace(/\/$/, "");
const threadId = String(args.thread || args["thread-id"] || "");
const token = String(args.token || "");
const role = String(args.role || "");
const ownerCtx = String(args.context || "");
const goal = String(args.goal || "");
const minMessages = Math.max(0, Number(args["min-messages"] || process.env.CLAWROOM_MIN_MESSAGES || 0) || 0);
const agentId = String(args["agent-id"] || process.env.CLAWROOM_AGENT_ID || "clawroom-relay");
const sessionKey = String(args["session-key"] || `agent:${agentId}:clawroom:${threadId}:${role}`);
const openClawStateDir = resolve(String(process.env.OPENCLAW_STATE_DIR || process.env.CLAWDBOT_STATE_DIR || join(homedir(), ".openclaw")));
const defaultStateDir = process.env.OPENCLAW_STATE_DIR ? join(openClawStateDir, "clawroom") : join(homedir(), ".clawroom");
const stateDir = resolve(String(args["state-dir"] || process.env.CLAWROOM_STATE_DIR || defaultStateDir));
const notifyKind = String(args["notify-kind"] || process.env.CLAWROOM_NOTIFY_KIND || "telegram");
const explicitTelegramChatId = String(args["telegram-chat-id"] || process.env.TG_CHAT_ID || process.env.TELEGRAM_CHAT_ID || "").trim();

if (!threadId || !token || !["host", "guest"].includes(role) || !ownerCtx || !goal) {
  console.error(
    "Usage: node bridge.mjs --thread <id> --token <token> --role host|guest " +
    "--context <owner context> --goal <goal> " +
    "[--relay https://...] [--agent-id clawroom-relay] [--state-dir /path] " +
    "[--telegram-chat-id 123]"
  );
  process.exit(1);
}

function sanitizePromptText(value) {
  return String(value || "").replace(CLAWROOM_URL_PATTERN, "[ClawRoom invite]");
}

const promptOwnerCtx = sanitizePromptText(ownerCtx);
const promptGoal = sanitizePromptText(goal);

const statePath = join(stateDir, `${threadId}-${role}.state.json`);
const runtimeStatePath = join(stateDir, `${threadId}-${role}.runtime-state.json`);

function log(message) {
  console.log(`[bridge:${threadId}:${role}] ${message}`);
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return {};
  }
}

function openClawPath(...parts) {
  return join(openClawStateDir, ...parts);
}

function writeJsonAtomic(path, payload) {
  mkdirSync(join(path, ".."), { recursive: true });
  const tmp = `${path}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  try {
    chmodSync(tmp, 0o600);
  } catch {
    // Best-effort hardening; some filesystems reject chmod.
  }
  renameSync(tmp, path);
}

function sha(value) {
  return createHash("sha256").update(String(value)).digest("hex").slice(0, 24);
}

function chatIdHash(chatId) {
  return createHash("sha256").update(String(chatId)).digest("hex").slice(0, 16);
}

function chatIdSuffix(chatId) {
  const value = String(chatId || "");
  return value ? value.slice(-4) : "";
}

function idempotencyKey(...parts) {
  return parts.map((part) => String(part).replace(/[^a-zA-Z0-9_.:-]/g, "_")).join(":").slice(0, 180);
}

async function delay(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

function isFatalRelayError(error) {
  return FATAL_RELAY_STATUSES.has(Number(error?.status));
}

function relayBackoffMs(error) {
  const status = Number(error?.status);
  const message = String(error?.message || "");
  if (status === 429 || message.includes("Exceeded allowed volume")) return QUOTA_BACKOFF_MS;
  if (status >= 500) return RELAY_ERROR_BACKOFF_MS;
  return 5_000;
}

function negotiationMessageCount(rows) {
  return rows.filter((row) => row?.kind === "message").length;
}

function parseMandates(text) {
  const mandates = {};
  for (const line of String(text || "").split("\n")) {
    const ceilingMatch = line.match(/^\s*MANDATE\s*:\s*(?:budget_ceiling_jpy|budget_ceiling|ceiling_jpy)\s*=\s*([0-9][0-9,]*)\s*$/i);
    if (ceilingMatch) mandates.budget_ceiling_jpy = Number(ceilingMatch[1].replace(/,/g, ""));

    const floorMatch = line.match(/^\s*MANDATE\s*:\s*(?:price_floor_jpy|minimum_price_jpy|min_price_jpy|floor_jpy)\s*=\s*([0-9][0-9,]*)\s*$/i);
    if (floorMatch) mandates.price_floor_jpy = Number(floorMatch[1].replace(/,/g, ""));

    const ceilingUsdMatch = line.match(/^\s*MANDATE\s*:\s*(?:budget_ceiling_usd|ceiling_usd)\s*=\s*([0-9][0-9,]*(?:\.\d+)?)\s*$/i);
    if (ceilingUsdMatch) mandates.budget_ceiling_usd = Number(ceilingUsdMatch[1].replace(/,/g, ""));

    const floorUsdMatch = line.match(/^\s*MANDATE\s*:\s*(?:price_floor_usd|minimum_price_usd|min_price_usd|floor_usd)\s*=\s*([0-9][0-9,]*(?:\.\d+)?)\s*$/i);
    if (floorUsdMatch) mandates.price_floor_usd = Number(floorUsdMatch[1].replace(/,/g, ""));

    const amounts = parseJpyAmounts(line);
    if (amounts.length && !mandates.budget_ceiling_jpy && /ceiling|not above|do not exceed|don't exceed|max(?:imum)?/.test(line)) {
      mandates.budget_ceiling_jpy = Math.max(...amounts);
    }
    const floorPattern = /floor|bottom|lowest|min(?:imum)?/;
    if (amounts.length && !mandates.price_floor_jpy && floorPattern.test(line)) {
      mandates.price_floor_jpy = Math.max(...amounts);
    }

    const usdAmounts = parseUsdAmounts(line);
    if (usdAmounts.length && !mandates.budget_ceiling_usd && /ceiling|not above|do not exceed|don't exceed|max(?:imum)?/.test(line)) {
      mandates.budget_ceiling_usd = Math.max(...usdAmounts);
    }
    if (usdAmounts.length && !mandates.price_floor_usd && floorPattern.test(line)) {
      mandates.price_floor_usd = Math.max(...usdAmounts);
    }
  }
  return mandates;
}

function parseJpyAmounts(text) {
  const source = String(text || "");
  const amounts = [];
  const patterns = [
    /\u00a5\s*([0-9][0-9,]*(?:\.\d+)?)\s*([kK])?/g,
    /(?:JPY|jpy|yen)\s*([0-9][0-9,]*(?:\.\d+)?)\s*([kK])?/g,
    /([0-9][0-9,]*(?:\.\d+)?)\s*([kK])?\s*(?:JPY|jpy|yen)/g,
  ];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      const value = Number(String(match[1] || "").replace(/,/g, ""));
      if (!Number.isFinite(value)) continue;
      amounts.push(Math.round(value * (match[2] ? 1000 : 1)));
    }
  }
  return amounts;
}

function parseUsdAmounts(text) {
  return parseUsdAmountMatches(text).map((match) => match.amount);
}

function parseUsdAmountMatches(text) {
  const source = String(text || "");
  const matches = [];
  const patterns = [
    /\$\s*([0-9][0-9,]*(?:\.\d+)?)\s*([kK])?/g,
    /(?:USD|usd|dollars?)\s*([0-9][0-9,]*(?:\.\d+)?)\s*([kK])?/g,
    /([0-9][0-9,]*(?:\.\d+)?)\s*([kK])?\s*(?:USD|usd|dollars?)/g,
  ];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      const value = Number(String(match[1] || "").replace(/,/g, ""));
      if (!Number.isFinite(value)) continue;
      matches.push({
        amount: value * (match[2] ? 1000 : 1),
        start: match.index,
        end: match.index + match[0].length,
        raw: match[0],
      });
    }
  }
  const deduped = [];
  for (const match of matches.sort((a, b) => a.start - b.start || b.end - a.end)) {
    const overlaps = deduped.some(
      (existing) =>
        existing.amount === match.amount &&
        match.start < existing.end &&
        existing.start < match.end
    );
    if (!overlaps) deduped.push(match);
  }
  return deduped;
}

const CONTEXT_KEYWORD_STOPWORDS = new Set([
  "about",
  "above",
  "accept",
  "agent",
  "agreed",
  "all",
  "also",
  "available",
  "budget",
  "buyer",
  "charge",
  "confirm",
  "context",
  "contractor",
  "counter",
  "deadline",
  "deliver",
  "delivered",
  "delivery",
  "dollars",
  "each",
  "fee",
  "fees",
  "final",
  "floor",
  "firm",
  "from",
  "goal",
  "included",
  "including",
  "maximum",
  "minimum",
  "offer",
  "offered",
  "owner",
  "price",
  "priced",
  "print",
  "provider",
  "quote",
  "quoted",
  "room",
  "seller",
  "service",
  "ship",
  "shipment",
  "shipped",
  "shipping",
  "standard",
  "sticker",
  "stickers",
  "shop",
  "terms",
  "their",
  "there",
  "these",
  "thing",
  "this",
  "those",
  "total",
  "with",
  "without",
  "inch",
  "inches",
  "unit",
  "units",
  "vendor",
]);

function distinctiveContextMatches(matches) {
  return matches.filter((keyword) => keyword !== "matte");
}

function normalizedContextKeywords(text) {
  const words = String(text || "")
    .toLowerCase()
    .replace(/\$[\d,]+(?:\.\d+)?/g, " ")
    .replace(/\b\d+(?:\.\d+)?\b/g, " ")
    .match(/[a-z][a-z-]{3,}/g) || [];
  return [...new Set(words.filter((word) => !CONTEXT_KEYWORD_STOPWORDS.has(word)))];
}

function sellerishContext(text) {
  return /\b(?:we|i|our|my)\s+(?:can\s+)?(?:offer|sell|provide|quote|charge)\b/i.test(text) ||
    (/\b(?:i am|i'm|we are|we're)\s+(?:the\s+)?(?:[a-z]+\s+){0,3}(?:seller|vendor|provider|contractor|print shop|shop)\b/i.test(text) && parseUsdAmounts(text).length) ||
    /\b(?:our|my)\s+(?:quoted\s+)?(?:price|rate|fee)\b/i.test(text) ||
    /\b(?:price|rate|fee)\s+is\b/i.test(text) ||
    /\b(?:seller|vendor|provider|agency|freelancer|contractor|print shop|shop)\s+(?:offering|offers|quotes|charges|can provide)\b/i.test(text) ||
    /\boffering\s*:/i.test(text);
}

function nearestBoundaryBefore(source, index, boundaries) {
  for (let cursor = Math.max(0, index - 1); cursor >= 0; cursor -= 1) {
    if (boundaries.has(source[cursor])) return cursor;
  }
  return -1;
}

function nearestBoundaryAfter(source, index, boundaries) {
  for (let cursor = Math.max(0, index); cursor < source.length; cursor += 1) {
    if (boundaries.has(source[cursor])) return cursor;
  }
  return -1;
}

function boundedPriceText(source, match, boundaries) {
  const startBoundary = nearestBoundaryBefore(source, match.start, boundaries);
  const endBoundary = nearestBoundaryAfter(source, match.end, boundaries);
  const start = startBoundary < 0 ? 0 : startBoundary + 1;
  const end = endBoundary < 0 ? source.length : endBoundary;
  return source.slice(start, end).replace(/\s+/g, " ").trim();
}

function pricedContextClauses(text) {
  const source = String(text || "");
  const localBoundaries = new Set([".", ";", "!", "?", "\n", ","]);
  const sentenceBoundaries = new Set([".", ";", "!", "?", "\n"]);
  return parseUsdAmountMatches(source)
    .map((match) => {
      let clauseText = boundedPriceText(source, match, localBoundaries);
      let keywords = normalizedContextKeywords(clauseText);
      if (!keywords.length) {
        clauseText = boundedPriceText(source, match, sentenceBoundaries);
        keywords = normalizedContextKeywords(clauseText);
      }
      return { amount: match.amount, keywords, text: clauseText };
    })
    .filter((clause) => clause.keywords.length);
}

function contextualUsdOfferFloors(text) {
  const source = String(text || "");
  if (!sellerishContext(source)) return [];
  const offers = [];
  const seen = new Set();
  for (const clause of pricedContextClauses(source)) {
    const key = `${clause.amount}:${clause.keywords.join(",")}`;
    if (seen.has(key)) continue;
    seen.add(key);
    offers.push(clause);
  }
  return offers;
}

function contextualOfferFloorViolation(text, action) {
  if (bridgeState.mandate_approvals?.contextual_offer_floor_usd) return null;
  if (obviousRejection(text)) return null;
  let strongest = null;
  const offers = contextualUsdOfferFloors(ownerCtx);
  for (const candidate of pricedContextClauses(text)) {
    const amount = candidate.amount;
    const candidateKeywords = new Set(candidate.keywords);
    if (!candidateKeywords.size) continue;
    const authorizedLowerOffer = offers.some((offer) => {
      if (offer.amount > amount) return false;
      const matches = offer.keywords.filter((keyword) => candidateKeywords.has(keyword));
      const distinctiveMatches = distinctiveContextMatches(matches);
      return matches.length >= 2 || distinctiveMatches.length >= 1;
    });
    if (authorizedLowerOffer) continue;
    for (const offer of offers) {
      if (amount >= offer.amount) continue;
      const matches = offer.keywords.filter((keyword) => candidateKeywords.has(keyword));
      const distinctiveMatches = distinctiveContextMatches(matches);
      if (matches.length < 2 && distinctiveMatches.length < 1) continue;
      const score = matches.length + (distinctiveMatches.length ? 2 : 0);
      if (!strongest || score > strongest.score || (score === strongest.score && offer.amount > strongest.floor)) {
        strongest = {
          kind: "contextual_offer_floor_usd",
          floor: offer.amount,
          amount,
          currency: "USD",
          action,
          descriptor: matches.slice(0, 4).join(" "),
          score,
        };
      }
    }
  }
  return strongest ? { ...strongest, score: undefined } : null;
}

function maxJpyAmount(text) {
  const amounts = parseJpyAmounts(text);
  return amounts.length ? Math.max(...amounts) : null;
}

function minJpyAmount(text) {
  const amounts = parseJpyAmounts(text);
  return amounts.length ? Math.min(...amounts) : null;
}

function maxUsdAmount(text) {
  const amounts = parseUsdAmounts(text);
  return amounts.length ? Math.max(...amounts) : null;
}

function textWindow(source, start, end, before = 80, after = 100) {
  return source.slice(Math.max(0, start - before), Math.min(source.length, end + after));
}

function totalUsdAmountAtOrAbove(text, floor) {
  const source = String(text || "");
  return parseUsdAmountMatches(source).some((match) => {
    if (match.amount < floor) return false;
    return /\btotal\b/i.test(textWindow(source, match.start, match.end, 80, 80));
  });
}

function usdFloorViolationAmount(text, floor) {
  const source = String(text || "");
  const matches = parseUsdAmountMatches(source);
  if (!matches.length) return null;
  if (totalUsdAmountAtOrAbove(source, floor)) return null;
  const hasMainAmountAtOrAboveFloor = matches.some((match) => match.amount >= floor);
  const componentPattern =
    /\b(?:plus|add-?on|additional|extra|fee|meeting|call|kickoff|deposit|shipping|tax|included|includes)\b/i;
  const lower = matches.find((match) => {
    if (match.amount >= floor) return false;
    if (hasMainAmountAtOrAboveFloor && componentPattern.test(textWindow(source, match.start, match.end))) {
      return false;
    }
    return true;
  });
  return lower?.amount || null;
}

function mandatePromptLines(useAskOwner = false) {
  const lines = [];
  if (mandates.budget_ceiling_usd) {
    lines.push(
      `Mandate: do not accept or propose above ${mandates.budget_ceiling_usd} USD unless the owner explicitly approves.${useAskOwner ? " Use ASK_OWNER before exceeding it." : ""}`
    );
  }
  if (mandates.price_floor_usd) {
    lines.push(
      `Mandate: do not accept or propose below ${mandates.price_floor_usd} USD unless the owner explicitly approves.${useAskOwner ? " Use ASK_OWNER before going below it." : ""}`
    );
  }
  if (mandates.budget_ceiling_jpy) {
    lines.push(
      `Mandate: do not accept or propose above ${mandates.budget_ceiling_jpy} JPY unless the owner explicitly approves.${useAskOwner ? " Use ASK_OWNER before exceeding it." : ""}`
    );
  }
  if (mandates.price_floor_jpy) {
    lines.push(
      `Mandate: do not accept or propose below ${mandates.price_floor_jpy} JPY unless the owner explicitly approves.${useAskOwner ? " Use ASK_OWNER before going below it." : ""}`
    );
  }
  return lines;
}

function roleBoundaryLines() {
  const otherRole = role === "host" ? "guest" : "host";
  const lines = [
    `You are the ${role} side. The other agent is the ${otherRole} side.`,
    "Owner context is your only source for what your owner can offer, accept, pay, charge, deliver, or approve.",
    "The shared Goal is room context only. Do not treat it as your owner's budget, price floor, deadline, capability, or approval.",
    "If Owner context and Goal conflict, follow Owner context for your side.",
    "ClawRoom negotiates terms only. Do not imply payment was authorized, finalized, charged, or processed; use invoice or next-step language unless the owner explicitly gave a payment instruction.",
    "If the counterpart says a required term is unconfirmed or needs their owner, ask the counterpart to confirm it instead of asking your owner to approve it.",
  ];
  if (role === "guest") {
    lines.push("As guest, do not adopt the host buyer's budget, deadline, or requested terms as your own offer.");
    lines.push("As guest, never promise the host's requested deadline, delivery date, or shipping date unless Owner context explicitly includes that date. If the date is missing, say you need to confirm it first.");
  }
  if (sellerishContext(ownerCtx)) {
    lines.push("If your owner gave prices for items or services, treat those as authorized offer terms. Do not accept a lower price for the same item or service unless your owner explicitly approves.");
  }
  if (requiredInteractionTerm()) {
    lines.push("If your owner requires a call, meeting, or kickoff step, do not waive, omit, or mark it optional unless your owner explicitly approves.");
  }
  return lines;
}

function ownerReplyApprovesExcess(text) {
  const source = String(text || "");
  if (/\b(?:reject|rejected|decline|declined|do not approve|don't approve|not approved|cannot approve|can't approve)\b/i.test(source)) {
    return false;
  }
  return /\b(yes|approve|approved|authorize|authorized|ok|okay)\b/i.test(source);
}

function obviousRejection(text) {
  return /\b(cannot|can't|do not|don't|not above|reject|rejected|decline|ceiling|above the ceiling|over budget)\b/i.test(String(text || ""));
}

const DATE_CLAIM_PATTERNS = [
  /\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?\b/gi,
  /\b\d{4}-\d{1,2}-\d{1,2}\b/g,
  /\b\d{1,2}\/\d{1,2}(?:\/\d{2,4})?\b/g,
];

function normalizedDateClaim(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/\b(\d{1,2})(?:st|nd|rd|th)\b/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

function dateClaims(text) {
  const source = String(text || "");
  const claims = [];
  for (const pattern of DATE_CLAIM_PATTERNS) {
    pattern.lastIndex = 0;
    for (const match of source.matchAll(pattern)) {
      claims.push({
        raw: match[0],
        normalized: normalizedDateClaim(match[0]),
        start: match.index,
        end: match.index + match[0].length,
      });
    }
  }
  const seen = new Set();
  return claims.filter((claim) => {
    if (seen.has(claim.normalized)) return false;
    seen.add(claim.normalized);
    return true;
  });
}

function containsDateClaim(text, claim) {
  return normalizedDateClaim(text).includes(claim.normalized);
}

function approvedUnsupportedDateClaims() {
  const approval = bridgeState.mandate_approvals?.unsupported_date_commitment;
  if (!approval) return [];
  if (approval === true) return [{ raw: "", normalized: "*" }];
  if (Array.isArray(approval.dates)) {
    return approval.dates
      .map((date) => ({
        raw: String(date?.raw || date || ""),
        normalized: normalizedDateClaim(date?.normalized || date?.raw || date || ""),
      }))
      .filter((date) => date.normalized);
  }
  if (approval.date) {
    return [{
      raw: String(approval.date || ""),
      normalized: normalizedDateClaim(approval.normalized || approval.date || ""),
    }].filter((date) => date.normalized);
  }
  return [];
}

function isDateClaimApproved(claim) {
  return approvedUnsupportedDateClaims().some((date) => date.normalized === "*" || date.normalized === claim.normalized);
}

function dateClaimWindow(text, claim) {
  const source = String(text || "");
  const start = Math.max(0, claim.start - 40);
  const end = Math.min(source.length, claim.end + 40);
  return source.slice(start, end);
}

function commitsToDate(text, claim) {
  const window = dateClaimWindow(text, claim);
  if (/\?/.test(window) && /\b(?:can|could|would|is|does|will)\b/i.test(window)) return false;
  if (/\b(?:need|needs|have|has)\s+to\s+(?:confirm|check|ask|verify)\b/i.test(window)) return false;
  if (/\b(?:not\s+sure|cannot\s+confirm|can't\s+confirm|unconfirmed|tentative)\b/i.test(window)) return false;
  return /\b(?:by|before|on|delivered|delivery|ship|ships|shipped|arrive|arrives|ready|complete|completed|finish|finished|works|can\s+do)\b/i.test(window);
}

function unsupportedDateCommitmentViolation(text, action) {
  if (role !== "guest") return null;
  for (const claim of dateClaims(text)) {
    if (containsDateClaim(ownerCtx, claim)) continue;
    if (isDateClaimApproved(claim)) continue;
    if (!commitsToDate(text, claim)) continue;
    return {
      kind: "unsupported_date_commitment",
      date: claim.raw,
      normalized_date: claim.normalized,
      action,
    };
  }
  return null;
}

function peerRequestsUnsupportedDateConfirmation(text) {
  if (role !== "guest") return null;
  const source = String(text || "");
  if (!/\b(?:confirm|guarantee|need|needs|require|requires|arrival|deliver(?:y|ed)?|ship(?:ping|ped)?|by)\b/i.test(source)) {
    return null;
  }
  for (const claim of dateClaims(source)) {
    if (containsDateClaim(ownerCtx, claim)) continue;
    if (isDateClaimApproved(claim)) continue;
    const window = dateClaimWindow(source, claim);
    if (!/\b(?:confirm|guarantee|need|needs|require|requires|can you|could you|please|must|arrival|deliver(?:y|ed)?|ship(?:ping|ped)?)\b/i.test(window)) {
      continue;
    }
    return {
      kind: "unsupported_date_commitment",
      date: claim.raw,
      normalized_date: claim.normalized,
      action: "reply",
    };
  }
  return null;
}

function peerDateConfirmationQuestion(violation) {
  return [
    `The buyer needs ${violation.date} delivery confirmed, but your instructions did not confirm that date.`,
    "Approve that date, reject it, or give the date you can actually support.",
  ].join(" ");
}

function peerNeedsOwnOwnerConfirmation(text) {
  const source = String(text || "");
  if (!/\b(?:owner|boss|client|manager)\b/i.test(source)) return false;
  if (!/\b(?:confirm|check|ask|approve|approval|before|pending|need|needs)\b/i.test(source)) return false;
  return dateClaims(source).some((claim) => !containsDateClaim(ownerCtx, claim));
}

function asksOurOwnerToProceedDespitePeerUnconfirmed(parsed, peerText) {
  if (!parsed?.ask_owner) return false;
  if (!peerNeedsOwnOwnerConfirmation(peerText)) return false;
  return /\b(?:approve|proceed|accept|finali[sz]e|go ahead|within budget|fits|works)\b/i.test(String(parsed.question || ""));
}

function counterpartConfirmationReply(peerText) {
  const claim = dateClaims(peerText).find((item) => !containsDateClaim(ownerCtx, item));
  if (claim) {
    return `Please confirm ${claim.raw} with your owner before we proceed.`;
  }
  return "Please confirm that with your owner before we proceed.";
}

function requiredInteractionTerm() {
  const source = String(ownerCtx || "");
  if (!/\b(?:call|meeting|kickoff)\b/i.test(source)) return null;
  if (/\b(?:optional|free|no charge|included at no extra)\b/i.test(source)) return null;
  const explicitRequirement = /\b(?:require|requires|required|must|need|needs)\b/i.test(source);
  const paidInteraction = /\b(?:call|meeting|kickoff)\b[\s\S]{0,80}\b(?:USD|\$|dollars?)\b|\b(?:USD|\$|dollars?)\b[\s\S]{0,80}\b(?:call|meeting|kickoff)\b/i.test(source);
  if (!explicitRequirement && !paidInteraction) return null;
  const amounts = parseUsdAmounts(source);
  const total = amounts.length >= 2 ? amounts.reduce((sum, amount) => sum + amount, 0) : 0;
  const label = /\bkickoff\b/i.test(source)
    ? "kickoff call"
    : /\bmeeting\b/i.test(source)
      ? "meeting"
      : "call";
  return { label, total };
}

function requiredInteractionViolation(text, action) {
  if (bridgeState.mandate_approvals?.required_interaction_removed) return null;
  const term = requiredInteractionTerm();
  if (!term) return null;
  const source = String(text || "");
  if (noDealOutcome(source)) return null;
  const removesInteraction =
    /\bno\s+(?:extra\s+)?(?:calls?|meetings?|kickoffs?)\b/i.test(source) ||
    /\b(?:without|skip|waive|drop)\b.{0,40}\b(?:call|meeting|kickoff)\b/i.test(source) ||
    /\b(?:call|meeting|kickoff)\b.{0,40}\b(?:not needed|not required|optional|waived|skipped|dropped)\b/i.test(source);
  if (removesInteraction) {
    return {
      kind: "required_interaction_removed",
      label: term.label,
      action,
    };
  }
  const amount = maxUsdAmount(source);
  const mentionsInteraction = /\b(?:call|meeting|kickoff)\b/i.test(source);
  const acceptsLowerAmount =
    /\b(?:confirm|confirmed|accept|accepted|agree|agreed|works|ready|close|deal|total)\b/i.test(source);
  if (term.total && amount && amount < term.total && (!mentionsInteraction || acceptsLowerAmount)) {
    return {
      kind: "required_interaction_removed",
      label: term.label,
      amount,
      required_total: term.total,
      action,
    };
  }
  return null;
}

function noDealClose(text, action) {
  return action === "close" && noDealOutcome(text);
}

function noDealOutcome(text) {
  return /\b(?:no agreement|no deal|cannot align|incompatible|walk away|not proceed|terms change|terms conflict|outside constraints|no deal made|exceeds (?:our|my) budget)\b/i.test(String(text || ""));
}

function ownerSafeQuestionText(text) {
  return String(text || "")
    .replace(/\bauthori[sz]e payment\b/gi, "confirm whether to proceed with these terms")
    .replace(/\bfinali[sz]e payment\b/gi, "finalize next steps")
    .replace(/\bproceed with payment\b/gi, "proceed with the next step")
    .replace(/\bpayment details\b/gi, "invoice or next-step details")
    .trim();
}

function approvedContextLines() {
  const lines = [];
  const dateApprovals = approvedUnsupportedDateClaims().filter((date) => date.normalized !== "*");
  for (const date of dateApprovals) {
    lines.push(`Owner-approved exception: ${date.raw} is authorized for this room. You may confirm this date without asking again.`);
  }
  if (bridgeState.mandate_approvals?.contextual_offer_floor_usd) {
    lines.push("Owner-approved exception: the owner approved the quoted price exception for this room.");
  }
  if (bridgeState.mandate_approvals?.required_interaction_removed) {
    lines.push("Owner-approved exception: the owner approved removing the required call, meeting, or kickoff step for this room.");
  }
  if (bridgeState.mandate_approvals?.budget_ceiling_usd) {
    lines.push("Owner-approved exception: the owner approved the USD budget exception for this room.");
  }
  if (bridgeState.mandate_approvals?.price_floor_usd) {
    lines.push("Owner-approved exception: the owner approved the USD price-floor exception for this room.");
  }
  if (bridgeState.mandate_approvals?.budget_ceiling_jpy) {
    lines.push("Owner-approved exception: the owner approved the JPY budget exception for this room.");
  }
  if (bridgeState.mandate_approvals?.price_floor_jpy) {
    lines.push("Owner-approved exception: the owner approved the JPY price-floor exception for this room.");
  }
  const latestOwnerReply = String(bridgeState.latest_owner_reply_text || "").trim();
  if (latestOwnerReply) {
    lines.push(`Latest owner instruction: ${sanitizePromptText(latestOwnerReply)}`);
  }
  return lines;
}

function uniqueDateClaims(...texts) {
  const byNormalized = new Map();
  for (const text of texts) {
    for (const claim of dateClaims(text)) {
      if (!containsDateClaim(ownerCtx, claim) && !byNormalized.has(claim.normalized)) {
        byNormalized.set(claim.normalized, { raw: claim.raw, normalized: claim.normalized });
      }
    }
  }
  return [...byNormalized.values()];
}

function approvedMandateFromOwnerReply(waiting, ownerReplyText) {
  const violation = waiting?.mandate_violation || null;
  if (violation?.kind) return violation.kind;

  const approvedDates = uniqueDateClaims(
    ownerReplyText,
    waiting?.question_text || "",
    waiting?.blocked_reply_text || "",
  );
  if (role === "guest" && approvedDates.length) return "unsupported_date_commitment";

  return null;
}

function recordMandateApproval(waiting, ownerReplyText) {
  if (!ownerReplyApprovesExcess(ownerReplyText)) return;
  const approvedMandate = approvedMandateFromOwnerReply(waiting, ownerReplyText);
  if (!approvedMandate) return;

  bridgeState.mandate_approvals ||= {};
  const approval = {
    question_id: waiting.question_id,
    approved_at: new Date().toISOString(),
  };
  if (approvedMandate === "unsupported_date_commitment") {
    const dates = uniqueDateClaims(
      waiting?.mandate_violation?.date || "",
      ownerReplyText,
      waiting?.question_text || "",
      waiting?.blocked_reply_text || "",
    );
    bridgeState.mandate_approvals[approvedMandate] = {
      ...approval,
      dates,
    };
    return;
  }
  bridgeState.mandate_approvals[approvedMandate] = approval;
}

function unsafeAgentOutputReason(value) {
  const text = String(value || "").trim();
  if (!text) return "empty_agent_output";
  if (text.length > MAX_AGENT_MESSAGE_CHARS) return "agent_output_too_long";
  const firstLine = text.split("\n").map((line) => line.trim()).find(Boolean) || text;
  const internalPlaceholders = [
    /^\[TOOL_CALL\]$/i,
    /^\[TOOL_RESULT\]$/i,
    /^\[object Object\]$/i,
    /^(undefined|null)$/i,
  ];
  if (internalPlaceholders.some((pattern) => pattern.test(text) || pattern.test(firstLine))) {
    return "internal_agent_output";
  }
  CLAWROOM_URL_PATTERN.lastIndex = 0;
  if (CLAWROOM_URL_PATTERN.test(text)) {
    CLAWROOM_URL_PATTERN.lastIndex = 0;
    return "clawroom_url_leak";
  }
  CLAWROOM_URL_PATTERN.lastIndex = 0;
  return "";
}

function noteUnsafeAgentOutput(reason) {
  bridgeState.blocked_agent_outputs = Number(bridgeState.blocked_agent_outputs || 0) + 1;
  bridgeState.last_blocked_agent_output_at = new Date().toISOString();
  bridgeState.last_blocked_agent_output_reason = reason;
  persistState();
  writeRuntimeState("running", {
    blocked_agent_outputs: bridgeState.blocked_agent_outputs,
    last_blocked_agent_output_at: bridgeState.last_blocked_agent_output_at,
    last_blocked_agent_output_reason: reason,
  });
}

const mandates = parseMandates(ownerCtx);

let bridgeState = {
  cursor: -1,
  notified: {},
  started_at: new Date().toISOString(),
  ...(readJson(statePath) || {}),
};

function persistState() {
  writeJsonAtomic(statePath, bridgeState);
}

function writeRuntimeState(status, extra = {}) {
  const payload = {
    bridge_version: VERSION,
    bridge_features: FEATURES,
    status,
    room_id: threadId,
    role,
    pid: process.pid,
    relay: relayBase,
    agent_id: agentId,
    session_key: sessionKey,
    state_path: statePath,
    cursor: bridgeState.cursor ?? -1,
    updated_at: new Date().toISOString(),
    ...extra,
  };
  writeJsonAtomic(runtimeStatePath, payload);
}

async function relayFetch(path, options = {}) {
  const url = new URL(`${relayBase}${path}`);
  for (const [key, value] of Object.entries(options.query || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  const headers = {
    accept: "application/json",
    authorization: `Bearer ${token}`,
    ...(options.headers || {}),
  };
  const init = {
    method: options.method || "GET",
    headers,
    signal: AbortSignal.timeout(options.timeoutMs || 30_000),
  };
  if (options.idempotencyKey) headers["x-idempotency-key"] = options.idempotencyKey;
  if (options.body !== undefined) {
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }

  const attempts = Math.max(1, Number(options.retries || process.env.CLAWROOM_RELAY_RETRIES || 4) || 4);
  const allowStatuses = new Set((options.allowStatuses || []).map((status) => Number(status)));
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const response = await fetch(url.toString(), init);
      const text = await response.text();
      let data;
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        data = { raw: text };
      }
      if (data && typeof data === "object" && !Array.isArray(data)) {
        data._status = response.status;
      }
      if (!response.ok && !allowStatuses.has(response.status)) {
        const error = new Error(`relay ${response.status} for ${path}: ${JSON.stringify(data).slice(0, 300)}`);
        error.status = response.status;
        error.data = data;
        error.retriable = response.status >= 500 || response.status === 429;
        if (attempt < attempts && error.retriable) {
          log(`relay ${response.status} for ${path}; retry ${attempt}/${attempts}`);
          await delay(250 * attempt * attempt);
          continue;
        }
        throw error;
      }
      if (response.status >= 500 && attempt < attempts) {
        log(`relay ${response.status} for ${path}; retry ${attempt}/${attempts}`);
        await delay(250 * attempt * attempt);
        continue;
      }
      return data;
    } catch (error) {
      lastError = error;
      if (error.status && !error.retriable) throw error;
      if (attempt >= attempts) break;
      log(`relay fetch failed for ${path}: ${error.message}; retry ${attempt}/${attempts}`);
      await delay(250 * attempt * attempt);
    }
  }
  throw lastError || new Error(`relay fetch failed for ${path}`);
}

async function getMessages(after = -1, wait = 0) {
  const data = await relayFetch(`/threads/${threadId}/messages`, {
    query: { after, wait },
    timeoutMs: (wait + 10) * 1000,
  }).catch(async (error) => {
    if (isFatalRelayError(error)) throw error;
    const backoffMs = relayBackoffMs(error);
    log(`relay poll failed: ${error.message}; backoff_ms=${backoffMs}`);
    await delay(backoffMs);
    return [];
  });
  return Array.isArray(data) ? data : [];
}

async function postMessage(text, key) {
  return relayFetch(`/threads/${threadId}/messages`, {
    method: "POST",
    body: { text },
    idempotencyKey: key,
    allowStatuses: [400, 409],
  });
}

async function postAskOwner(text, key) {
  return relayFetch(`/threads/${threadId}/ask-owner`, {
    method: "POST",
    body: { text, ttl_seconds: OWNER_REPLY_TTL_SECONDS },
    idempotencyKey: key,
    allowStatuses: [400, 409],
  });
}

async function closeThread(summary, key) {
  return relayFetch(`/threads/${threadId}/close`, {
    method: "POST",
    body: { summary },
    idempotencyKey: key,
    allowStatuses: [400, 409],
  });
}

async function getThreadState() {
  return relayFetch(`/threads/${threadId}/join`);
}

async function heartbeat(status = "running", extra = {}) {
  writeRuntimeState(status, extra);
  const result = await relayFetch(`/threads/${threadId}/heartbeat`, {
    method: "POST",
    body: {
      bridge_version: VERSION,
      status,
      cursor: bridgeState.cursor ?? -1,
      pid: String(process.pid),
      state_path: statePath,
      runtime_state_path: runtimeStatePath,
      agent_id: agentId,
      session_key: sessionKey,
      ...extra,
    },
  }).catch((error) => {
    log(`heartbeat failed: ${error.message}`);
    return null;
  });
  if (result?.ok) {
    writeRuntimeState(status, {
      ...extra,
      relay_heartbeat_ok: true,
      last_relay_heartbeat_at: new Date().toISOString(),
    });
  }
  return result;
}

let lastHeartbeatAt = 0;
async function maybeHeartbeat(status = "running", force = false, extra = {}) {
  const now = Date.now();
  if (!force && now - lastHeartbeatAt < HEARTBEAT_MS) return;
  lastHeartbeatAt = now;
  await heartbeat(status, extra);
}

function resolveGatewayUrl() {
  const env = process.env.OPENCLAW_GATEWAY_URL?.trim();
  if (env) return env;
  const cfg = readJson(openClawPath("openclaw.json"));
  const url = cfg?.gateway?.remote?.url?.trim();
  if (url) return url;
  return "ws://127.0.0.1:18789";
}

function resolveGatewayToken() {
  const deviceAuth = readJson(openClawPath("identity", "device-auth.json"));
  const operatorToken = deviceAuth?.tokens?.operator?.token;
  if (operatorToken) return operatorToken;

  const env = process.env.OPENCLAW_GATEWAY_TOKEN?.trim();
  if (env) return env;

  const cfg = readJson(openClawPath("openclaw.json"));
  return cfg?.gateway?.auth?.token || "";
}

function readDeviceIdentity() {
  return readJson(openClawPath("identity", "device.json"));
}

function buildDeviceParams(deviceId, privateKeyPem, publicKeyPem, clientId, clientMode, gatewayRole, scopes, tokenValue, nonce) {
  try {
    const signedAt = Date.now();
    const platform = process.platform === "darwin" ? "darwin" : "linux";
    const payload = [
      ["v", "3"].join(""),
      deviceId,
      clientId,
      clientMode,
      gatewayRole,
      scopes.join(","),
      String(signedAt),
      tokenValue,
      nonce,
      platform,
      "",
    ].join("|");

    const signature = cryptoSign(null, Buffer.from(payload, "utf8"), createPrivateKey(privateKeyPem));
    const publicKeyDer = createPublicKey(publicKeyPem).export({ type: "spki", format: "der" });
    const rawPublicKey = Buffer.from(publicKeyDer).slice(-32).toString("base64url");

    return {
      id: deviceId,
      publicKey: rawPublicKey,
      signature: signature.toString("base64url"),
      signedAt,
      nonce,
    };
  } catch (error) {
    log(`device signing failed: ${error.message}`);
    return null;
  }
}

function extractText(payload) {
  const result = payload?.result;
  if (result && typeof result === "object") {
    const payloads = result.payloads;
    if (Array.isArray(payloads) && payloads[0]) {
      const text = String(payloads[0].text || "").trim();
      if (text) return text;
    }
    const text = String(result.text || result.content || "").trim();
    if (text) return text;
  }
  if (typeof result === "string" && result.trim()) return result.trim();
  const summary = String(payload?.summary || "").trim();
  if (summary && summary !== "completed") return summary;
  return JSON.stringify(payload);
}

function extractChatMessageText(message) {
  const content = message?.content;
  if (Array.isArray(content)) {
    const text = content
      .map((part) => typeof part === "string" ? part : String(part?.text || ""))
      .join("")
      .trim();
    if (text) return text;
  }
  if (typeof content === "string" && content.trim()) return content.trim();
  return String(message?.text || "").trim();
}

async function gatewayCall(message) {
  const wsUrl = resolveGatewayUrl();
  const gatewayToken = resolveGatewayToken();
  if (!gatewayToken) {
    throw new Error("No OpenClaw gateway token found. Expected device-auth.json, OPENCLAW_GATEWAY_TOKEN, or openclaw.json.");
  }
  if (!globalThis.WebSocket) {
    throw new Error("Built-in WebSocket unavailable. Use Node 22+ for the zero-npm bridge.");
  }

  return new Promise((resolvePromise, reject) => {
    const ws = new globalThis.WebSocket(wsUrl);
    let state = "waiting_challenge";
    let connectId = "";
    let reqId = "";
    let runId = "";
    let lastAssistantText = "";
    let settled = false;
    const pendingRunEvents = [];

    const mainTimer = setTimeout(() => {
      try { ws.close(); } catch {}
      reject(new Error(`OpenClaw timeout after ${AGENT_TIMEOUT / 1000}s (state=${state})`));
    }, AGENT_TIMEOUT);

    const challengeFallback = setTimeout(() => {
      if (state === "waiting_challenge") {
        log("No connect.challenge received; sending connect anyway");
        sendConnect(null);
      }
    }, CHALLENGE_WAIT);

    function finish(value) {
      if (settled) return;
      settled = true;
      clearTimeout(mainTimer);
      clearTimeout(challengeFallback);
      try { ws.close(); } catch {}
      if (value instanceof Error) reject(value);
      else resolvePromise(value);
    }

    function sendConnect(challengeNonce) {
      if (ws.readyState !== globalThis.WebSocket.OPEN) {
        if (ws.readyState === globalThis.WebSocket.CLOSING || ws.readyState === globalThis.WebSocket.CLOSED) {
          finish(new Error(`Gateway WS closed before connect at ${wsUrl}`));
          return;
        }
        setTimeout(() => sendConnect(challengeNonce), 250);
        return;
      }
      state = "connecting";
      clearTimeout(challengeFallback);
      connectId = randomUUID();

      const clientId = "gateway-client";
      const clientMode = "backend";
      const gatewayRole = "operator";
      const scopes = ["operator.read", "operator.write"];
      const nonce = challengeNonce || randomUUID();
      const platform = process.platform === "darwin" ? "darwin" : "linux";

      const params = {
        minProtocol: 3,
        maxProtocol: 3,
        client: {
          id: clientId,
          version: VERSION,
          platform,
          mode: clientMode,
          instanceId: randomUUID(),
        },
        role: gatewayRole,
        scopes,
        auth: { token: gatewayToken },
        caps: [],
      };

      const device = readDeviceIdentity();
      if (device.deviceId && device.privateKeyPem && device.publicKeyPem) {
        const deviceParams = buildDeviceParams(
          device.deviceId,
          device.privateKeyPem,
          device.publicKeyPem,
          clientId,
          clientMode,
          gatewayRole,
          scopes,
          gatewayToken,
          nonce,
        );
        if (deviceParams) params.device = deviceParams;
      }

      ws.send(JSON.stringify({ type: "req", id: connectId, method: "connect", params }));
    }

    function handleRunEvent(msg) {
      const payload = msg.payload || {};
      if (!runId || payload.runId !== runId) return false;
      if (msg.event === "agent" && payload.stream === "assistant") {
        const text = String(payload.data?.text || "").trim();
        if (text) lastAssistantText = text;
        return true;
      }
      if (msg.event === "agent" && payload.stream === "lifecycle" && payload.data?.phase === "end") {
        if (lastAssistantText) {
          state = "done";
          writeRuntimeState("running", { gateway_connected: true, last_gateway_success_at: new Date().toISOString() });
          finish(lastAssistantText);
        }
        return true;
      }
      if (msg.event === "chat" && payload.state === "final") {
        const text = extractChatMessageText(payload.message);
        if (text) {
          state = "done";
          writeRuntimeState("running", { gateway_connected: true, last_gateway_success_at: new Date().toISOString() });
          finish(text);
        }
        return true;
      }
      return false;
    }

    function drainPendingRunEvents() {
      const buffered = pendingRunEvents.splice(0);
      for (const eventMsg of buffered) {
        if (handleRunEvent(eventMsg) && state === "done") return;
      }
    }

    ws.addEventListener("error", (event) => {
      finish(new Error(`Gateway WS error at ${wsUrl}: ${event.message || "connection failed"}`));
    });

    ws.addEventListener("message", (event) => {
      let msg;
      try {
        msg = JSON.parse(typeof event.data === "string" ? event.data : event.data.toString());
      } catch {
        return;
      }

      if (state === "waiting_challenge" && msg.type === "event" && msg.event === "connect.challenge") {
        sendConnect(msg.payload?.nonce || null);
        return;
      }

      if (state === "connecting" && msg.type === "res" && msg.id === connectId) {
        if (!msg.ok) {
          finish(new Error(`Gateway connect failed: ${JSON.stringify(msg.error || {})}`));
          return;
        }
        state = "requesting";
        reqId = randomUUID();
        ws.send(JSON.stringify({
          type: "req",
          id: reqId,
          method: "agent",
          params: {
            message,
            sessionKey,
            agentId,
            idempotencyKey: randomUUID(),
            deliver: false,
          },
        }));
        return;
      }

      if ((state === "requesting" || state === "accepted") && msg.type === "event") {
        const payload = msg.payload || {};
        if (!runId && payload.runId && (msg.event === "agent" || msg.event === "chat")) {
          if (pendingRunEvents.length < 100) pendingRunEvents.push(msg);
          return;
        }
        if (handleRunEvent(msg)) {
          return;
        }
      }

      if ((state === "requesting" || state === "accepted") && msg.type === "res" && msg.id === reqId) {
        const payload = msg.payload || {};
        if (payload.status === "accepted") {
          state = "accepted";
          runId = String(payload.runId || "");
          if (runId) log(`OpenClaw accepted run ${runId.slice(0, 8)}`);
          drainPendingRunEvents();
          return;
        }
        if (!msg.ok) {
          finish(new Error(`Agent error: ${JSON.stringify(msg.error || {})}`));
          return;
        }
        state = "done";
        writeRuntimeState("running", { gateway_connected: true, last_gateway_success_at: new Date().toISOString() });
        finish(extractText(payload));
      }
    });
  });
}

function parseReply(raw) {
  const rawReason = unsafeAgentOutputReason(raw);
  if (rawReason) {
    noteUnsafeAgentOutput(rawReason);
    return { invalid: true, reason: rawReason, marker_inferred: false };
  }

  const lines = String(raw || "").split("\n").map((line) => line.trim()).filter(Boolean);
  const closeMatch = lines.map((line) => line.match(/^\s*CLAWROOM[\s_]*CLOSE\s*:\s*(.*)$/i)).find(Boolean);
  if (closeMatch) {
    const summary = closeMatch[1].trim();
    const reason = unsafeAgentOutputReason(summary);
    if (reason) {
      noteUnsafeAgentOutput(reason);
      return { invalid: true, reason, marker_inferred: false };
    }
    return { close: true, summary, marker_inferred: false };
  }
  if (process.env.CLAWROOM_ALLOW_LEGACY_CLOSE === "true") {
    const legacyMatch = lines.map((line) => line.match(/^\s*CLOSE\s*:\s*(.*)$/i)).find(Boolean);
    if (legacyMatch) {
      const summary = legacyMatch[1].trim();
      const reason = unsafeAgentOutputReason(summary);
      if (reason) {
        noteUnsafeAgentOutput(reason);
        return { invalid: true, reason, marker_inferred: false };
      }
      return { close: true, summary, marker_inferred: false };
    }
  }
  const askOwnerMatch = lines.map((line) => line.match(/^\s*ASK[\s_]*OWNER\s*:\s*(.*)$/i)).find(Boolean);
  if (askOwnerMatch) {
    const question = askOwnerMatch[1].trim();
    const reason = unsafeAgentOutputReason(question);
    if (reason) {
      noteUnsafeAgentOutput(reason);
      return { invalid: true, reason, marker_inferred: false };
    }
    return { ask_owner: true, question, marker_inferred: false };
  }
  const replyMatch = lines.map((line) => line.match(/^\s*REPLY\s*:\s*(.*)$/i)).find(Boolean);
  if (replyMatch) {
    const text = replyMatch[1].trim();
    const reason = unsafeAgentOutputReason(text);
    if (reason) {
      noteUnsafeAgentOutput(reason);
      return { invalid: true, reason, marker_inferred: false };
    }
    return { close: false, text, marker_inferred: false };
  }

  const text = lines[0] || String(raw || "").trim();
  const reason = unsafeAgentOutputReason(text);
  if (reason) {
    noteUnsafeAgentOutput(reason);
    return { invalid: true, reason, marker_inferred: false };
  }
  if (text) {
    bridgeState.unmatched_marker_turns = Number(bridgeState.unmatched_marker_turns || 0) + 1;
    bridgeState.last_marker_inferred_at = new Date().toISOString();
    if (/\b(owner|approval|permission|authorize|authorized|boss)\b/i.test(text)) {
      bridgeState.last_soft_ask_owner_candidate_at = bridgeState.last_marker_inferred_at;
      bridgeState.soft_ask_owner_candidates = Number(bridgeState.soft_ask_owner_candidates || 0) + 1;
    }
    persistState();
    writeRuntimeState("running", {
      unmatched_marker_turns: bridgeState.unmatched_marker_turns,
      last_marker_inferred_at: bridgeState.last_marker_inferred_at,
      soft_ask_owner_candidates: bridgeState.soft_ask_owner_candidates || 0,
    });
    log(`marker inferred: no REPLY/CLAWROOM_CLOSE/ASK_OWNER marker in agent output; total=${bridgeState.unmatched_marker_turns}`);
  }
  return { close: false, text, marker_inferred: Boolean(text) };
}

function retryWithoutToolsPrompt(originalPrompt) {
  return [
    "Your previous answer could not be sent because it was not a safe room message.",
    "Do not use tools. Do not call functions. Return plain text only.",
    "Do not include ClawRoom invite, watch, API, worker, room, or relay URLs.",
    "Follow the original task and return exactly one allowed line.",
    "",
    originalPrompt,
  ].join("\n");
}

async function gatewayParsed(prompt, label) {
  const first = parseReply(await gatewayCall(prompt));
  if (!first.invalid) return first;

  log(`blocked unsafe agent output during ${label}; retrying without tools`);
  const second = parseReply(await gatewayCall(retryWithoutToolsPrompt(prompt)));
  if (!second.invalid) return second;

  log(`blocked unsafe agent output during ${label} after retry`);
  return second;
}

function openingPrompt() {
  return [
    "You are acting for your owner inside an already-created private two-agent coordination room.",
    "This is not a user request. Do not use tools, call skills, create rooms, join rooms, generate invites, or mention invite URLs.",
    "Only write the next room message for this existing room.",
    "",
    `Owner context: ${promptOwnerCtx}`,
    ...approvedContextLines(),
    `Goal: ${promptGoal}`,
    ...roleBoundaryLines(),
    ...mandatePromptLines(false),
    minMessages ? `Minimum negotiation messages before close: ${minMessages}.` : "",
    "",
    "Start with one concrete proposal or the most useful context.",
    "Preserve every numeric, date, deadline, budget, floor, ceiling, scope, and exclusivity constraint from Owner context exactly.",
    "Do not invent, round, shrink, or reinterpret owner constraints.",
    "Use natural language. Do not mention APIs, tokens, relays, sessions, or internal mechanics.",
    "",
    "Return exactly one line:",
    "REPLY: <short message under 30 words>",
  ].join("\n");
}

function agreementArtifactInstruction() {
  return "When closing, format the owner-ready agreement as: Agreed terms: ...; Unresolved: ...; Assumptions: ...; Owner approvals: ...; Next steps: ...; Handoff text: ... Do not say ClawRoom processed payment or that owner approval is still needed after the room closes.";
}

function replyPrompt(otherRole, text, firstTurn, messageCount) {
  const canClose = !minMessages || messageCount >= minMessages;
  return [
    firstTurn ? "You are acting for your owner inside an already-created private two-agent coordination room." : "",
    "This is not a user request. Do not use tools, call skills, create rooms, join rooms, generate invites, or mention invite URLs.",
    "Only write the next room message for this existing room.",
    firstTurn ? `Owner context: ${promptOwnerCtx}` : "",
    ...approvedContextLines(),
    `Goal: ${promptGoal}`,
    ...roleBoundaryLines(),
    ...mandatePromptLines(true),
    minMessages ? `Negotiation messages so far, including the latest received message: ${messageCount}.` : "",
    minMessages ? `Minimum negotiation messages before close: ${minMessages}.` : "",
    minMessages && !canClose ? "You MUST continue with REPLY. Do not close yet." : "",
    firstTurn ? "" : "",
    `The other agent (${otherRole}) says: ${JSON.stringify(sanitizePromptText(text))}`,
    "",
    "Reply with one concise message that moves toward the goal.",
    "Preserve every numeric, date, deadline, budget, floor, ceiling, scope, and exclusivity constraint from Owner context exactly.",
    "Do not invent, round, shrink, or reinterpret owner constraints.",
    canClose ? "If the agreement is clear and ready to report to your owner, close instead." : "Do not close yet; ask a useful question, make a counteroffer, or confirm one missing detail.",
    canClose ? agreementArtifactInstruction() : "",
    "Use natural language. Do not mention APIs, tokens, relays, sessions, or internal mechanics.",
    "",
    "Return exactly one line, choosing one:",
    "REPLY: <short message under 30 words>",
    "ASK_OWNER: <short authorization question for your owner>",
    canClose ? "CLAWROOM_CLOSE: <concise owner-ready summary with final terms and next step>" : "",
  ].filter(Boolean).join("\n");
}

function earlyClosePrompt(otherRole, text, summary, messageCount) {
  return [
    "You attempted to close the room before the minimum negotiation length.",
    "This is an already-created room. Do not use tools, call skills, create rooms, join rooms, generate invites, or mention invite URLs.",
    `Owner context: ${promptOwnerCtx}`,
    ...approvedContextLines(),
    `Goal: ${promptGoal}`,
    ...roleBoundaryLines(),
    `Negotiation messages so far: ${messageCount}.`,
    `Minimum negotiation messages before close: ${minMessages}.`,
    `The other agent (${otherRole}) last said: ${JSON.stringify(sanitizePromptText(text))}`,
    `Your premature close summary was: ${JSON.stringify(sanitizePromptText(summary))}`,
    "",
    "Continue the negotiation with one substantive question, counteroffer, or missing-detail confirmation.",
    "Use natural language. Do not mention APIs, tokens, relays, sessions, or internal mechanics.",
    "",
    "Return exactly one line:",
    "REPLY: <short message under 30 words>",
  ].join("\n");
}

function ownerReplyPrompt(waiting, ownerReplyText, messageCount) {
  const canClose = !minMessages || messageCount >= minMessages;
  return [
    "Your owner has replied to your authorization question.",
    "This is an already-created room. Do not use tools, call skills, create rooms, join rooms, generate invites, or mention invite URLs.",
    `Owner context: ${promptOwnerCtx}`,
    ...approvedContextLines(),
    `Goal: ${promptGoal}`,
    ...roleBoundaryLines(),
    ...mandatePromptLines(true),
    `Original counterpart message: ${JSON.stringify(sanitizePromptText(waiting.peer_text || ""))}`,
    waiting.attempted_close_summary ? `Your blocked close summary: ${JSON.stringify(sanitizePromptText(waiting.attempted_close_summary))}` : "",
    waiting.blocked_reply_text ? `Your blocked reply: ${JSON.stringify(sanitizePromptText(waiting.blocked_reply_text))}` : "",
    `OWNER_REPLY: ${sanitizePromptText(ownerReplyText)}`,
    minMessages ? `Negotiation messages so far: ${messageCount}. Minimum before close: ${minMessages}.` : "",
    "",
    "Continue the negotiation according to the owner reply.",
    "Use natural language. Do not mention APIs, tokens, relays, sessions, or internal mechanics.",
    canClose ? agreementArtifactInstruction() : "",
    "",
    "Return exactly one line, choosing one:",
    "REPLY: <short message under 30 words>",
    "ASK_OWNER: <short authorization question for your owner>",
    canClose ? "CLAWROOM_CLOSE: <concise owner-ready summary with final terms and next step>" : "",
  ].filter(Boolean).join("\n");
}

function resolveTelegramConfig() {
  const botToken = (process.env.TG_BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN || "").trim();
  const cfg = readJson(openClawPath("openclaw.json"));
  const telegram = cfg?.channels?.telegram || {};
  const credentialAllowFrom =
    readJson(openClawPath("credentials", "telegram-allowFrom.json"))?.allowFrom ||
    readJson(openClawPath("credentials", "telegram-default-allowFrom.json"))?.allowFrom ||
    [];
  const chatId =
    explicitTelegramChatId ||
    String(telegram.allowFrom?.[0] || "").trim() ||
    String(credentialAllowFrom?.[0] || "").trim();
  return {
    botToken: botToken || telegram.botToken || "",
    chatId,
  };
}

async function telegramNotify(text, options = {}) {
  const { botToken, chatId } = resolveTelegramConfig();
  if (!botToken || !chatId) {
    log("notify skipped: missing Telegram bot token or chat_id");
    return { ok: false, message_id: null, chat_id: null };
  }
  const requestBody = { chat_id: chatId, text };
  if (options.replyMarkup) {
    requestBody.reply_markup = options.replyMarkup;
  } else if (options.forceReply) {
    requestBody.reply_markup = {
      force_reply: true,
      input_field_placeholder: "Approve, reject, or give instructions",
    };
  }
  const response = await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(requestBody),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`Telegram API ${response.status}: ${await response.text()}`);
  const body = await response.json().catch(() => ({}));
  const resolvedChatId = body?.result?.chat?.id != null ? String(body.result.chat.id) : String(chatId);
  log(`Telegram delivered to chat_id=...${chatIdSuffix(resolvedChatId)}`);
  return { ok: true, message_id: body?.result?.message_id || null, chat_id: resolvedChatId };
}

async function notifyOwnerOnce(key, summary) {
  if (notifyKind === "none") return;
  bridgeState.notified ||= {};
  if (bridgeState.notified[key]) return;
  const text = `ClawRoom done\n\n${summary}`;
  if (notifyKind !== "telegram") {
    log(`notify skipped: unsupported notify kind ${notifyKind}`);
    return;
  }
  await telegramNotify(text);
  bridgeState.notified[key] = new Date().toISOString();
  persistState();
}

function ownerReplyEndpoint() {
  return `${relayBase}/threads/${threadId}/owner-reply`;
}

function ownerDecisionUrl(question) {
  return String(question?.owner_reply_url || "");
}

function askOwnerBindingPath(chatId, messageId) {
  const dir = join(openClawStateDir, "clawroom", "ask-owner-bindings");
  const safeMessageId = String(messageId || "").replace(/[^0-9a-zA-Z_.:-]/g, "_");
  return join(dir, `${chatIdHash(chatId)}.${safeMessageId}.json`);
}

function writeAskOwnerTelegramBinding(question, delivery) {
  if (!delivery?.ok || !delivery.message_id || !delivery.chat_id) {
    return { ok: false, reason: "missing_telegram_delivery_fields" };
  }
  const messageId = delivery.message_id;
  const chatId = String(delivery.chat_id);
  const path = askOwnerBindingPath(chatId, messageId);
  const binding = {
    version: 1,
    source: "clawroom_bridge",
    created_at: new Date().toISOString(),
    expires_at: question.expires_at || null,
    relay: relayBase,
    thread_id: threadId,
    role,
    question_id: question.question_id || "",
    ask_event_id: question.id ?? null,
    owner_reply_token: question.owner_reply_token || "",
    telegram: {
      chat_id_hash: chatIdHash(chatId),
      chat_id_suffix: chatIdSuffix(chatId),
      message_id: messageId,
    },
  };
  writeJsonAtomic(path, binding);
  return {
    ok: true,
    path,
    chat_id_hash: binding.telegram.chat_id_hash,
    chat_id_suffix: binding.telegram.chat_id_suffix,
    message_id: messageId,
  };
}

function publicWaitingOwner(waiting = bridgeState.waiting_owner || null) {
  if (!waiting) return null;
  const { owner_reply_token, ...rest } = waiting;
  return {
    ...rest,
    owner_reply_token: owner_reply_token ? "REDACTED" : "",
  };
}

function mandateViolation(text, action) {
  if ((action === "close" || action === "reply") && noDealOutcome(text)) return null;

  const unsupportedDateViolation = unsupportedDateCommitmentViolation(text, action);
  if (unsupportedDateViolation) return unsupportedDateViolation;

  const requiredInteraction = requiredInteractionViolation(text, action);
  if (requiredInteraction) return requiredInteraction;

  const contextualViolation = contextualOfferFloorViolation(text, action);
  if (contextualViolation) return contextualViolation;

  const usdCeiling = Number(mandates.budget_ceiling_usd || 0);
  if (usdCeiling && !bridgeState.mandate_approvals?.budget_ceiling_usd) {
    const amount = maxUsdAmount(text);
    if (amount && amount > usdCeiling && !(action === "reply" && obviousRejection(text))) {
      return {
        kind: "budget_ceiling_usd",
        ceiling: usdCeiling,
        amount,
        currency: "USD",
        action,
      };
    }
  }

  const usdFloor = Number(mandates.price_floor_usd || 0);
  if (usdFloor && !bridgeState.mandate_approvals?.price_floor_usd) {
    const amount = usdFloorViolationAmount(text, usdFloor);
    if (amount && amount < usdFloor && !(action === "reply" && obviousRejection(text))) {
      return {
        kind: "price_floor_usd",
        floor: usdFloor,
        amount,
        currency: "USD",
        action,
      };
    }
  }

  const ceiling = Number(mandates.budget_ceiling_jpy || 0);
  if (ceiling && !bridgeState.mandate_approvals?.budget_ceiling_jpy) {
    const amount = maxJpyAmount(text);
    if (amount && amount > ceiling && !(action === "reply" && obviousRejection(text))) {
      return {
        kind: "budget_ceiling_jpy",
        ceiling,
        amount,
        action,
      };
    }
  }

  const floor = Number(mandates.price_floor_jpy || 0);
  if (floor && !bridgeState.mandate_approvals?.price_floor_jpy) {
    const amount = minJpyAmount(text);
    if (amount && amount < floor && !(action === "reply" && obviousRejection(text))) {
      return {
        kind: "price_floor_jpy",
        floor,
        amount,
        action,
      };
    }
  }

  return null;
}

function ownerQuestionText(parsed, violation) {
  if (parsed.ask_owner) return ownerSafeQuestionText(parsed.question);
  if (violation?.kind === "budget_ceiling_usd") {
    return [
      `The proposed ${violation.action} mentions ${violation.amount} USD, above your ${violation.ceiling} USD ceiling.`,
      "Approve this exception, reject it, or give a counter-instruction.",
    ].join(" ");
  }
  if (violation?.kind === "price_floor_usd") {
    return [
      `The proposed ${violation.action} mentions ${violation.amount} USD, below your ${violation.floor} USD floor.`,
      "Approve this exception, reject it, or give a counter-instruction.",
    ].join(" ");
  }
  if (violation?.kind === "contextual_offer_floor_usd") {
    return [
      `The proposed ${violation.action} mentions ${violation.amount} USD for ${violation.descriptor || "a quoted offer"}, below the ${violation.floor} USD price in your instructions.`,
      "Approve this exception, reject it, or give a counter-instruction.",
    ].join(" ");
  }
  if (violation?.kind === "unsupported_date_commitment") {
    return [
      `The proposed ${violation.action} commits to ${violation.date}, but your instructions did not confirm that date.`,
      "Approve that date, reject it, or give the date you can actually support.",
    ].join(" ");
  }
  if (violation?.kind === "required_interaction_removed") {
    return [
      `The proposed ${violation.action} removes your required ${violation.label}.`,
      violation.required_total ? `It also appears below your required total of ${violation.required_total} USD.` : "",
      "Approve dropping that requirement, reject it, or give the terms you actually support.",
    ].filter(Boolean).join(" ");
  }
  if (violation?.kind === "budget_ceiling_jpy") {
    return [
      `The proposed ${violation.action} mentions ${violation.amount} JPY, above your ${violation.ceiling} JPY ceiling.`,
      "Approve this exception, reject it, or give a counter-instruction.",
    ].join(" ");
  }
  if (violation?.kind === "price_floor_jpy") {
    return [
      `The proposed ${violation.action} mentions ${violation.amount} JPY, below your ${violation.floor} JPY floor.`,
      "Approve this exception, reject it, or give a counter-instruction.",
    ].join(" ");
  }
  return "Authorization needed before continuing. Please approve, reject, or give a counter-instruction.";
}

async function notifyOwnerQuestion(question, questionText) {
  if (notifyKind !== "telegram") {
    log(`ASK_OWNER notify skipped: unsupported notify kind ${notifyKind}`);
    return null;
  }
  const endpoint = ownerReplyEndpoint();
  const decisionUrl = ownerDecisionUrl(question);
  const debugOwnerReply = process.env.CLAWROOM_DEBUG_OWNER_REPLY === "true";
  const text = [
    "ClawRoom needs your decision",
    questionText,
    "",
    decisionUrl ? "Tap Open Decision Page to approve, reject, or give a counter-instruction." : "Approve, reject, or give a counter-instruction.",
    "",
    "Examples: approve; reject; do not go above 65000 JPY; offer extra deliverables instead.",
    debugOwnerReply ? "" : null,
    debugOwnerReply ? "Debug details:" : null,
    debugOwnerReply ? `Room: ${threadId}` : null,
    debugOwnerReply ? `Role: ${role}` : null,
    debugOwnerReply ? `Endpoint: ${endpoint}` : null,
  ].filter((line) => line !== null).join("\n");
  const replyMarkup = decisionUrl
    ? { inline_keyboard: [[{ text: "Open Decision Page", url: decisionUrl }]] }
    : { force_reply: true, input_field_placeholder: "Approve, reject, or give instructions" };
  const delivered = await telegramNotify(text, { replyMarkup });
  return {
    ...delivered,
    owner_reply_endpoint: endpoint,
    owner_reply_url: decisionUrl || null,
  };
}

async function enterWaitingOwner(parsed, context = {}) {
  const violation = context.violation || null;
  const questionText = ownerQuestionText(parsed, violation);
  const key = idempotencyKey(
    "ask-owner",
    threadId,
    role,
    context.peer_message_id || "",
    sha(questionText),
  );
  const question = await postAskOwner(questionText, key);
  if (question?.id == null || !question.question_id || !question.owner_reply_token) {
    log(`ASK_OWNER post failed: ${JSON.stringify(question)}`);
    await maybeHeartbeat("error", true, { last_error: JSON.stringify(question) });
    return false;
  }

  setCursor(question.id);
  const waiting = {
    question_id: question.question_id,
    owner_reply_token: question.owner_reply_token,
    ask_event_id: question.id,
    question_text: questionText,
    asked_at: new Date().toISOString(),
    expires_at: question.expires_at || null,
    peer_message_id: context.peer_message_id ?? null,
    peer_text: context.peer_text || "",
    attempted_close_summary: context.attempted_close_summary || "",
    blocked_reply_text: context.blocked_reply_text || "",
    mandate_violation: violation,
  };
  bridgeState.waiting_owner = waiting;
  persistState();

  try {
    const delivery = await notifyOwnerQuestion(question, questionText);
    let binding = { ok: false, reason: "not_attempted" };
    if (process.env.CLAWROOM_ENABLE_TELEGRAM_INBOUND_BINDINGS === "true") {
      try {
        binding = writeAskOwnerTelegramBinding(question, delivery);
        if (binding.ok) {
          log(`ASK_OWNER Telegram binding written message_id=${binding.message_id} chat_id=...${binding.chat_id_suffix}`);
        } else {
          log(`ASK_OWNER Telegram binding skipped: ${binding.reason}`);
        }
      } catch (error) {
        binding = { ok: false, reason: "write_failed" };
        log(`ASK_OWNER Telegram binding write failed: ${error.message}`);
      }
    } else {
      binding = { ok: false, reason: "disabled_optional_adapter" };
      log("ASK_OWNER Telegram inbound binding skipped: optional adapter disabled");
    }
    bridgeState.waiting_owner = {
      ...bridgeState.waiting_owner,
      telegram_message_id: delivery?.message_id || null,
      telegram_chat_hash: binding.ok ? binding.chat_id_hash : null,
      telegram_binding_written: Boolean(binding.ok),
      owner_reply_endpoint: delivery?.owner_reply_endpoint || null,
      owner_reply_url: delivery?.owner_reply_url || null,
      notified_at: new Date().toISOString(),
    };
    persistState();
  } catch (error) {
    log(`ASK_OWNER notify failed: ${error.message}`);
    await maybeHeartbeat("waiting_owner", true, {
      waiting_owner: publicWaitingOwner(waiting),
      notify_error: error.message,
    });
    return true;
  }

  log(`Waiting for owner reply question_id=${question.question_id}`);
  await maybeHeartbeat("waiting_owner", true, { waiting_owner: publicWaitingOwner() });
  return true;
}

function preflight() {
  mkdirSync(stateDir, { recursive: true });
  writeJsonAtomic(join(stateDir, ".write-test.json"), { ok: true, ts: Date.now() });
  if (!globalThis.WebSocket) throw new Error("Node built-in WebSocket is unavailable. Use Node 22+.");
  if (!resolveGatewayToken()) throw new Error("OpenClaw gateway token is missing.");
  if (agentId === "main" && process.env.CLAWROOM_ALLOW_MAIN_AGENT !== "true") {
    log("warning: agent-id is main. Dedicated agent-id clawroom-relay is recommended.");
  }
  if (!existsSync(runtimeStatePath)) writeRuntimeState("starting", { preflight: "ok" });
}

function setCursor(id) {
  bridgeState.cursor = Math.max(Number(bridgeState.cursor ?? -1), Number(id));
  persistState();
}

async function handlePeerClose(message) {
  const summary = message.text || "The counterpart closed the room.";
  log(`Peer close observed (id=${message.id}): ${summary}`);
  setCursor(message.id);
  await notifyOwnerOnce(`peer-close:${sha(summary)}`, summary).catch((error) => {
    log(`owner notify failed: ${error.message}`);
  });
  const key = idempotencyKey("close", threadId, role, "ack", message.id, sha(summary));
  const result = await closeThread(summary, key);
  log(`Close acknowledged: ${JSON.stringify({ closed: result?.closed, status: result?._status })}`);
  await maybeHeartbeat("stopped", true, { stop_reason: "peer_close" });
}

async function stopForUnsafeAgentOutput(parsed, context = {}) {
  const summary = "I stopped this room because I could not produce a safe message for the other side. Please restart with clearer instructions.";
  const key = idempotencyKey(
    "unsafe-output-close",
    threadId,
    role,
    context.peer_message_id || "",
    parsed?.reason || "unsafe_agent_output",
    sha(summary),
  );

  try {
    const result = await closeThread(summary, key);
    if (result?.id != null) setCursor(result.id);
    log(`Stopped safely after unsafe agent output: ${parsed?.reason || "unknown"}`);
  } catch (error) {
    log(`safe stop close failed: ${error.message}`);
  }

  await notifyOwnerOnce(`unsafe-agent-output:${role}:${sha(summary)}`, [
    "ClawRoom stopped safely",
    "",
    "I could not produce a safe message for the other side, so I stopped instead of continuing.",
  ].join("\n")).catch((error) => {
    log(`owner notify failed: ${error.message}`);
  });

  await maybeHeartbeat("stopped", true, {
    stop_reason: "unsafe_agent_output",
    last_blocked_agent_output_reason: parsed?.reason || null,
  });
  return false;
}

async function handleParsedReply(parsed, context = {}) {
  if (parsed.invalid) {
    return await stopForUnsafeAgentOutput(parsed, context);
  }

  if (asksOurOwnerToProceedDespitePeerUnconfirmed(parsed, context.peer_text || "")) {
    return await handleParsedReply({
      close: false,
      text: counterpartConfirmationReply(context.peer_text || ""),
      marker_inferred: true,
    }, context);
  }

  if (parsed.ask_owner) {
    await enterWaitingOwner(parsed, context);
    return true;
  }

  const textForGuard = parsed.close ? parsed.summary : parsed.text;
  const violation = mandateViolation(textForGuard, parsed.close ? "close" : "reply");
  if (violation) {
    await enterWaitingOwner(parsed, {
      ...context,
      violation,
      attempted_close_summary: parsed.close ? parsed.summary : "",
      blocked_reply_text: parsed.close ? "" : parsed.text,
    });
    return true;
  }

  if (parsed.close) {
    const key = idempotencyKey("close", threadId, role, context.peer_message_id || "", sha(parsed.summary));
    let result;
    try {
      result = await closeThread(parsed.summary, key);
    } catch (error) {
      if (Number(error?.status) === 413) {
        log(`close summary rejected as too long; stopping safely`);
        return await stopForUnsafeAgentOutput({ invalid: true, reason: "agent_output_too_long" }, context);
      }
      log(`close post failed: ${error.message}; will retry on the next poll`);
      await maybeHeartbeat("running", true, {
        last_error: error.message,
        pending_action: "close",
      }).catch((heartbeatError) => log(`heartbeat after close failure failed: ${heartbeatError.message}`));
      await delay(RELAY_ERROR_BACKOFF_MS);
      return true;
    }
    if (result?.id != null) setCursor(result.id);
    log(`Closed by ${role}: ${parsed.summary}`);
    await notifyOwnerOnce(`own-close:${sha(parsed.summary)}`, parsed.summary).catch((error) => {
      log(`owner notify failed: ${error.message}`);
    });
    await maybeHeartbeat("stopped", true, { stop_reason: "own_close" });
    return false;
  }

  const text = parsed.text || "";
  const key = idempotencyKey("reply", threadId, role, context.peer_message_id || "", sha(text));
  let result;
  try {
    result = await postMessage(text, key);
  } catch (error) {
    if (Number(error?.status) === 413) {
      log(`reply rejected as too long; stopping safely`);
      return await stopForUnsafeAgentOutput({ invalid: true, reason: "agent_output_too_long" }, context);
    }
    log(`reply post failed: ${error.message}; will retry on the next poll`);
    await maybeHeartbeat("running", true, {
      last_error: error.message,
      pending_action: "reply",
    }).catch((heartbeatError) => log(`heartbeat after reply failure failed: ${heartbeatError.message}`));
    await delay(RELAY_ERROR_BACKOFF_MS);
    return true;
  }
  if (result?.id != null) {
    setCursor(result.id);
    log(`Posted (id=${result.id}): ${text}`);
    return true;
  }
  if (result?.error === "not_your_turn") {
    log(`not_your_turn at last_id=${result.last_id}; refetching`);
    if (context.peer_message_id != null) setCursor(context.peer_message_id);
    return true;
  }
  if (result?.error === "thread is closed") {
    log("Thread closed by other side");
    await maybeHeartbeat("stopped", true, { stop_reason: "thread_closed" });
    return false;
  }
  log(`Post failed: ${JSON.stringify(result)}`);
  await maybeHeartbeat("error", true, { last_error: JSON.stringify(result) });
  return true;
}

async function handleWaitingOwner() {
  const waiting = bridgeState.waiting_owner;
  if (!waiting?.question_id) return false;

  if (ownerWaitExpired(waiting)) {
    return await handleOwnerWaitExpired(waiting);
  }

  await maybeHeartbeat("waiting_owner", false, { waiting_owner: publicWaitingOwner(waiting) });
  const messages = await getMessages(bridgeState.cursor ?? -1, POLL_WAIT_SECONDS);
  if (!messages.length) return true;

  for (const message of messages) {
    if (Number(message.id) <= Number(bridgeState.cursor ?? -1)) continue;

    if (message.kind === "owner_reply" && message.from === role && message.question_id === waiting.question_id) {
      setCursor(message.id);
      log(`Owner reply observed for question_id=${waiting.question_id}`);
      bridgeState.latest_owner_reply_text = message.text || "";
      recordMandateApproval(waiting, message.text);
      delete bridgeState.waiting_owner;
      persistState();

      const allMessages = await getMessages(-1, 0);
      const messageCount = negotiationMessageCount(allMessages);
      let reply;
      try {
        reply = await gatewayParsed(ownerReplyPrompt(waiting, message.text, messageCount), "owner-reply");
      } catch (error) {
        log(`Gateway error after owner reply: ${error.message}`);
        await maybeHeartbeat("error", true, { last_error: error.message });
        return true;
      }
      return await handleParsedReply(reply, {
        peer_message_id: waiting.peer_message_id,
        peer_text: waiting.peer_text,
      });
    }

    if (message.kind === "close" && message.from !== role) {
      await handlePeerClose(message);
      return false;
    }

    setCursor(message.id);
  }
  return true;
}

function ownerWaitExpired(waiting) {
  const expiresAt = Number(waiting?.expires_at || 0);
  return Number.isFinite(expiresAt) && expiresAt > 0 && Date.now() > expiresAt;
}

async function handleOwnerWaitExpired(waiting) {
  const summary = `Owner authorization ${waiting.question_id} expired without a reply. Closing without approving the requested exception.`;
  const key = idempotencyKey("close", threadId, role, "owner-timeout", waiting.question_id, sha(summary));
  log(`Owner reply expired question_id=${waiting.question_id}; closing room`);

  const result = await closeThread(summary, key);
  if (result?.id != null) setCursor(result.id);

  delete bridgeState.waiting_owner;
  persistState();

  await notifyOwnerOnce(`owner-timeout:${waiting.question_id}`, [
    "ClawRoom authorization expired",
    "",
    "No reply was recorded before the question expired, so I closed without approving the exception.",
    process.env.CLAWROOM_DEBUG_OWNER_REPLY === "true" ? "" : null,
    process.env.CLAWROOM_DEBUG_OWNER_REPLY === "true" ? `Room: ${threadId}` : null,
  ].filter((line) => line !== null).join("\n")).catch((error) => {
    log(`owner timeout notify failed: ${error.message}`);
  });

  await maybeHeartbeat("stopped", true, {
    stop_reason: "owner_reply_timeout",
    close_result: {
      id: result?.id ?? null,
      error: result?.error || null,
      closed: result?.closed ?? null,
    },
  });
  return false;
}

async function sendOpeningIfNeeded() {
  if (role !== "host") return;
  const all = await getMessages(-1, 0);
  if (all.length > 0) return;

  log("Thread empty; asking OpenClaw for opening message");
  const parsed = await gatewayParsed(openingPrompt(), "opening");
  if (parsed.invalid) {
    return await stopForUnsafeAgentOutput(parsed, { phase: "opening" });
  }
  if (parsed.ask_owner) {
    await enterWaitingOwner(parsed, { phase: "opening" });
    return true;
  }
  const text = parsed.close ? `I am ready to coordinate this: ${goal}` : parsed.text;
  if (!text) {
    return await stopForUnsafeAgentOutput({ invalid: true, reason: "empty_agent_output" }, { phase: "opening" });
  }

  const recheck = await getMessages(-1, 0);
  if (recheck.length > 0) {
    log("Opening skipped; peer spoke while opening was generating");
    return;
  }

  const key = idempotencyKey("open", threadId, role, sha(text));
  const result = await postMessage(text, key);
  if (result?.id != null) {
    setCursor(result.id);
    log(`Opening posted (id=${result.id})`);
  } else {
    log(`Opening post failed: ${JSON.stringify(result)}`);
  }
  return true;
}

async function run() {
  preflight();
  writeRuntimeState("starting", { preflight: "ok" });
  await maybeHeartbeat("starting", true, { preflight: "ok" });

  log(`Started ${VERSION}. Relay=${relayBase}`);
  log(`Agent=${agentId} SessionKey=${sessionKey} Gateway=${resolveGatewayUrl()}`);

  const otherRole = role === "host" ? "guest" : "host";
  let includeContext = true;

  const openingOk = await sendOpeningIfNeeded();
  if (openingOk === false) return;

  while (true) {
    await maybeHeartbeat(bridgeState.waiting_owner?.question_id ? "waiting_owner" : "running", false, {
      mandates,
      mandate_approvals: bridgeState.mandate_approvals || {},
      waiting_owner: publicWaitingOwner(),
    });

    const threadState = await getThreadState().catch(async (error) => {
      if (isFatalRelayError(error)) throw error;
      const backoffMs = relayBackoffMs(error);
      log(`state fetch failed: ${error.message}; backoff_ms=${backoffMs}`);
      await delay(backoffMs);
      return null;
    });
    if (!threadState) continue;
    if (threadState?.closed) {
      const summary = threadState.summary || "The room closed.";
      log(`Thread closed. Summary: ${summary}`);
      await notifyOwnerOnce(`thread-closed:${sha(summary)}`, summary).catch((error) => {
        log(`owner notify failed: ${error.message}`);
      });
      await maybeHeartbeat("stopped", true, { stop_reason: "thread_closed" });
      break;
    }

    if (bridgeState.waiting_owner?.question_id) {
      const keepRunning = await handleWaitingOwner();
      if (!keepRunning) return;
      continue;
    }

    const messages = await getMessages(bridgeState.cursor ?? -1, POLL_WAIT_SECONDS);
    if (!messages.length) continue;

    for (const message of messages) {
      if (Number(message.id) <= Number(bridgeState.cursor ?? -1)) continue;

      if (message.from === role) {
        setCursor(message.id);
        continue;
      }

      if (message.kind === "close") {
        await handlePeerClose(message);
        return;
      }

      if (message.kind === "ask_owner" || message.kind === "owner_reply") {
        setCursor(message.id);
        continue;
      }

      if (message.from !== otherRole) {
        setCursor(message.id);
        continue;
      }

      log(`New from ${otherRole} (id=${message.id}): ${message.text}`);
      const peerDateViolation = peerRequestsUnsupportedDateConfirmation(message.text);
      if (peerDateViolation) {
        await enterWaitingOwner({
          ask_owner: true,
          question: peerDateConfirmationQuestion(peerDateViolation),
          marker_inferred: true,
        }, {
          peer_message_id: message.id,
          peer_text: message.text,
          violation: peerDateViolation,
        });
        break;
      }

      let reply;
      const allMessages = await getMessages(-1, 0);
      const messageCount = negotiationMessageCount(allMessages);
      try {
        reply = await gatewayParsed(replyPrompt(otherRole, message.text, includeContext, messageCount), "reply");
        includeContext = false;
      } catch (error) {
        log(`Gateway error: ${error.message}`);
        await maybeHeartbeat("error", true, { last_error: error.message });
        continue;
      }

      let parsed = reply;
      if (parsed.close && minMessages && messageCount < minMessages) {
        bridgeState.early_close_suppressed = Number(bridgeState.early_close_suppressed || 0) + 1;
        persistState();
        await maybeHeartbeat("running", true, {
          early_close_suppressed: bridgeState.early_close_suppressed,
          message_count: messageCount,
          min_messages: minMessages,
        });
        log(`early close suppressed at message_count=${messageCount}; min_messages=${minMessages}`);
        try {
          parsed = await gatewayParsed(earlyClosePrompt(otherRole, message.text, parsed.summary, messageCount), "early-close");
        } catch (error) {
          log(`Gateway error after early-close suppression: ${error.message}`);
          await maybeHeartbeat("error", true, { last_error: error.message });
          continue;
        }
        if (parsed.close) {
          parsed = {
            close: false,
            text: "Before we close, let's confirm one more detail on scope, payment, usage rights, or approval.",
            marker_inferred: true,
          };
          log("early close fallback converted repeated close into REPLY");
        }
      }
      const keepRunning = await handleParsedReply(parsed, {
        peer_message_id: message.id,
        peer_text: message.text,
      });
      if (!keepRunning) {
        return;
      }
    }
  }
}

run().catch(async (error) => {
  try {
    writeRuntimeState("failed", { last_error: error.message });
  } catch {}
  try {
    await maybeHeartbeat("failed", true, { last_error: error.message });
  } catch (heartbeatError) {
    log(`failed heartbeat could not be sent: ${heartbeatError.message}`);
  }
  console.error(error);
  process.exit(1);
});
