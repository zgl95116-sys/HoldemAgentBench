"""Decide whether a request goes to OpenRouter or directly to Anthropic."""
from __future__ import annotations


def route_request(model: str, anthropic_key: str | None) -> str:
    if model.startswith("anthropic/") and anthropic_key:
        return "anthropic_direct"
    return "openrouter"


def normalize_model_for_provider(model: str, provider: str) -> str:
    if provider == "anthropic_direct" and model.startswith("anthropic/"):
        return model[len("anthropic/"):]
    return model
