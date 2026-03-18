"""AutoPilot AI — Browser Tool"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from playwright.sync_api import sync_playwright, Page, Browser

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = Path("screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)


class BrowserSession:

    def __init__(self, headless: bool = True) -> None:
        self._playwright = sync_playwright().start()

        if not headless:
            try:
                self.browser: Browser = self._playwright.chromium.launch(
                    channel="chrome",
                    headless=False,
                    args=["--no-sandbox", "--start-maximized", "--disable-infobars"],
                )
                logger.info("Launched system Chrome (visible)")
            except Exception as e:
                logger.warning("System Chrome unavailable (%s), using Chromium", e)
                self.browser = self._playwright.chromium.launch(
                    headless=False,
                    args=["--no-sandbox", "--start-maximized"],
                )
                logger.info("Launched bundled Chromium (visible)")
        else:
            self.browser = self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            logger.info("Launched headless Chromium")

        ctx_kwargs: dict = {
            # ── FIX 1: Realistic user agent — helps avoid bot detection on Myntra/Amazon ──
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            # ── FIX 2: Extra headers that real browsers send ──
            "extra_http_headers": {
                "Accept-Language": "en-IN,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        }
        if headless:
            ctx_kwargs["viewport"] = {"width": 1280, "height": 800}
        else:
            ctx_kwargs["no_viewport"] = True

        self.context = self.browser.new_context(**ctx_kwargs)
        self.page: Page = self.context.new_page()
        self.history = []
        logger.info("BrowserSession ready (headless=%s)", headless)

    # ── Navigation ────────────────────────────────────────────────────────────

    def navigate(self, url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
            # ── FIX 3: Wait longer for JS-heavy sites like Myntra/Amazon ──
            self.page.wait_for_timeout(2500)
            # Try to dismiss any login popup that appeared after load
            self.handle_login_popups()
            self.history.append(f"navigate:{url}")
            logger.info("Navigated to %s", url)
            return True
        except Exception as e:
            logger.error("navigate(%s) failed: %s", url, e)
            return False

    # ── FIX 4: NEW — Login/popup handler ─────────────────────────────────────
    # This is the key fix for Amazon and Myntra login walls.
    # Call this after every navigation and Gemini can focus on the actual content.

    def handle_login_popups(self) -> bool:
        """
        Dismiss login modals, sign-in prompts, cookie banners, and overlays.
        Works on Amazon, Myntra, Flipkart, and most e-commerce sites.
        Returns True if a popup was dismissed.
        """
        dismissed = False

        # --- Amazon specific ---
        amazon_selectors = [
            # "Continue shopping" / close the sign-in nag
            '[data-action="a-popover-close"]',
            "span.a-button-close",
            "#auth-modal-close-link",
            ".a-popover-closebutton",
            # "No thanks" on email capture
            "#p13n-subs-surface-2 .a-button-close",
        ]

        # --- Myntra specific ---
        myntra_selectors = [
            # Login modal close
            ".myntraweb-sprite.pdp-close",
            ".modal-close-btn",
            '[class*="Modal"] [class*="close"]',
            '[class*="modal"] [class*="close"]',
            # Cookie/notification banner
            '[class*="CookieConsent"] button',
        ]

        # --- Flipkart specific ---
        flipkart_selectors = [
            # Login popup close
            "._2KpZ6l._2doB4z",
            "button._2KpZ6l",
        ]

        # --- Generic patterns (work on most sites) ---
        generic_selectors = [
            'button[aria-label*="close" i]',
            'button[aria-label*="dismiss" i]',
            'button[aria-label*="skip" i]',
            '[class*="popup"] [class*="close"]',
            '[class*="overlay"] [class*="close"]',
            '[class*="dialog"] [class*="close"]',
            '[id*="popup"] [class*="close"]',
            # "Continue as guest" / "No thanks" text buttons
            'button:has-text("Continue as Guest")',
            'button:has-text("No thanks")',
            'button:has-text("No, Thanks")',
            'button:has-text("Skip")',
            'button:has-text("Maybe Later")',
            'button:has-text("×")',
            'a:has-text("Continue as Guest")',
        ]

        all_selectors = (
            amazon_selectors + myntra_selectors + flipkart_selectors + generic_selectors
        )

        for selector in all_selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=500):
                    btn.click(timeout=2000)
                    self.page.wait_for_timeout(800)
                    logger.info("Dismissed popup via: %s", selector)
                    dismissed = True
                    break  # One dismissal per call — call again if needed
            except Exception:
                continue

        return dismissed

    # ── Screenshot ────────────────────────────────────────────────────────────

    def screenshot(self, path: Optional[str] = None, step: int = 0) -> str:
        try:
            fp = Path(path) if path else SCREENSHOT_DIR / f"step_{step}.png"
            fp.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(fp), full_page=False)
            return str(fp)
        except Exception as e:
            logger.error("screenshot failed: %s", e)
            return ""

    # ── Click ─────────────────────────────────────────────────────────────────

    def click(self, text: str) -> bool:
        if not text:
            return False
        strategies = [
            self.page.get_by_role("link",   name=text, exact=False),
            self.page.get_by_role("button", name=text, exact=False),
            self.page.get_by_text(text, exact=False),
            self.page.locator(f"[title*='{text}']"),
        ]
        for loc in strategies:
            try:
                if loc.count() > 0:
                    loc.first.scroll_into_view_if_needed()
                    loc.first.click(timeout=5000)
                    # ── FIX 5: After every click, wait for JS and dismiss popups ──
                    self.page.wait_for_timeout(2000)
                    self.handle_login_popups()
                    self.history.append(f"click:{text}")
                    logger.info("Clicked: %r", text)
                    return True
            except Exception:
                continue
        logger.warning("click(%r): element not found", text)
        return False

    # ── Type ──────────────────────────────────────────────────────────────────

    def type_text(self, target: str, value: str) -> bool:
        if not value:
            return False
        try:
            field = self.page.locator(f"input[placeholder*='{target}']")
            if field.count() == 0:
                field = self.page.locator(f"input[aria-label*='{target}']")
            if field.count() == 0:
                field = self.page.locator("input[type='text'], input:not([type])")
            if field.count() == 0:
                logger.warning("type_text: no input found for target=%r", target)
                return False
            field.first.fill(value)
            field.first.press("Enter")
            self.page.wait_for_timeout(2000)
            self.handle_login_popups()
            self.history.append(f"type:{value}")
            logger.info("Typed %r into %r", value, target)
            return True
        except Exception as e:
            logger.error("type_text failed: %s", e)
            return False

    # ── Scroll ────────────────────────────────────────────────────────────────

    def scroll(self, direction: str = "down", amount: int = 600) -> bool:
        try:
            delta = amount if direction == "down" else -amount
            self.page.evaluate(f"window.scrollBy(0, {delta})")
            self.page.wait_for_timeout(700)
            self.history.append(f"scroll:{direction}")
            return True
        except Exception as e:
            logger.error("scroll(%s) failed: %s", direction, e)
            return False

    # ── FIX 6: NEW — Extract Myntra product data directly from DOM ────────────

    def extract_myntra_products(self) -> List[Dict[str, Any]]:
        """
        Directly scrape product name + price from Myntra search/category pages.
        Much more reliable than vision for structured price data.
        """
        try:
            products = self.page.evaluate("""() => {
                const items = document.querySelectorAll('.product-base, [class*="product-base"]');
                return Array.from(items).slice(0, 20).map(item => {
                    const brand = item.querySelector('.product-brand')?.textContent?.trim() || '';
                    const name  = item.querySelector('.product-product')?.textContent?.trim() || '';
                    const title = brand + (name ? ' ' + name : '');
                    const disc  = item.querySelector('.product-discountedPrice')?.textContent?.trim() || '';
                    const orig  = item.querySelector('.product-strike')?.textContent?.trim() || '';
                    const price = parseFloat((disc || orig).replace(/[^0-9.]/g, '')) || 0;
                    const rating = item.querySelector('.product-ratingsCount')?.textContent?.trim() || '';
                    return { title, priceText: disc || orig, price, originalPrice: orig, rating };
                }).filter(p => p.title || p.price);
            }""")
            logger.info("Extracted %d Myntra products from DOM", len(products))
            return products
        except Exception as e:
            logger.error("extract_myntra_products failed: %s", e)
            return []

    # ── FIX 7: NEW — Extract Amazon product data directly from DOM ───────────

    def extract_amazon_products(self) -> List[Dict[str, Any]]:
        """
        Directly scrape product name + price from Amazon search results.
        Works without vision — reads the DOM directly.
        """
        try:
            products = self.page.evaluate("""() => {
                const items = document.querySelectorAll(
                    '[data-component-type="s-search-result"], .s-result-item[data-asin]'
                );
                return Array.from(items).slice(0, 20).map(item => {
                    const title = item.querySelector('h2 span, h2 a span')?.textContent?.trim() || '';
                    const whole = item.querySelector('.a-price-whole')?.textContent?.trim() || '';
                    const frac  = item.querySelector('.a-price-fraction')?.textContent?.trim() || '';
                    const priceText = whole ? `₹${whole}${frac ? '.' + frac : ''}` : '';
                    const price = parseFloat((whole + (frac ? '.' + frac : '')).replace(/[^0-9.]/g, '')) || 0;
                    const rating = item.querySelector('.a-icon-star-small span, .a-icon-alt')?.textContent?.trim() || '';
                    const asin  = item.getAttribute('data-asin') || '';
                    return { title, priceText, price, rating, asin };
                }).filter(p => p.title && p.price > 0);
            }""")
            logger.info("Extracted %d Amazon products from DOM", len(products))
            return products
        except Exception as e:
            logger.error("extract_amazon_products failed: %s", e)
            return []

    # ── Existing DOM extractor for books.toscrape.com ────────────────────────

    def extract_books_data(self) -> List[Dict[str, Any]]:
        """Directly scrape book title + price from books.toscrape.com."""
        try:
            books = self.page.evaluate("""() => {
                const articles = document.querySelectorAll('article.product_pod');
                return Array.from(articles).map(a => {
                    const title = a.querySelector('h3 a')?.getAttribute('title') ||
                                  a.querySelector('h3 a')?.textContent?.trim() || '';
                    const priceText = a.querySelector('.price_color')?.textContent?.trim() || '';
                    const price = parseFloat(priceText.replace(/[^0-9.]/g, '')) || 999;
                    const ratingClass = a.querySelector('.star-rating')?.className || '';
                    const ratingMap = {One:1, Two:2, Three:3, Four:4, Five:5};
                    const ratingWord = ratingClass.split(' ')[1] || '';
                    const rating = ratingMap[ratingWord] || 0;
                    const href = a.querySelector('h3 a')?.getAttribute('href') || '';
                    return { title, price, priceText, rating, href };
                });
            }""")
            logger.info("Extracted %d books from DOM", len(books))
            return books
        except Exception as e:
            logger.error("extract_books_data failed: %s", e)
            return []

    def find_cheapest_book(self) -> Optional[Dict[str, Any]]:
        """Navigate all pages of books.toscrape.com and find the cheapest book."""
        all_books = []
        base = "https://books.toscrape.com/catalogue"
        page_num = 1

        while page_num <= 50:
            url = f"{base}/page-{page_num}.html"
            if not self.navigate(url):
                break
            books = self.extract_books_data()
            if not books:
                break
            all_books.extend(books)
            logger.info("Page %d: got %d books, total=%d", page_num, len(books), len(all_books))
            has_next = self.page.locator("li.next a").count() > 0
            if not has_next:
                break
            page_num += 1

        if not all_books:
            return None
        cheapest = min(all_books, key=lambda b: b["price"])
        logger.info("Cheapest book: %s at %s", cheapest["title"], cheapest["priceText"])
        return cheapest

    # ── Info ──────────────────────────────────────────────────────────────────

    def get_current_url(self) -> str:
        try:
            return self.page.url
        except Exception:
            return ""

    def get_page_title(self) -> str:
        try:
            return self.page.title()
        except Exception:
            return ""

    def get_page_text(self, max_chars: int = 2000) -> str:
        try:
            return self.page.inner_text("body")[:max_chars]
        except Exception:
            return ""

    # ── Close ─────────────────────────────────────────────────────────────────

    def close(self) -> None:
        try:
            self.context.close()
            self.browser.close()
            self._playwright.stop()
            logger.info("BrowserSession closed")
        except Exception:
            pass


# ── Action executor ───────────────────────────────────────────────────────────

def execute_action(session: BrowserSession, decision: dict) -> Tuple[bool, str]:
    action = (decision.get("action") or "").lower().strip()

    if action == "click":
        t = decision.get("target_text", "")
        ok = session.click(t)
        return ok, f"click → {t!r}"

    if action == "type":
        t = decision.get("target_text", "")
        v = decision.get("input_value", "")
        ok = session.type_text(t, v)
        return ok, f"type {v!r}"

    if action == "scroll":
        d = decision.get("scroll_direction", "down")
        ok = session.scroll(d)
        return ok, f"scroll {d}"

    if action == "navigate":
        url = decision.get("url", "")
        ok = session.navigate(url)
        return ok, f"navigate → {url}"

    if action == "done":
        return True, decision.get("result", "done")

    if action == "report":
        return False, decision.get("reason", "agent reported stuck")

    if action == "dismiss_popup":
        # ── FIX 8: Gemini can now explicitly request popup dismissal ──
        ok = session.handle_login_popups()
        return ok, "popup dismissed" if ok else "no popup found"

    if action == "extract":
        what = decision.get("extract_type", "books")
        if what == "cheapest_book":
            result = session.find_cheapest_book()
            if result:
                return True, f"Cheapest: '{result['title']}' at {result['priceText']}"
            return False, "No books found"
        if what == "myntra_products":
            products = session.extract_myntra_products()
            if products:
                cheapest = min(products, key=lambda p: p["price"])
                return True, f"Cheapest on Myntra: '{cheapest['title']}' at {cheapest['priceText']}"
            return False, "No Myntra products found"
        if what == "amazon_products":
            products = session.extract_amazon_products()
            if products:
                cheapest = min(products, key=lambda p: p["price"])
                return True, f"Cheapest on Amazon: '{cheapest['title']}' at {cheapest['priceText']}"
            return False, "No Amazon products found"
        return False, f"unknown extract type: {what}"

    logger.warning("Unknown action: %r", action)
    return False, f"unknown action: {action!r}"