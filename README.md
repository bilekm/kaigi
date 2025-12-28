# Orchestrator

CLI-based workflow orchestrator for AI agent coordination. Run multi-agent conversations where AI agents discuss topics, collaborate on tasks, and reach consensus.

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Configure agents
orchestrator agents init
# Edit ~/.orchestrator/agents.yaml with your agents

# 3. Initialize a project
cd your-project
orchestrator init

# 4. Start conversation
orchestrator
```

## Features

- **Multi-agent conversations**: Claude, Copilot, and other AI agents discuss topics together
- **Two collaboration modes**: Team (democratic) or Orchestrated (lead assigns tasks)
- **Persistent agents**: Keep agents running for instant responses (no cold-start latency)
- **Interactive control**: Inject messages, change agents mid-conversation

## Usage

### Basic Conversation

```bash
# Just run orchestrator in a project directory
orchestrator

# Or specify a workflow file
orchestrator converse workflow.yaml
```

### Agent Management

```bash
# List configured agents
orchestrator agents list

# Start agents in background (auto-started by default)
orchestrator agents start claude --background
orchestrator agents start copilot --background

# Start all agents from workflow in visible terminals
orchestrator agents start-all --terminal

# Check running agents
orchestrator agents running

# Stop agents
orchestrator agents stop --all
```

### Persistent Agents (Faster Responses)

By default, `orchestrator converse` auto-starts agents in the background. For visible agent terminals:

```bash
# Option 1: Start all agents in visible terminal windows
orchestrator agents start-all --terminal

# Then run conversation (agents already running)
orchestrator converse workflow.yaml --no-auto-start

# Option 2: Manually start each agent in separate terminals
# Terminal 1:
orchestrator agents start claude

# Terminal 2:
orchestrator agents start copilot

# Terminal 3:
orchestrator converse workflow.yaml --no-auto-start
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

Global settings: `~/.orchestrator/agents.yaml`
Project settings: `.orchestrator/agents.yaml` (overrides global)

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
│                    orchestrator converse                     │
│                                                              │
│   Auto-detects persistent agents via Unix sockets            │
│   Falls back to spawning new processes if not running        │
│                                                              │
│         ┌──────────────┬──────────────┬──────────────┐      │
│         ▼              ▼              ▼              ▼      │
│    /tmp/orchestrator-agents/                                 │
│    ├── claude.sock     ├── copilot.sock   ├── glm.sock      │
│    └── claude.pid      └── copilot.pid    └── glm.pid       │
└─────────────────────────────────────────────────────────────┘
```

## Commands Reference

```
orchestrator                    Run project workflow or init wizard
orchestrator init               Create new project configuration
orchestrator converse <file>    Run conversation workflow
orchestrator validate <file>    Validate workflow file

orchestrator agents list        List configured agents
orchestrator agents init        Create agents.yaml template
orchestrator agents show <name> Show agent details
orchestrator agents start <n>   Start persistent agent
orchestrator agents start-all   Start all agents from workflow
orchestrator agents stop [id]   Stop running agent(s)
orchestrator agents running     List running agents
orchestrator agents ping <id>   Test agent connectivity
orchestrator agents test <id>   Send test message to agent

orchestrator say <message>      Inject message into conversation
orchestrator transcript [id]    Show conversation transcript
orchestrator status [id]        Show execution status
orchestrator list               List recent executions
orchestrator cleanup            Remove old execution data
```

## License

MIT
