"""Command-line entry point and terminal interface."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

import requests
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from .agent import CodingAgent
from .config import MODEL, Settings, resolve_repository
from .tools import CodebaseTools
from .web import run_web_server

console = Console(highlight=False, soft_wrap=True)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A local OpenRouter coding agent.")
    parser.add_argument("repository", nargs="?", help="Repository to inspect. Defaults to the current directory.")
    parser.add_argument("--web", action="store_true", help="Run the browser interface.")
    return parser.parse_args()


def require_api_key(settings: Settings) -> None:
    if not settings.api_key:
        raise ValueError("OPENROUTER_API_KEY is not configured. Export it before starting the agent.")


def multiline_input() -> str:
    first = Prompt.ask("[bold cyan]You[/bold cyan]")
    lines = [first.removesuffix("\\")]
    while first.endswith("\\"):
        first = Prompt.ask("[dim]...[/dim]")
        lines.append(first.removesuffix("\\"))
    return "\n".join(lines).strip()


def run_terminal(settings: Settings, repository: Path) -> None:
    agent = CodingAgent(settings.api_key, CodebaseTools(repository))
    console.print(Panel(f"[bold cyan]Pratham's AI Agent[/bold cyan]\n{repository}\n[dim]Type /help for commands[/dim]", border_style="cyan"))

    while True:
        try:
            message = multiline_input()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[cyan]Goodbye.[/cyan]")
            return

        if not message:
            continue
        if message == "/exit":
            return
        if message == "/clear":
            agent.reset()
            console.print("[green]Conversation cleared.[/green]")
            continue
        if message == "/help":
            console.print("/help  /clear  /status  /files  /exit")
            continue
        if message == "/status":
            console.print(agent.stats)
            continue
        if message == "/files":
            console.print(agent.tools.list_files(max_results=100))
            continue

        response_parts: list[str] = []

        def emit(event: str, data: Dict[str, Any]) -> None:
            if event == "status":
                console.print(f"[dim cyan]◌ {data['message']}[/dim cyan]")
            elif event == "delta":
                response_parts.append(data["text"])
            elif event == "tool_start":
                console.print(f"[cyan]⚙ {data['name']}[/cyan]")
            elif event == "tool_result":
                result = data["result"]
                color = "green" if data["success"] else "red"
                console.print(Panel(result or "No output", title=f"[{color}]{data['elapsed']:.2f}s[/{color}]", border_style=color))

        try:
            agent.run_turn(message, emit)
            if response_parts:
                console.print(Markdown("".join(response_parts)))
        except requests.RequestException as exc:
            console.print(f"[red]Network error: {exc}[/red]")
        except Exception as exc:
            console.print(f"[red]{exc}[/red]")


def main() -> None:
    arguments = parse_arguments()
    try:
        settings = Settings.from_environment()
        repository = resolve_repository(arguments.repository)
        require_api_key(settings)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    if arguments.web:
        run_web_server(settings, repository)
    else:
        run_terminal(settings, repository)