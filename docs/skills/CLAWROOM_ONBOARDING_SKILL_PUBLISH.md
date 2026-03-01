# ClawRoom Onboarding Skill 发布与引用指南

Skill 路径：
- `skills/clawroom`

已创建发布仓库：
- `https://github.com/heyzgj/clawroom`

## 1. 发布前检查

1. `SKILL.md` frontmatter 保持单行值（兼容 OpenClaw parser）。
2. `name` 使用稳定 slug：`clawroom`。
3. `metadata.version` 与变更同步递增。
4. 可执行脚本可直接运行：

```bash
python skills/clawroom/scripts/create_room.py --help
```

## 2. 在 skills.sh 生态可引用

### 2.1 公开仓库路径

当前仓库（已可访问）：
- `https://github.com/heyzgj/clawroom/tree/main`

### 2.2 通过 skills CLI 引用安装

```bash
npx skills add https://github.com/heyzgj/clawroom/tree/main
```

### 2.3 指定 agent 安装（示例：codex）

```bash
npx skills add https://github.com/heyzgj/clawroom/tree/main -a codex -y
```

## 3. 发布到 ClawHub（OpenClaw）

### 3.1 安装 ClawHub CLI

```bash
npm i -g clawhub
```

### 3.2 登录

```bash
clawhub login
```

### 3.3 发起发布

本地目录发布（推荐）：

```bash
clawhub publish /Users/supergeorge/Desktop/project/clawroom \
  --slug clawroom \
  --name "ClawRoom" \
  --version 1.0.0 \
  --tags latest
```

### 3.4 引用方式（OpenClaw）

```bash
clawhub install clawroom
```

## 4. 版本策略

1. 每次行为变更都升级 `metadata.version`。
2. 采用 Git tag 对齐版本（例如 `skill/clawroom/1.0.0`）。
3. 发布描述包含：
- 变更摘要（create/join/monitor）
- 兼容性（API 字段、默认值、preflight）
- 回滚建议（回退到上一个 tag）

## 5. 来源（2026-03-01 校验）

- skills.sh 官方仓库（发布命令与目录发现规则）：
  [https://github.com/vercel-labs/skills](https://github.com/vercel-labs/skills)
- OpenClaw Skills 文档（SKILL frontmatter 约束、安装方式）：
  [https://docs.openclaw.ai/tools/creating-skills](https://docs.openclaw.ai/tools/creating-skills)
  [https://docs.openclaw.ai/tools/skills](https://docs.openclaw.ai/tools/skills)
- OpenClaw 官方 `clawhub` skill（发布对话命令）：
  [https://raw.githubusercontent.com/openclaw/openclaw/main/skills/clawhub/SKILL.md](https://raw.githubusercontent.com/openclaw/openclaw/main/skills/clawhub/SKILL.md)
