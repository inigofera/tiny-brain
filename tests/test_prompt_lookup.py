from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from tiny_brain.agent.llama import LlamaClient


def test_compose_yaml_has_prompt_lookup_flag():
    compose_path = Path(__file__).parent.parent / "compose.yaml"
    content = compose_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    cmd = parsed["services"]["llama"]["command"]

    assert "--spec-type" in cmd
    idx = cmd.index("--spec-type")
    assert "ngram-mod" in cmd[idx + 1]


@pytest.mark.asyncio
async def test_llama_client_passes_lookup_ngram_min():
    client = LlamaClient("http://fake:8080/v1", "test-model", lookup_ngram_min=2)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        from unittest.mock import MagicMock
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        # Verify client passes lookup_ngram_min in payload
        await client.complete([{"role": "user", "content": "test"}])
        assert mock_post.call_count == 1
        payload = mock_post.call_args.kwargs["json"]
        assert payload.get("lookup_ngram_min") == 2

        # Explicit override
        await client.complete([{"role": "user", "content": "test"}], lookup_ngram_min=3)
        assert mock_post.call_count == 2
        payload = mock_post.call_args.kwargs["json"]
        assert payload.get("lookup_ngram_min") == 3


def test_ngram_speculative_lookup_algorithm():
    """
    Unit test verifying the core algorithmic mechanic of Prompt Lookup Decoding:
    Given prompt tokens and generation context, finds the last N-gram in the prompt
    and extracts candidate speculative tokens.
    """
    def find_speculative_draft(prompt_tokens: list[str], generated_tokens: list[str], ngram_size: int = 2, max_draft: int = 5) -> list[str]:
        if len(generated_tokens) < ngram_size:
            return []
        query_ngram = generated_tokens[-ngram_size:]
        # Search prompt tokens for matching ngram
        for i in range(len(prompt_tokens) - ngram_size):
            if prompt_tokens[i : i + ngram_size] == query_ngram:
                # Draft candidate tokens following the match
                start = i + ngram_size
                return prompt_tokens[start : start + max_draft]
        return []

    prompt = ["We", "utilize", "high-speed", "NVMe", "solid", "state", "drives", "for", "storage", "."]
    
    # Model generates "high-speed", "NVMe"
    draft = find_speculative_draft(prompt, ["According", "to", "sources,", "we", "utilize", "high-speed", "NVMe"], ngram_size=2, max_draft=4)
    # Draft should speculate "solid", "state", "drives", "for"
    assert draft == ["solid", "state", "drives", "for"]
