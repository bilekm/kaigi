# CLI Contract: Orchestrator

**Version**: 1.0
**Date**: 2025-12-27

## Overview

The orchestrator CLI follows Unix conventions:
- Input: stdin, command-line arguments
- Output: stdout (data), stderr (logs/errors)
- Exit codes: 0 = success, non-zero = failure

All commands support `--json` flag for machine-readable output.

## Commands

### `orchestrator run <workflow-file>`

Execute a workflow from a YAML definition file.

**Arguments**:
| Argument | Required | Description |
|----------|----------|-------------|
| workflow-file | yes | Path to workflow YAML file |

**Options**:
| Option | Type | Default | Description |
|--------|------|---------|-------------|
| --json | flag | false | Output JSON format |
| --quiet | flag | false | Suppress progress output |

**Exit Codes**:
| Code | Meaning |
|------|---------|
| 0 | Workflow completed successfully |
| 1 | Workflow failed (agent error) |
| 2 | Invalid workflow definition |
| 3 | Workflow already running |

**Output (human)**:
```
Starting workflow: my-pipeline
[1/3] fetch-data... done (2.3s)
[2/3] transform... done (0.5s)
[3/3] store... done (1.1s)
Workflow completed in 3.9s
```

**Output (JSON)**:
```json
{
  "execution_id": "abc123",
  "workflow_name": "my-pipeline",
  "status": "completed",
  "duration_seconds": 3.9,
  "steps": [
    {"id": "fetch-data", "status": "completed", "duration": 2.3},
    {"id": "transform", "status": "completed", "duration": 0.5},
    {"id": "store", "status": "completed", "duration": 1.1}
  ]
}
```

---

### `orchestrator status [execution-id]`

Get status of a workflow execution.

**Arguments**:
| Argument | Required | Description |
|----------|----------|-------------|
| execution-id | no | Execution ID (defaults to latest) |

**Options**:
| Option | Type | Default | Description |
|--------|------|---------|-------------|
| --json | flag | false | Output JSON format |
| --workflow | string | none | Filter by workflow name |

**Exit Codes**:
| Code | Meaning |
|------|---------|
| 0 | Status retrieved successfully |
| 1 | Execution not found |

**Output (human)**:
```
Execution: abc123
Workflow: my-pipeline
Status: running
Started: 2025-12-27 10:30:00

Steps:
  [x] fetch-data (completed, 2.3s)
  [>] transform (running, 0.5s elapsed)
  [ ] store (pending)
```

**Output (JSON)**:
```json
{
  "execution_id": "abc123",
  "workflow_name": "my-pipeline",
  "status": "running",
  "started_at": "2025-12-27T10:30:00Z",
  "current_step": "transform",
  "steps": [
    {"id": "fetch-data", "status": "completed", "duration": 2.3},
    {"id": "transform", "status": "running", "elapsed": 0.5},
    {"id": "store", "status": "pending"}
  ]
}
```

---

### `orchestrator cancel [execution-id]`

Cancel a running workflow execution.

**Arguments**:
| Argument | Required | Description |
|----------|----------|-------------|
| execution-id | no | Execution ID (defaults to current running) |

**Options**:
| Option | Type | Default | Description |
|--------|------|---------|-------------|
| --json | flag | false | Output JSON format |
| --force | flag | false | Send SIGKILL immediately (skip SIGTERM) |

**Exit Codes**:
| Code | Meaning |
|------|---------|
| 0 | Workflow cancelled successfully |
| 1 | No running workflow found |
| 2 | Cancellation failed |

**Output (human)**:
```
Cancelling execution abc123...
Sent SIGTERM to agent 'transform' (PID 12345)
Workflow cancelled. Partial results preserved.
```

---

### `orchestrator retry [execution-id]`

Retry a failed or cancelled workflow from the point of failure.

**Arguments**:
| Argument | Required | Description |
|----------|----------|-------------|
| execution-id | no | Execution ID (defaults to latest failed/cancelled) |

**Options**:
| Option | Type | Default | Description |
|--------|------|---------|-------------|
| --json | flag | false | Output JSON format |
| --from-start | flag | false | Restart from beginning instead of failure point |

**Exit Codes**:
| Code | Meaning |
|------|---------|
| 0 | Retry completed successfully |
| 1 | Retry failed |
| 2 | Execution not in retryable state |
| 3 | Another workflow already running |

**Output (human)**:
```
Retrying execution abc123 from step 'transform'...
[2/3] transform... done (0.6s)
[3/3] store... done (1.0s)
Workflow completed in 1.6s
```

---

### `orchestrator list`

List recent workflow executions.

**Options**:
| Option | Type | Default | Description |
|--------|------|---------|-------------|
| --json | flag | false | Output JSON format |
| --limit | int | 10 | Maximum number of executions to show |
| --status | string | all | Filter by status (running/completed/failed/cancelled) |

**Exit Codes**:
| Code | Meaning |
|------|---------|
| 0 | Success |

**Output (human)**:
```
EXECUTION    WORKFLOW       STATUS     STARTED              DURATION
abc123       my-pipeline    completed  2025-12-27 10:30:00  3.9s
def456       etl-job        failed     2025-12-27 09:15:00  45.2s
ghi789       my-pipeline    cancelled  2025-12-27 08:00:00  12.0s
```

---

### `orchestrator validate <workflow-file>`

Validate a workflow definition without executing.

**Arguments**:
| Argument | Required | Description |
|----------|----------|-------------|
| workflow-file | yes | Path to workflow YAML file |

**Options**:
| Option | Type | Default | Description |
|--------|------|---------|-------------|
| --json | flag | false | Output JSON format |

**Exit Codes**:
| Code | Meaning |
|------|---------|
| 0 | Workflow is valid |
| 2 | Validation errors found |

**Output (human, valid)**:
```
Workflow 'my-pipeline' is valid.
3 steps defined.
```

**Output (human, invalid)**:
```
Validation errors in my-pipeline.yaml:
  - Step 'fetch': missing required field 'command'
  - Step 'store': timeout must be between 1 and 86400
```

---

### `orchestrator output <execution-id> <step-id>`

Retrieve the output of a specific step.

**Arguments**:
| Argument | Required | Description |
|----------|----------|-------------|
| execution-id | yes | Execution ID |
| step-id | yes | Step identifier |

**Options**:
| Option | Type | Default | Description |
|--------|------|---------|-------------|
| --stderr | flag | false | Show stderr instead of stdout |
| --tail | int | none | Show last N lines only |

**Exit Codes**:
| Code | Meaning |
|------|---------|
| 0 | Output retrieved |
| 1 | Execution or step not found |
| 2 | Step has no output (not yet executed or failed before output) |

**Output**: Raw step output to stdout.

---

### `orchestrator cleanup`

Remove expired workflow data (older than 7 days).

**Options**:
| Option | Type | Default | Description |
|--------|------|---------|-------------|
| --json | flag | false | Output JSON format |
| --dry-run | flag | false | Show what would be deleted |
| --force | flag | false | Delete without confirmation |

**Exit Codes**:
| Code | Meaning |
|------|---------|
| 0 | Cleanup completed |
| 1 | Cleanup failed |

**Output (human)**:
```
Found 5 executions older than 7 days.
Deleted 5 executions, freed 234 MB.
```

## Error Output Format

All errors are written to stderr in structured format:

**Human**:
```
Error: Agent 'transform' exited with code 1
  Workflow: my-pipeline
  Execution: abc123
  Step: transform (2/3)

  stderr output:
    jq: error: Cannot iterate over null
```

**JSON** (with `--json`):
```json
{
  "error_code": "AGENT_FAILED",
  "message": "Agent 'transform' exited with code 1",
  "source": "workflow:my-pipeline/execution:abc123/step:transform",
  "details": {
    "exit_code": 1,
    "stderr": "jq: error: Cannot iterate over null"
  }
}
```

## Error Codes

| Code | Name | Description |
|------|------|-------------|
| AGENT_FAILED | Agent process exited with non-zero code |
| AGENT_TIMEOUT | Agent exceeded timeout limit |
| WORKFLOW_INVALID | Workflow definition failed validation |
| WORKFLOW_LOCKED | Another workflow is already running |
| EXECUTION_NOT_FOUND | Requested execution does not exist |
| STEP_NOT_FOUND | Requested step does not exist |
| NOT_RETRYABLE | Execution is not in a retryable state |
| IO_ERROR | File system error (read/write failed) |
