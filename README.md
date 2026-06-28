# Kaigi

CLI-based multi-agent collaboration platform. Run multi-agent conversations where AI agents discuss topics, collaborate on tasks, and reach consensus.

> **Multi-Agent Collaboration:** This project is developed through collaboration between author and multiple AI agents including Claude (Anthropic), GLM-4.7, and Gemini 3 Pro.

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Configure agents
kaigi agents init
# Edit ~/.kaigi/agents.yaml with your agents

# 3. Initialize a project
cd your-project
kaigi init

# 4. Start conversation
kaigi
```

## Features

- **Multi-agent conversations**: Claude, GLM, Copilot and other AI agents discuss topics together
- **Two collaboration modes**: Team (democratic) or Orchestrated (lead assigns tasks)
- **Persistent agents**: Keep agents running for instant responses (no cold-start latency)
- **Interactive control**: Inject messages, change agents mid-conversation

## Usage

### Basic Conversation

```bash
# Just run kaigi in a project directory
kaigi

# Or specify a workflow file
kaigi converse workflow.yaml
```

### Continuous Conversation Mode

After a conversation reaches consensus, kaigi automatically loops back for a new topic while keeping agents running:

```bash
# Start conversation with persistent agents
kaigi converse workflow.yaml

# After consensus, you'll be prompted for a new topic automatically
# Agents retain context - no cold-start latency
# Press Ctrl+C or type /quit to exit
```

### Agent Management

```bash
# List configured agents
kaigi agents list

# Start agents in background (auto-started by default)
kaigi agents start claude --background
kaigi agents start copilot --background

# Start all agents from workflow in visible terminals
kaigi agents start-all --terminal

# Check running agents
kaigi agents running

# Stop agents
kaigi agents stop --all
```

### Persistent Agents (Faster Responses)

By default, `kaigi converse` auto-starts agents in the background. For visible agent terminals:

```bash
# Option 1: Start all agents in visible terminal windows
kaigi agents start-all --terminal

# Then run conversation (agents already running)
kaigi converse workflow.yaml --no-auto-start

# Option 2: Manually start each agent in separate terminals
# Terminal 1:
kaigi agents start claude

# Terminal 2:
kaigi agents start copilot

# Terminal 3:
kaigi converse workflow.yaml --no-auto-start
```

## Agent Memory Management

Kaigi tracks what information has been sent to each persistent agent to avoid duplicate context and save tokens.

### State Storage

- **Location:** `.kaigi/.agent_state.yaml`
- **Tracks:**
  - Whether `KAIGI_RULES` have been sent (collaboration protocol)
  - Whether `PROJECT_CONTEXT` has been sent (topic, persona, team)
  - Workflow hash (detects workflow changes)

### When State Resets

- Workflow file content changes (hash mismatch)
- Manual reset via `kaigi cleanup`
- Agent is removed and re-added

## Prompt Architecture

For persistent agents, kaigi uses a three-tier prompt system to minimize token usage:

### 1. KAIGI_RULES (sent once at startup)

Contains the collaboration protocol, permission states, and tool definitions.

**Permission States:**
- **Discussion Phase** (default): Agents can read, search, analyze — but NOT write/edit
- **Execution Phase** (after approval): Agents can write, edit, execute commands

### 2. PROJECT_CONTEXT (sent once per workflow)

Contains the agent's persona, team members, collaboration mode, and topic.

### 3. COMPACT/FOLLOWUP (subsequent turns)

Contains only new messages since the agent's last response — saves ~800 tokens per turn.

## Workflow Configuration

### Team Mode (Default)

All agents work as equals. Consensus requires all agents to agree.

```yaml
name: design-discussion
version: "1.0"
mode: conversation

agents:
  - id: architect
    agent: claude
    persona: "Senior software architect focused on scalability"

  - id: reviewer
    agent: copilot
    persona: "Code reviewer focused on maintainability"

topic: |
  Design a caching strategy for our REST API.
  Consider performance, consistency, and simplicity.

max_rounds: 5
consensus_keyword: "AGREED:"
```

### Orchestrated Mode

One lead agent coordinates the team and makes final decisions.

```yaml
name: code-review
version: "1.0"
mode: conversation

collaboration: orchestrated
lead: architect

agents:
  - id: architect
    agent: claude
    persona: "Lead architect - coordinate the review"

  - id: security
    agent: copilot
    persona: "Security specialist"

  - id: performance
    agent: claude
    persona: "Performance engineer"

topic: |
  Review the authentication module for production readiness.
  Lead: assign review tasks to team members.

max_rounds: 5
consensus_keyword: "AGREED:"
```

## Agent Configuration

Global settings: `~/.kaigi/agents.yaml`
Project settings: `.kaigi/agents.yaml` (overrides global)

```yaml
agents:
  claude:
    command: claude
    args: ["-p", "{{prompt}}", "--output-format", "text"]
    timeout: 300
    description: "Claude Code CLI"

  copilot:
    command: copilot
    args: ["-p", "{{prompt}}"]
    timeout: 300
    description: "GitHub Copilot CLI"
    model: gemini-3-pro-preview  # Optional model selection
```

## Environment Variables

| Variable | Description | Values |
|----------|-------------|--------|
| `KAIGI_STYLE` | Pygments syntax highlighting style | `pastie`, `monokai`, `vs`, etc. |
| `KAIGI_BACKGROUND` | Terminal background for theme selection | `light`, `dark`, `auto` (default: auto-detect) |
| `NO_COLOR` | Disable all colored output | Any value (if set, colors disabled) |

```bash
# Example: Light terminal theme
export KAIGI_BACKGROUND=light

# Example: Specific Pygments style
export KAIGI_STYLE=breeze

# Example: Disable colors
export NO_COLOR=1
```

## In-Conversation Commands

During a conversation, you can type commands:

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/agents` | List current agents |
| `/add <agent>` | Add agent to conversation |
| `/remove <id>` | Remove agent |
| `/model <id> <model>` | Change agent's model |
| `/persona <id> <text>` | Update agent's persona |
| `/save` | Save agents to project config |
| `/quit` | End conversation |

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         kaigi converse                            │
│                                                                   │
│   Auto-detects persistent agents via Unix sockets                 │
│   Falls back to spawning new processes if not running             │
│   Tracks agent state to avoid duplicate context sends             │
│                                                                   │
│         ┌──────────────┬──────────────┬──────────────┐          │
│         ▼              ▼              ▼              ▼          │
│    /tmp/kaigi-agents/                                               │
│    ├── claude.sock     ├── copilot.sock   ├── glm.sock            │
│    └── claude.pid      └── copilot.pid    └── glm.pid             │
│                                                                   │
│   Prompt Architecture (per agent):                                │
│   ├── KAIGI_RULES      (sent once at startup)                    │
│   ├── PROJECT_CONTEXT  (sent once per workflow)                  │
│   └── COMPACT/FOLLOWUP (subsequent turns, ~800 tokens saved)     │
│                                                                   │
│   State Storage: .kaigi/.agent_state.yaml                        │
└──────────────────────────────────────────────────────────────────┘
```

## Commands Reference

```
kaigi                    Run project workflow or init wizard
kaigi init               Create new project configuration
kaigi converse <file>    Run conversation workflow
kaigi validate <file>    Validate workflow file

kaigi agents list        List configured agents
kaigi agents init        Create agents.yaml template
kaigi agents show <name> Show agent details
kaigi agents start <n>   Start persistent agent
kaigi agents start-all   Start all agents from workflow
kaigi agents stop [id]   Stop running agent(s)
kaigi agents running     List running agents
kaigi agents ping <id>   Test agent connectivity
kaigi agents test <id>   Send test message to agent

kaigi say <message>      Inject message into conversation
kaigi transcript [id]    Show conversation transcript
kaigi status [id]        Show execution status
kaigi list               List recent executions
kaigi cleanup            Remove old execution data
```

## License

MIT
