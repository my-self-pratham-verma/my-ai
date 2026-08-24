"""OpenRouter streaming agent and browser/terminal event protocol."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, Optional

import requests

from .config import (
    MAX_AGENT_ITERATIONS,
    MAX_DISPLAY_OUTPUT,
    MAX_TOOL_OUTPUT,
    MODEL,
    OPENROUTER_URL,
    REQUEST_RETRY_LIMIT,
    REQUEST_TIMEOUT,
    SYSTEM_PROMPT,
    USER_AGENT_NAME,
)
from .tools import CodebaseTools, execute_tool, tool_definitions, truncate

EmitEvent = Callable[[str, Dict[str, Any]], None]


@dataclass
class ToolCall:
    index: int
    call_id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class SessionStats:
    requests: int = 0
    tool_calls: int = 0
    turns: int = 0
    total_latency: float = 0.0
    started_at: float = field(default_factory=time.time)

    def uptime(self) -> float:
        return time.time() - self.started_at


class CodingAgent:
    """Stateful agent whose events work for either browser or terminal clients."""

    def __init__(self, api_key: str, tools: CodebaseTools) -> None:
        self.api_key = api_key
        self.tools = tools
        self.messages: list[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.stats = SessionStats()

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.stats = SessionStats()

    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-OpenRouter-Title": USER_AGENT_NAME,
        }

    def request_stream(self) -> requests.Response:
        payload = {
            "model": MODEL,
            "messages": self.messages,
            "tools": tool_definitions(),
            "tool_choice": "auto",
            "stream": True,
            "temperature": 0.2,
        }
        last_error = "Unknown OpenRouter error."

        for attempt in range(REQUEST_RETRY_LIMIT):
            try:
                response = requests.post(
                    OPENROUTER_URL,
                    headers=self.headers(),
                    json=payload,
                    stream=True,
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                response = None
            else:
                if response.status_code == 200:
                    # Some SSE responses omit a charset. Decode every provider
                    # chunk ourselves as UTF-8 instead of trusting that default.
                    response.encoding = "utf-8"
                    return response
                last_error = response.text or f"HTTP {response.status_code}"
                retryable = response.status_code in {408, 409, 429, 500, 502, 503, 504}
                response.close()
                if not retryable:
                    break

            if attempt < REQUEST_RETRY_LIMIT - 1:
                time.sleep(attempt + 1)

        raise RuntimeError(f"OpenRouter request failed after {REQUEST_RETRY_LIMIT} attempts: {last_error}")

    @staticmethod
    def parse_sse(response: requests.Response) -> Iterator[Dict[str, Any]]:
        """Parse provider SSE safely, including providers without a final blank line."""

        data_lines: list[str] = []
        for raw_line in response.iter_lines(decode_unicode=False):
            if raw_line is None:
                continue

            # Provider chunks are UTF-8 JSON. Requests can otherwise fall back
            # to ISO-8859-1 for an SSE content type with no explicit charset.
            line = raw_line.decode("utf-8", errors="replace").strip()

            if not line:
                if not data_lines:
                    continue
                payload = "\n".join(data_lines)
                data_lines.clear()
                if payload == "[DONE]":
                    return
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue
                continue

            if line.startswith(":") or not line.startswith("data:"):
                continue
            value = line[5:].strip()
            if value == "[DONE]":
                return
            data_lines.append(value)

        if data_lines:
            try:
                yield json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                return

    @staticmethod
    def extract_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)
        return str(content)

    @staticmethod
    def _merge_tool_call(tool_calls: Dict[int, ToolCall], incoming: Dict[str, Any]) -> None:
        index = incoming.get("index", 0)
        call = tool_calls.setdefault(index, ToolCall(index=index))
        call.call_id = incoming.get("id") or call.call_id
        function = incoming.get("function", {})
        name = function.get("name")
        arguments = function.get("arguments")

        if name:
            if not call.name:
                call.name = name
            elif name != call.name and not call.name.endswith(name):
                call.name += name
        if arguments:
            call.arguments += arguments

    def stream_completion(self, emit: EmitEvent) -> Dict[str, Any]:
        started = time.time()
        response = self.request_stream()
        self.stats.requests += 1
        assistant_text = ""
        reasoning_details: list[Any] = []
        tool_calls: Dict[int, ToolCall] = {}

        try:
            for chunk in self.parse_sse(response):
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})
                content = self.extract_content(delta.get("content"))

                if content:
                    assistant_text += content
                    emit("delta", {"text": content})

                details = delta.get("reasoning_details")
                if details:
                    reasoning_details.extend(details if isinstance(details, list) else [details])

                for incoming in delta.get("tool_calls", []):
                    self._merge_tool_call(tool_calls, incoming)
        finally:
            response.close()
            self.stats.total_latency += time.time() - started

        return {"content": assistant_text, "reasoning_details": reasoning_details, "tool_calls": tool_calls}

    def run_turn(self, user_message: str, emit: EmitEvent) -> None:
        """Run one complete request, emitting normalized lifecycle events."""

        self.messages.append({"role": "user", "content": user_message})
        self.stats.turns += 1

        for iteration in range(1, MAX_AGENT_ITERATIONS + 1):
            emit("completion_start", {"iteration": iteration})
            emit("status", {"message": "Reviewing your request…"})
            completion = self.stream_completion(emit)
            assistant_text = completion["content"]
            reasoning_details = completion["reasoning_details"]
            tool_calls = completion["tool_calls"]

            if not tool_calls:
                if assistant_text:
                    message: Dict[str, Any] = {"role": "assistant", "content": assistant_text}
                    if reasoning_details:
                        message["reasoning_details"] = reasoning_details
                    self.messages.append(message)
                emit("done", {"iteration": iteration, "stats": self.stats.__dict__})
                return

            formatted_calls = []
            for index in sorted(tool_calls):
                call = tool_calls[index]
                formatted_calls.append({
                    "id": call.call_id or f"call_{uuid.uuid4().hex[:10]}",
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                })

            assistant_message: Dict[str, Any] = {
                "role": "assistant",
                "content": assistant_text or None,
                "tool_calls": formatted_calls,
            }
            if reasoning_details:
                assistant_message["reasoning_details"] = reasoning_details
            self.messages.append(assistant_message)

            for call in formatted_calls:
                name = call["function"]["name"]
                arguments_raw = call["function"]["arguments"]
                tool_id = call["id"]

                try:
                    arguments = json.loads(arguments_raw)
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must be a JSON object.")
                except (json.JSONDecodeError, ValueError) as exc:
                    result = f"Tool error: Invalid arguments for {name}: {exc}"
                    emit("tool_start", {"tool_id": tool_id, "name": name, "arguments": {}})
                    emit("tool_result", {"tool_id": tool_id, "success": False, "elapsed": 0.0, "result": result})
                else:
                    self.stats.tool_calls += 1
                    emit("tool_start", {"tool_id": tool_id, "name": name, "arguments": arguments})
                    started = time.time()
                    result = execute_tool(self.tools, name, arguments)
                    elapsed = time.time() - started
                    emit("tool_result", {
                        "tool_id": tool_id,
                        "success": not result.startswith("Tool error:"),
                        "elapsed": elapsed,
                        "result": truncate(result, MAX_DISPLAY_OUTPUT),
                    })

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": truncate(result, MAX_TOOL_OUTPUT),
                })

            emit("status", {"message": "Interpreting tool results…"})

        raise RuntimeError("Agent stopped after reaching the maximum tool iterations.")