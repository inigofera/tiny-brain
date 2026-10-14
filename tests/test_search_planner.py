import pytest

from tiny_brain.agent.search_planner import extract_search_query, is_chitchat, plan_vault_search


def test_is_chitchat_detects_greetings_and_acknowledgements():
    assert is_chitchat("hi")
    assert is_chitchat("Hello!")
    assert is_chitchat("Hey there")
    assert is_chitchat("good morning")
    assert is_chitchat("thanks")
    assert is_chitchat("thank you!")
    assert is_chitchat("thx")
    assert is_chitchat("ok")
    assert is_chitchat("Okay")
    assert is_chitchat("cool")
    assert is_chitchat("yes")
    assert is_chitchat("no")
    assert is_chitchat("bye")
    assert is_chitchat("?")
    assert not is_chitchat("What is Tiny Brain?")
    assert not is_chitchat("Tell me about distributed caching")


def test_extract_search_query_skips_chitchat():
    assert extract_search_query("hello") is None
    assert extract_search_query("thanks!") is None
    assert extract_search_query("ok") is None


def test_extract_search_query_extracts_wikilinks_and_quotes():
    assert extract_search_query("tell me what is in [[Knowledge Management]]") == "Knowledge Management"
    assert extract_search_query('can you search for "distributed caching" please') == "distributed caching"


def test_extract_search_query_strips_stopwords_and_prefixes():
    assert extract_search_query("What is Tiny Brain and how does it work?") == "Tiny Brain work"
    assert extract_search_query("do we have anything written down about Essentialism? if so give me a quick summary") == "Essentialism"
    assert extract_search_query("Tell me about the P.A.R.A. framework") == "P.A.R.A framework"
    assert extract_search_query("what does our notes say about Stoicism") == "Stoicism"


@pytest.mark.asyncio
async def test_plan_vault_search_deterministic():
    query = await plan_vault_search(None, "What is Tiny Brain and how does it work?")
    assert query == "Tiny Brain work"

    query_chitchat = await plan_vault_search(None, "hello there!")
    assert query_chitchat is None
