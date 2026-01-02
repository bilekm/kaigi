"""Tool call and result models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """A tool invocation request from an agent."""

    type: Literal["tool_call"] = "tool_call"
    id: str = Field(description="Unique ID for this tool call")
    tool: str = Field(description="Tool name (read_file, write_file, exec_bash)")
    args: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")


class ToolResult(BaseModel):
    """Result of a tool execution."""

    type: Literal["tool_result"] = "tool_result"
    id: str = Field(description="ID of the tool call this responds to")
    status: Literal["ok", "error"] = Field(description="Execution status")
    content: str | None = Field(default=None, description="Tool output or error message")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
