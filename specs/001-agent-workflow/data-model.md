# Data Model: Agent Workflow

**Date**: 2025-12-27
**Feature**: 001-agent-workflow

## Entities

### Workflow

A named, ordered sequence of agent steps defined in YAML.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | string (UUID) | auto | Unique identifier, generated on first run |
| name | string | yes | Human-readable workflow name |
| version | string | yes | Schema version (e.g., "1.0") |
| steps | list[AgentStep] | yes | Ordered list of agent steps |
| created_at | datetime | auto | Timestamp of first execution |

**Validation Rules**:
- `name`: 1-100 characters, alphanumeric with hyphens/underscores
- `steps`: At least 1 step required
- No circular dependencies (steps are sequential, so N/A for v1)

### AgentStep

A single agent invocation within a workflow.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | string | yes | Step identifier (unique within workflow) |
| command | string | yes | Executable command or path |
| args | list[string] | no | Command arguments |
| timeout | int | no | Timeout in seconds (default: 300) |
| env | dict[string, string] | no | Additional environment variables |

**Validation Rules**:
- `id`: 1-50 characters, alphanumeric with hyphens/underscores
- `command`: Non-empty string
- `timeout`: 1-86400 seconds (1 second to 24 hours)
- Step IDs must be unique within workflow

### ExecutionRecord

Tracks a single workflow run.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | string (UUID) | auto | Unique execution identifier |
| workflow_id | string | yes | Reference to workflow |
| workflow_name | string | yes | Snapshot of workflow name |
| status | enum | yes | pending, running, completed, failed, cancelled |
| current_step_index | int | yes | Index of currently executing step (0-based) |
| started_at | datetime | auto | Execution start timestamp |
| ended_at | datetime | auto | Execution end timestamp (null if running) |
| steps | list[StepResult] | yes | Results for each step |

**State Transitions**:
```
pending → running    (on start)
running → completed  (all steps succeed)
running → failed     (any step fails or times out)
running → cancelled  (operator cancels)
failed → running     (on retry)
cancelled → running  (on retry)
```

### StepResult

Result of a single step execution.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| step_id | string | yes | Reference to AgentStep.id |
| status | enum | yes | pending, running, completed, failed, skipped |
| started_at | datetime | auto | Step start timestamp |
| ended_at | datetime | auto | Step end timestamp |
| exit_code | int | auto | Agent exit code (null if not completed) |
| output_path | string | auto | Path to stdout output file |
| error_path | string | auto | Path to stderr output file |
| error_message | string | auto | Error description if failed |

## File Storage Layout

```
~/.kaigi/
├── workflows/
│   └── {workflow_id}/
│       ├── workflow.yaml          # Original workflow definition
│       └── executions/
│           └── {execution_id}/
│               ├── execution.json  # ExecutionRecord
│               ├── steps/
│               │   ├── {step_id}.out       # stdout
│               │   ├── {step_id}.err       # stderr
│               │   └── {step_id}.out.tmp   # in-progress output
│               └── run.lock        # Lock file (deleted on completion)
└── kaigi.lock                     # Global lock for sequential execution
```

## YAML Workflow Schema

```yaml
# workflow.yaml
name: my-data-pipeline
version: "1.0"
steps:
  - id: fetch-data
    command: curl
    args: ["-s", "https://api.example.com/data"]
    timeout: 60

  - id: transform
    command: jq
    args: [".items[]"]
    # timeout defaults to 300

  - id: store
    command: ./store-results.sh
    env:
      OUTPUT_DIR: /tmp/results
```

## Relationships

```
Workflow (1) ──defines──> (*) AgentStep
    │
    └── (1) ──has many──> (*) ExecutionRecord
                              │
                              └── (1) ──contains──> (*) StepResult
                                                        │
                                                        └── references AgentStep.id
```

## Indexes / Lookup Patterns

| Query | Implementation |
|-------|----------------|
| Get workflow by name | Scan `workflows/*/workflow.yaml`, match name |
| Get workflow by ID | Direct path: `workflows/{id}/` |
| List executions for workflow | List `workflows/{id}/executions/` |
| Get latest execution | Sort execution dirs by mtime, take newest |
| Find stale executions (cleanup) | Scan all execution dirs, filter by mtime > 7 days |
