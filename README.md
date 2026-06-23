# Essence — Agentic Intelligence System

> **Local-first · Production-grade · Deterministic · Observable · Safe**

Essence is an autonomous agent kernel built on the **APDE** (Autonomous Planning, Dispatch, and Execution) architecture. It turns natural-language prompts into frozen DAG plans, executes them task-by-task inside sandboxed contexts, verifies results against strict rubrics, and routes every LLM call through a multi-provider SmartRouter — all on your own hardware with no cloud lock-in.

---

## Table of Contents

- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [CLI Reference](#cli-reference)
- [Web UI & Server](#web-ui--server)
- [Terminal Pane](#terminal-pane)
- [Prompt Management](#prompt-management)
- [API Reference](#api-reference)
- [SmartRouter](#smartrouter)
- [SmartPeerSelector (A2A)](#smartpeerselector-a2a)
- [Skill System](#skill-system)
- [TUI Dashboard](#tui-dashboard)
- [Configuration](#configuration)
- [Environment Variables](#environment-variables)
- [Hardware Tiers](#hardware-tiers)
- [Security & Safety](#security--safety)
- [Memory System](#memory-system)
- [Channels & Integrations](#channels--integrations)
- [Multi-Agent (A2A)](#multi-agent-a2a)
- [Observability](#observability)
- [Development](#development)
- [License](#license)

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                           Essence Kernel (APDE)                       │
│                                                                       │
│  Prompt ─► IntentCompressor ─► Decomposer ─► PlanDAG (frozen DAG)    │
│                                                                       │
│  PlanDAG ─► PipelineExecutor ─► Task (sandboxed) ─► Result           │
│                                                                       │
│  Result  ─► APDEVerifier ─► RubricRegistry ─► AuditTrail             │
└───────────────────────────────────────────────────────────────────────┘
         │                                    │
    GuardrailLayer (G1–G4)             AuditLogger (tamper-proof)
    QuotaStore (per-user)              DecisionQueue (human-in-loop)

┌───────────────────────────────────────────────────────────────────────┐
│                          Routing Fabric                               │
│                                                                       │
│  IntentRouter ─► TaskRouter ─► SubagentRouter                        │
│       │               │               │                               │
│  ProtocolRouter   SmartRouter    SmartPeerSelector                    │
│  (scheme→transport) (LLM provider)  (A2A peer ranking)               │
└───────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────┐
│                          Web Server (FastAPI)                         │
│                                                                       │
│  REST API  ·  OpenAI-compatible /v1/  ·  SSE streaming               │
│  A2A /.well-known/agent.json          ·  MCP /mcp                    │
│  WebSocket /ws/chat  /ws/terminal                                     │
│                                                                       │
│  Static UI (single-file HTML)                                        │
│  Chat · Agents · Skills · Prompts · Terminal · Settings · …          │
└───────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────┐
│                          Infrastructure                               │
│                                                                       │
│  Memory: Working (KV) · Episodic (JSONL/SQLite) · Semantic (FAISS)   │
│  Observability: Prometheus /metrics · OpenTelemetry spans             │
│  Auth: JWT + API-key middleware                                       │
│  Rate limiting: token bucket (in-process or Valkey)                  │
│  Circuit breakers: per-provider, per-adapter                         │
│  Health monitor: background probes + /api/health                     │
└───────────────────────────────────────────────────────────────────────┘
```

### Core Subsystems

| Subsystem | Package | Description |
|-----------|---------|-------------|
| **APDE Kernel** | `essence.boot` | Plans tasks as frozen DAGs, executes step-by-step, verifies results |
| **Capsule Store** | `essence.infra.capsule_store` | SQLite-backed persistence for `IntentCapsule` and `PlanDAG` |
| **GuardrailLayer** | `essence.security` | Multi-tier safety (G1–G4) with per-user quota enforcement |
| **SmartRouter** | `essence.backends.smart_router` | Intent-aware multi-provider LLM routing with circuit breaker and TTL health cache |
| **SmartPeerSelector** | `essence.protocols.a2a` | Composite A2A peer scoring (capability 40 % · latency EMA 35 % · recency 25 %) |
| **Routing Fabric** | `essence.routing` | Intent → Task → Subagent routers, event bus, protocol router |
| **PromptManager** | `essence.prompts` | CRUD, usage-frequency scoring, auto-learning, and progressive suggestions |
| **Skill System** | `essence.skills` | Discovery, execution, autonomous builder, usage scoring |
| **Autonomy Layer** | `essence.autonomy` | Goal manager and curiosity engine for proactive behaviour |
| **Memory System** | `essence.memory` | Three-layer: working (KV) · episodic (JSONL) · semantic (vector) |
| **Channels** | `essence.channels` | Telegram · Discord · WhatsApp · Gmail · Slack · Matrix · Teams |
| **A2A Protocol** | `essence.protocols.a2a` | Agent-to-Agent peer discovery and orchestration |
| **MCP Server** | `essence.tools.mcp` | Model Context Protocol endpoint for Claude Desktop / Cursor |
| **Observability** | `essence.infra.metrics`, `essence.infra.otel` | Prometheus metrics and OpenTelemetry spans |

---

## Quick Start

```bash
# 1. Clone and install
git clone <repo-url> && cd essence
pip install -e .

# 2. Scaffold workspace and probe hardware
essence

# 3. Install hardware-appropriate dependencies
essence install

# 4. Pull a local model (requires Ollama)
essence pull llama3.2

# 5. Start the full web UI
essence up
# → http://localhost:7860

# 6. Or chat directly in the terminal
essence chat
```

### With cloud providers

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GEMINI_API_KEY="..."

essence up
# SmartRouter will auto-select the best available provider per intent
```

---

## Installation

### Requirements

- Python **3.10+** (3.11+ recommended)
- [Ollama](https://ollama.com) for local LLM inference (optional when using cloud providers)
- 8 GB RAM minimum (T0); 16 GB+ recommended for T1+

### Install options

```bash
pip install -e .                    # core only — no optional deps
pip install -e ".[full]"            # all optional integrations
pip install -e ".[analytics]"       # pandas / numpy / sklearn / faiss
pip install -e ".[test]"            # pytest + coverage
pip install -e ".[observability]"   # prometheus-client + opentelemetry
```

### First-run

```bash
essence                             # auto-detects hardware tier, scaffolds ~/.essence/
essence install                     # installs tier-appropriate packages
essence probe                       # prints detected tier, RAM, GPU, recommended model
```

---

## CLI Reference

```
Usage: essence [--workspace DIR] [--autonomy-tier N] <subcommand> ...
```

### Kernel (APDE engine)

| Command | Description |
|---------|-------------|
| `essence plan "<prompt>"` | Compress prompt → frozen PlanDAG; prints `capsule_id` |
| `essence tick <capsule_id>` | Advance one ready task in the plan |
| `essence plan-audit [capsule_id]` | Print kernel audit trail (all or per-capsule) |
| `essence doctor` | Full system health check |

```bash
essence plan "Write a Python script that monitors disk usage"
# → capsule_id: c1a2b3d4-...

essence tick c1a2b3d4-...
# → {"status": "complete", "result": "..."}

essence plan-audit c1a2b3d4-...
```

### Workspace & Server

| Command | Description |
|---------|-------------|
| `essence install` | Install hardware-appropriate dependencies |
| `essence up [--port N]` | Start FastAPI server + Web UI (default: 7860) |
| `essence chat [--model M]` | Interactive streaming terminal chat |
| `essence tui` | Textual TUI dashboard |
| `essence agent "<task>"` | One-shot multi-agent task |
| `essence probe` | Hardware detection report |
| `essence scaffold` | (Re)generate workspace directory |
| `essence models` | List available models |
| `essence bench` | Performance benchmark |
| `essence pull <model>` | Pull a model via Ollama |
| `essence control [--open]` | Print/open server URL |

### Agent & Evaluation

| Command | Description |
|---------|-------------|
| `essence eval [--model M]` | Run behavioral eval harness |
| `essence eval --save-baseline` | Save results as regression baseline |
| `essence eval --regression` | Compare against baseline; exit 1 on drop |
| `essence eval --drift` | Semantic drift check with optional webhook |

### Memory & Workspace

| Command | Description |
|---------|-------------|
| `essence memory export [--out FILE]` | Export encrypted memory bundle |
| `essence memory import <file>` | Import memory bundle |
| `essence export [dest]` | Export full workspace as ZIP |
| `essence import <file.zip>` | Import workspace from ZIP |
| `essence sop` | List Standard Operating Procedures |
| `essence team` | Team memory namespace info |
| `essence cost` | Token cost report |

### Decisions & Workflows

| Command | Description |
|---------|-------------|
| `essence decisions list` | List pending human-approval decisions |
| `essence decisions approve <id>` | Approve a pending decision |
| `essence decisions reject <id>` | Reject a pending decision |
| `essence decisions approve-all` | Approve all pending decisions |
| `essence workflows` | List recent workflow executions |
| `essence workflows --id <id>` | Detailed step view |

### Channels, Peers & Skills

| Command | Description |
|---------|-------------|
| `essence channels` | Show channel adapter status |
| `essence peers` | List known A2A peer agents |
| `essence peers --add <url>` | Register a new peer |
| `essence skill list` | List installed skills |
| `essence skill new <name>` | Scaffold a new skill |
| `essence skill install <url>` | Install a skill from URL |
| `essence skill gulp <url>` | Absorb skill from any open-source URL |

---

## Web UI & Server

```bash
essence up                          # → http://localhost:7860
essence up --port 8080 --model llama3.2
```

The single-file Web UI (`essence/server/static_ui.html`) includes:

| Tab | Description |
|-----|-------------|
| **Chat** | Streaming conversation with canvas, terminal pane, and quick prompts |
| **Agents** | Agent roster, status, task assignment |
| **Cron** | Scheduled task configuration |
| **Skills** | Skill browser, installer, and usage stats |
| **Prompts** | Prompt library with usage-frequency ranking |
| **Models** | Provider health, model selection, latency stats |
| **Gateway** | SmartRouter status and routing configuration |
| **Traces** | OpenTelemetry span viewer |
| **Workflows** | Execution history |
| **Logs** | Live log stream |
| **MCP** | MCP server status and tool listing |
| **Settings** | Sandbox, permissions, cost budget, API keys |

---

## Terminal Pane

A collapsible PTY-backed terminal is embedded in the Chat view.

**Open/close:** Click the **Terminal** button in the chat header or press `Ctrl+\``.

**Resize:** Drag the handle between the chat area and the terminal.

**Transport:**

1. **WebSocket PTY** (`ws[s]://<host>/ws/terminal`) — full interactive shell with bash/sh, terminal resize, and streaming I/O. Used by default.
2. **REST fallback** (`POST /api/terminal/exec`) — executes single commands and returns `{stdout, stderr, returncode}`. Used when WebSocket is unavailable.

**Features:**
- Command history via ↑/↓ arrow keys
- Clear button
- ANSI escape decoding from the PTY
- Working directory is the Essence workspace

---

## Prompt Management

Prompts are stored in `workspace/prompts.json` and managed through the **Prompts** tab or the REST API.

### Auto-learning

`PromptManager` mines chat history for recurring patterns and promotes them to saved prompts when they cross a frequency threshold. Learned prompts are tagged `learned=True` and displayed with a purple badge in the UI.

### Progressive quick prompts

On the Chat empty-state, the quick-prompt buttons are populated from:
```
POST /api/prompts/suggest  {"context": "quick-start", "limit": 6}
```
Prompts are ranked by a decay-weighted usage score so the most-used prompts always appear first.

### Scoring formula

```
score = usage_count × decay_weight × (1 + tag_boost)
decay_weight = e^(-λ × days_since_last_use)   # λ = 0.05
```

---

## API Reference

All endpoints are served by the FastAPI server started with `essence up`.

### Chat & Completions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/chat/completions` | OpenAI-compatible streaming SSE endpoint |
| `POST` | `/api/chat` | Native chat endpoint (session-aware) |
| `GET`  | `/v1/models` | List available models |

### Sessions

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/v1/sessions` | List sessions |
| `POST` | `/v1/sessions` | Create a new session |
| `DELETE` | `/v1/sessions/{id}` | Delete a session |

### Agents

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/agents` | List registered agents |
| `POST` | `/api/agents/{id}/task` | Dispatch a task to an agent |
| `GET`  | `/api/agents/{id}/status` | Agent status and last result |

### Prompts

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/prompts` | List prompts (optional `?category=`) |
| `POST` | `/api/prompts` | Create a new prompt |
| `PUT`  | `/api/prompts/{id}` | Update an existing prompt |
| `DELETE` | `/api/prompts/{id}` | Delete a prompt |
| `POST` | `/api/prompts/{id}/use` | Record a use and return the prompt text |
| `GET`  | `/api/prompts/suggest` | Usage-ranked suggestions (`?context=&limit=`) |
| `POST` | `/api/prompts/suggest` | Usage-ranked suggestions (POST body form) |
| `GET`  | `/api/prompts/stats` | Aggregate usage statistics |

### Skills

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/skills` | List installed skills with usage scores |
| `POST` | `/api/skills` | Create a new skill |
| `POST` | `/api/skills/{name}/run` | Execute a skill |
| `DELETE` | `/api/skills/{name}` | Delete a skill |

### Peers (A2A)

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/peers` | List known A2A peers |
| `GET`  | `/api/peers/smart` | SmartPeerSelector-ranked peer list for an intent |
| `POST` | `/api/peers` | Register a new peer |
| `DELETE` | `/api/peers/{id}` | Remove a peer |

### Router & Health

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/router/status` | SmartRouter provider health and latency EMA |
| `GET`  | `/api/health` | System health (uptime, backends, circuit breakers) |
| `GET`  | `/api/status` | Lightweight status for telemetry polling |
| `GET`  | `/metrics` | Prometheus metrics (requires `ESSENCE_METRICS=1`) |

### Terminal

| Method | Path | Description |
|--------|------|-------------|
| `WS`   | `/ws/terminal` | PTY-backed interactive shell (WebSocket) |
| `POST` | `/api/terminal/exec` | Single-command REST execution fallback |

### Protocol & Discovery

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/.well-known/agent.json` | A2A agent card (capability declaration) |
| `POST` | `/mcp` | Model Context Protocol handler |
| `POST` | `/a2a/task` | Inbound A2A task delegation |

### WebSocket Events

| Path | Direction | Events |
|------|-----------|--------|
| `/ws/chat` | bidirectional | `{type:"user_message"}` → `{type:"token"}`, `{type:"done"}`, `{type:"error"}` |
| `/ws/terminal` | bidirectional | `{type:"input","data":"cmd\n"}` → `{type:"output","data":"..."}`, `{type:"exit","code":0}` |

---

## SmartRouter

`essence.backends.smart_router.SmartRouter` selects the best available provider and model for each LLM call.

### Selection algorithm

1. Ping all providers in parallel (TTL-cached for 10 s, semaphore-capped at 8 concurrent pings).
2. Skip providers whose circuit breaker is open.
3. Score each provider: `score = pref × 10 + tier × 2 + latency_score`.
4. Resolve the model for the winning provider using the precedence chain below.
5. Stream the completion; on failure, pick the next-ranked provider and repeat without splicing streams.

### Model resolution precedence

```
ESSENCE_MODEL_{PROVIDER}_{INTENT}   (e.g. ESSENCE_MODEL_OPENAI_CODING)
ESSENCE_MODEL_{INTENT}              (only if valid for the provider)
store.get_settings(provider)["default_model"]
_PROVIDER_MODEL_MAP[provider][intent]
provider.list_models()[0]
"default"
```

### Intent preferences

| Intent | Preferred order |
|--------|----------------|
| `reasoning` | anthropic → openai → gemini → ollama |
| `coding` | openai → anthropic → ollama → gemini |
| `research` | perplexity → gemini → anthropic → openai |
| `vision` | gemini → openai → anthropic → ollama |
| `planning` | anthropic → openai → gemini → ollama |
| `search` | perplexity → gemini → openai |
| `general` | ollama → gemini → openai → anthropic |
| `voice` | elevenlabs → local → openai |

### Circuit breaker

- Opens after **3 failures** within **60 seconds**
- Stays open for **120 seconds** before re-attempting
- `status()` includes `circuit_open: bool` per provider

### Environment overrides

```bash
ESSENCE_MODEL_ANTHROPIC_CODING=claude-sonnet-4-5   # provider+intent scoped
ESSENCE_MODEL_REASONING=o3-mini                    # intent-scoped fallback
```

---

## SmartPeerSelector (A2A)

`SmartPeerSelector` ranks known A2A peers for a given task intent using a composite score:

```
composite = 0.40 × capability_match
          + 0.35 × (1 - normalised_latency_ema)
          + 0.25 × recency_score
```

**Capability match** compares the task intent against the peer's declared skills from its `/.well-known/agent.json` agent card.

**Latency EMA** is updated on every completed delegation (α = 0.3).

**Recency** decays linearly over 24 hours.

```python
from essence.protocols.a2a import SmartPeerSelector

selector = SmartPeerSelector()
best_peers = selector.rank_peers(peers, intent="coding")
```

The API endpoint `/api/peers/smart?intent=coding` returns the ranked list as JSON.

---

## Skill System

Skills are Markdown-first capability bundles stored in `workspace/skills/<name>/SKILL.md`.

### Usage-frequency scoring

Every skill execution is recorded in `workspace/skills/.usage.json`:

```python
from essence.skills.discovery import SkillDiscovery

disc = SkillDiscovery(workspace)
disc.record_skill_usage("web_browse")       # increment counter
score = disc.skill_score("web_browse")       # decay-weighted score
top   = disc.top_skills(n=5)                 # ranked list
prompts = disc.progressive_prompts(n=6)      # quick-prompt suggestions
```

### Skill operations

```bash
essence skill list                           # list with usage counts
essence skill new my_skill                   # scaffold SKILL.md
essence skill install https://...            # install from URL
essence skill gulp https://github.com/...    # absorb from open-source
```

---

## TUI Dashboard

```bash
essence tui
```

Requires `pip install textual>=0.63`. The TUI provides:

- Live agent status panel
- Task queue and execution progress
- Memory inspector
- Log viewer
- Cost tracker

---

## Configuration

The workspace is scaffolded at `~/.essence/` (or `ESSENCE_WORKSPACE` env var):

```
~/.essence/
├── config.toml              # model, tier, backend, routing settings
├── SOUL.md                  # agent identity and personality
├── IDENTITY.md              # user identity and preferences
├── TOOLS.md                 # tool whitelist + domain policies
├── HEARTBEAT.md             # recurring task instructions
├── AGENTS.md                # agent roster and routing rules
├── MEMORY.md                # distilled long-term memory
├── GOALS.md                 # current goals and priorities
├── LEARNED.md               # append-only learning log
├── prompts.json             # saved prompts + usage counts
├── procedures/              # Standard Operating Procedures (.md)
├── skills/                  # installed skills (SKILL.md directories)
│   └── .usage.json          # skill usage-frequency counters
├── memory/                  # structured memory storage
│   ├── episodic.jsonl        # conversation history
│   ├── semantic.db           # vector index (FAISS or sqlite-vec)
│   └── working.json          # current session KV facts
├── sessions/                # JSONL conversation transcripts
├── logs/                    # execution logs
├── channel_identity.json    # cross-channel user identity map
└── tui_app.py               # auto-generated TUI application
```

Edit `SOUL.md` to change agent personality, `TOOLS.md` to restrict capabilities, and `IDENTITY.md` to set user context.

---

## Environment Variables

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `ESSENCE_WORKSPACE` | `~/.essence` | Workspace directory |
| `ESSENCE_MODEL` | *(auto)* | Override model selection globally |
| `ESSENCE_BACKEND` | *(auto)* | Force backend: `ollama`, `vllm`, `mlx`, `openai`, `anthropic` |
| `ESSENCE_TIER` | *(auto)* | Override hardware tier (0–3) |
| `ESSENCE_DEBUG` | `0` | Enable debug logging |
| `ESSENCE_METRICS` | `0` | Enable `/metrics` Prometheus endpoint |

### SmartRouter model overrides

| Variable | Example | Description |
|----------|---------|-------------|
| `ESSENCE_MODEL_{PROVIDER}_{INTENT}` | `ESSENCE_MODEL_OPENAI_CODING=gpt-4o` | Provider + intent scoped |
| `ESSENCE_MODEL_{INTENT}` | `ESSENCE_MODEL_REASONING=o3-mini` | Intent-scoped fallback |

### Safety & budget

| Variable | Default | Description |
|----------|---------|-------------|
| `ESSENCE_ALIGNMENT_FAILOPEN` | `0` | Fail open on alignment errors (unsafe) |
| `ESSENCE_VAULT_ALLOW_WEAK` | `0` | Allow XOR vault fallback when `cryptography` unavailable |
| `ESSENCE_COST_BUDGET` | `0` | Token budget per task (0 = unlimited) |

### Team & A2A

| Variable | Default | Description |
|----------|---------|-------------|
| `ESSENCE_TEAM_ID` | `local` | Team memory namespace |
| `ESSENCE_TEAM_SYNC` | `0` | Enable team memory sync |
| `ESSENCE_A2A_PEERS` | *(none)* | Comma-separated peer agent URLs |
| `ESSENCE_CONTAINER` | `1` | Enable container sandboxing for tool execution |

### LLM provider API keys

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic (Claude) |
| `OPENAI_API_KEY` | OpenAI (GPT, o-series) |
| `GEMINI_API_KEY` | Google Gemini |
| `PERPLEXITY_API_KEY` | Perplexity (research/search) |

### Local backends

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama server URL |
| `VLLM_HOST` | `http://127.0.0.1:8000` | vLLM server URL |
| `MLX_HOST` | `http://127.0.0.1:8080` | MLX-LM server URL |

### Channels

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token |
| `TELEGRAM_ALLOWED_IDS` | Comma-separated allowed chat IDs |
| `DISCORD_WEBHOOK_URL` | Discord outbound webhook |
| `DISCORD_BOT_TOKEN` | Discord Bot API token |
| `DISCORD_CHANNEL_ID` | Discord channel to poll |
| `SLACK_BOT_TOKEN` | Slack Bot token |

### Observability

| Variable | Description |
|----------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry OTLP export URL (e.g. `http://jaeger:4317`) |
| `ESSENCE_PROMETHEUS_GATEWAY` | Prometheus Pushgateway URL |

---

## Hardware Tiers

Essence auto-detects your hardware and installs tier-appropriate packages:

| Tier | Label | RAM | Acceleration | Recommended Models |
|------|-------|-----|--------------|--------------------|
| **T0** | Micro | < 8 GB | CPU only | `gemma2:2b`, `qwen2.5:1.5b` |
| **T1** | Standard | 8–16 GB | CPU / GGUF | `llama3.2:3b`, `mistral:7b` |
| **T2** | Pro | 16–64 GB | Apple Silicon / CUDA | `llama3.1:8b`, `qwen2.5:14b` |
| **T3** | Max | 64 GB+ | CUDA / Multi-GPU | `llama3.1:70b`, `mixtral:8x7b` |

```bash
essence probe        # detect tier, RAM, GPU, recommended model
essence bench        # run performance benchmark
```

---

## Security & Safety

### Guardrail Layers (G1–G4)

| Layer | Name | Description |
|-------|------|-------------|
| G1 | **Schema** | Input validation and type checking |
| G2 | **Alignment** | Value alignment and policy enforcement |
| G3 | **Quota** | Per-user rate limiting and cost budgets |
| G4 | **Sandbox** | OS-level container isolation for tool execution |

All layers are applied in sequence before any tool call or LLM completion. A violation at any layer returns a structured error and is logged to the audit trail.

### Vault

Sensitive values (API keys, tokens) are stored in `~/.essence/.essence_vault` encrypted with **AES-256-GCM** (requires `cryptography`). Falls back to XOR obfuscation with a warning when `cryptography` is not installed (unless `ESSENCE_VAULT_ALLOW_WEAK=0`).

```bash
essence keys         # list managed API keys
```

### Decision Queue (Human-in-the-Loop)

High-risk tool calls are queued for human approval before execution:

```bash
essence decisions list
essence decisions approve <id>
essence decisions approve-all
```

### Audit Log

Every kernel operation is appended to a tamper-resistant audit trail (SQLite, WAL mode):

```bash
essence plan-audit               # show all events
essence plan-audit <capsule_id>  # filter by capsule
essence audit verify             # verify log integrity
```

### Rate Limiting

Token-bucket rate limiting protects the REST API:
- Default: **100 req/min** per API key
- Backed by in-process token bucket (default) or **Valkey** for multi-process deployments
- Override per-key via the Settings UI or `ESSENCE_RATE_LIMIT_RPM` env var

### Authentication

The server supports two authentication modes:
- **JWT bearer tokens** — issued by `POST /auth/token`
- **API keys** — set via `X-API-Key` header or the integrations store

---

## Memory System

Three-layer memory persists context across sessions:

| Layer | Storage | Contents |
|-------|---------|----------|
| **Working** | JSON KV store | Current session facts, active goals, preferences |
| **Episodic** | JSONL + SQLite | Conversation history, outcomes, timestamps |
| **Semantic** | FAISS / sqlite-vec | Vector-indexed long-term knowledge, skill extracts |

All layers support:
- **Team sync** — shared namespace across multiple Essence instances (`ESSENCE_TEAM_ID`)
- **Export/import** — encrypted bundles transferable between machines
- **Lifecycle hooks** — TTL expiry, importance scoring, compaction

```bash
essence memory export --out backup.bundle --passphrase "secret"
essence memory import backup.bundle --merge
```

---

## Channels & Integrations

Essence connects to external messaging platforms as a multi-channel bot. All adapters share a `ChannelIdentity` registry so a user's Telegram and Discord sessions share the same memory context.

### Supported channels

| Channel | Adapter | Required env var |
|---------|---------|-----------------|
| Telegram | `TelegramAdapter` | `TELEGRAM_BOT_TOKEN` |
| Discord | `DiscordAdapter` | `DISCORD_WEBHOOK_URL` or `DISCORD_BOT_TOKEN` |
| Slack | via bridge | `SLACK_BOT_TOKEN` |
| Gmail | via bridge | `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` |
| WhatsApp | via bridge | `WHATSAPP_TOKEN` |
| Matrix | via bridge | `MATRIX_HOMESERVER`, `MATRIX_TOKEN` |

```bash
essence channels                 # show channel adapter status
export TELEGRAM_BOT_TOKEN="..."
export DISCORD_WEBHOOK_URL="..."
```

---

## Multi-Agent (A2A)

Essence implements the **Agent-to-Agent (A2A)** protocol for peer discovery and orchestration.

### Discovery

Every running Essence instance exposes:
```
GET /.well-known/agent.json
```

This returns an **agent card** declaring the instance's capabilities, skill list, supported intents, and delegation endpoint.

### Peer registration

```bash
# Static peers via env var
export ESSENCE_A2A_PEERS="http://agent-b:7860,http://agent-c:7860"

# Dynamic registration via CLI
essence peers --add http://agent-b:7860

# List known peers
essence peers
```

### SmartPeerSelector routing

```bash
GET /api/peers/smart?intent=coding
```

```json
{
  "peers": [
    {"url": "http://agent-b:7860", "score": 0.91, "capability_match": 0.95, "latency_ms": 42},
    {"url": "http://agent-c:7860", "score": 0.74, "capability_match": 0.80, "latency_ms": 110}
  ]
}
```

### Roles

```bash
essence up --role orchestrator   # route tasks to worker agents
essence up --role worker         # execute tasks from orchestrator
```

---

## Observability

### Prometheus metrics

Enable with `ESSENCE_METRICS=1`. Metrics are exposed at `GET /metrics`.

| Metric | Type | Description |
|--------|------|-------------|
| `essence_llm_calls_total` | Counter | LLM completions by model + backend |
| `essence_tokens_in_total` | Counter | Input tokens consumed |
| `essence_tokens_out_total` | Counter | Output tokens generated |
| `essence_request_duration_seconds` | Histogram | HTTP request latency by route |
| `essence_skill_duration_seconds` | Histogram | Skill execution time |
| `essence_circuit_breaker_state` | Gauge | Circuit breaker state (0=closed, 1=open) |
| `essence_sessions_active` | Gauge | Active agent sessions |

### OpenTelemetry

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to export spans to Jaeger, Tempo, or any OTLP-compatible backend.

Spans are created for:
- Every LLM completion (`llm.complete`)
- Every tool dispatch (`tool.<name>`)
- Inbound A2A requests

W3C `traceparent` headers are injected into all outbound A2A calls and extracted from inbound requests, connecting multi-agent workflows into a single trace tree.

### Health endpoint

```bash
curl http://localhost:7860/api/health
```

```json
{
  "uptime_s": 3600,
  "version": "1.1.0",
  "backends": ["ollama", "openai"],
  "circuit_breakers": [],
  "retry_queue_size": 0,
  "active_sessions": 2,
  "rate_limiter": "in-process",
  "nats_connected": false,
  "otel_active": true
}
```

---

## Development

### Running tests

```bash
pip install -e ".[test]"
pytest essence/tests/ -v
pytest essence/tests/test_boot.py          # kernel boot
pytest essence/tests/test_backends.py     # backend routing
pytest essence/tests/contracts/           # interface contracts
```

### Project structure

```
essence/
├── __init__.py              # public API exports
├── _shared.py               # stdlib imports, constants, logging
├── apde_types.py            # core domain types (IntentCapsule, PlanDAG, …)
├── boot.py                  # APDE kernel (Kernel class, boot_kernel())
├── cli.py                   # unified CLI entry point
├── installer.py             # dependency installer + workspace subcommands
├── updater.py               # self-update mechanism
│
├── agents/                  # agent, planning, verification, eval, workflow
│   ├── agent.py             # base Agent class
│   ├── planning/            # decomposer, intent, coverage, disjointness
│   └── verification/        # rubric judges, predicates, SOT extractors
│
├── analytics/               # analytics engine, ML tools, reward, vision
├── attention/               # attention manager (context window budget)
├── autonomy/                # goal manager, curiosity engine, research engine
│
├── backends/                # LLM backend adapters
│   ├── smart_router.py      # SmartRouter (multi-provider routing)
│   ├── cloud.py             # cloud provider builders (OpenAI, Anthropic, …)
│   ├── adapters.py          # provider ABC + shared adapter base
│   └── apde_router.py       # APDE-aware router wrapper
│
├── capability/              # capability discovery and retirement
├── channels/                # messaging channel adapters (Telegram, Discord, …)
├── core/                    # hardware detection, model registry, vault
│
├── infra/                   # production infrastructure
│   ├── auth.py              # JWT + API-key authentication middleware
│   ├── ratelimit.py         # token-bucket rate limiter
│   ├── circuit.py           # circuit breaker registry
│   ├── health.py            # health monitor + /api/health builder
│   ├── metrics.py           # Prometheus metrics
│   ├── otel.py              # OpenTelemetry spans + W3C trace headers
│   ├── cache.py             # in-process + Redis/Valkey cache
│   ├── audit.py             # tamper-resistant audit logger
│   ├── sandbox2.py          # OS-level sandbox (seccomp / Docker)
│   └── capsule_store/       # SQLite capsule + plan persistence
│
├── integrations/            # integration store and registry
├── intelligence/            # state detector, wisdom engine, briefing
├── memory/                  # three-layer memory system
│
├── prompts/                 # PromptManager — CRUD, usage scoring, auto-learn
│   ├── __init__.py
│   └── manager.py
│
├── protocols/               # A2A protocol + SmartPeerSelector
├── routing/                 # intent / task / subagent routers, event bus
├── security/                # guardrails, PII redactor, tokens
│
├── server/                  # FastAPI server
│   ├── api.py               # all REST + WebSocket endpoints
│   ├── static_ui.html       # single-file web UI
│   ├── app.py               # ASGI app factory
│   ├── sse_manager.py       # server-sent events manager
│   └── opencanvas.py        # OpenCanvas collaborative workspace
│
├── skills/                  # skill system
│   ├── discovery.py         # skill discovery + usage-frequency scoring
│   ├── executor.py          # skill execution engine
│   └── autonomous_builder.py # autonomous skill creation
│
├── simulation/              # dry-run / simulation mode
├── tools/                   # tool belt, browser, voice, computer-use, MCP
├── tui/                     # Textual TUI application
├── workspace/               # scaffold, benchmark, SOP, skill system, gulper
└── tests/                   # pytest test suite
    ├── conftest.py
    └── contracts/           # interface contract tests
```

### Key public API

```python
from essence import boot_kernel, Kernel

# Boot the kernel
kernel = boot_kernel(workspace="~/.essence", autonomy_tier=2)

# Plan a task
capsule_id = kernel.ingest_capsule(
    raw_prompt="Summarize the last 7 days of AI news",
    user_id="alice",
)

# Execute step by step
result = kernel.tick(capsule_id)      # advance one task
trail  = kernel.audit()               # full audit trail

# Direct LLM access via SmartRouter
from essence.backends.smart_router import get_router

router = get_router()
provider, model = router.select("coding")
for chunk in router.complete_with_routing(messages, intent="coding"):
    print(chunk, end="", flush=True)

# Prompt management
from essence.prompts.manager import PromptManager

mgr = PromptManager(workspace)
pid = mgr.create("My prompt", "Summarize {topic} in 3 bullets", category="analysis")
mgr.record_usage(pid)
suggestions = mgr.suggest(limit=6, context="data analysis")
```

### Adding a new LLM provider

1. Implement the provider interface (subclass or duck-type `ProviderBase` in `essence/backends/adapters.py`):
   - `NAME: str`
   - `alive() -> bool`
   - `list_models() -> list[str]`
   - `complete(messages, model, **kwargs) -> Iterator[str]`

2. Register with the router:
   ```python
   from essence.backends.smart_router import get_router
   get_router().add_provider(MyProvider())
   ```

3. Optionally add entries to `_PROVIDER_TIER` and `_PROVIDER_MODEL_MAP` in `smart_router.py`.

### Adding a new skill

```bash
essence skill new my_skill
# Creates: workspace/skills/my_skill/SKILL.md
```

Edit `SKILL.md` to define the skill's purpose, example prompts, and tool requirements.

---

## License

Apache-2.0 — see [LICENSE](LICENSE) for details.

---

*Essence v1.1.0 — Build 20260623*
