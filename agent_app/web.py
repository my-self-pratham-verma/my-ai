"""Local HTTP API and static-file server for the browser agent interface."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from .agent import CodingAgent
from .config import MAX_MESSAGE_LENGTH, MAX_REQUEST_BODY_SIZE, MODEL, Settings
from .tools import CodebaseTools

STATIC_DIR = Path(__file__).with_name("static")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,100}$")
SESSION_TTL_SECONDS = 60 * 60 * 4
MAX_SESSIONS = 100


@dataclass
class WebSession:
    agent: CodingAgent
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_used_at: float = field(default_factory=time.monotonic)


class SessionStore:
    """Bounded, in-memory browser conversations for the local server."""

    def __init__(self, settings: Settings, tools: CodebaseTools) -> None:
        self.settings = settings
        self.tools = tools
        self._sessions: Dict[str, WebSession] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> WebSession:
        with self._lock:
            self._prune_expired()
            session = self._sessions.get(session_id)
            if session is None:
                if len(self._sessions) >= MAX_SESSIONS:
                    oldest_id = min(self._sessions, key=lambda key: self._sessions[key].last_used_at)
                    self._sessions.pop(oldest_id, None)
                session = WebSession(agent=CodingAgent(self.settings.api_key, self.tools))
                self._sessions[session_id] = session
            session.last_used_at = time.monotonic()
            return session

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _prune_expired(self) -> None:
        cutoff = time.monotonic() - SESSION_TTL_SECONDS
        expired_ids = [key for key, session in self._sessions.items() if session.last_used_at < cutoff]
        for session_id in expired_ids:
            self._sessions.pop(session_id, None)


class AgentWebServer(ThreadingHTTPServer):
    """HTTP server carrying application dependencies instead of global state."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, settings: Settings, repository: Path) -> None:
        super().__init__((settings.host, settings.port), AgentWebHandler)
        self.settings = settings
        self.repository = repository
        self.sessions = SessionStore(settings, CodebaseTools(repository))


class AgentWebHandler(BaseHTTPRequestHandler):
    """Serve a local-only UI and strict JSON/SSE API routes."""

    protocol_version = "HTTP/1.1"
    server_version = "PrathamAgent/2.0"

    @property
    def app_server(self) -> AgentWebServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *arguments: Any) -> None:
        """Do not print request logs that may include user content."""

    def add_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )

    def send_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.add_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> Dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length must be a number.") from exc

        if content_length < 0 or content_length > MAX_REQUEST_BODY_SIZE:
            raise ValueError("Request body is too large.")

        payload = json.loads(self.rfile.read(content_length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    @staticmethod
    def session_id_from(payload: Dict[str, Any]) -> str:
        session_id = str(payload.get("session_id", ""))
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("Invalid session identifier.")
        return session_id

    def serve_static(self, filename: str, content_type: str) -> None:
        file_path = STATIC_DIR / filename
        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Static file not found."})
            return

        self.send_response(HTTPStatus.OK)
        self.add_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        static_routes = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
        }

        if path in static_routes:
            self.serve_static(*static_routes[path])
            return

        if path == "/api/info":
            self.send_json(HTTPStatus.OK, {
                "repository": str(self.app_server.repository),
                "model": MODEL,
            })
            return

        self.send_json(HTTPStatus.NOT_FOUND, {"error": "Route not found."})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json_body()
            session_id = self.session_id_from(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if path == "/api/reset":
            self.app_server.sessions.reset(session_id)
            self.send_json(HTTPStatus.OK, {"ok": True})
            return

        if path != "/api/chat":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Route not found."})
            return

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "A message is required."})
            return
        if len(message) > MAX_MESSAGE_LENGTH:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Message is too long."})
            return

        self.stream_chat(session_id, message.strip())

    def stream_chat(self, session_id: str, message: str) -> None:
        session = self.app_server.sessions.get(session_id)
        self.send_response(HTTPStatus.OK)
        self.add_security_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        def emit(event: str, data: Dict[str, Any]) -> None:
            encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(f"event: {event}\ndata: {encoded}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            with session.lock:
                session.last_used_at = time.monotonic()
                session.agent.run_turn(message, emit)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                emit("error", {"message": str(exc)})
                emit("done", {"failed": True})
            except (BrokenPipeError, ConnectionResetError):
                return


def run_web_server(settings: Settings, repository: Path) -> None:
    """Run the local browser interface until the user stops it."""

    server = AgentWebServer(settings, repository)
    print(f"Web agent available at http://{settings.host}:{settings.port}")
    print("Press Ctrl+C to stop the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWeb agent stopped.")
    finally:
        server.server_close()
