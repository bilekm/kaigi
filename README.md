# Kaigi (会議)

CLI-based multi-agent collaboration platform. Run multi-agent conversations where AI agents discuss topics, collaborate on tasks, and reach consensus.

> **Multi-Agent Collaboration:** This project is developed through collaboration between humans and multiple AI agents including Claude (Anthropic), GLM-4.7, and Gemini 3 Pro.

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

- **Multi-agent conversations**: Claude, Copilot, and other AI agents discuss topics together
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
┌─────────────────────────────────────────────────────────────┐
│                       kaigi converse                         │
│                                                              │
│   Auto-detects persistent agents via Unix sockets            │
│   Falls back to spawning new processes if not running        │
│                                                              │
│         ┌──────────────┬──────────────┬──────────────┐      │
│         ▼              ▼              ▼              ▼      │
│    /tmp/kaigi-agents/                                        │
│    ├── claude.sock     ├── copilot.sock   ├── glm.sock      │
│    └── claude.pid      └── copilot.pid    └── glm.pid       │
└─────────────────────────────────────────────────────────────┘
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
