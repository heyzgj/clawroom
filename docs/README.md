# ClawRoom Docs

> **ClawRoom 当前首先是一个 cross-runtime、cross-owner 的 AI agent 协作执行底座**：让不同 owner、不同 runtime 的 gateway/worker 能在一个 bounded work thread 上可靠协作、升级、恢复，而不是先把它当完整产品壳。

所有的规范、设计、状态都在这个 `docs/` 目录下。

## 📍 目录导航

按更新频率从低到高分为 4 层：

### 🟢 `context/` (背景层)
> **了解项目是什么、系统怎么构成**。适合新 agent 阅读，极少变更。

- [PRD.md](context/PRD.md) - 当前 repo 的产品定位、边界、用户旅程与长期方向
- [ARCHITECTURE.md](context/ARCHITECTURE.md) - 当前基础层架构：gateway / runnerd / bridges / room core / ops
- [OPENCLAW_PLATFORM.md](context/OPENCLAW_PLATFORM.md) - OpenClaw 基础架构备忘（Daemon, pi-mono, ACP）

### 🔵 `spec/` (规范层)
> **编写代码的重要契约**。仅在调整协议时变更。

- [PROTOCOL.md](spec/PROTOCOL.md) - ClawRoom 协议规范、消息/事件结构、服务端兜底规则
- [SDK_INTERFACE.md](spec/SDK_INTERFACE.md) - Internal shared module（核心客户端内核）的接口定义
- [CONFORMANCE.md](spec/CONFORMANCE.md) - 所有 runtime agent 必须遵循的契约测试场景
- [TEST_STRATEGY.md](spec/TEST_STRATEGY.md) - 分层测试策略与发布门禁（L0-L4）

### 🟡 `ops/` (运维层)
> **如何部署和排障**。基本不变更。

- [DEPLOY.md](ops/DEPLOY.md) - 开发与生产环境部署指引 (Cloudflare Worker & Pages)
- [RUNBOOK.md](ops/RUNBOOK.md) - 常见问题的排障手册和恢复指南

### 🔴 `progress/` (进度层)
> **了解项目做到了哪里、踩了什么坑**。频繁变更，每个 Sprint/PR 都会更新。

- [ROADMAP.md](progress/ROADMAP.md) - 当前阶段严格路线图：foundation first, upper layers later
- [CHANGELOG.md](progress/CHANGELOG.md) - 重大功能与架构更新的时间线记录
- [KNOWN_ISSUES.md](progress/KNOWN_ISSUES.md) - 当前已知的 bug 列表及其 workaround
- [DEBUG_LESSONS.md](progress/DEBUG_LESSONS.md) - **血泪史**：记录花 >30 分钟排查的深度坑，防重复踩
- [TELEGRAM_E2E_LOG.md](progress/TELEGRAM_E2E_LOG.md) - 真实 Telegram/OpenClaw 回归记录与 learnings

---

## 🚀 新人/Agent 指南

**1. 若你想快速理解项目大局：**
先读 `context/PRD.md` 和 `context/ARCHITECTURE.md`，然后看 `progress/ROADMAP.md` 知道我们在忙什么。

**2. 若你想写代码 / 改 bug：**
查阅 `spec/PROTOCOL.md` 与 `spec/SDK_INTERFACE.md`，然后必须查 `progress/KNOWN_ISSUES.md` 看当前有什么雷。代码改完需确保符合 `spec/CONFORMANCE.md` 测试场景，并按 `spec/TEST_STRATEGY.md` 过对应层级的测试。

**3. 若你刚完成一个 Task：**
- 在 `progress/ROADMAP.md` 勾选对应的 checkbox
- 每完成一个 Phase/重大更新，去 `progress/CHANGELOG.md` 加一行
- 若排出了诡异的 bug，去 `progress/DEBUG_LESSONS.md` 记录教训
- 若跑了真实 Telegram/OpenClaw 回归，去 `progress/TELEGRAM_E2E_LOG.md` 追加结果和 learnings

## 📂 历史区

MVP 阶段的文档和废弃决定已移至 `decisions/archive/`。
架构设计决策存放在 `decisions/`。
UI 设计与前端原型需求存放在 `fe-design/`。
未并入主干层的提案存放在 `proposals/`。

当前最值得先读的提案：

- [technical_route_2026_03_12.md](proposals/technical_route_2026_03_12.md) - 基于 Symphony / Relay / A2A / agenthub / Paperclip 的严格路线收敛
- [project_control_plane_thesis.md](proposals/project_control_plane_thesis.md) - 北极星层：为什么 room 以后只是原子协作单元，而不是最终产品壳
