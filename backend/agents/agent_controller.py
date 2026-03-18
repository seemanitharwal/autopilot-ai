"""AutoPilot AI — Agent Controller"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agents.vision_agent import analyze_screenshot
from agents.goal_parser import parse_goal
from tools.browser_tool import BrowserSession, execute_action

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = Path("screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

STEP_DELAY        = 1.0
MAX_HARD_FAILURES = 3
STALL_THRESHOLD   = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class StepResult:
    step: int
    screenshot_path: str
    decision: Dict[str, Any]
    action_success: bool
    action_message: str
    url: str = ""
    page_title: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step":          self.step,
            "action":        self.decision.get("action"),
            "target_text":   self.decision.get("target_text"),
            "reason":        self.decision.get("reason"),
            "thought":       self.decision.get("reason"),
            "confidence":    self.decision.get("confidence"),
            "goal_progress": self.decision.get("goal_progress"),
            "success":       self.action_success,
            "message":       self.action_message,
            "url":           self.url,
            "page_title":    self.page_title,
            "screenshot":    self.screenshot_path,
            "timestamp":     self.timestamp,
        }


@dataclass
class AgentSession:
    session_id: str
    goal: str
    start_url: str
    status: str = "running"
    steps: List[StepResult] = field(default_factory=list)
    result: Optional[str] = None
    final_url: str = ""
    final_title: str = ""
    error: Optional[str] = None
    started_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id":  self.session_id,
            "goal":        self.goal,
            "status":      self.status,
            "result":      self.result,
            "step_count":  len(self.steps),
            "steps":       [s.to_dict() for s in self.steps],
            "final_url":   self.final_url,
            "final_title": self.final_title,
            "error":       self.error,
            "started_at":  self.started_at,
            "finished_at": self.finished_at,
        }


# ── Stall detector ────────────────────────────────────────────────────────────

class StallDetector:
    def __init__(self, threshold: int = STALL_THRESHOLD) -> None:
        self.threshold = threshold
        self._window: List[tuple] = []

    def record(self, url: str, action: str) -> bool:
        self._window.append((url, action))
        if len(self._window) > self.threshold:
            self._window.pop(0)
        return len(self._window) == self.threshold and len(set(self._window)) == 1

    def reset(self) -> None:
        self._window.clear()


# ── FIX 1: Site-aware recovery URL ───────────────────────────────────────────
# Old version ALWAYS recovered to books.toscrape.com.
# New version checks current_url and goal to pick the right recovery.

def _recovery_url(goal: str, current_url: str) -> str:
    g = goal.lower()

    # Amazon recovery
    if "amazon" in current_url or "amazon" in g:
        query = g.replace("amazon", "").replace("find", "").replace("cheapest", "")
        query = "".join(c for c in query if c.isalnum() or c == " ").strip().replace(" ", "+")
        return f"https://www.amazon.in/s?k={query}&s=price-asc-rank"

    # Myntra recovery
    if "myntra" in current_url or "myntra" in g:
        # Extract product category from goal
        words = [w for w in g.split() if w not in
                 ("myntra", "find", "price", "cheapest", "on", "show", "me", "the", "of", "in")]
        category = words[0] if words else "kurtas"
        return f"https://www.myntra.com/{category}"

    # Flipkart recovery
    if "flipkart" in current_url or "flipkart" in g:
        query = g.replace("flipkart", "").replace("find", "").replace("cheapest", "")
        query = "".join(c for c in query if c.isalnum() or c == " ").strip().replace(" ", "+")
        return f"https://www.flipkart.com/search?q={query}&sort=price_asc"

    # books.toscrape.com recovery
    base = "https://books.toscrape.com"
    if "toscrape" in current_url or ("book" in g and not any(
            s in g for s in ["amazon", "myntra", "flipkart"])):
        if "travel"  in g: return f"{base}/catalogue/category/books/travel_2/index.html"
        if "mystery" in g: return f"{base}/catalogue/category/books/mystery_3/index.html"
        if "sci-fi"  in g or "science fiction" in g:
            return f"{base}/catalogue/category/books/science-fiction_16/index.html"
        return f"{base}/catalogue/page-1.html"

    # Generic Google fallback
    query = "".join(c for c in g if c.isalnum() or c == " ").strip().replace(" ", "+")
    return f"https://www.google.com/search?q={query}"


# ── FIX 2: Direct extraction for supported sites ─────────────────────────────
# Skips vision entirely when we can get data from the DOM directly.
# Much faster and more reliable than vision for structured price data.

def _try_direct_extraction(
    goal: str,
    task_type: str,
    site: str,
    browser: BrowserSession,
    session: AgentSession,
    step_callback: Optional[Callable],
) -> Optional[str]:
    """
    For tasks that can be solved by direct DOM scraping, do it immediately.
    Returns the result string if handled, None if we should fall through to vision loop.
    """
    g = goal.lower()
    is_price_task = any(w in g for w in ["cheapest", "lowest price", "most expensive", "price", "cost"])

    # ── books.toscrape.com ──
    if ("cheapest" in g or "lowest price" in g or "most expensive" in g) and (
        "book" in g and not any(s in g for s in ["amazon", "myntra", "flipkart"])
    ):
        logger.info("Detected toscrape price task — using direct DOM scraping")
        if "cheapest" in g or "lowest" in g:
            book = browser.find_cheapest_book()
            if book:
                return f"The cheapest book is '{book['title']}' at {book['priceText']}"
        elif "most expensive" in g:
            all_books = []
            for page_num in range(1, 51):
                url = f"https://books.toscrape.com/catalogue/page-{page_num}.html"
                if not browser.navigate(url):
                    break
                books = browser.extract_books_data()
                if not books:
                    break
                all_books.extend(books)
                if browser.page.locator("li.next a").count() == 0:
                    break
            if all_books:
                priciest = max(all_books, key=lambda b: b["price"])
                return f"The most expensive book is '{priciest['title']}' at {priciest['priceText']}"

    # ── Myntra — direct DOM extraction ──
    if "myntra" in g and is_price_task:
        logger.info("Detected Myntra price task — using direct DOM scraping")
        products = browser.extract_myntra_products()
        if products:
            if "cheapest" in g or "lowest" in g:
                p = min(products, key=lambda x: x["price"])
                return f"Cheapest on Myntra: '{p['title']}' at {p['priceText']}"
            elif "most expensive" in g:
                p = max(products, key=lambda x: x["price"])
                return f"Most expensive on Myntra: '{p['title']}' at {p['priceText']}"
            else:
                # Return top 5 results
                results = "\n".join(
                    f"- {p['title']}: {p['priceText']}"
                    for p in products[:5] if p["title"]
                )
                return f"Myntra products found:\n{results}"

    # ── Amazon — direct DOM extraction ──
    if "amazon" in g and is_price_task:
        logger.info("Detected Amazon price task — using direct DOM scraping")
        products = browser.extract_amazon_products()
        if products:
            if "cheapest" in g or "lowest" in g:
                p = min(products, key=lambda x: x["price"])
                return f"Cheapest on Amazon: '{p['title']}' at {p['priceText']}"
            elif "most expensive" in g:
                p = max(products, key=lambda x: x["price"])
                return f"Most expensive on Amazon: '{p['title']}' at {p['priceText']}"
            else:
                results = "\n".join(
                    f"- {p['title']}: {p['priceText']}"
                    for p in products[:5] if p["title"]
                )
                return f"Amazon products found:\n{results}"

    return None  # Fall through to vision loop


# ── Controller ────────────────────────────────────────────────────────────────

class AgentController:

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless

    def run(
        self,
        user_instruction: str,
        max_steps: Optional[int] = None,
        headless: Optional[bool] = None,
        session_id: Optional[str] = None,
        step_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> AgentSession:
        sid = session_id or str(uuid.uuid4())[:8]
        logger.info("[%s] Agent run starting: %r", sid, user_instruction[:60])

        try:
            goal_config = parse_goal(user_instruction)
        except Exception as e:
            logger.error("[%s] Goal parse error: %s", sid, e)
            s = AgentSession(session_id=sid, goal=user_instruction, start_url="")
            s.status = "failed"
            s.error  = f"Goal parsing failed: {e}"
            s.finished_at = _now()
            return s

        goal        = goal_config["goal_summary"]
        start_url   = goal_config["start_url"]
        task_type   = goal_config.get("task_type", "navigate")
        site        = goal_config.get("site", "other")
        steps_limit = max_steps if max_steps is not None else goal_config.get("max_steps", 15)
        steps_limit = max(1, min(int(steps_limit), 20))
        use_headless = headless if headless is not None else self.headless

        logger.info(
            "[%s] goal=%r  start=%s  steps=%d  headless=%s  site=%s  task=%s",
            sid, goal, start_url, steps_limit, use_headless, site, task_type,
        )

        session = AgentSession(session_id=sid, goal=goal, start_url=start_url)
        browser = BrowserSession(headless=use_headless)

        history: List[str] = []
        stall = StallDetector()
        hard_failures    = 0
        stall_recoveries = 0

        try:
            if not browser.navigate(start_url):
                raise RuntimeError(f"Could not load start URL: {start_url}")

            # ── Try direct extraction first (fast path) ──
            direct_result = _try_direct_extraction(
                goal, task_type, site, browser, session, step_callback
            )
            if direct_result:
                session.result = direct_result
                session.status = "done"
                session.steps.append(StepResult(
                    step=1,
                    screenshot_path="",
                    decision={
                        "action": "extract",
                        "reason": "Used direct DOM extraction — faster and more reliable than vision",
                        "confidence": 1.0,
                        "goal_progress": "Complete",
                    },
                    action_success=True,
                    action_message=direct_result,
                    url=browser.get_current_url(),
                    page_title=browser.get_page_title(),
                ))
                if step_callback:
                    step_callback(session.to_dict())
                logger.info("[%s] Direct extraction succeeded: %s", sid, direct_result[:100])
                session.final_url   = browser.get_current_url()
                session.final_title = browser.get_page_title()
                session.finished_at = _now()
                browser.close()
                return session

            # ── Standard vision loop ──────────────────────────────────────────
            for step_idx in range(steps_limit):
                logger.info("[%s] ── Step %d / %d ──", sid, step_idx + 1, steps_limit)

                ss_file = SCREENSHOT_DIR / f"{sid}_{step_idx}.png"
                ss_abs  = browser.screenshot(path=str(ss_file))
                ss_web  = f"/static/{ss_file.name}"

                current_url   = browser.get_current_url()
                current_title = browser.get_page_title()
                url_slug = current_url.replace("https://", "").replace("http://", "")[:50]

                decision = analyze_screenshot(
                    image_path=ss_abs or str(ss_file),
                    goal=goal,
                    step=step_idx,
                    history=history,
                    current_url=current_url,
                )

                success, message = execute_action(browser, decision)
                action = (decision.get("action") or "").lower()

                history.append(
                    f"{action}:{decision.get('target_text', decision.get('scroll_direction', ''))}"
                    f"@{url_slug}"
                )

                step_result = StepResult(
                    step=step_idx + 1,
                    screenshot_path=ss_web,
                    decision=decision,
                    action_success=success,
                    action_message=message,
                    url=current_url,
                    page_title=current_title,
                )
                session.steps.append(step_result)

                if step_callback:
                    try:
                        step_callback(session.to_dict())
                    except Exception as cb_e:
                        logger.warning("[%s] step_callback error: %s", sid, cb_e)

                if action == "done":
                    result = (
                        decision.get("result")
                        or decision.get("goal_progress")
                        or browser.get_page_text(500)
                    )
                    session.result = result
                    session.status = "done"
                    logger.info("[%s] Done after %d steps. Result: %s", sid, step_idx + 1, result)
                    break

                # ── FIX 3: After vision decides "done" try DOM extraction as confirmation ──
                # If Gemini thinks it found data, also try direct extraction to verify
                if action == "done" and not session.result:
                    dom_result = _try_direct_extraction(
                        goal, task_type, site, browser, session, None
                    )
                    if dom_result:
                        session.result = dom_result

                if stall.record(current_url, action):
                    stall_recoveries += 1
                    logger.warning("[%s] Stall #%d: %r on %s", sid, stall_recoveries, action, url_slug)
                    if stall_recoveries > 2:
                        session.status = "failed"
                        session.error  = f"Agent stuck: '{action}' repeated on same page."
                        break
                    recovery = _recovery_url(goal, current_url)
                    logger.info("[%s] Stall recovery → %s", sid, recovery)
                    browser.navigate(recovery)
                    stall.reset()
                    hard_failures = 0
                    history.append(f"[RECOVERY: navigated to {recovery}]")
                    continue

                if not success and action not in ("scroll", "report", "dismiss_popup"):
                    hard_failures += 1
                    logger.warning("[%s] Hard failure %d/%d: %s", sid, hard_failures, MAX_HARD_FAILURES, message)
                    if hard_failures >= MAX_HARD_FAILURES:
                        session.status = "failed"
                        session.error  = f"Too many consecutive failures. Last: {message}"
                        break
                else:
                    hard_failures = 0

                time.sleep(STEP_DELAY)

            else:
                session.status = "stopped"
                session.error  = f"Reached max {steps_limit} steps without completing the goal."
                logger.info("[%s] Stopped at max steps", sid)

            session.final_url   = browser.get_current_url()
            session.final_title = browser.get_page_title()

        except Exception as e:
            logger.exception("[%s] Unexpected error: %s", sid, e)
            session.status = "failed"
            session.error  = str(e)
        finally:
            browser.close()
            session.finished_at = _now()

        if step_callback:
            try:
                step_callback(session.to_dict())
            except Exception:
                pass

        logger.info("[%s] Finished: status=%s  steps=%d", sid, session.status, len(session.steps))
        return session