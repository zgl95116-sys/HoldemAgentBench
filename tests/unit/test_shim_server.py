import httpx
import pytest

from hab.shim.server import ShimServer


@pytest.mark.asyncio
async def test_shim_health():
    shim = ShimServer(openrouter_key="dummy")
    await shim.start()
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{shim.base_url}/healthz")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"
    finally:
        await shim.stop()


def test_register_player_returns_unique_token():
    shim = ShimServer(openrouter_key="dummy")
    t1 = shim.register_player("player_a", "openai/gpt-4o-mini")
    t2 = shim.register_player("player_b", "anthropic/claude-opus-4-7")
    assert t1 != t2
    assert shim.token_to_model[t1] == "openai/gpt-4o-mini"
    assert shim.token_to_model[t2] == "anthropic/claude-opus-4-7"
