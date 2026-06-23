"""chat loop +  FastAPI app: all routes, middleware, lifespan.
v29.0: /chat route injects Analytics Engine spine context."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.core.constants import BANNER, amber, magenta  # noqa: F401

from essence.analytics.spine import get_analytical_spine  # noqa: F401
# INTERACTIVE CHAT
# ══════════════════════════════════════════════════════════════════════════════

def run_chat(hw: HardwareProfile, prov: ProviderChain,
             model: str, thinking: bool, budget: int,
             ws: Path) -> None:
    print(BANNER)
    try: bk = prov.active.NAME
    except Exception: bk = "offline"
    print(f"  Model    {cyan(model)}  Backend {cyan(bk)}  Tier {cyan(hw.tier_label)}")
    print(f"  Thinking {green('ON') if thinking else red('OFF')}"
          f"  Budget {dim(str(budget))}")
    _cmds = '/think  /no_think  /model <tag>  /agent <task>  /soul  /heartbeat list  /quit'
    print(f"\n  {dim(_cmds)}\n")

    soul     = load_ws_file(ws, "SOUL.md",     _DEFAULT_SOUL)
    identity = load_ws_file(ws, "IDENTITY.md", "")
    tools_md = load_ws_file(ws, "TOOLS.md",    _DEFAULT_TOOLS)
    skills   = load_skills_index(ws)
    mem      = Memory(ws, hw.tier)
    cfg      = AgentConfig(provider=prov, model=model, workspace=ws,
                           thinking=thinking, budget=budget)
    agent    = Agent(cfg, soul=soul, identity=identity,
                     tools_md=tools_md, skills=skills, memory=mem, hw=hw)
    sched    = HeartbeatScheduler(
        ws, lambda m: agent.run_task(m, log=lambda *_: None))
    agent._scheduler = sched
    sched.start()

    while True:
        try:
            user = input(f"{bold('You')} \u203a ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye."); sched.stop(); break

        if not user: continue
        if user in ("/quit", "/q", "exit"):
            sched.stop(); break
        if user in ("/think", "/think on"):
            cfg.thinking = True; print(f"  thinking {green('ON')}"); continue
        if user in ("/no_think", "/think off"):
            cfg.thinking = False; print(f"  thinking {red('OFF')}"); continue
        if user.startswith("/model "):
            cfg.model = user[7:].strip()
            print(f"  model \u2192 {cyan(cfg.model)}"); continue
        if user.startswith("/agent "):
            task = user[7:].strip()
            _hdr = "\u25b8 Running agent task \u2026"
            print(f"\n{cyan(_hdr)}\n")
            result = agent.run_task(task)
            _sep = "\u2500\u2500 Result \u2500\u2500"
            print(f"\n{green(_sep)}\n{result}\n"); continue
        if user == "/soul":
            print(f"\n{soul[:400]}\n"); continue
        if user.startswith("/heartbeat "):
            parts = user.split()
            if len(parts) >= 2 and parts[1] == "list":
                for j in sched.list_jobs():
                    print(f"  {green(j.name):<22} {j.schedule:<12} "
                          f"{j.message[:55]}")
            elif len(parts) >= 4 and parts[1] == "add":
                sched.add(parts[2], " ".join(parts[4:]), parts[3])
                print(f"  {green('added')} {parts[2]}")
            continue

        print(f"\n{amber('Essence')} \u203a ", end="", flush=True)
        agent.chat(user, emit=lambda t: print(t, end="", flush=True))
        print("\n")


# ══════════════════════════════════════════════════════════════════════════════

# SERVER LAUNCHER
# ══════════════════════════════════════════════════════════════════════════════

def run_server(ws: Path, hw: HardwareProfile, port: int = 7860) -> None:
    try:
        import uvicorn  # type: ignore
    except ImportError:
        print(red("uvicorn not installed. Run: essence install")); sys.exit(1)
    scaffold(ws, hw)
    # Verify critical files exist after scaffold — fail fast with clear message
    _required = [ws / "server" / "app.py", ws / "server" / "index.html",
                 ws / "server" / "__init__.py"]
    for _req in _required:
        if not _req.exists():
            print(red(f"  Missing scaffold file: {_req}"))
            print(red("  Run: essence scaffold"))
            sys.exit(1)
    print(BANNER)
    print(f"  {bold(f'Starting Essence v{Essence_VERSION}')} → {cyan(f'http://localhost:{port}')}")
    print(f"  Tier {cyan(hw.tier_label)}  Model {cyan(hw.model)}\n")
    sys.path.insert(0, str(ws))
    uvicorn.run("server.app:app", host="0.0.0.0", port=port,
                reload=False, log_level="info", app_dir=str(ws))


# ══════════════════════════════════════════════════════════════════════════════
