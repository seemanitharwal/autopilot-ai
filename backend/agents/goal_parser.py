"""AutoPilot AI — Goal Parser"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is not set in environment.")
        _client = genai.Client(api_key=api_key)
    return _client


MODEL = "gemini-2.0-flash"

# ── FIX 1: Completely rewritten PROMPT ──
# Old prompt hardcoded books.toscrape.com for everything.
# New prompt teaches Gemini to pick the RIGHT site from the goal.
PROMPT = """You are a task parser for an autonomous web browsing agent.
Convert the user instruction into a structured goal. Return ONLY valid JSON, no markdown fences.

CRITICAL — start_url rules:
- If the user mentions a specific website (amazon, myntra, flipkart, etc.) → use THAT site
- For Amazon India product searches → https://www.amazon.in/s?k=QUERY&s=price-asc-rank
- For Myntra searches → https://www.myntra.com/CATEGORY (e.g. https://www.myntra.com/kurtas)
- For Flipkart searches → https://www.flipkart.com/search?q=QUERY&sort=price_asc
- For books.toscrape.com → https://books.toscrape.com/catalogue/page-1.html
- For general web tasks → https://www.google.com/search?q=QUERY
- Replace QUERY/CATEGORY with URL-encoded keywords from the user instruction
- NEVER default to books.toscrape.com unless the user explicitly says "books.toscrape.com"

Required JSON format:
{
  "goal_summary": "concise 1-sentence goal",
  "start_url": "best URL to start — follow the rules above",
  "success_criteria": "how to know the goal is achieved",
  "max_steps": 15,
  "task_type": "search|navigate|find_cheapest|compare|extract_info",
  "site": "amazon|myntra|flipkart|toscrape|google|other"
}

IMPORTANT:
- Always set max_steps to at least 15 for e-commerce tasks (Amazon/Myntra need many steps)
- For price searches, add sort=price_asc or equivalent in the start_url when possible
- For Myntra kurtas price query: https://www.myntra.com/kurtas"""


def _clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return m.group(0) if m else text


# ── FIX 2: Smart fallback URL builder ──
def _fallback_url(instruction: str) -> str:
    """Pick a sensible start URL when Gemini parse fails."""
    g = instruction.lower()
    if "amazon" in g:
        query = re.sub(r"(amazon|find|cheapest|price|buy|search)", "", g)
        query = re.sub(r"[^a-z0-9 ]", "", query).strip().replace(" ", "+")
        return f"https://www.amazon.in/s?k={query}&s=price-asc-rank"
    if "myntra" in g:
        query = re.sub(r"(myntra|find|price|buy|search|cheapest)", "", g)
        query = re.sub(r"[^a-z0-9 ]", "", query).strip()
        path = query.split()[0] if query else "kurtas"
        return f"https://www.myntra.com/{path}"
    if "flipkart" in g:
        query = re.sub(r"(flipkart|find|cheapest|price|buy|search)", "", g)
        query = re.sub(r"[^a-z0-9 ]", "", query).strip().replace(" ", "+")
        return f"https://www.flipkart.com/search?q={query}&sort=price_asc"
    if "toscrape" in g or ("book" in g and not any(s in g for s in ["amazon", "myntra", "flipkart"])):
        return "https://books.toscrape.com/catalogue/page-1.html"
    query = re.sub(r"[^a-z0-9 ]", "", g).strip().replace(" ", "+")
    return f"https://www.google.com/search?q={query}"


def parse_goal(instruction: str) -> Dict[str, Any]:
    client = _get_client()
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=[f"User instruction: {instruction}"],
                config=types.GenerateContentConfig(
                    system_instruction=PROMPT,
                    temperature=0.1,
                ),
            )
            parsed = json.loads(_clean(resp.text))
            parsed.setdefault("max_steps", 15)
            parsed["max_steps"] = max(3, min(int(parsed["max_steps"]), 20))
            parsed.setdefault("start_url", _fallback_url(instruction))
            parsed.setdefault("goal_summary", instruction)
            parsed.setdefault("task_type", "navigate")
            parsed.setdefault("site", "other")
            logger.info(
                "Goal parsed: type=%s site=%s url=%s",
                parsed["task_type"], parsed["site"], parsed["start_url"],
            )
            return parsed
        except Exception as e:
            logger.warning("Goal parse attempt %d failed: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(0.5)

    logger.error("Goal parsing failed, using fallback defaults")
    return {
        "goal_summary": instruction,
        "start_url": _fallback_url(instruction),
        "success_criteria": "Complete the user request",
        "max_steps": 15,
        "task_type": "navigate",
        "site": "other",
    }