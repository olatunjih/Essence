"""Essence TUI — rich-based terminal UI for managing integrations and settings.

Launch with:   python -m essence.tui
Or via CLI:    essence tui
"""
from __future__ import annotations

import os
import sys
import json
import threading
from pathlib import Path
from typing import Any

# ── rich imports (already in requirements) ────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich.columns import Columns
    from rich.text import Text
    from rich.layout import Layout
    from rich.live import Live
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False

console = Console() if _RICH else None

# ── category display order ────────────────────────────────────────────────────
_CATEGORIES = ["llm", "channel", "productivity", "observability", "voice", "search"]
_CAT_LABELS = {
    "llm":           "🧠  LLM Providers",
    "channel":       "💬  Messaging Channels",
    "productivity":  "📋  Productivity",
    "observability": "📈  Observability",
    "voice":         "🔊  Voice",
    "search":        "🔍  Search",
}


def _get_store() -> Any:
    from essence.integrations.store import get_store
    return get_store()


def _get_registry() -> dict:
    from essence.integrations.registry import INTEGRATION_REGISTRY
    return INTEGRATION_REGISTRY


def _status_badge(configured: bool, enabled: bool) -> Text:
    if not configured:
        return Text("○ not set", style="dim")
    if not enabled:
        return Text("⏸ disabled", style="yellow")
    return Text("● active", style="green bold")


# ── screens ───────────────────────────────────────────────────────────────────

def screen_main(store: Any) -> str:
    """Show main menu. Returns selected action."""
    registry = _get_registry()
    console.clear()
    console.print(Panel.fit(
        "[bold cyan]Essence[/bold cyan]  ·  Integration & Settings Manager",
        border_style="cyan",
    ))

    for cat in _CATEGORIES:
        items = [d for d in registry.values() if d.category == cat]
        if not items:
            continue
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column("icon", width=3)
        table.add_column("name", width=24)
        table.add_column("status", width=16)
        table.add_column("description")
        for d in items:
            configured = store.is_configured(d.id)
            enabled    = store.is_enabled(d.id)
            table.add_row(
                d.icon,
                d.name,
                _status_badge(configured, enabled),
                Text(d.description, style="dim"),
            )
        console.print(Panel(table, title=_CAT_LABELS.get(cat, cat),
                            border_style="blue"))

    console.print()
    console.print("[bold]Actions:[/bold]  "
                  "[cyan](c)[/cyan] Configure  "
                  "[cyan](t)[/cyan] Test all  "
                  "[cyan](p)[/cyan] Providers  "
                  "[cyan](x)[/cyan] Custom provider  "
                  "[cyan](q)[/cyan] Quit")
    return Prompt.ask("\nChoose", choices=["c", "t", "p", "x", "q"],
                      default="c")


def screen_configure(store: Any) -> None:
    """Prompt to pick and configure an integration."""
    registry = _get_registry()
    console.clear()
    console.print(Panel.fit("[bold]Configure Integration[/bold]",
                            border_style="cyan"))

    ids = list(registry.keys())
    for i, iid in enumerate(ids, 1):
        d = registry[iid]
        configured = store.is_configured(iid)
        badge = "[green]●[/green]" if configured else "[dim]○[/dim]"
        console.print(f"  [cyan]{i:2}[/cyan]  {badge} {d.icon} {d.name}")

    console.print()
    raw = Prompt.ask("Enter number (or blank to cancel)", default="")
    if not raw.strip():
        return
    try:
        idx = int(raw.strip()) - 1
        if idx < 0 or idx >= len(ids):
            raise ValueError
        iid = ids[idx]
    except ValueError:
        console.print("[red]Invalid selection[/red]")
        return

    defn = registry[iid]
    screen_edit_integration(store, defn)


def screen_edit_integration(store: Any, defn: Any) -> None:
    console.clear()
    console.print(Panel.fit(
        f"{defn.icon}  [bold]{defn.name}[/bold]\n"
        f"[dim]{defn.description}[/dim]",
        border_style="cyan",
    ))
    if defn.docs_url:
        console.print(f"  [dim]Docs: {defn.docs_url}[/dim]")
    console.print()

    all_fields = defn.credential_fields + defn.optional_fields
    credentials: dict[str, str] = {}
    for field in all_fields:
        current = store.get_credential(defn.id, field)
        masked  = "***" if current else ""
        label   = f"  {field}" + (" [dim](optional)[/dim]"
                                   if field in defn.optional_fields else "")
        console.print(label)
        if current:
            console.print(f"    [dim]current: {masked}[/dim]")
        val = Prompt.ask("    value", default="", password="key" in field or "token" in field or "dsn" in field)
        if val:
            credentials[field] = val
        elif current:
            credentials[field] = current   # keep existing

    settings: dict[str, str] = {}
    if defn.settings_fields:
        console.print()
        console.print("[bold]Settings[/bold]")
        for sf in defn.settings_fields:
            cur = store.get_settings(defn.id).get(sf, "")
            val = Prompt.ask(f"  {sf}", default=cur or "")
            if val:
                settings[sf] = val

    enabled = Confirm.ask("Enable this integration?", default=True)

    store.upsert(defn.id, credentials, settings, enabled)
    console.print(f"\n[green]✓ {defn.name} saved.[/green]")

    # Try to run health check
    if Confirm.ask("Run connection test now?", default=True):
        import asyncio
        try:
            result = asyncio.run(defn.health_check(store))
            if result.get("ok"):
                console.print("[green]✓ Connection OK[/green]")
                for k, v in result.items():
                    if k != "ok":
                        console.print(f"  {k}: {v}")
            else:
                console.print(f"[red]✗ {result.get('error', 'failed')}[/red]")
        except Exception as exc:
            console.print(f"[red]✗ {exc}[/red]")

    Prompt.ask("\nPress Enter to continue", default="")


def screen_test_all(store: Any) -> None:
    import asyncio
    from essence.integrations.registry import health_check_all
    console.clear()
    console.print(Panel.fit("[bold]Testing All Integrations[/bold]",
                            border_style="cyan"))
    console.print("[dim]Running health checks in parallel…[/dim]\n")

    results = asyncio.run(health_check_all(store))
    registry = _get_registry()

    table = Table(box=box.ROUNDED)
    table.add_column("Integration", style="bold")
    table.add_column("Status", width=12)
    table.add_column("Detail")

    for iid, res in results.items():
        defn = registry.get(iid)
        name = defn.name if defn else iid
        if res.get("ok"):
            status = Text("✓ OK", style="green bold")
            detail = ", ".join(
                f"{k}={v}" for k, v in res.items()
                if k not in ("ok", "error") and v
            )
        else:
            status = Text("✗ FAIL", style="red bold")
            detail = res.get("error", "unknown")

        table.add_row(name, status, str(detail)[:60])

    console.print(table)
    Prompt.ask("\nPress Enter to continue", default="")


def screen_providers(store: Any) -> None:
    import asyncio
    from essence.backends.smart_router import SmartRouter
    console.clear()
    console.print(Panel.fit("[bold]LLM Provider Status[/bold]",
                            border_style="cyan"))
    console.print("[dim]Checking availability in parallel…[/dim]\n")

    router = SmartRouter(store)
    status = asyncio.run(router.status_async())

    table = Table(box=box.ROUNDED)
    table.add_column("Provider", style="bold")
    table.add_column("Status", width=10)
    table.add_column("Latency EMA")
    table.add_column("Models (first 5)")

    for p in status["providers"]:
        alive  = p["alive"]
        badge  = Text("● alive", style="green") if alive else Text("○ down", style="dim")
        lat    = f"{p['latency_ema']:.2f}s" if p['latency_ema'] >= 0 else "—"
        models = ", ".join(p["models"][:5]) or "—"
        table.add_row(p["name"], badge, lat, models)

    console.print(table)
    console.print(f"\n[dim]{status['count']} provider(s) registered[/dim]")
    Prompt.ask("\nPress Enter to continue", default="")


def screen_custom_provider(store: Any) -> None:
    console.clear()
    console.print(Panel.fit("[bold]Custom OpenAI-Compatible Provider[/bold]",
                            border_style="cyan"))

    existing = store.list_custom_providers()
    if existing:
        table = Table(box=box.SIMPLE)
        table.add_column("Name")
        table.add_column("Base URL")
        table.add_column("Models")
        for p in existing:
            table.add_row(p["name"], p["base_url"],
                          ", ".join(p.get("models", [])[:3]) or "auto-discover")
        console.print(table)
        console.print()

    action = Prompt.ask("Action",
                        choices=["add", "remove", "test", "back"],
                        default="add")

    if action == "back":
        return

    if action == "add":
        name     = Prompt.ask("  Provider name (e.g. my-llm)")
        base_url = Prompt.ask("  Base URL (e.g. http://localhost:8080)")
        api_key  = Prompt.ask("  API key", default="sk-", password=True)
        models_r = Prompt.ask("  Known models (comma-separated, or blank to auto-discover)",
                              default="")
        models   = [m.strip() for m in models_r.split(",") if m.strip()]
        desc     = Prompt.ask("  Description (optional)", default="")

        store.upsert_custom_provider({
            "name": name, "base_url": base_url,
            "api_key": api_key, "models": models, "description": desc,
        })
        console.print(f"\n[green]✓ Custom provider '{name}' saved.[/green]")

    elif action == "remove":
        if not existing:
            console.print("[dim]No custom providers configured.[/dim]")
        else:
            name = Prompt.ask("  Provider name to remove")
            ok   = store.remove_custom_provider(name)
            console.print("[green]✓ Removed[/green]" if ok
                          else f"[red]Not found: {name}[/red]")

    elif action == "test":
        if not existing:
            console.print("[dim]No custom providers configured.[/dim]")
        else:
            import asyncio
            from essence.backends.cloud import CustomProvider
            name    = Prompt.ask("  Provider name to test")
            cp_data = next((p for p in existing if p["name"] == name), None)
            if cp_data is None:
                console.print(f"[red]Not found: {name}[/red]")
            else:
                cp    = CustomProvider(name=name, base_url=cp_data["base_url"],
                                       api_key=cp_data.get("api_key", "sk-"))
                alive = cp.alive()
                if alive:
                    models = cp.list_models()
                    console.print(f"[green]✓ Alive[/green]  models: {models[:5]}")
                else:
                    console.print("[red]✗ Not reachable[/red]")

    Prompt.ask("\nPress Enter to continue", default="")


# ── main loop ─────────────────────────────────────────────────────────────────

def run_tui(workspace: Path | None = None) -> None:
    if not _RICH:
        print("ERROR: 'rich' package not installed. Run: pip install rich")
        sys.exit(1)

    if workspace:
        from essence.integrations.store import init_store
        store = init_store(workspace)
    else:
        store = _get_store()

    while True:
        try:
            action = screen_main(store)
            if action == "q":
                console.print("[dim]Goodbye.[/dim]")
                break
            elif action == "c":
                screen_configure(store)
            elif action == "t":
                screen_test_all(store)
            elif action == "p":
                screen_providers(store)
            elif action == "x":
                screen_custom_provider(store)
        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted.[/dim]")
            break
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")
            Prompt.ask("Press Enter to continue", default="")


def main() -> None:
    ws_env = os.environ.get("ESSENCE_WORKSPACE",
                             str(Path.home() / ".essence" / "workspace"))
    run_tui(Path(ws_env))


if __name__ == "__main__":
    main()
