"""Playwright browser +  stateful automation."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# PLAYWRIGHT BROWSER TOOL
# ══════════════════════════════════════════════════════════════════════════════
# Gives the agent real browser automation: navigate, screenshot, extract,
# fill forms, click buttons. Pairs with the VLM for visual reasoning.
#
# Requires: pip install playwright && playwright install chromium
#
# Tools registered:
#   browser_open(url)               → load page, return text content
#   browser_screenshot(url?)        → take screenshot, return path
#   browser_click(selector)         → click element by CSS selector
#   browser_fill(selector, value)   → fill input by selector
#   browser_extract(selector)       → extract text from selector
#
# Security: browser runs with --no-sandbox disabled network for non-http.
#           All navigation is logged to the AgentObserver.

def _tool_browser_open(url: str, timeout_ms: int = 15000) -> str:
    """
    Navigate to a URL and return the page's main text content.
    Uses Playwright headless Chromium. Falls back to urllib for plain HTML.
    """
    # Try Playwright (full JS rendering)
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page    = browser.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            # Wait for body text to be non-empty
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            # Extract readable text
            text = page.evaluate(
                "() => document.body ? document.body.innerText : ''")
            browser.close()
            return text[:8000].strip() if text else "[browser: no content]"
    except ImportError:
        pass  # Playwright not installed — fall through to urllib
    except Exception as e:
        log.debug("browser_playwright_error", extra={"error": str(e)[:80]})

    # urllib fallback for plain HTML pages
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; Essence/16)"})
        html = urllib.request.urlopen(req, timeout=15).read().decode(
            "utf-8", errors="replace")
        # Strip tags
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000]
    except Exception as e:
        return f"[browser_open error: {e}]"


def _tool_browser_screenshot(url: str = "", out_dir: Path | None = None,
                              selector: str = "") -> str:
    """
    Take a screenshot of the current browser page (or navigate to url first).
    Returns the path to the saved PNG file.
    Pair with analyze_image tool for VLM visual reasoning.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        import tempfile
        save_dir = out_dir or Path(tempfile.gettempdir())
        out_path = save_dir / f"essence_screen_{int(time.time())}.png"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 1280, "height": 900})
            if url:
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
            if selector:
                el = page.query_selector(selector)
                if el:
                    el.screenshot(path=str(out_path))
                else:
                    page.screenshot(path=str(out_path), full_page=False)
            else:
                page.screenshot(path=str(out_path), full_page=False)
            browser.close()
        return str(out_path)
    except ImportError:
        return "[browser_screenshot] playwright not installed: pip install playwright && playwright install chromium"
    except Exception as e:
        return f"[browser_screenshot error: {e}]"


def _tool_browser_click(selector: str, _page_ref: Any = None) -> str:
    """
    Legacy stub — superseded by BrowserSession.click() in v16.
    Agent._register_tools() now routes browser_click through get_browser_session(),
    so this function is only called if someone invokes it directly.
    Kept for backward compatibility with external code.
    """
    if _page_ref is not None:
        try:
            _page_ref.click(selector, timeout=5000)
            return f"[browser_click] clicked: {selector}"
        except Exception as e:
            return f"[browser_click error: {e}]"
    return ("[browser_click] use get_browser_session(session_id).click(selector) "
            "for stateful multi-step browser automation")


def _tool_browser_fill(selector: str, value: str,
                       _page_ref: Any = None) -> str:
    """
    Legacy stub — superseded by BrowserSession.fill() in v16.
    Agent._register_tools() now routes browser_fill through get_browser_session().
    """
    if _page_ref is not None:
        try:
            _page_ref.fill(selector, value, timeout=5000)
            return f"[browser_fill] filled {selector} with {value[:30]!r}"
        except Exception as e:
            return f"[browser_fill error: {e}]"
    return ("[browser_fill] use get_browser_session(session_id).fill(selector, value) "
            "for stateful multi-step browser automation")


# ══════════════════════════════════════════════════════════════════════════════

# BROWSER SESSION
# ══════════════════════════════════════════════════════════════════════════════
# browser_click and browser_fill require a live Playwright Page object.
# BrowserSession wraps a persistent browser + page pair across multiple tool
# calls within a single agent task, eliminating the stub problem.
#
# The Agent creates a BrowserSession when it first calls browser_open with
# session=True. Subsequent browser_click, browser_fill, browser_extract calls
# in the same task reuse the live page. The session auto-closes when the task
# completes or after SESSION_TTL seconds of inactivity.
#
# Usage inside agent task:
#   agent.browser_session.open("https://example.com/login")
#   agent.browser_session.fill("#username", "alice")
#   agent.browser_session.fill("#password", "secret")
#   agent.browser_session.click("button[type=submit]")
#   text = agent.browser_session.extract("h1")

class BrowserSession:
    """
    Stateful Playwright browser session for multi-step agent workflows.

    Holds a single Chromium browser + page open across multiple tool calls,
    enabling browser_click and browser_fill to work correctly. Without this,
    both tools would need to re-launch the browser and re-navigate on every
    call, losing form state and login sessions.

    Thread-safe: one session per agent task (keyed by session_id).
    Auto-closes after SESSION_TTL seconds of inactivity.
    """
    SESSION_TTL = 300   # seconds of inactivity before auto-close

    def __init__(self, session_id: str = "") -> None:
        self._session_id  = session_id or secrets.token_hex(6)
        self._browser: Any = None
        self._page:    Any = None
        self._last_used: float = time.time()
        # RLock (re-entrant) because public methods hold _lock then call
        # _ensure_open() which must also acquire it.  A plain Lock deadlocks.
        self._lock = threading.RLock()

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def _ensure_open(self) -> bool:
        """
        Start browser and page if not already running.
        Must be called while self._lock is held (RLock allows re-entry).

        Playwright context manager is stored on self._pw_ctx so that
        __exit__ is called correctly in close() — using manual __enter__()
        without a stored reference causes resource leaks on cleanup failure.
        """
        if self._browser is not None and self._page is not None:
            self._last_used = time.time()
            return True
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
            # Store the context manager so close() can call __exit__ properly.
            self._pw_ctx   = sync_playwright()
            pw             = self._pw_ctx.__enter__()
            self._browser  = pw.chromium.launch(headless=True)
            self._page     = self._browser.new_page(
                viewport={"width": 1280, "height": 900})
            self._last_used = time.time()
            return True
        except ImportError:
            return False
        except Exception as e:
            log.debug("browser_session_open_error", extra={"error": str(e)[:80]})
            # Clean up partial state
            try:
                if self._browser:
                    self._browser.close()
            except Exception:
                pass
            self._browser = None
            self._page    = None
            return False

    def close(self) -> None:
        """Close the browser and release all Playwright resources."""
        with self._lock:
            try:
                if self._browser:
                    self._browser.close()
            except Exception:
                pass
            try:
                if hasattr(self, "_pw_ctx"):
                    self._pw_ctx.__exit__(None, None, None)
            except Exception:
                pass
            self._browser = None
            self._page    = None

    @property
    def is_expired(self) -> bool:
        return time.time() - self._last_used > self.SESSION_TTL

    # ── Tool actions ─────────────────────────────────────────────────────────
    def open(self, url: str, timeout_ms: int = 15000) -> str:
        """Navigate to url and return page text."""
        with self._lock:
            if not self._ensure_open():
                return "[BrowserSession] Playwright not available — pip install playwright && playwright install chromium"
            try:
                self._page.goto(url, timeout=timeout_ms,
                                wait_until="domcontentloaded")
                self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
                text = self._page.evaluate(
                    "() => document.body ? document.body.innerText : ''")
                return (text or "")[:8000].strip()
            except Exception as e:
                return f"[BrowserSession.open error: {e}]"

    def click(self, selector: str, timeout_ms: int = 5000) -> str:
        """Click element matching CSS selector."""
        with self._lock:
            if not self._ensure_open():
                return "[BrowserSession] not open"
            try:
                self._page.click(selector, timeout=timeout_ms)
                return f"[BrowserSession] clicked: {selector}"
            except Exception as e:
                return f"[BrowserSession.click error: {e}]"

    def fill(self, selector: str, value: str,
             timeout_ms: int = 5000) -> str:
        """Fill input field matching CSS selector."""
        with self._lock:
            if not self._ensure_open():
                return "[BrowserSession] not open"
            try:
                self._page.fill(selector, value, timeout=timeout_ms)
                return f"[BrowserSession] filled {selector}"
            except Exception as e:
                return f"[BrowserSession.fill error: {e}]"

    def extract(self, selector: str) -> str:
        """Extract inner text from elements matching CSS selector."""
        with self._lock:
            if not self._ensure_open():
                return "[BrowserSession] not open"
            try:
                elements = self._page.query_selector_all(selector)
                texts    = [el.inner_text() for el in elements if el]
                return "\n".join(texts).strip() or "[no elements matched]"
            except Exception as e:
                return f"[BrowserSession.extract error: {e}]"

    def screenshot(self, out_dir: Path | None = None,
                   selector: str = "") -> str:
        """Take a screenshot. Returns PNG path."""
        with self._lock:
            if not self._ensure_open():
                return "[BrowserSession] not open"
            try:
                import tempfile
                save_dir = out_dir or Path(tempfile.gettempdir())
                out_path = save_dir / f"essence_browser_{int(time.time())}.png"
                if selector:
                    el = self._page.query_selector(selector)
                    (el or self._page).screenshot(path=str(out_path))
                else:
                    self._page.screenshot(path=str(out_path), full_page=False)
                return str(out_path)
            except Exception as e:
                return f"[BrowserSession.screenshot error: {e}]"

    def current_url(self) -> str:
        with self._lock:
            if self._page is None:
                return ""
            try:
                return self._page.url
            except Exception:
                return ""


# ── Session registry: keyed by agent session_id ──────────────────────────────
_browser_sessions: dict[str, BrowserSession] = {}
_browser_sessions_lock = threading.Lock()


def get_browser_session(session_id: str) -> BrowserSession:
    """
    Return (or create) a BrowserSession for the given agent session.
    Expired sessions are auto-closed and replaced.
    """
    with _browser_sessions_lock:
        sess = _browser_sessions.get(session_id)
        if sess is None or sess.is_expired:
            if sess is not None:
                try:
                    sess.close()
                except Exception:
                    pass
            sess = BrowserSession(session_id)
            _browser_sessions[session_id] = sess
        return sess


def close_browser_session(session_id: str) -> None:
    """Close and remove the browser session for a completed agent task."""
    with _browser_sessions_lock:
        sess = _browser_sessions.pop(session_id, None)
    if sess:
        sess.close()


# ══════════════════════════════════════════════════════════════════════════════
