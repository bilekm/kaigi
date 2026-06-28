# kaigi Development Guidelines

Auto-generated from all feature plans. Last updated: 2025-12-27

## Active Technologies

- Python 3.11+ + PyYAML (workflow parsing), Click (CLI framework), Pydantic (data validation) (001-agent-workflow)

## Project Structure

```text
src/
tests/
```

## Commands

cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style

Python 3.11+: Follow standard conventions

## Recent Changes

- 001-agent-workflow: Added Python 3.11+ + PyYAML (workflow parsing), Click (CLI framework), Pydantic (data validation)

<!-- MANUAL ADDITIONS START -->

## Versioning

Version format: `MAJOR.MINOR.PATCH.BUILD` (e.g., `0.2.0.1`)

**Rule:** Increment the 4th digit (BUILD) on every code change to pyproject.toml.

- BUILD: Increment on every change (0.2.0.1 → 0.2.0.2 → ...)
- PATCH: Increment for bug fixes, reset BUILD (0.2.1.0)
- MINOR: Increment for new features, reset PATCH and BUILD (0.3.0.0)
- MAJOR: Increment for breaking changes (1.0.0.0)

Check current version: `kaigi --version`

## Commit Message Format

All commits should use the following footer:

```
Multi-agent collaboration: Claude (Anthropic), GLM-4.7, Gemini 3 Pro
```

This can be auto-included by configuring the git commit template:
```bash
git config commit.template .gitmessage
```

<!-- MANUAL ADDITIONS END -->
