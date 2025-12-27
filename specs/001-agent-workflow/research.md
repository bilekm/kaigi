# Research: Agent Workflow

**Date**: 2025-12-27
**Feature**: 001-agent-workflow

## Technology Decisions

### Language: Python 3.11+

**Decision**: Python 3.11+ with type hints and Pydantic for runtime validation.

**Rationale**:
- Excellent subprocess handling (`subprocess` module, `asyncio.create_subprocess_exec`)
- Rich CLI ecosystem (Click is mature and well-documented)
- Rapid development for initial feature
- Good enough performance for orchestration (not CPU-bound work)

**Alternatives Considered**:
- **Rust**: Better performance, but slower development; overkill for orchestration layer
- **Go**: Good subprocess handling, but less expressive data modeling
- **TypeScript/Node**: Viable, but Python's subprocess handling is more mature

### Workflow Definition Format: YAML

**Decision**: YAML with JSON Schema validation.

**Rationale**:
- Human-readable and editable
- Comments supported (important for workflow documentation)
- Widely familiar to operators
- PyYAML is well-maintained

**Alternatives Considered**:
- **JSON**: No comments, less readable for complex workflows
- **TOML**: Less familiar, awkward for nested structures like step lists

### CLI Framework: Click

**Decision**: Click for CLI construction.

**Rationale**:
- Decorator-based, clean interface
- Built-in help generation
- Good testing support (`CliRunner`)
- Supports nested command groups

**Alternatives Considered**:
- **argparse**: Lower-level, more boilerplate
- **Typer**: Built on Click, adds type inference but less mature

### Storage: File-based JSON

**Decision**: JSON files in `~/.orchestrator/` directory.

**Rationale**:
- No external dependencies (no database to install)
- Human-inspectable state files
- Simple backup/restore (copy files)
- Sufficient for single-operator, sequential execution
- Easy cleanup implementation (file mtime-based)

**Alternatives Considered**:
- **SQLite**: Overkill for single-user CLI tool; adds complexity
- **Redis**: Requires external process; inappropriate for local CLI

### Process Control: subprocess + asyncio

**Decision**: Use `asyncio.create_subprocess_exec` for agent execution.

**Rationale**:
- Non-blocking I/O for streaming large outputs to disk
- Proper signal handling for cancellation (SIGTERM → SIGKILL escalation)
- Timeout implementation via `asyncio.wait_for`

**Best Practices**:
- Use `asyncio.create_subprocess_exec` (not `shell=True`) for security
- Stream stdout to temp file, then rename on completion (atomic writes)
- Capture stderr separately for error reporting
- Implement graceful shutdown: SIGTERM, wait 2s, SIGKILL

## Architecture Patterns

### State Machine for Workflow Status

```
pending → running → completed
              ↓
           failed
              ↓
         (retry) → running

running → cancelled (via explicit cancel)
```

States are stored in execution record JSON file, updated atomically.

### Output Streaming Pattern

1. Create temp file: `{workflow_id}/{step_id}.out.tmp`
2. Stream agent stdout to temp file (chunked writes)
3. On success: rename to `{step_id}.out`
4. On failure: keep temp file for debugging, mark step as failed

### Structured Logging Format

```json
{
  "timestamp": "2025-12-27T10:30:00.000Z",
  "level": "INFO",
  "workflow_id": "abc123",
  "step_id": "step_1",
  "message": "Agent started",
  "context": {"command": "agent-fetch", "timeout": 300}
}
```

Output to stderr to keep stdout clean for data.

### Error Structure

```json
{
  "error_code": "AGENT_FAILED",
  "message": "Agent 'transform' exited with code 1",
  "source": "workflow:abc123/step:2",
  "details": {"exit_code": 1, "stderr": "..."}
}
```

## Risk Mitigations

| Risk | Mitigation |
|------|------------|
| Agent hangs indefinitely | 5-minute default timeout, configurable per-step |
| Disk fills with outputs | 7-day auto-cleanup; operator can manually purge |
| Orphaned processes on crash | On startup, scan for stale `.lock` files and clean up |
| Concurrent execution attempts | Lock file per operator; reject with clear error |

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| pyyaml | >=6.0 | YAML parsing |
| click | >=8.0 | CLI framework |
| pydantic | >=2.0 | Data validation and serialization |

**Dev Dependencies**:
- pytest >=7.0
- pytest-asyncio >=0.21

## Open Questions (Resolved)

All technical clarifications have been resolved. Ready for Phase 1 design.
