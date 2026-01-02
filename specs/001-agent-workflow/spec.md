# Feature Specification: Agent Workflow

**Feature Branch**: `001-agent-workflow`
**Created**: 2025-12-27
**Status**: Draft
**Input**: User description: "Define how agents coordinate and execute tasks"

## Clarifications

### Session 2025-12-27

- Q: Should the system support concurrent workflow execution? → A: Sequential only (one workflow at a time per operator)
- Q: What should be the default agent timeout? → A: 5 minutes
- Q: What observability approach should the system use? → A: Structured logs to stderr (JSON with timestamps, levels, context)
- Q: How should the system handle agent output data? → A: Stream to disk (no size limit)
- Q: How long should completed workflow state be retained? → A: 7 days (auto-cleanup)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Define and Run a Workflow (Priority: P1)

An operator defines a workflow as a sequence of agent tasks and executes it. The workflow coordinates multiple agents, passing outputs from one agent as inputs to the next.

**Why this priority**: This is the core value proposition—without the ability to define and execute workflows, there is no agent coordination.

**Independent Test**: Can be fully tested by defining a simple two-agent workflow (e.g., "fetch data" → "transform data") and verifying each agent receives the correct input and produces expected output.

**Acceptance Scenarios**:

1. **Given** a workflow definition with two sequential agents, **When** the operator starts the workflow, **Then** the first agent executes and its output becomes input to the second agent.
2. **Given** a workflow definition with agents A → B → C, **When** agent B fails, **Then** the workflow stops and reports which agent failed with error details.
3. **Given** a valid workflow definition, **When** the operator runs it, **Then** the system provides progress updates showing which agent is currently executing.

---

### User Story 2 - Monitor Workflow Execution (Priority: P2)

An operator monitors a running workflow to see current status, which agents have completed, and what outputs they produced.

**Why this priority**: Visibility into execution state is essential for debugging and operational confidence, but the system must first be able to execute workflows (P1).

**Independent Test**: Can be tested by starting a multi-step workflow and querying its status at various points, verifying accurate reporting of completed/pending agents.

**Acceptance Scenarios**:

1. **Given** a running workflow, **When** the operator requests status, **Then** the system shows: current agent, completed agents with their outputs, and pending agents.
2. **Given** a completed workflow, **When** the operator requests status, **Then** the system shows all agent outputs and total execution summary.
3. **Given** a failed workflow, **When** the operator requests status, **Then** the system shows which agent failed, the error, and any partial outputs from prior agents.

---

### User Story 3 - Cancel or Retry Workflow (Priority: P3)

An operator cancels a running workflow or retries a failed workflow from the point of failure.

**Why this priority**: Control over workflow lifecycle is important for production operations but depends on execution (P1) and monitoring (P2) being in place.

**Independent Test**: Can be tested by starting a long-running workflow, canceling it mid-execution, and verifying clean termination with partial results preserved.

**Acceptance Scenarios**:

1. **Given** a running workflow, **When** the operator cancels it, **Then** the current agent is signaled to stop, and the workflow status shows "cancelled" with partial results.
2. **Given** a failed workflow at agent B, **When** the operator retries it, **Then** execution resumes from agent B using the preserved output from agent A.
3. **Given** a cancelled workflow, **When** the operator retries it, **Then** execution resumes from the last incomplete agent.

---

### Edge Cases

- What happens when an agent produces no output (empty result)?
  - The workflow continues; the next agent receives empty input. If the next agent requires input, it fails loudly with a clear error.

- What happens when a workflow has circular dependencies?
  - The system rejects the workflow definition at validation time with a clear error identifying the cycle.

- What happens when an agent times out?
  - The workflow treats timeout as failure: stops execution, reports timeout error with agent context, preserves partial results.

- What happens when kaigi crashes mid-workflow?
  - On restart, the system recovers workflow state from persistent storage and reports status; operator can retry from last checkpoint.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow operators to define workflows as ordered sequences of agent tasks.
- **FR-002**: System MUST execute agents sequentially, passing the stdout of each agent as stdin to the next.
- **FR-003**: System MUST halt workflow execution when any agent fails (non-zero exit code) and report the failure with agent identity and error output.
- **FR-004**: System MUST provide workflow status via CLI command, showing current state, completed agents, and outputs.
- **FR-005**: System MUST support workflow cancellation, signaling the current agent to terminate.
- **FR-006**: System MUST support retry-from-failure, resuming execution from the failed agent using preserved prior outputs.
- **FR-007**: System MUST persist workflow state to allow recovery after kaigi restart.
- **FR-008**: System MUST validate workflow definitions before execution, rejecting circular dependencies or invalid agent references.
- **FR-009**: System MUST provide all functionality via CLI with both JSON and human-readable output formats.
- **FR-010**: System MUST emit structured error output (error code, message, source) for all failure conditions.
- **FR-011**: System MUST enforce sequential workflow execution (one workflow at a time per operator); concurrent workflow requests MUST be rejected with a clear error.
- **FR-012**: System MUST apply a default 5-minute timeout per agent step; timeout MUST be configurable per-step in the workflow definition.
- **FR-013**: System MUST emit structured JSON logs to stderr including: timestamp, log level, workflow ID, step ID, and contextual message for all significant operations (start, complete, fail, cancel).
- **FR-014**: System MUST stream agent outputs to disk storage (not buffer in memory) to support arbitrarily large outputs without size limits.
- **FR-015**: System MUST retain completed workflow state (outputs, logs, execution records) for 7 days, then automatically delete.

### Key Entities

- **Workflow**: A named, ordered sequence of agent tasks. Contains: unique identifier, name, list of agent steps, current state (pending/running/completed/failed/cancelled), creation timestamp.

- **Agent Step**: A single agent invocation within a workflow. Contains: step order, agent command/reference, input source (prior step output or explicit), execution status, output data, error data if failed.

- **Execution Record**: Tracks a single workflow run. Contains: workflow reference, start time, end time, current step index, per-step results, final status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Operators can define and execute a 5-agent workflow in under 5 minutes from first interaction.
- **SC-002**: System correctly propagates outputs between 10 sequential agents with 100% data integrity.
- **SC-003**: Failed workflows report actionable error information within 1 second of agent failure.
- **SC-004**: Workflow status queries return current state within 500ms.
- **SC-005**: Cancelled workflows terminate the current agent within 5 seconds of cancel request.
- **SC-006**: Retry-from-failure successfully resumes execution in 95% of recoverable failure cases.
- **SC-007**: System recovers workflow state after restart with no data loss for completed steps.

## Assumptions

- Agents are external CLI programs that follow stdin/stdout/stderr conventions.
- Agent commands are provided as executable paths or shell commands.
- Workflow definitions are provided as configuration files (format to be determined in planning).
- Kaigi runs as a long-lived process or can be restarted with state recovery.
- Network partitions and agent host failures are out of scope for this initial feature.
