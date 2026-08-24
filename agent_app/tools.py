"""Safe, bounded operations exposed to the coding agent."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict

from .config import (
    DEFAULT_LIST_RESULTS,
    DEFAULT_SEARCH_RESULTS,
    IGNORED_DIRS,
    MAX_FILE_SIZE,
    MAX_TOOL_OUTPUT,
    SPECIAL_TEXT_FILES,
    TEXT_EXTENSIONS,
)


def truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    """Limit tool output before it is returned to the model or browser."""

    if len(text) <= limit:
        return text

    return f"{text[:limit]}\n\n[... output truncated ...]"


class CodebaseTools:
    """Repository-scoped tools that never resolve paths outside their root."""

    def __init__(self, repository: Path) -> None:
        self.repository = repository.resolve()

    def safe_path(self, requested_path: str) -> Path:
        requested = Path(requested_path).expanduser()
        resolved = (requested if requested.is_absolute() else self.repository / requested).resolve()

        try:
            resolved.relative_to(self.repository)
        except ValueError as exc:
            raise ValueError(f"Access denied: {requested_path} is outside the current codebase.") from exc

        return resolved

    @staticmethod
    def should_ignore(path: Path) -> bool:
        return any(part in IGNORED_DIRS for part in path.parts)

    @staticmethod
    def is_text_file(path: Path) -> bool:
        return path.suffix.lower() in TEXT_EXTENSIONS or path.name in SPECIAL_TEXT_FILES

    def relative_path(self, path: Path) -> str:
        return str(path.relative_to(self.repository))

    def list_files(
        self,
        directory: str = ".",
        pattern: str = "*",
        max_results: int = DEFAULT_LIST_RESULTS,
    ) -> str:
        root = self.safe_path(directory)

        if not root.exists():
            return f"Directory does not exist: {directory}"

        if not root.is_dir():
            return f"Not a directory: {directory}"

        results = []
        for path in root.rglob(pattern):
            if not path.is_file() or self.should_ignore(path):
                continue

            results.append(self.relative_path(path))
            if len(results) >= max(1, min(max_results, DEFAULT_LIST_RESULTS)):
                break

        if not results:
            return "No files found."

        return f"Files found: {len(results)}\n\n" + "\n".join(sorted(results))

    def read_file(self, path: str) -> str:
        file_path = self.safe_path(path)

        if not file_path.exists():
            return f"File does not exist: {path}"

        if not file_path.is_file():
            return f"Not a file: {path}"

        size = file_path.stat().st_size
        if size > MAX_FILE_SIZE:
            return (
                "File is too large to read directly.\n"
                f"Path: {path}\nSize: {size:,} bytes\nLimit: {MAX_FILE_SIZE:,} bytes"
            )

        try:
            return truncate(file_path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return f"Failed to read {path}: {exc}"

    def search_codebase(
        self,
        query: str,
        path: str = ".",
        max_results: int = DEFAULT_SEARCH_RESULTS,
        case_sensitive: bool = False,
    ) -> str:
        root = self.safe_path(path)

        if not root.exists():
            return f"Path does not exist: {path}"

        needle = query if case_sensitive else query.lower()
        results = []
        files_scanned = 0

        for file_path in root.rglob("*"):
            if not file_path.is_file() or self.should_ignore(file_path) or not self.is_text_file(file_path):
                continue

            try:
                if file_path.stat().st_size > MAX_FILE_SIZE:
                    continue
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            files_scanned += 1
            for line_number, line in enumerate(content.splitlines(), start=1):
                comparison = line if case_sensitive else line.lower()
                if needle not in comparison:
                    continue

                results.append(f"{self.relative_path(file_path)}:{line_number}: {line.strip()}")
                if len(results) >= max(1, min(max_results, DEFAULT_SEARCH_RESULTS)):
                    break

            if len(results) >= max(1, min(max_results, DEFAULT_SEARCH_RESULTS)):
                break

        if not results:
            return f'No matches found for "{query}".\nFiles scanned: {files_scanned}'

        return f'Found {len(results)} match(es) for "{query}".\n\n' + "\n".join(results)

    def run_command(self, command: str, timeout: int = 30) -> str:
        """Run a bounded repository command after rejecting known destructive forms."""

        dangerous_patterns = (
            "rm -rf /", "rm -rf ~", "mkfs", "shutdown", "reboot", "poweroff",
            ":(){ :|:& };:", "dd if=",
        )
        normalized = command.strip().lower()

        if any(pattern in normalized for pattern in dangerous_patterns):
            return "Command blocked because it matches a dangerous command pattern."

        try:
            process = subprocess.run(
                command,
                shell=True,
                cwd=self.repository,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=max(1, min(timeout, 120)),
            )
            return f"Exit code: {process.returncode}\n\n{truncate(process.stdout or '')}"
        except subprocess.TimeoutExpired:
            return f"Command timed out after {max(1, min(timeout, 120))} seconds."
        except OSError as exc:
            return f"Command execution failed: {exc}"


def tool_definitions() -> list[Dict[str, Any]]:
    """OpenAI-compatible function definitions sent to OpenRouter."""

    return [
        {
            "type": "function",
            "function": {
                "name": "search_codebase",
                "description": "Search the current codebase for a text string.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "case_sensitive": {"type": "boolean"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a source or configuration file from the current codebase.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in the current codebase.",
                "parameters": {
                    "type": "object",
                    "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}, "max_results": {"type": "integer"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Run a diagnostic, test, build, or git command inside the repository.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
                    "required": ["command"],
                },
            },
        },
    ]


def execute_tool(tools: CodebaseTools, name: str, arguments: Dict[str, Any]) -> str:
    """Dispatch a model tool call without exposing internal exceptions."""

    handlers: Dict[str, Callable[..., str]] = {
        "search_codebase": tools.search_codebase,
        "read_file": tools.read_file,
        "list_files": tools.list_files,
        "run_command": tools.run_command,
    }
    handler = handlers.get(name)

    if handler is None:
        return f"Tool error: Unknown tool: {name}"

    try:
        return handler(**arguments)
    except (KeyError, TypeError, ValueError) as exc:
        return f"Tool error: {exc}"