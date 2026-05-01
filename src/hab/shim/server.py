"""Local FastAPI server that pretends to be the Anthropic /v1/messages endpoint."""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request

from hab.shim.router import normalize_model_for_provider, route_request
from hab.shim.translator import (
    anthropic_request_to_openai,
    openai_response_to_anthropic,
)

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class ShimServer:
    def __init__(
        self,
        openrouter_key: str | None,
        anthropic_key: str | None = None,
        host: str = "127.0.0.1",
        port: int | None = None,
    ):
        self.openrouter_key = openrouter_key
        self.anthropic_key = anthropic_key
        self.host = host
        self.port = port if port is not None else self._find_free_port()
        self.app = FastAPI(title="HAB Shim")
        # token → forced model. Lets us pin every model call from a specific
        # claude subprocess to the configured model, regardless of what claude
        # asks for internally (e.g. haiku-4-5 routing).
        self.token_to_model: dict[str, str] = {}
        self._setup_routes()
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task | None = None
        # trust_env=False: ignore system proxy. macOS users with a local-loopback
        # VPN/proxy (Clash/V2Ray/etc.) often see flaky TLS-over-CONNECT for
        # outbound HTTPS. OpenRouter is reachable directly from most networks,
        # so we go direct. Combined with built-in retries on transient failures.
        self._client = httpx.AsyncClient(
            timeout=180.0,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(retries=3),
        )

    def register_player(self, player_id: str, model: str) -> str:
        """Register a player. Returns the fake auth token claude should send."""
        # Token doubles as identifier; doesn't have to be cryptographically secure.
        token = f"hab-{player_id}"
        self.token_to_model[token] = model
        return token

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket() as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _setup_routes(self):
        @self.app.post("/v1/messages")
        async def messages(request: Request):
            body: dict[str, Any] = await request.json()

            # Override model based on caller's auth token. Claude CLI uses
            # haiku-4-5 internally for some routing/classifier calls; we want
            # ALL of those to go to the player's configured model so the
            # benchmark measures the model+agent combo, not whatever Anthropic
            # decided to route to.
            auth = (
                request.headers.get("x-api-key")
                or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            )
            forced = self.token_to_model.get(auth)
            if forced:
                body["model"] = forced

            model = body.get("model", "")
            provider = route_request(model, self.anthropic_key)
            normalized = normalize_model_for_provider(model, provider)

            if provider == "anthropic_direct":
                body["model"] = normalized
                resp = await self._client.post(
                    ANTHROPIC_URL,
                    headers={
                        "x-api-key": self.anthropic_key or "",
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=body,
                )
                return resp.json()

            openai_req = anthropic_request_to_openai(body)
            resp = await self._client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self.openrouter_key}",
                    "HTTP-Referer": "https://github.com/holdem-agent-bench",
                    "X-Title": "HoldemAgentBench",
                    "content-type": "application/json",
                },
                json=openai_req,
            )
            data = resp.json()
            if "choices" not in data:
                logger.warning("OpenRouter returned non-standard response: %s", data)
                return data
            return openai_response_to_anthropic(data)

        @self.app.get("/healthz")
        async def healthz():
            return {"status": "ok", "port": self.port}

    async def start(self):
        config = uvicorn.Config(
            self.app, host=self.host, port=self.port, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(50):
            await asyncio.sleep(0.1)
            if self.server.started:
                return
        raise RuntimeError("Shim server failed to start")

    async def stop(self):
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=5.0)
            except asyncio.TimeoutError:
                self.task.cancel()
        await self._client.aclose()
