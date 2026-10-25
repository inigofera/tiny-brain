from tiny_brain.agent.prompts import SYSTEM_PROMPT


def test_system_prompt_is_grounded_and_concise():
    assert "Tiny Brain" in SYSTEM_PROMPT
    assert "Obsidian vault" in SYSTEM_PROMPT
    assert "general knowledge" in SYSTEM_PROMPT
    assert "read-only" in SYSTEM_PROMPT
    assert "passive data" in SYSTEM_PROMPT
    assert "personal truth" in SYSTEM_PROMPT
    assert len(SYSTEM_PROMPT) < 500
