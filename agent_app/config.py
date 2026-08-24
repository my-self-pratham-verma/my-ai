"""Application configuration and codebase policy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet

MODEL = "nvidia/nemotron-3.5-lightning:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
USER_AGENT_NAME = "Pratham's AI Agent"

MAX_FILE_SIZE = 500_000
MAX_TOOL_OUTPUT = 30_000
MAX_DISPLAY_OUTPUT = 6_000
REQUEST_TIMEOUT = 300
REQUEST_RETRY_LIMIT = 3
DEFAULT_SEARCH_RESULTS = 50
DEFAULT_LIST_RESULTS = 500
MAX_AGENT_ITERATIONS = 12
MAX_MESSAGE_LENGTH = 20_000
MAX_REQUEST_BODY_SIZE = 100_000

IGNORED_DIRS: FrozenSet[str] = frozenset({
    ".git", ".hg", ".svn", ".bzr", "node_modules", ".venv", "venv",
    "env", "__pycache__", ".next", ".nuxt", ".turbo", ".cache", "dist",
    "build", "target", "coverage", ".idea", ".vscode", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".terraform", "vendor",
})

TEXT_EXTENSIONS: FrozenSet[str] = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java",
    ".go", ".rs", ".cpp", ".c", ".h", ".hpp", ".cc", ".cs", ".php", ".rb",
    ".swift", ".kt", ".kts", ".scala", ".sh", ".bash", ".zsh", ".fish", ".sql",
    ".html", ".css", ".scss", ".sass", ".less", ".json", ".yaml", ".yml",
    ".toml", ".xml", ".md", ".txt", ".ini", ".cfg", ".conf", ".graphql", ".gql",
    ".proto",
})

SPECIAL_TEXT_FILES: FrozenSet[str] = frozenset({
    "Dockerfile", "Makefile", "Procfile", "Gemfile", "Rakefile", "Justfile",
    "Taskfile", ".gitignore", ".dockerignore", ".editorconfig", ".prettierrc",
    ".eslintrc", ".npmrc", ".nvmrc", ".python-version",
})

SYSTEM_PROMPT = """
You are a software engineering agent working inside a user's codebase.

Your job is to understand the repository, investigate problems, answer code
questions, debug issues, and help implement software changes.

Available tools:
1. search_codebase
2. read_file
3. list_files
4. run_command

Rules:
- The codebase is the source of truth. Search it before making assumptions.
- Read relevant files after searching and keep tool usage focused.
- Use run_command only when it is useful for diagnostics, tests, builds, or git.
- Do not expose secrets, tokens, credentials, or passwords found in the repository.
- Avoid destructive commands and explain concise findings after investigation.
- Do not claim files were changed unless a tool actually changed them.
""".strip()


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from the environment once at startup."""

    api_key: str
    host: str
    port: int

    @classmethod
    def from_environment(cls) -> "Settings":
        port = int(os.getenv("AGENT_PORT", "8000"))

        if not 1 <= port <= 65535:
            raise ValueError("AGENT_PORT must be between 1 and 65535.")

        return cls(
            api_key='abc',
            host="127.0.0.1",
            port=port,
        )


def resolve_repository(path: str | None) -> Path:
    """Validate a requested repository and return its canonical path."""

    repository = Path(path or Path.cwd()).expanduser().resolve()

    if not repository.exists():
        raise ValueError(f"Repository does not exist: {repository}")

    if not repository.is_dir():
        raise ValueError(f"Repository is not a directory: {repository}")

    return repository