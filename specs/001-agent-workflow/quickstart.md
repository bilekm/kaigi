# Quickstart: Agent Workflow

Get started with the Orchestrator in 5 minutes.

## Installation

```bash
# Clone and install
git clone <repository-url>
cd orchestrator
pip install -e .
```

## Verify Installation

```bash
orchestrator --version
# orchestrator 0.1.0

orchestrator --help
# Usage: orchestrator [OPTIONS] COMMAND [ARGS]...
# ...
```

## Your First Workflow

### 1. Create a workflow file

Create `hello-world.yaml`:

```yaml
name: hello-world
version: "1.0"
steps:
  - id: greet
    command: echo
    args: ["Hello from Orchestrator!"]

  - id: timestamp
    command: date
    args: ["+%Y-%m-%d %H:%M:%S"]
```

### 2. Validate the workflow

```bash
orchestrator validate hello-world.yaml
# Workflow 'hello-world' is valid.
# 2 steps defined.
```

### 3. Run the workflow

```bash
orchestrator run hello-world.yaml
# Starting workflow: hello-world
# [1/2] greet... done (0.1s)
# [2/2] timestamp... done (0.1s)
# Workflow completed in 0.2s
```

### 4. Check the status

```bash
orchestrator status
# Execution: abc123
# Workflow: hello-world
# Status: completed
# ...
```

### 5. View step output

```bash
orchestrator output abc123 greet
# Hello from Orchestrator!

orchestrator output abc123 timestamp
# 2025-12-27 10:30:00
```

## Pipeline Example

Create a data pipeline that fetches, transforms, and counts:

```yaml
# data-pipeline.yaml
name: data-pipeline
version: "1.0"
steps:
  - id: fetch
    command: curl
    args: ["-s", "https://jsonplaceholder.typicode.com/posts"]
    timeout: 30

  - id: filter
    command: jq
    args: ["[.[] | select(.userId == 1)]"]

  - id: count
    command: jq
    args: ["length"]
```

Run it:

```bash
orchestrator run data-pipeline.yaml
# Starting workflow: data-pipeline
# [1/3] fetch... done (1.2s)
# [2/3] filter... done (0.1s)
# [3/3] count... done (0.1s)
# Workflow completed in 1.4s

orchestrator output $(orchestrator status --json | jq -r .execution_id) count
# 10
```

## Handling Failures

When an agent fails, the workflow stops and preserves state:

```bash
orchestrator run broken-workflow.yaml
# Starting workflow: broken-workflow
# [1/2] step1... done (0.5s)
# [2/2] step2... FAILED (exit code 1)
# Error: Agent 'step2' exited with code 1
```

Retry from the failure point:

```bash
orchestrator retry
# Retrying from step 'step2'...
# [2/2] step2... done (0.3s)
# Workflow completed in 0.3s
```

## Cancelling a Workflow

For long-running workflows:

```bash
# In terminal 1
orchestrator run long-workflow.yaml

# In terminal 2
orchestrator cancel
# Cancelling execution...
# Workflow cancelled. Partial results preserved.
```

## JSON Output

All commands support `--json` for scripting:

```bash
orchestrator run hello-world.yaml --json | jq .status
# "completed"

orchestrator list --json | jq '.[0].workflow_name'
# "hello-world"
```

## Next Steps

- See [CLI Contract](contracts/cli.md) for full command reference
- See [Data Model](data-model.md) for workflow definition details
- See [Workflow Schema](contracts/workflow-schema.yaml) for validation rules
