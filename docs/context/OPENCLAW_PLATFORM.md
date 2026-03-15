# OpenClaw Platform Architecture

> 这份文档是 ClawRoom 开发者对 OpenClaw 工作原理的备忘。
> 来源：OpenClaw 官方文档 (docs.openclaw.ai) 深度研究。

## 关键事实

| 事实 | 说明 |
|---|---|
| Gateway 是**长驻 daemon** | 跑在用户 Mac 或云端 Docker，通过 `launchd`/`systemd` 管理，进程永不退出 |
| Gateway 管理所有 channel | Telegram、WhatsApp、Slack、Discord、Signal、WebChat 都通过同一个 Gateway |
| Session 跨消息持久化 | `~/.openclaw/agents/<id>/sessions/<id>.jsonl`，context 跨消息保留 |
| 单次 run 有超时 | 默认 600s（10 分钟），可配置 |
| 消息排队 | 同一 session 的请求串行执行，后到的等前一个完成 |

## 两种 Agent 执行模式

```
OpenClaw Gateway (长驻)
├── 模式 A: pi-mono 嵌入式运行时
│   ├── 直接调 LLM API（Claude / GPT / 任意 provider）
│   ├── 支持 tool calling（bash / exec / read / write / browser）
│   └── 单次 run 超时：默认 600s
│
└── 模式 B: ACP Session（Agent Client Protocol）
    ├── spawn 完整的 Claude Code CLI 子进程
    ├── spawn 完整的 Codex CLI 子进程
    └── 独立进程，不受 600s 限制
```

## 关于"Telegram 能不能做长对话"

**常见误解**："Telegram 是 webhook，处理完就结束，不能做长对话"

**实际情况**：

| 层 | 实际行为 |
|---|---|
| Telegram channel | 消息通过 grammY 持续连接，不是 serverless webhook |
| Gateway daemon | 一直运行，可以维持子进程（bridge.sh 不会被随机杀死） |
| pi-mono run | 一次 run 可跑 600s + 多次 tool calls（curl、bash 等） |
| ACP harness | Claude Code CLI 作为子进程，完全独立，无时间限制 |

**结论**：OpenClaw + Telegram 能可靠地支持 bridge daemon 模式。"进程被回收"风险远低于最初判断。

## 对 ClawRoom 执行策略的影响

决定选哪种策略的不是 "Telegram vs Terminal"，而是：

1. **Agent run 超时是否够用？**（pi-mono 默认 600s）
2. **Provider 是 LLM 直连还是 harness？**（ACP harness 无超时限制）
3. **是否需要 owner escalation？**（Inline 最流畅，Bridge 旁路通知）

详见 `ARCHITECTURE.md` 的执行策略选择逻辑。

## 参考链接

- [OpenClaw Gateway Architecture](https://docs.openclaw.ai/architecture)
- [OpenClaw Agent Runtime](https://docs.openclaw.ai/concepts/agent)
- [OpenClaw Agent Loop](https://docs.openclaw.ai/concepts/agent-loop)
- [OpenClaw Multi-Agent Sandbox](https://docs.openclaw.ai/tools/multi-agent-sandbox-tools)
