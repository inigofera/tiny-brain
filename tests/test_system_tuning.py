from __future__ import annotations

from pathlib import Path
import pytest
import yaml
from fastapi.testclient import TestClient

from tiny_brain.agent.llama import StreamChunk
from tiny_brain.agent.prompts import SYSTEM_PROMPT
from tiny_brain.config import Settings
from tiny_brain.web.app import create_app


def test_compose_yaml_mlock_and_ulimits():
    compose_path = Path(__file__).parent.parent / "compose.yaml"
    content = compose_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    llama = parsed["services"]["llama"]

    # 1. --mlock in command
    assert "--mlock" in llama["command"]

    # 2. memlock ulimits unlimited
    assert "ulimits" in llama
    assert "memlock" in llama["ulimits"]
    assert llama["ulimits"]["memlock"]["soft"] == -1
    assert llama["ulimits"]["memlock"]["hard"] == -1


def test_provision_host_sh_cpu_governor_tuning():
    provision_path = Path(__file__).parent.parent / "deployment" / "provision_host.sh"
    content = provision_path.read_text(encoding="utf-8")
    assert "scaling_governor" in content or "cpupower" in content
    assert "performance" in content


def test_system_prompt_enforces_concise_answers():
    assert "3 sentences or fewer" in SYSTEM_PROMPT
    assert "Tiny Brain" in SYSTEM_PROMPT


def test_chat_stream_caps_max_tokens_for_fast_chat(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(
        vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models",
        "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4,
        "tiny-brain.test", True, 0.9, True, "127.0.0.1", 8000,
        max_output_tokens=4096,
        fast_chat_max_tokens=250,
    )
    app = create_app(settings)
    captured_kwargs: dict[str, object] = {}

    class StreamCapturingLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "NONE"}}]}

        async def stream(self, messages, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = dict(kwargs)
            yield StreamChunk(content="Concise reply.")

    app.state.llama = StreamCapturingLlama()
    client = TestClient(app)

    # 1. Fast Chat mode (thinking=False by default) -> caps at 250 tokens
    response = client.post("/chat/stream", json={"message": "quick question", "thinking": False})
    assert response.status_code == 200
    assert captured_kwargs.get("max_tokens") == 250

    # 2. Deep Think mode (thinking=True) -> allows up to max_output_tokens (4096)
    response = client.post("/chat/stream", json={"message": "complex question", "thinking": True})
    assert response.status_code == 200
    assert captured_kwargs.get("max_tokens") == 4096

    # 3. Draft Note mode -> caps at 1024
    response = client.post("/chat/stream", json={"message": "draft a note about caching", "thinking": False})
    assert response.status_code == 200
    assert captured_kwargs.get("max_tokens") == 1024
