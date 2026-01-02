<!--
SYNC IMPACT REPORT
==================
Version change: 0.0.0 → 1.0.0 (MAJOR: initial constitution ratification)

Modified principles: N/A (initial creation)

Added sections:
- Core Principles (3): CLI-Centric Interface, Composability, Fail Loudly
- Development Workflow
- Quality Standards
- Governance

Removed sections: N/A (initial creation)

Templates requiring updates:
- .specify/templates/plan-template.md: ✅ compatible (Constitution Check section exists)
- .specify/templates/spec-template.md: ✅ compatible (no constitution-specific sections)
- .specify/templates/tasks-template.md: ✅ compatible (no constitution-specific sections)
- .specify/templates/checklist-template.md: ✅ compatible (no constitution-specific sections)
- .specify/templates/agent-file-template.md: ✅ compatible (no constitution-specific sections)

Follow-up TODOs: None
-->

# Kaigi Constitution

## Core Principles

### I. CLI-Centric Interface

All Kaigi components MUST expose functionality via command-line interface.

- **Text protocol**: stdin/args as input, stdout as output, stderr for errors
- **Machine-readable output**: MUST support JSON output format (`--json` or equivalent)
- **Human-readable default**: Plain text output for interactive use
- **Exit codes**: Zero for success, non-zero for failure with meaningful codes
- **No GUI dependencies**: Core functionality MUST NOT require graphical interfaces

**Rationale**: CLI-first enables scripting, automation, testing, and composition with standard Unix tools. Agent coordination requires programmatic control.

### II. Composability

Components MUST be designed for composition via standard I/O patterns.

- **Single responsibility**: Each component does one thing well
- **Pipeable output**: stdout MUST be valid input for downstream components
- **Stateless operations**: Prefer stateless transformations; state belongs in explicit stores
- **Contract stability**: I/O contracts MUST be versioned; breaking changes require MAJOR version bump
- **No hidden coupling**: Dependencies MUST be explicit in interface, not assumed from environment

**Rationale**: Agent coordination requires orchestrating multiple independent components. Composability enables flexible workflow construction without tight coupling.

### III. Fail Loudly

Errors MUST be explicit, visible, and actionable. Silent failures are prohibited.

- **No silent fallbacks**: `or {}`, `|| true`, and similar patterns that mask errors are forbidden
- **Propagate errors**: Errors MUST bubble up with context, not be swallowed
- **Structured error output**: Errors MUST include: error code, message, and source location
- **Fail fast**: Invalid state MUST cause immediate, visible failure
- **Log before crash**: Critical failures SHOULD log diagnostic info before terminating

**Rationale**: In agent coordination, silent failures cause cascading issues that are expensive to debug. Hard failures with clear information enable rapid diagnosis and recovery.

## Development Workflow

- **Feature branches**: All work happens on feature branches; main/master is protected
- **Code review**: Changes require review before merge
- **Incremental delivery**: Features should be decomposable into independently testable increments
- **Documentation**: Public interfaces MUST have usage documentation

## Quality Standards

- **Testing**: Critical paths require tests; coverage expectations set per-feature
- **Linting**: Code MUST pass configured linters before merge
- **Type safety**: Prefer statically typed languages or strict type checking where available
- **Contract tests**: Inter-component communication requires contract tests

## Governance

This constitution supersedes all other development practices within the Kaigi project.

**Amendment process**:
1. Propose amendment with rationale
2. Document impact on existing code and workflows
3. Obtain approval from project maintainers
4. Update constitution with version bump
5. Propagate changes to dependent templates and documentation

**Versioning policy**:
- MAJOR: Principle removal, redefinition, or backward-incompatible governance changes
- MINOR: New principle added, section expanded with new requirements
- PATCH: Clarifications, wording improvements, non-semantic refinements

**Compliance**: All code reviews MUST verify adherence to constitutional principles. Violations require explicit justification in the Complexity Tracking section of the implementation plan.

**Version**: 1.0.0 | **Ratified**: 2025-12-27 | **Last Amended**: 2025-12-27
