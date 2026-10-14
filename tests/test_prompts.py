from tiny_brain.agent.prompts import SYSTEM_PROMPT


def test_system_prompt_is_minimal():
    assert SYSTEM_PROMPT == "You are Tiny Brain, a local assistant for the user's Obsidian vault."