import { badRequest, json } from "./worker_util";

type IntakeResolveRequest = {
  owner_request?: unknown;
  owner_reply?: unknown;
  counterpart_hint?: unknown;
};

const CJK_RE = /[\u4e00-\u9fff]/;
const URL_RE = /https?:\/\/\S+/i;
const NON_SLUG_RE = /[^a-z0-9_]+/g;

function containsCjk(text: string): boolean {
  return CJK_RE.test(text);
}

function normalized(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, " ");
}

function keywordScore(text: string, keywords: string[]): number {
  return keywords.reduce((score, keyword) => score + (keyword && text.includes(keyword) ? 1 : 0), 0);
}

function slugifyCounterpartHint(value: string): string {
  const raw = normalized(value).replace(/^@+/, "");
  if (!raw) return "";
  const slug = raw.replace(NON_SLUG_RE, "_").replace(/^_+|_+$/g, "");
  if (!slug || slug === "host") return "";
  return slug.slice(0, 60);
}

function inferCounterpartSlot(combinedText: string, counterpartHint: string): string {
  const hintSlug = slugifyCounterpartHint(counterpartHint);
  if (hintSlug) return hintSlug;
  const text = normalized(combinedText);
  if (["another openclaw", "另一个 openclaw", "另一个openclaw"].some((marker) => text.includes(marker))) {
    return "counterpart_openclaw";
  }
  if (["another agent", "另一个 agent", "另一个agent"].some((marker) => text.includes(marker))) {
    return "peer_agent";
  }
  return "counterpart";
}

function inferScenarioKey(text: string): string {
  const scores: Array<[string, number]> = [
    ["feedback_prioritization", keywordScore(text, ["feedback", "priority", "prioritize", "用户反馈", "优先级", "先做哪类反馈", "优先做"])],
    ["weekly_content_plan", keywordScore(text, ["content", "calendar", "social", "post", "小红书", "内容", "内容日历", "下周内容", "发什么"])],
    ["beta_launch_readiness", keywordScore(text, ["beta", "launch", "ship", "release", "上线", "发布", "能不能发", "ship this"])],
    ["cross_role_alignment", keywordScore(text, ["需求文档", "prd", "requirement", "product", "研发", "运营", "align", "对齐", "产品经理"])],
    ["founder_persona_sync", keywordScore(text, ["创始人", "persona", "人设", "海外社媒", "小红书账号", "founder", "voice"])],
    ["task_okr_sync", keywordScore(text, ["okr", "工作计划", "任务拆解", "认领", "同步", "进度", "执行"])],
  ];
  scores.sort((a, b) => b[1] - a[1]);
  return scores[0] && scores[0][1] > 0 ? scores[0][0] : "generic_decision";
}

function scenarioShape(key: string, lang: "zh" | "en"): { topic: string; goal: string; required_fields: string[]; field_principles: Record<string, string> } {
  const zh = lang === "zh";
  if (key === "feedback_prioritization") {
    return {
      topic: zh ? "用户反馈优先级排序" : "User feedback prioritization",
      goal: zh ? "一起判断先做哪类用户反馈，并带回优先级结论、理由和第一周实验方案。" : "Decide which user feedback to prioritize first and return a priority call, rationale, and a week-one experiment plan.",
      required_fields: ["priority_call", "rationale", "experiment_plan"],
      field_principles: {
        priority_call: "Must name the first feedback category to tackle.",
        rationale: "Must explain why that category should come first in concrete terms.",
        experiment_plan: "Must include one action this week and a clear pass/fail signal.",
      },
    };
  }
  if (key === "weekly_content_plan") {
    return {
      topic: zh ? "下周内容规划" : "Weekly content planning",
      goal: zh ? "一起排出下周内容安排，并带回内容日历、核心角度和接下来的动作。" : "Plan next week's content and return a content calendar, core angles, and next steps.",
      required_fields: ["content_plan", "core_angles", "next_steps"],
      field_principles: {
        content_plan: "Must outline the actual posts or deliverables for the week.",
        core_angles: "Must explain the main angle or hook for each item.",
        next_steps: "Must include one action the owner can take this week.",
      },
    };
  }
  if (key === "beta_launch_readiness") {
    return {
      topic: zh ? "Beta 发布判断" : "Beta launch readiness",
      goal: zh ? "一起判断这个 beta 现在能不能发，并带回发布结论、主要风险和下一步。" : "Judge whether the beta is ready to launch and return a launch call, top risks, and next steps.",
      required_fields: ["launch_decision", "blocking_risks", "next_steps"],
      field_principles: {
        launch_decision: "Must be a concrete launch or no-launch call.",
        blocking_risks: "Must name the risks that actually matter for launch.",
        next_steps: "Must include one concrete next action.",
      },
    };
  }
  if (key === "cross_role_alignment") {
    return {
      topic: zh ? "跨角色对齐" : "Cross-role alignment",
      goal: zh ? "一起对齐产品、研发和运营的理解，并带回共同理解、待澄清问题和 owner 需要处理的动作。" : "Align product, engineering, and ops and return shared understanding, open questions, and owner actions.",
      required_fields: ["shared_understanding", "open_questions", "owner_actions"],
      field_principles: {
        shared_understanding: "Must summarize what the teams are actually trying to do.",
        open_questions: "Must list the questions that still need a human answer.",
        owner_actions: "Must include concrete actions for the owner.",
      },
    };
  }
  if (key === "founder_persona_sync") {
    return {
      topic: zh ? "创始人人设周同步" : "Founder persona weekly sync",
      goal: zh ? "一起对齐本周创始人人设的内容方向，并带回本周主题、可复用素材和需要避免的表达。" : "Align the founder persona for the week and return themes, reusable assets, and do-not-say guidance.",
      required_fields: ["this_week_themes", "shared_assets", "do_not_say"],
      field_principles: {
        this_week_themes: "Must name the concrete themes for this week.",
        shared_assets: "Must list reusable assets both channels can use.",
        do_not_say: "Must list phrases or positions to avoid.",
      },
    };
  }
  if (key === "task_okr_sync") {
    return {
      topic: zh ? "任务拆解与 OKR 同步" : "Task and OKR sync",
      goal: zh ? "一起把任务拆解同步清楚，并带回工作计划、负责人和检查指标。" : "Align task breakdown and OKR progress and return workplan, owners, and check-in metrics.",
      required_fields: ["workplan", "owners", "check_in_metric"],
      field_principles: {
        workplan: "Must list the concrete work to be done next.",
        owners: "Must map work items to owners.",
        check_in_metric: "Must define how progress will be checked.",
      },
    };
  }
  return {
    topic: zh ? "协作决策" : "Collaborative decision",
    goal: zh ? "一起把问题收敛成一个可执行结论，并带回决定、理由和下一步。" : "Converge on an executable answer and return a decision, rationale, and next steps.",
    required_fields: ["decision", "rationale", "next_steps"],
    field_principles: {},
  };
}

function needsSourceMaterial(scenarioKey: string, text: string): boolean {
  if (scenarioKey !== "cross_role_alignment") return false;
  if (URL_RE.test(text)) return false;
  if (text.trim().length >= 180) return false;
  return true;
}

function buildQuestion(
  scenarioKey: string,
  lang: "zh" | "en",
  draft: { topic: string; required_fields: string[] },
  needsSource: boolean,
): string {
  const [a, b, c] = draft.required_fields;
  if (lang === "zh") {
    if (needsSource) {
      return `我可以开一个房间来做${draft.topic}，并带回${a}、${b}和${c}。把需求文档贴给我，或者发链接，我就继续。`;
    }
    if (scenarioKey === "feedback_prioritization") {
      return "我可以开一个房间来排反馈优先级，并带回优先级结论、理由和第一周实验方案。要我按这个形状开吗？";
    }
    if (scenarioKey === "weekly_content_plan") {
      return "我可以开一个房间来排下周内容，并带回内容日历、核心角度和下一步。要我按这个形状开吗？";
    }
    if (scenarioKey === "beta_launch_readiness") {
      return "我可以开一个房间来判断这个 beta 能不能发，并带回发布结论、主要风险和下一步。要我按这个形状开吗？";
    }
    if (scenarioKey === "founder_persona_sync") {
      return "我可以开一个房间来做创始人人设周同步，并带回本周主题、可复用素材和需要避免的表达。要我按这个形状开吗？";
    }
    if (scenarioKey === "task_okr_sync") {
      return "我可以开一个房间来同步任务拆解和 OKR，并带回工作计划、负责人和检查指标。要我按这个形状开吗？";
    }
    return `我可以开一个房间来处理${draft.topic}，并带回${a}、${b}和${c}。要我按这个形状开吗？`;
  }
  if (needsSource) {
    return `I can open a room for ${draft.topic} and bring back ${a}, ${b}, and ${c}. Paste the requirement doc or send the link, and I will continue.`;
  }
  return `I can open a room for ${draft.topic} and bring back ${a}, ${b}, and ${c}. Want me to proceed with that shape?`;
}

function buildClarifyGuidance(
  scenarioKey: string,
  lang: "zh" | "en",
  blockers: string[],
  draft: { topic: string; required_fields: string[] },
  needsSource: boolean,
): Record<string, unknown> | null {
  if (!blockers.length) return null;
  const askFor: string[] = [];
  const blockerDetails: Array<Record<string, string>> = [];
  if (blockers.includes("owner_confirmation")) {
    askFor.push("confirmation");
    blockerDetails.push({
      code: "owner_confirmation",
      reason: "Need one explicit owner reply before creating the room.",
    });
  }
  if (blockers.includes("source_material")) {
    askFor.push("source_material");
    blockerDetails.push({
      code: "source_material",
      reason: "Need the requirement doc or source material to avoid guessing the real work.",
    });
  }
  return {
    style: "brief_human",
    language: lang,
    question_goal: lang === "zh"
      ? `确认${draft.topic}的房间形状，并解除开房阻塞。`
      : `Confirm the room shape for ${draft.topic} and unblock creation.`,
    ask_for: askFor,
    blockers: blockerDetails,
    suggested_outcomes: [...draft.required_fields],
    example_question: buildQuestion(scenarioKey, lang, draft, needsSource),
    do_not_copy_verbatim: true,
  };
}

export async function handleIntakeResolve(request: Request): Promise<Response> {
  let body: IntakeResolveRequest;
  try {
    body = await request.json() as IntakeResolveRequest;
  } catch {
    return badRequest("invalid json");
  }
  const ownerRequest = String(body.owner_request || "").trim();
  if (!ownerRequest) return badRequest("owner_request required");
  const ownerReply = String(body.owner_reply || "").trim();
  const counterpartHint = String(body.counterpart_hint || "").trim();
  const combined = [ownerRequest, ownerReply, counterpartHint].filter(Boolean).join("\n");
  const lang: "zh" | "en" = containsCjk(combined) ? "zh" : "en";
  const scenarioKey = inferScenarioKey(normalized(combined));
  const shape = scenarioShape(scenarioKey, lang);
  const sourceNeeded = needsSourceMaterial(scenarioKey, combined);
  const counterpartSlot = inferCounterpartSlot(combined, counterpartHint);

  const blockers: string[] = [];
  if (!ownerReply) blockers.push("owner_confirmation");
  if (sourceNeeded) blockers.push("source_material");
  const guidance = buildClarifyGuidance(scenarioKey, lang, blockers, shape, sourceNeeded);

  const response = {
    status: blockers.length === 0 ? "ready" : "input_required",
    ready_to_create: blockers.length === 0,
    draft_payload: {
      topic: shape.topic,
      goal: shape.goal,
      participants: ["host", counterpartSlot],
      required_fields: [...shape.required_fields],
      outcome_contract: {
        field_principles: { ...shape.field_principles },
      },
    },
    missing_blockers: blockers,
    clarify_guidance: guidance,
    one_question: guidance ? guidance.example_question : null,
    one_question_mode: guidance ? "example_only" : null,
    soft_notes: [
      "Use reasonable defaults for minor room config.",
      "Only blockers should stop room creation.",
      "Rewrite the clarify naturally instead of copying the example verbatim.",
    ],
    inferred: {
      scenario_key: scenarioKey,
      language: lang,
      counterpart_slot: counterpartSlot,
    },
  };
  return json(response);
}
