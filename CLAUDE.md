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
