"""Computer use: screenshot + keyboard/mouse."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# COMPUTER USE
# ══════════════════════════════════════════════════════════════════════════════
# Gives the agent control over the host desktop — take screenshots, click,
# type text, move the mouse. Works on Linux (ydotool/xdotool), macOS
# (pyautogui), and Windows (pyautogui).
#
# Requires:
#   pip install pyautogui pillow
#   Linux headless: pip install pyautogui pyscreenshot   (no display needed)
#
# Security: ONLY grants these tools when Essence_COMPUTER_USE=1 env var is set.
#           Always logs every action to the AgentObserver.

def _computer_use_available() -> bool:
    """True if computer use is explicitly enabled and pyautogui is installed."""
    if os.environ.get("Essence_COMPUTER_USE", "0") != "1":
        return False
    try:
        import pyautogui  # type: ignore
        return True
    except ImportError:
        return False


def _tool_computer_screenshot(out_dir: Path | None = None) -> str:
    """
    Take a screenshot of the entire desktop.
    Returns path to saved PNG. Pair with analyze_image for VLM reasoning.
    Set Essence_COMPUTER_USE=1 to enable.
    """
    if not _computer_use_available():
        return "[computer_screenshot] disabled — set Essence_COMPUTER_USE=1 and pip install pyautogui pillow"
    try:
        import pyautogui, tempfile  # type: ignore
        save_dir = out_dir or Path(tempfile.gettempdir())
        out_path = save_dir / f"essence_desktop_{int(time.time())}.png"
        img = pyautogui.screenshot()
        img.save(str(out_path))
        return str(out_path)
    except Exception as e:
        return f"[computer_screenshot error: {e}]"


def _tool_computer_click(x: int, y: int, button: str = "left") -> str:
    """Click at screen coordinates (x, y). button: left|right|middle."""
    if not _computer_use_available():
        return "[computer_click] disabled — set Essence_COMPUTER_USE=1"
    try:
        import pyautogui  # type: ignore
        pyautogui.click(x, y, button=button)
        log.info("computer_click", extra={"x": x, "y": y, "button": button})
        return f"[computer_click] clicked ({x},{y}) with {button} button"
    except Exception as e:
        return f"[computer_click error: {e}]"


def _tool_computer_type(text: str, interval: float = 0.02) -> str:
    """
    Type text at the current cursor position.

    Unicode strategy:
      1. pyperclip + Ctrl+V (clipboard paste) — full Unicode, fast, recommended.
      2. pyautogui.write()                    — ASCII + some Latin, no emoji/CJK.
      3. pyautogui.typewrite()                — ASCII-only legacy fallback.

    Requires Essence_COMPUTER_USE=1.
    """
    if not _computer_use_available():
        return "[computer_type] disabled — set Essence_COMPUTER_USE=1"
    try:
        import pyautogui  # type: ignore
        # Strategy 1: clipboard paste — works for all Unicode characters
        try:
            import pyperclip  # type: ignore
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            log.info("computer_type_clipboard",
                     extra={"chars": len(text)})
            return f"[computer_type] pasted {len(text)} chars via clipboard"
        except ImportError:
            pass  # pyperclip not installed — fall through to write()

        # Strategy 2: pyautogui.write() — handles ASCII + basic Latin,
        # silently drops non-ASCII rather than crashing
        pyautogui.write(text, interval=interval)
        log.info("computer_type_write", extra={"chars": len(text)})
        return (f"[computer_type] typed {len(text)} chars via write(). "
                "Install pyperclip for full Unicode support: pip install pyperclip")
    except Exception as e:
        return f"[computer_type error: {e}]"


# ══════════════════════════════════════════════════════════════════════════════
