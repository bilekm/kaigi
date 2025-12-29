"""Skills registry and loader for high-level tool composition."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from typing import Any, Callable

from orchestrator.lib.logging import get_logger
from orchestrator.services.tools.executor import ToolExecutor
from orchestrator.services.tools.models import ToolCall, ToolResult


class Skill:
    """A high-level tool composed from primitive tools."""

    def __init__(
        self,
        name: str,
        description: str,
        handler: Callable,
        required_permissions: list[str] | None = None,
    ):
        """Initialize a skill.

        Args:
            name: Skill name (e.g., "git_safe_commit")
            description: Human-readable description
            handler: Async function that implements the skill
            required_permissions: Permissions needed to use this skill
        """
        self.name = name
        self.description = description
        self.handler = handler
        self.required_permissions = required_permissions or []

    async def execute(
        self, tool_executor: ToolExecutor, args: dict[str, Any]
    ) -> ToolResult:
        """Execute the skill.

        Args:
            tool_executor: Tool executor for primitive operations
            args: Skill arguments

        Returns:
            ToolResult with execution outcome
        """
        try:
            # Call the skill handler
            result_content = await self.handler(tool_executor, **args)

            return ToolResult(
                id=args.get("_tool_call_id", "skill"),
                status="ok",
                content=str(result_content),
                metadata={"skill": self.name},
            )
        except Exception as e:
            return ToolResult(
                id=args.get("_tool_call_id", "skill"),
                status="error",
                content=f"Skill error: {e}",
                metadata={"skill": self.name},
            )


class SkillRegistry:
    """Registry for loading and managing skills."""

    def __init__(self):
        self.skills: dict[str, Skill] = {}
        self.logger = get_logger()

    def register(self, skill: Skill) -> None:
        """Register a skill.

        Args:
            skill: Skill to register
        """
        self.skills[skill.name] = skill
        self.logger.info("Skill registered", skill=skill.name)

    def get(self, name: str) -> Skill | None:
        """Get a skill by name.

        Args:
            name: Skill name

        Returns:
            Skill if found, None otherwise
        """
        return self.skills.get(name)

    def list_skills(self) -> list[str]:
        """List all registered skill names.

        Returns:
            List of skill names
        """
        return list(self.skills.keys())

    def load_from_directory(self, skills_dir: Path | str) -> None:
        """Load skills from a directory.

        Each .py file in the directory is loaded as a skill module.
        The module should define a `register_skill()` function that
        returns a Skill object.

        Args:
            skills_dir: Directory containing skill modules
        """
        skills_path = Path(skills_dir)

        if not skills_path.exists():
            self.logger.warning("Skills directory not found", path=str(skills_path))
            return

        if not skills_path.is_dir():
            self.logger.warning("Skills path is not a directory", path=str(skills_path))
            return

        # Load each .py file
        for skill_file in skills_path.glob("*.py"):
            if skill_file.name.startswith("_"):
                continue  # Skip __init__.py and private files

            try:
                # Load module
                spec = importlib.util.spec_from_file_location(
                    f"orchestrator.skills.{skill_file.stem}",
                    skill_file,
                )

                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    # Look for register_skill function
                    if hasattr(module, "register_skill"):
                        skill = module.register_skill()
                        if isinstance(skill, Skill):
                            self.register(skill)
                        else:
                            self.logger.warning(
                                "register_skill() did not return a Skill",
                                file=skill_file.name,
                            )
                    else:
                        self.logger.warning(
                            "Skill file missing register_skill()",
                            file=skill_file.name,
                        )

            except Exception as e:
                self.logger.error(
                    "Failed to load skill",
                    file=skill_file.name,
                    error=str(e),
                )


# Global skill registry
_global_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    """Get the global skill registry.

    Returns:
        Global SkillRegistry instance
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = SkillRegistry()

        # Auto-load skills from .orchestrator/skills/ if it exists
        project_skills = Path.cwd() / ".orchestrator" / "skills"
        if project_skills.exists():
            _global_registry.load_from_directory(project_skills)

    return _global_registry
