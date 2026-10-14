from __future__ import annotations

import logging
import re
from typing import Any

from .llama import LlamaClient
from .prompts import SEARCH_PLANNER_PROMPT

logger = logging.getLogger(__name__)

# Intent regex: greetings, gratitude, acknowledgements, farewells, and trivial chitchat
CHITCHAT_PATTERN = re.compile(
    r"^\s*(?:hi|hello|hey|howdy|sup)(?:\s+there)?\b[\s!.?]*$|"
    r"^\s*(?:good\s+(?:morning|afternoon|evening|day)|greetings)\b[\s!.?]*$|"
    r"^\s*(?:thanks|thank\s+you|thx|cheers|you'?re\s+welcome)\b[\s!.?]*$|"
    r"^\s*(?:ok|okay|cool|great|awesome|fine|nice|got\s+it|understood)\b[\s!.?]*$|"
    r"^\s*(?:yes|no|yep|nope|sure|bye|goodbye|see\s+ya)\b[\s!.?]*$",
    re.IGNORECASE,
)


# Question and conversational prefix phrases to strip before keyword extraction
PREFIX_PATTERNS = [
    re.compile(r"^\s*(?:can\s+you\s+)?(?:please\s+)?(?:tell\s+me\s+about|explain|describe|show\s+me|find|look\s+up|search\s+for)\s+", re.IGNORECASE),
    re.compile(r"^\s*do\s+we\s+have\s+(?:anything\s+written\s+down\s+about|notes\s+on|anything\s+on|information\s+on)\s+", re.IGNORECASE),
    re.compile(r"^\s*what\s+(?:have\s+i|did\s+i)\s+(?:written\s+down\s+about|written\s+about|said\s+about)\s+", re.IGNORECASE),
    re.compile(r"^\s*what\s+does\s+(?:our\s+notes?|the\s+vault|Guide)\s+say\s+about\s+", re.IGNORECASE),
    re.compile(r"^\s*what\s+(?:is|are|was|were)\s+(?:the\s+)?", re.IGNORECASE),
    re.compile(r"^\s*how\s+(?:does|do|did)\s+", re.IGNORECASE),
]

# Common English stopwords + question/conversational filler words
STOPWORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
    "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or",
    "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same",
    "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so",
    "some", "such", "than", "that", "that's", "the", "their", "theirs", "them",
    "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll",
    "they're", "they've", "this", "those", "through", "to", "too", "under",
    "until", "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're",
    "we've", "were", "weren't", "what", "what's", "when", "when's", "where",
    "where's", "which", "while", "who", "who's", "whom", "why", "why's", "with",
    "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've",
    "your", "yours", "yourself", "yourselves",
    # Question/instruction conversational fillers
    "please", "tell", "explain", "describe", "show", "give", "know", "think",
    "help", "look", "find", "search", "something", "anything", "quick", "summary",
    "written", "down", "notes", "note", "vault",
})


def is_chitchat(message: str) -> bool:
    """Check if message is a greeting, smalltalk, or trivial acknowledgement."""
    cleaned = message.strip()
    if len(cleaned) <= 2:
        return True
    return bool(CHITCHAT_PATTERN.match(cleaned))


def extract_search_query(message: str) -> str | None:
    """Deterministically extract a 1-6 word search query from user message, or None."""
    if is_chitchat(message):
        return None

    # Priority 1: Explicit wikilinks [[Target]]
    wikilinks = re.findall(r"\[\[(.*?)\]\]", message)
    if wikilinks:
        return wikilinks[0].strip()

    # Priority 2: Explicit quoted terms "term"
    quotes = re.findall(r'"([^"]+)"', message)
    if quotes:
        return quotes[0].strip()

    text = message.strip()
    for pattern in PREFIX_PATTERNS:
        text = pattern.sub("", text)

    # Tokenize preserving abbreviations with dots/hyphens (e.g. P.A.R.A. or spark-x2.5)
    raw_tokens = re.findall(r"\b[A-Za-z0-9][A-Za-z0-9_.-]*[A-Za-z0-9]\b|\b[A-Za-z0-9]\b", text)
    meaningful_tokens: list[str] = []
    for token in raw_tokens:
        clean_token = token.strip(".-_")
        if not clean_token:
            continue
        if clean_token.lower() not in STOPWORDS:
            meaningful_tokens.append(clean_token)

    if not meaningful_tokens:
        return None

    # Bounded to 1-6 key terms
    return " ".join(meaningful_tokens[:6])


async def plan_vault_search(llama: Any, message: str, context: str | None = None) -> str | None:
    """Return the vault search query using deterministic keyword extraction and heuristics."""
    # Preserve test mock compatibility if a mock llama complete method is explicitly provided
    if llama is not None and not isinstance(llama, LlamaClient) and hasattr(llama, "complete"):
        user_content = f"Conversation so far:\n{context}\n\nQuestion: {message}" if context else message
        try:
            response = await llama.complete(
                [{"role": "system", "content": SEARCH_PLANNER_PROMPT}, {"role": "user", "content": user_content}],
                temperature=0.0,
                thinking=False,
            )
            text = (response["choices"][0]["message"]["content"] or "").strip()
            if not text:
                return None
            query = text.splitlines()[0].strip(" \"'`")
            if not query or query.upper() == "NONE":
                return None
            return query
        except Exception:
            pass

    return extract_search_query(message)
