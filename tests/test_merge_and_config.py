from tiny_brain.changes.merge import three_way_merge
from tiny_brain.config import Settings, _confidence


def test_revert_merge_and_confidence_levels():
    assert three_way_merge("a\nb\n", "A\nb\n", "a\nB\n").clean
    assert _confidence("high") == 0.9
    assert _confidence("0.95") == 0.95


def test_model_budget_configuration(monkeypatch):
    monkeypatch.setenv("TINY_BRAIN_CONTEXT_TOKENS", "16384")
    monkeypatch.setenv("TINY_BRAIN_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("TINY_BRAIN_LLAMA_TIMEOUT", "300")
    settings = Settings.from_env()
    assert settings.context_tokens == 16384
    assert settings.max_output_tokens == 4096
    assert settings.llama_timeout == 300
