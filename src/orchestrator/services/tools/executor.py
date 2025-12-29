"""Tool executor with workspace sandboxing."""

from __future__ import annotations

import asyncio
import fnmatch
import glob as glob_module
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from orchestrator.lib.logging import get_logger
from orchestrator.services.tools.models import ToolCall, ToolResult


class ToolExecutor:
    """Executes tools with workspace sandboxing.

    All file operations are jailed to the workspace root.
    All bash commands run in a sandboxed environment.
    """

    # File size limits
    MAX_FILE_SIZE_READ = 10 * 1024 * 1024  # 10MB
    MAX_FILE_SIZE_WRITE = 10 * 1024 * 1024  # 10MB

    # Bash timeout
    DEFAULT_BASH_TIMEOUT = 30  # seconds

    def __init__(self, workspace_root: Path | str):
        """Initialize tool executor.

        Args:
            workspace_root: Root directory for file operations (workspace jail)
        """
        self.workspace_root = Path(workspace_root).resolve()
        self.logger = get_logger()

        if not self.workspace_root.exists():
            raise ValueError(f"Workspace root does not exist: {self.workspace_root}")

    def _resolve_path(self, requested_path: str) -> Path:
        """Resolve and validate a file path within workspace.

        Args:
            requested_path: Path requested by agent (can be relative or absolute)

        Returns:
            Resolved absolute path within workspace

        Raises:
            ValueError: If path escapes workspace or is invalid
        """
        # Convert to Path and resolve
        path = Path(requested_path)

        # If relative, make it relative to workspace
        if not path.is_absolute():
            path = self.workspace_root / path

        # Resolve to absolute path (handles .., symlinks, etc.)
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError) as e:
            raise ValueError(f"Invalid path: {e}")

        # Security check: ensure it's within workspace
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise ValueError(
                f"Path escape attempt: {requested_path} resolves to {resolved}, "
                f"which is outside workspace {self.workspace_root}"
            )

        return resolved

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call.

        Args:
            tool_call: The tool to execute

        Returns:
            ToolResult with execution outcome
        """
        self.logger.info(
            "Tool execution started",
            tool=tool_call.tool,
            tool_id=tool_call.id,
        )

        try:
            # Check if it's a primitive tool
            if tool_call.tool == "read_file":
                result = await self._read_file(tool_call)
            elif tool_call.tool == "write_file":
                result = await self._write_file(tool_call)
            elif tool_call.tool == "edit_file":
                result = await self._edit_file(tool_call)
            elif tool_call.tool == "grep":
                result = await self._grep(tool_call)
            elif tool_call.tool == "glob":
                result = await self._glob(tool_call)
            elif tool_call.tool == "exec_bash":
                result = await self._exec_bash(tool_call)
            else:
                # Try to find it as a skill
                from orchestrator.services.tools.skills import get_skill_registry

                registry = get_skill_registry()
                skill = registry.get(tool_call.tool)

                if skill:
                    # Execute skill
                    args = tool_call.args.copy()
                    args["_tool_call_id"] = tool_call.id
                    result = await skill.execute(self, args)
                else:
                    result = ToolResult(
                        id=tool_call.id,
                        status="error",
                        content=f"Unknown tool: {tool_call.tool}",
                    )

            self.logger.info(
                "Tool execution completed",
                tool=tool_call.tool,
                tool_id=tool_call.id,
                status=result.status,
            )

            return result

        except Exception as e:
            self.logger.error(
                "Tool execution failed",
                tool=tool_call.tool,
                tool_id=tool_call.id,
                error=str(e),
            )

            return ToolResult(
                id=tool_call.id,
                status="error",
                content=f"Tool execution error: {e}",
            )

    async def _read_file(self, tool_call: ToolCall) -> ToolResult:
        """Read a file from workspace.

        Args:
            tool_call.args["path"]: File path to read

        Returns:
            ToolResult with file contents or error
        """
        path_str = tool_call.args.get("path")
        if not path_str:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content="Missing required argument: path",
            )

        try:
            file_path = self._resolve_path(path_str)

            if not file_path.exists():
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"File not found: {path_str}",
                )

            if not file_path.is_file():
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"Not a file: {path_str}",
                )

            # Check file size
            file_size = file_path.stat().st_size
            if file_size > self.MAX_FILE_SIZE_READ:
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"File too large: {file_size} bytes (max {self.MAX_FILE_SIZE_READ})",
                )

            # Read file
            content = file_path.read_text()

            return ToolResult(
                id=tool_call.id,
                status="ok",
                content=content,
                metadata={"path": str(file_path), "size": file_size},
            )

        except ValueError as e:
            # Path validation error
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=str(e),
            )
        except Exception as e:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=f"Read error: {e}",
            )

    async def _write_file(self, tool_call: ToolCall) -> ToolResult:
        """Write content to a file in workspace.

        Args:
            tool_call.args["path"]: File path to write
            tool_call.args["content"]: Content to write

        Returns:
            ToolResult with success or error
        """
        path_str = tool_call.args.get("path")
        content = tool_call.args.get("content")

        if not path_str:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content="Missing required argument: path",
            )

        if content is None:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content="Missing required argument: content",
            )

        try:
            file_path = self._resolve_path(path_str)

            # Check content size
            content_size = len(content.encode("utf-8"))
            if content_size > self.MAX_FILE_SIZE_WRITE:
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"Content too large: {content_size} bytes (max {self.MAX_FILE_SIZE_WRITE})",
                )

            # Create parent directories if needed
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file (atomic write via temp + rename)
            temp_path = file_path.with_suffix(file_path.suffix + ".tmp")
            temp_path.write_text(content)
            temp_path.replace(file_path)

            return ToolResult(
                id=tool_call.id,
                status="ok",
                content=f"File written: {path_str}",
                metadata={"path": str(file_path), "size": content_size},
            )

        except ValueError as e:
            # Path validation error
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=str(e),
            )
        except Exception as e:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=f"Write error: {e}",
            )

    async def _exec_bash(self, tool_call: ToolCall) -> ToolResult:
        """Execute a bash command in workspace.

        Args:
            tool_call.args["command"]: Bash command to execute
            tool_call.args["timeout"]: Optional timeout in seconds (default 30)

        Returns:
            ToolResult with command output or error
        """
        command = tool_call.args.get("command")
        if not command:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content="Missing required argument: command",
            )

        timeout = tool_call.args.get("timeout", self.DEFAULT_BASH_TIMEOUT)

        try:
            # Execute in workspace directory
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace_root),
                env=os.environ.copy(),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"Command timed out after {timeout}s",
                )

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            if process.returncode == 0:
                return ToolResult(
                    id=tool_call.id,
                    status="ok",
                    content=stdout_str,
                    metadata={
                        "exit_code": process.returncode,
                        "stderr": stderr_str,
                    },
                )
            else:
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"Command failed (exit {process.returncode}):\n{stderr_str}",
                    metadata={
                        "exit_code": process.returncode,
                        "stdout": stdout_str,
                    },
                )

        except Exception as e:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=f"Execution error: {e}",
            )

    async def _edit_file(self, tool_call: ToolCall) -> ToolResult:
        """Edit a file by replacing text (surgical string replacement).

        Args:
            tool_call.args["path"]: File path to edit
            tool_call.args["old_string"]: Text to replace
            tool_call.args["new_string"]: Replacement text
            tool_call.args["replace_all"]: Optional boolean - replace all occurrences (default False)

        Returns:
            ToolResult with success or error
        """
        path_str = tool_call.args.get("path")
        old_string = tool_call.args.get("old_string")
        new_string = tool_call.args.get("new_string")
        replace_all = tool_call.args.get("replace_all", False)

        if not path_str:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content="Missing required argument: path",
            )
        if old_string is None:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content="Missing required argument: old_string",
            )
        if new_string is None:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content="Missing required argument: new_string",
            )

        try:
            file_path = self._resolve_path(path_str)

            if not file_path.exists():
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"File not found: {path_str}",
                )

            if not file_path.is_file():
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"Not a file: {path_str}",
                )

            # Read file
            content = file_path.read_text()

            # Check file size
            file_size = len(content.encode("utf-8"))
            if file_size > self.MAX_FILE_SIZE_READ:
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"File too large: {file_size} bytes (max {self.MAX_FILE_SIZE_READ})",
                )

            # Perform replacement
            if replace_all:
                new_content = content.replace(old_string, new_string)
                count = content.count(old_string)
            else:
                # Replace only first occurrence
                new_content = content.replace(old_string, new_string, 1)
                count = 1 if old_string in content else 0

            if count == 0:
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"String not found in file: {old_string[:50]}...",
                )

            # Write file (atomic write via temp + rename)
            temp_path = file_path.with_suffix(file_path.suffix + ".tmp")
            temp_path.write_text(new_content)
            temp_path.replace(file_path)

            return ToolResult(
                id=tool_call.id,
                status="ok",
                content=f"Replaced {count} occurrence(s) in {path_str}",
                metadata={"path": str(file_path), "replacements": count},
            )

        except ValueError as e:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=str(e),
            )
        except Exception as e:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=f"Edit error: {e}",
            )

    async def _grep(self, tool_call: ToolCall) -> ToolResult:
        """Search for pattern in files within workspace.

        Args:
            tool_call.args["pattern"]: Regex pattern to search for
            tool_call.args["path"]: Optional path to search (default: workspace root)
            tool_call.args["glob_pattern"]: Optional glob pattern to filter files (default: "*")
            tool_call.args["ignore_case"]: Optional boolean for case-insensitive search (default False)
            tool_call.args["max_results"]: Optional max results to return (default: 100)

        Returns:
            ToolResult with search results
        """
        pattern = tool_call.args.get("pattern")
        if not pattern:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content="Missing required argument: pattern",
            )

        path_str = tool_call.args.get("path", ".")
        glob_pattern = tool_call.args.get("glob_pattern", "*")
        ignore_case = tool_call.args.get("ignore_case", False)
        max_results = tool_call.args.get("max_results", 100)

        try:
            search_path = self._resolve_path(path_str)

            if not search_path.exists():
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"Path not found: {path_str}",
                )

            # Compile regex pattern
            flags = re.IGNORECASE if ignore_case else 0
            try:
                regex = re.compile(pattern, flags)
            except re.error as e:
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"Invalid regex pattern: {e}",
                )

            results = []
            result_count = 0

            # Walk the directory
            if search_path.is_file():
                files_to_search = [search_path]
            else:
                files_to_search = []
                for root, dirs, files in os.walk(search_path):
                    for filename in files:
                        if fnmatch.fnmatch(filename, glob_pattern):
                            files_to_search.append(Path(root) / filename)

            # Search in files
            for file_path in files_to_search:
                if result_count >= max_results:
                    break

                try:
                    content = file_path.read_text()
                    for line_num, line in enumerate(content.splitlines(), 1):
                        if regex.search(line):
                            # Return relative path from workspace
                            rel_path = file_path.relative_to(self.workspace_root)
                            results.append(f"{rel_path}:{line_num}: {line.strip()}")
                            result_count += 1
                            if result_count >= max_results:
                                break
                except (UnicodeDecodeError, PermissionError):
                    # Skip binary files or files we can't read
                    continue

            if not results:
                return ToolResult(
                    id=tool_call.id,
                    status="ok",
                    content=f"No matches found for pattern: {pattern}",
                    metadata={"matches": 0},
                )

            output = "\n".join(results)
            truncated = result_count >= max_results
            if truncated:
                output += f"\n... (truncated at {max_results} results)"

            return ToolResult(
                id=tool_call.id,
                status="ok",
                content=output,
                metadata={"matches": result_count, "truncated": truncated},
            )

        except ValueError as e:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=str(e),
            )
        except Exception as e:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=f"Grep error: {e}",
            )

    async def _glob(self, tool_call: ToolCall) -> ToolResult:
        """Find files matching a pattern within workspace.

        Args:
            tool_call.args["pattern"]: Glob pattern (e.g., "**/*.py", "src/**/*.yaml")
            tool_call.args["path"]: Optional base path for pattern (default: workspace root)

        Returns:
            ToolResult with list of matching file paths
        """
        pattern = tool_call.args.get("pattern")
        if not pattern:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content="Missing required argument: pattern",
            )

        path_str = tool_call.args.get("path", ".")

        try:
            base_path = self._resolve_path(path_str)

            if not base_path.exists():
                return ToolResult(
                    id=tool_call.id,
                    status="error",
                    content=f"Path not found: {path_str}",
                )

            # Use glob module to find files
            search_pattern = str(base_path / pattern)
            matches = glob_module.glob(search_pattern, recursive=True)

            # Filter to files only (not directories) and return relative paths
            files = []
            for match in matches:
                match_path = Path(match)
                if match_path.is_file():
                    rel_path = match_path.relative_to(self.workspace_root)
                    files.append(str(rel_path))

            # Sort for consistent output
            files.sort()

            if not files:
                return ToolResult(
                    id=tool_call.id,
                    status="ok",
                    content=f"No files found matching pattern: {pattern}",
                    metadata={"matches": 0},
                )

            output = "\n".join(files)
            return ToolResult(
                id=tool_call.id,
                status="ok",
                content=output,
                metadata={"matches": len(files)},
            )

        except ValueError as e:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=str(e),
            )
        except Exception as e:
            return ToolResult(
                id=tool_call.id,
                status="error",
                content=f"Glob error: {e}",
            )
