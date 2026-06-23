"""CLI entry point: unified dispatcher for Kernel + Installer subcommands."""
# ruff: noqa
# fmt: off
from __future__ import annotations
import sys

# ── Kernel subcommands (everything else routes to installer) ──────────────────
# "audit" is intentionally absent — it belongs to the installer's workspace-
# level audit log, not to this kernel CLI.  The kernel's capsule-level audit
# trail is exposed as "plan-audit" to avoid the name collision.
_KERNEL_COMMANDS: frozenset[str] = frozenset({"plan", "tick", "plan-audit", "doctor"})


def main(argv: list[str] | None = None) -> int:
    """
    Essence unified CLI.

    Kernel subcommands (drive the APDE kernel directly):
        plan <prompt>              Ingest a prompt → frozen PlanDAG; prints capsule_id.
        tick <capsule_id>          Advance one ready task in the plan.
        plan-audit [capsule_id]    Print the kernel audit trail (all or per-capsule).
        doctor                     Run boot self-test and print system health.

    All other subcommands (install, up, chat, tui, audit, agent, …) are routed
    to the original Installer CLI unchanged.  Run `essence --help` to see both.
    """
    raw_argv: list[str] = argv if argv is not None else sys.argv[1:]

    # Determine which subcommand the user wants (first non-flag positional arg).
    first_cmd = next(
        (a for a in raw_argv if not a.startswith("-")),
        None,
    )

    if first_cmd in _KERNEL_COMMANDS:
        return _kernel_main(raw_argv)

    # No subcommand at all: print combined help and exit cleanly.
    # (Don't route to the installer for the empty-argv case because the
    # installer's no-args path tries to probe hardware and scaffold a
    # workspace, which is not what a bare `essence` call in tests should do.)
    if first_cmd is None:
        _print_combined_help()
        return 0

    # Named installer subcommand — route unchanged.
    # SystemExit is intentionally NOT caught here so that --help and
    # argparse error paths propagate to the caller as expected.
    from essence.installer import main as _installer_main
    return int(_installer_main(raw_argv) or 0)


def _print_combined_help() -> None:
    """Print a combined one-screen summary of all Essence subcommands."""
    print(
        "Usage: essence [--workspace DIR] [--autonomy-tier N] <subcommand> ...\n\n"
        "Kernel subcommands:\n"
        "  plan <prompt>              Ingest a prompt → frozen PlanDAG\n"
        "  tick <capsule_id>          Advance one task in a plan\n"
        "  plan-audit [capsule_id]    Print the kernel audit trail\n"
        "  doctor                     Boot self-test + system health check\n\n"
        "Installer / workspace subcommands:\n"
        "  install, up, chat, tui, agent, audit, pull, probe, scaffold,\n"
        "  models, bench, channels, skill, eval, decisions, workflows,\n"
        "  peers, control, cost, team, memory, sop, export, import, keys,\n"
        "  plugins, self-update, import-workspace, install-packages\n\n"
        "Run `essence <subcommand> --help` for per-command options."
    )


# ── Kernel CLI implementation ─────────────────────────────────────────────────

def _kernel_main(argv: list[str] | None = None) -> int:
    import argparse, json, logging
    from pathlib import Path

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="essence",
        description="Essence APDE kernel CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Installer subcommands (install, up, chat, tui, audit, agent, …):\n"
            "  Run `essence --help` with no kernel subcommand to see them."
        ),
    )
    parser.add_argument(
        "--workspace", default=str(Path.home() / ".essence"),
        help="Kernel workspace directory (default: ~/.essence)")
    parser.add_argument(
        "--autonomy-tier", type=int, default=2, dest="autonomy_tier",
        help="Autonomy tier 0-3 (default: 2)")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command")

    # plan
    plan_p = sub.add_parser("plan", help="Ingest a prompt and build a plan")
    plan_p.add_argument("prompt", nargs="+", help="Natural-language task prompt")

    # tick
    tick_p = sub.add_parser("tick", help="Advance one task in an existing plan")
    tick_p.add_argument("capsule_id", help="Capsule ID returned by `plan`")

    # plan-audit  (renamed from "audit" to avoid collision with installer's audit)
    pa_p = sub.add_parser("plan-audit", help="Print kernel audit trail")
    pa_p.add_argument("capsule_id", nargs="?", default="",
                      help="Capsule ID (optional — omit to see all events)")

    # doctor
    sub.add_parser("doctor", help="Boot self-test + system health check")

    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger("essence").setLevel(logging.DEBUG)

    if args.command is None:
        parser.print_help()
        return 0

    # ── Boot kernel ───────────────────────────────────────────────────────────
    if args.command == "doctor":
        print("Running Essence boot self-test …")

    try:
        from essence.boot import boot_kernel
        kernel = boot_kernel(
            workspace=args.workspace,
            autonomy_tier=args.autonomy_tier,
        )
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[ERROR] Kernel boot failed: {exc}", file=sys.stderr)
        return 1

    # ── Subcommand dispatch ───────────────────────────────────────────────────
    if args.command == "plan":
        prompt = " ".join(args.prompt)
        try:
            capsule_id = kernel.ingest_capsule(
                raw_prompt=prompt,
                user_id="cli",
                autonomy_tier=args.autonomy_tier,
            )
            print(f"capsule_id: {capsule_id}")
        except Exception as exc:
            print(f"[ERROR] plan failed: {exc}", file=sys.stderr)
            return 1

    elif args.command == "tick":
        try:
            result = kernel.tick(args.capsule_id)
            print(json.dumps(result, indent=2))
        except Exception as exc:
            print(f"[ERROR] tick failed: {exc}", file=sys.stderr)
            return 1

    elif args.command == "plan-audit":
        trail = kernel.audit()
        if args.capsule_id:
            trail = [r for r in trail
                     if args.capsule_id in str(r.get("data", {}))]
        print(json.dumps(trail, indent=2))

    elif args.command == "doctor":
        print("✓ Boot self-test passed")
        try:
            from essence.security.sandbox import _EphemeralContainerSandbox
            cs = _EphemeralContainerSandbox(Path(args.workspace))
            print(f"  Sandbox: {cs.status()}")
        except Exception:
            print("  Sandbox: status unavailable")
        trail = kernel.audit()
        print(f"  Audit events at boot: {len(trail)}")

    return 0


# ══════════════════════════════════════════════════════════════════════════════
