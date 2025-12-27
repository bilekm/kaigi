# Implementation Plan: Agent Workflow

**Branch**: `001-agent-workflow` | **Date**: 2025-12-27 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-agent-workflow/spec.md`

## Summary

Build a CLI-based workflow orchestrator that coordinates sequential agent execution. Agents are external CLI programs; the orchestrator passes stdout from each agent as stdin to the next. Features include workflow definition via YAML, execution monitoring, cancellation, retry-from-failure, and state persistence with 7-day retention.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: PyYAML (workflow parsing), Click (CLI framework), Pydantic (data validation)
**Storage**: File-based JSON (workflow state, outputs stored in `~/.orchestrator/`)
**Testing**: pytest with pytest-asyncio for subprocess handling
**Target Platform**: Linux/macOS (POSIX systems with standard process control)
**Project Type**: Single CLI application
**Performance Goals**: Status queries <500ms, agent termination <5s
**Constraints**: Sequential workflow execution only, 5-minute default agent timeout
**Scale/Scope**: Single operator, one workflow at a time, 7-day state retention

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. CLI-Centric Interface

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Text protocol (stdin/args → stdout, stderr) | PASS | FR-002, FR-009: CLI with stdin/stdout piping |
| JSON output format supported | PASS | FR-009: Both JSON and human-readable formats |
| Human-readable default | PASS | FR-009: Human-readable is default |
| Meaningful exit codes | PASS | FR-003, FR-010: Non-zero on failure with structured errors |
| No GUI dependencies | PASS | Pure CLI application |

### II. Composability

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Single responsibility | PASS | Orchestrator only coordinates; agents do work |
| Pipeable output | PASS | FR-002: stdout → stdin between agents |
| Stateless operations | PASS | State in explicit file store, not hidden |
| Contract stability | PASS | YAML schema will be versioned |
| Explicit dependencies | PASS | Agent commands explicit in workflow definition |

### III. Fail Loudly

| Requirement | Status | Evidence |
|-------------|--------|----------|
| No silent fallbacks | PASS | FR-003: Halt on any agent failure |
| Error propagation | PASS | FR-003, FR-010: Errors bubble with context |
| Structured error output | PASS | FR-010: Error code, message, source |
| Fail fast | PASS | FR-008: Validate before execution |
| Log before crash | PASS | FR-013: Structured JSON logs to stderr |

**Gate Status**: ALL PASS - Proceed to Phase 0

## Project Structure

### Documentation (this feature)

```text
specs/001-agent-workflow/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (CLI interface docs)
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
src/
├── orchestrator/
│   ├── __init__.py
│   ├── cli.py           # Click CLI entry points
│   ├── models/
│   │   ├── __init__.py
│   │   ├── workflow.py  # Workflow, AgentStep models
│   │   └── execution.py # ExecutionRecord, StepResult models
│   ├── services/
│   │   ├── __init__.py
│   │   ├── executor.py  # Workflow execution engine
│   │   ├── store.py     # State persistence (file-based)
│   │   └── cleanup.py   # 7-day retention cleanup
│   └── lib/
│       ├── __init__.py
│       ├── logging.py   # Structured JSON logging
│       └── errors.py    # Error types and formatting

tests/
├── conftest.py          # Shared fixtures
├── contract/
│   └── test_cli.py      # CLI contract tests
├── integration/
│   └── test_workflow.py # End-to-end workflow tests
└── unit/
    ├── test_executor.py
    ├── test_store.py
    └── test_models.py
```

**Structure Decision**: Single project layout. CLI application with clear separation: models (data structures), services (business logic), lib (utilities), cli (entry points).

## Complexity Tracking

> No constitution violations requiring justification.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none) | — | — |
