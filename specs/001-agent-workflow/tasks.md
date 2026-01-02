# Tasks: Agent Workflow

**Input**: Design documents from `/specs/001-agent-workflow/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/cli.md, research.md

**Tests**: Not explicitly requested in spec. Omitted per template guidelines.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/kaigi/`, `tests/` at repository root
- Structure per plan.md

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Create project directory structure per plan.md in src/kaigi/
- [x] T002 Initialize Python project with pyproject.toml (click, pyyaml, pydantic dependencies)
- [x] T003 [P] Create src/kaigi/__init__.py with version info
- [x] T004 [P] Configure ruff for linting in pyproject.toml
- [x] T005 [P] Create tests/conftest.py with shared pytest fixtures

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T006 [P] Implement error types and formatting in src/kaigi/lib/errors.py
- [x] T007 [P] Implement structured JSON logging in src/kaigi/lib/logging.py
- [x] T008 Create Workflow and AgentStep Pydantic models in src/kaigi/models/workflow.py
- [x] T009 Create ExecutionRecord and StepResult Pydantic models in src/kaigi/models/execution.py
- [x] T010 Implement YAML workflow parser with validation in src/kaigi/services/parser.py
- [x] T011 Implement file-based state store in src/kaigi/services/store.py
- [x] T012 Create Click CLI skeleton with --json and --version flags in src/kaigi/cli.py

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Define and Run a Workflow (Priority: P1)

**Goal**: Operator can define a workflow in YAML and execute it, with agents piped sequentially

**Independent Test**: Run a 2-agent workflow (echo → cat) and verify output piping works

### Implementation for User Story 1

- [x] T013 [US1] Implement workflow executor with subprocess streaming in src/kaigi/services/executor.py
- [x] T014 [US1] Add agent output streaming to disk (temp file → rename) in src/kaigi/services/executor.py
- [x] T015 [US1] Add timeout handling with SIGTERM/SIGKILL escalation in src/kaigi/services/executor.py
- [x] T016 [US1] Implement global lock for sequential execution in src/kaigi/services/store.py
- [x] T017 [US1] Implement `kaigi validate` command in src/kaigi/cli.py
- [x] T018 [US1] Implement `kaigi run` command in src/kaigi/cli.py
- [x] T019 [US1] Add progress output (human-readable) for run command in src/kaigi/cli.py
- [x] T020 [US1] Add JSON output support for run command in src/kaigi/cli.py
- [x] T021 [US1] Add structured error output for agent failures in src/kaigi/cli.py

**Checkpoint**: User Story 1 complete - can define and run workflows, see progress, handle failures

---

## Phase 4: User Story 2 - Monitor Workflow Execution (Priority: P2)

**Goal**: Operator can query workflow status, list executions, and view step outputs

**Independent Test**: Run a workflow, then use status/list/output commands to inspect results

### Implementation for User Story 2

- [x] T022 [US2] Implement execution lookup by ID and latest in src/kaigi/services/store.py
- [x] T023 [US2] Implement `kaigi status` command in src/kaigi/cli.py
- [x] T024 [US2] Add human-readable status output with step progress in src/kaigi/cli.py
- [x] T025 [US2] Add JSON output support for status command in src/kaigi/cli.py
- [x] T026 [US2] Implement `kaigi list` command with --limit and --status filters in src/kaigi/cli.py
- [x] T027 [US2] Implement `kaigi output` command for step stdout/stderr in src/kaigi/cli.py

**Checkpoint**: User Story 2 complete - can monitor and inspect workflow executions

---

## Phase 5: User Story 3 - Cancel or Retry Workflow (Priority: P3)

**Goal**: Operator can cancel running workflows and retry failed/cancelled workflows

**Independent Test**: Start long workflow, cancel it, verify partial results; then retry from failure point

### Implementation for User Story 3

- [x] T028 [US3] Implement process termination with SIGTERM/SIGKILL in src/kaigi/services/executor.py
- [x] T029 [US3] Implement `kaigi cancel` command in src/kaigi/cli.py
- [x] T030 [US3] Add --force flag for immediate SIGKILL in cancel command in src/kaigi/cli.py
- [x] T031 [US3] Implement retry-from-failure logic using preserved step outputs in src/kaigi/services/executor.py
- [x] T032 [US3] Implement `kaigi retry` command in src/kaigi/cli.py
- [x] T033 [US3] Add --from-start flag for full restart in retry command in src/kaigi/cli.py

**Checkpoint**: User Story 3 complete - full workflow lifecycle control available

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T034 Implement 7-day cleanup service in src/kaigi/services/cleanup.py
- [x] T035 Implement `kaigi cleanup` command with --dry-run in src/kaigi/cli.py
- [x] T036 Add crash recovery: detect stale locks on startup in src/kaigi/services/store.py
- [x] T037 [P] Create example workflows in examples/ directory
- [x] T038 [P] Validate implementation against quickstart.md scenarios
- [x] T039 Add CLI entry point to pyproject.toml [project.scripts]

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-5)**: All depend on Foundational phase completion
  - User stories can proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3)
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational - Uses store.py from US1 but independently testable
- **User Story 3 (P3)**: Can start after Foundational - Uses executor.py from US1 but independently testable

### Within Each User Story

- Models before services
- Services before CLI commands
- Core implementation before output formatting

### Parallel Opportunities

- T003, T004, T005 can run in parallel (Setup)
- T006, T007 can run in parallel (lib utilities)
- T037, T038 can run in parallel (Polish)

---

## Parallel Example: Foundational Phase

```bash
# Launch lib utilities in parallel:
Task: "Implement error types in src/kaigi/lib/errors.py"
Task: "Implement structured JSON logging in src/kaigi/lib/logging.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Run a simple 2-agent workflow
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add User Story 1 → Test with `kaigi run` (MVP!)
3. Add User Story 2 → Test with `kaigi status` and `kaigi list`
4. Add User Story 3 → Test with `kaigi cancel` and `kaigi retry`
5. Add Polish → Cleanup, examples, final validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
