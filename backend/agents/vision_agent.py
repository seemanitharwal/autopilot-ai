"""AutoPilot AI — Vision Agent"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_client: Optional[genai.Client] = None

MODEL = "gemini-2.0-flash"


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is not set in environment.")
        _client = genai.Client(api_key=api_key)
    return _client


# ── FIX 1: Rewritten SYSTEM_PROMPT ──
# Old prompt was books.toscrape.com specific and had no login/popup handling.
# New prompt is site-agnostic and handles login walls explicitly.
SYSTEM_PROMPT = """You are AutoPilot AI — an autonomous web browsing agent.

You receive a screenshot of a webpage and must decide the SINGLE BEST next action.

POPUP / LOGIN RULES (check FIRST before anything else):
1. If you see a login modal, sign-in dialog, or popup overlay → return action "dismiss_popup"
2. If you see "Continue as Guest", "No Thanks", "Skip", "×" close buttons → action "click" on that button
3. If you see a CAPTCHA → action "report" with reason "captcha encountered"
4. If you see "Continue Shopping" or similar dismissal → click it

GENERAL RULES:
5. If the answer is ALREADY VISIBLE on screen → return action "done" with the exact text
6. If scrolled 2+ times on the SAME URL without progress → use "navigate" to a better page
7. Never scroll more than 3 times on the same page
8. For e-commerce (Amazon/Myntra/Flipkart): look for product cards with prices
9. If on a search results page with prices visible → return action "done" listing the products found
10. Only reference elements ACTUALLY VISIBLE in the screenshot

SITE-SPECIFIC HINTS:
- Amazon: products shown as cards with ₹ prices, "Add to Cart" buttons
- Myntra: product grid with discounted/original prices, brand names above product names
- Flipkart: product tiles with ₹ prices and ratings
- books.toscrape.com: book covers with £ prices in green

Return ONLY valid JSON. No markdown. No explanation.

{
  "action": "click|type|scroll|navigate|done|report|dismiss_popup",
  "target_text": "exact visible text to click or type into",
  "input_value": "text to type (type action only)",
  "scroll_direction": "up|down",
  "url": "full https:// URL (navigate action only)",
  "result": "the extracted answer (done action only)",
  "reason": "one sentence why this action moves toward the goal",
  "confidence": 0.85,
  "goal_progress": "brief honest assessment of current progress"
}"""


# ── FIX 2: Smarter fallback — site-aware recovery ──
def _smart_fallback(goal: str, current_url: str, history: List[str]) -> Dict[str, Any]:
    """When vision fails, pick a recovery action based on current site."""
    scroll_count = sum(1 for h in history[-5:] if "scroll" in h)
    g = goal.lower()

    if scroll_count >= 2:
        # Too many scrolls — try something different
        if "myntra" in current_url:
            return {
                "action": "navigate",
                "url": "https://www.myntra.com/kurtas",
                "reason": "Vision failed with too many scrolls — navigating to Myntra kurtas",
                "confidence": 0.3,
                "goal_progress": "Recovery: re-navigating",
            }
        if "amazon" in current_url:
            query = re.sub(r"(amazon|find|cheapest|price|buy)", "", g)
            query = re.sub(r"[^a-z0-9 ]", "", query).strip().replace(" ", "+")
            return {
                "action": "navigate",
                "url": f"https://www.amazon.in/s?k={query}&s=price-asc-rank",
                "reason": "Vision failed — retrying Amazon search with price sort",
                "confidence": 0.3,
                "goal_progress": "Recovery: re-searching Amazon",
            }
        if "toscrape" in current_url or "book" in g:
            return {
                "action": "navigate",
                "url": "https://books.toscrape.com/catalogue/page-1.html",
                "reason": "Vision failed — navigating to catalogue directly",
                "confidence": 0.3,
                "goal_progress": "Recovery: navigating to catalogue",
            }

    return {
        "action": "scroll",
        "scroll_direction": "down",
        "reason": "Vision analysis failed — scrolling as fallback",
        "confidence": 0.1,
        "goal_progress": "unknown",
    }


def _to_bytes(path: Path) -> bytes:
    with Image.open(path) as img:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if img.width > 1280:
            h = int(img.height * 1280 / img.width)
            img = img.resize((1280, h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def _clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return m.group(0) if m else text


# ── FIX 3: Site-aware context injected into every prompt ──
def _site_context(current_url: str) -> str:
    """Add site-specific hints to help Gemini understand what it's looking at."""
    if "myntra" in current_url:
        return "\nSITE: Myntra — look for product-base cards with brand + product name and discounted price in pink/red."
    if "amazon" in current_url:
        return "\nSITE: Amazon India — look for s-result-item cards with ₹ price and product title in blue links."
    if "flipkart" in current_url:
        return "\nSITE: Flipkart — look for product tiles with ₹ price, rating stars, and flipkart blue header."
    if "toscrape" in current_url:
        return "\nSITE: books.toscrape.com — look for book cover images with £ price in green below each book."
    return ""


def analyze_screenshot(
    image_path: str,
    goal: str = "",
    step: int = 0,
    history: Optional[List[str]] = None,
    current_url: str = "",
) -> Dict[str, Any]:
    path = Path(image_path)
    if not path.exists():
        logger.error("Screenshot not found: %s", path)
        return _smart_fallback(goal, current_url, history or [])

    stall_warn = ""
    if history and current_url:
        slug = current_url.replace("https://", "").replace("http://", "")[:50]
        same_scrolls = sum(1 for h in history[-4:] if "scroll" in h and slug[:20] in h)
        if same_scrolls >= 2:
            stall_warn = (
                f"\n\nWARNING: You have scrolled {same_scrolls} times on {slug} with no progress. "
                "You MUST navigate to a different page or click a link — do NOT scroll again."
            )

    history_str = ""
    if history:
        history_str = "\n\nRecent actions:\n" + "\n".join(f"  {h}" for h in history[-5:])

    # ── FIX 4: Add site-specific context to every Gemini call ──
    site_ctx = _site_context(current_url)

    prompt = (
        f"Goal: {goal}\n"
        f"Current URL: {current_url or 'unknown'}\n"
        f"Step: {step + 1}"
        f"{site_ctx}"
        f"{history_str}"
        f"{stall_warn}\n\n"
        "Analyze the screenshot. Return the best next action as JSON."
    )

    img_bytes = _to_bytes(path)
    client = _get_client()

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=[
                    prompt,
                    types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                ],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.1,
                ),
            )
            parsed = json.loads(_clean(resp.text))
            if "action" not in parsed:
                raise ValueError("Response missing 'action' field")
            logger.info(
                "Step %d decision: action=%s target=%r confidence=%s",
                step + 1, parsed.get("action"), parsed.get("target_text"), parsed.get("confidence"),
            )
            return parsed
        except Exception as e:
            logger.warning("Vision attempt %d/3 failed: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))

    logger.error("Vision agent failed all retries, using smart fallback")
    return _smart_fallback(goal, current_url, history or [])