"""Catch questions that need current information, so they are never answered from memory."""

from __future__ import annotations

import re

_FRESH = re.compile(
    r"\b(today|tonight|yesterday|tomorrow|this (week|month|year)|right now|nowadays"
    r"|latest|recent(ly)?|current(ly)?|news|headlines?|happened|happening|breaking"
    r"|updates?|prices?|cost of|exchange rates?|interest rates?|stocks?|share price|crypto"
    r"|bitcoin|weather|temperature|forecast|scores?|standings|elections?|released?"
    r"|launch(ed|es)?|version)\b"
    r"|\bconvert\b.*\b(dollars?|rupees?|euros?|pounds?|yen|usd|inr|eur|gbp|currency)\b"
    r"|\b20[2-9]\d\b",
    re.IGNORECASE,
)

LOOKUP_NOTE = (
    "Your previous plan answered from memory, but this question depends on current or changing "
    "information. Plan a step that looks it up (for example on the web) and answer only from "
    "what you find."
)


def needs_fresh_data(message: str) -> bool:
    return bool(_FRESH.search(message))
