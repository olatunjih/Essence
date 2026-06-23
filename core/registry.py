"""Model registry: ModelSpec, REGISTRY, model selection."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.core.constants import BANNER, amber, magenta  # noqa: F401

# MODEL REGISTRY
# ══════════════════════════════════════════════════════════════════════════════
# Curated from: Qwen3/3.5 HF cards (2025-07), Nemotron 3 Super tech report
# (arXiv 2503.xxxxx, 2026-03-13), DeepSeek V3 paper, PinchBench rankings.
# MoE VRAM = active-layer Q4_K_M estimate (not total parameter count).

class ModelSpec(BaseModel):
    """Immutable Pydantic v2 model for a registry entry."""
    model_config = ConfigDict(frozen=True)

    id:           str
    ollama_tag:   str
    hf_slug:      str
    family:       str
    total_b:      float   # total parameters (billions)
    active_b:     float   # active per forward pass (MoE) or same as total
    vram_q4_gb:   float   # Q4_K_M VRAM estimate
    ctx_k:        int     # max context (K tokens)
    min_tier:     int
    thinking:     bool    # supports thinking/reasoning mode
    moe:          bool    # Mixture-of-Experts architecture
    pinch:        float   # PinchBench score 0-100 (0 = not measured)
    note:         str
    requires_vlm: bool = False  # VLM: vision-language model (image input)


REGISTRY: list[ModelSpec] = [
    # ── T0: IoT / Pi / SBC ──────────────────────────────────────────────────
    ModelSpec(id="qwen3-0.6b",  ollama_tag="qwen3:0.6b",  hf_slug="Qwen/Qwen3-0.6B-GGUF",
              family="Qwen3",  total_b=0.6,  active_b=0.6, vram_q4_gb=0.5,
              ctx_k=32,  min_tier=0, thinking=True,  moe=False, pinch=0.0,
              note="SBC / 2 GB devices. Thinking mode enabled."),
    ModelSpec(id="qwen3-1.7b",  ollama_tag="qwen3:1.7b",  hf_slug="Qwen/Qwen3-1.7B-GGUF",
              family="Qwen3",  total_b=1.7,  active_b=1.7, vram_q4_gb=1.4,
              ctx_k=32,  min_tier=0, thinking=True,  moe=False, pinch=0.0,
              note="SBC 4 GB / budget laptops."),
    # ── T1: Consumer laptop / PC ─────────────────────────────────────────────
    ModelSpec(id="qwen3-4b",    ollama_tag="qwen3:4b",    hf_slug="Qwen/Qwen3-4B-GGUF",
              family="Qwen3",  total_b=4.0,  active_b=4.0, vram_q4_gb=3.2,
              ctx_k=128, min_tier=1, thinking=True,  moe=False, pinch=0.0,
              note="Best everyday model for 6 GB VRAM / 8 GB RAM."),
    ModelSpec(id="qwen3-8b",    ollama_tag="qwen3:8b",    hf_slug="Qwen/Qwen3-8B-GGUF",
              family="Qwen3",  total_b=8.0,  active_b=8.0, vram_q4_gb=5.0,
              ctx_k=128, min_tier=1, thinking=True,  moe=False, pinch=0.0,
              note="Top open coding model at 8 GB VRAM Q4."),
    # ── T2: Workstation / M-series ───────────────────────────────────────────
    ModelSpec(id="qwen3-14b",   ollama_tag="qwen3:14b",   hf_slug="Qwen/Qwen3-14B-GGUF",
              family="Qwen3",  total_b=14.0, active_b=14.0, vram_q4_gb=9.0,
              ctx_k=128, min_tier=2, thinking=True,  moe=False, pinch=0.0,
              note="Excellent reasoning + coding. 12–16 GB VRAM."),
    ModelSpec(id="qwen3-30b-a3b", ollama_tag="qwen3:30b-a3b",
              hf_slug="Qwen/Qwen3-30B-A3B-GGUF",
              family="Qwen3",  total_b=30.0, active_b=3.0,  vram_q4_gb=8.0,
              ctx_k=128, min_tier=2, thinking=True,  moe=True,  pinch=0.0,
              note="MoE gem: 30B total / 3B active. Near-32B quality at 8 GB VRAM."),
    ModelSpec(id="qwen3-32b",   ollama_tag="qwen3:32b",   hf_slug="Qwen/Qwen3-32B-GGUF",
              family="Qwen3",  total_b=32.0, active_b=32.0, vram_q4_gb=20.0,
              ctx_k=128, min_tier=2, thinking=True,  moe=False, pinch=0.0,
              note="Dense 32B for 24 GB VRAM. Best T2 quality."),
    # ── T3: Server / Cloud ───────────────────────────────────────────────────
    ModelSpec(id="nemotron3-nano", ollama_tag="nvidia/nemotron-3-nano-30b-a3b",
              hf_slug="nvidia/Nemotron-3-Nano-30B-A3B",
              family="Nemotron3", total_b=31.6, active_b=3.6, vram_q4_gb=22.0,
              ctx_k=1024, min_tier=3, thinking=True, moe=True, pinch=0.0,
              note="Hybrid Mamba-Transformer. 1M ctx. 3.3× faster than Qwen3-30B-A3B."),
    ModelSpec(id="nemotron3-super", ollama_tag="nvidia/nemotron-3-super-120b-a12b",
              hf_slug="nvidia/Nemotron-3-Super-120B-A12B",
              family="Nemotron3", total_b=120.0, active_b=12.0, vram_q4_gb=50.0,
              ctx_k=1024, min_tier=3, thinking=True, moe=True, pinch=85.6,
              note="PinchBench 85.6% — #1 open agentic model (2026-03-13). "
                   "5× throughput vs GPT-OSS-120B."),
    ModelSpec(id="deepseek-v3", ollama_tag="deepseek-v3:671b",
              hf_slug="deepseek-ai/DeepSeek-V3",
              family="DeepSeek", total_b=671.0, active_b=37.0, vram_q4_gb=80.0,
              ctx_k=128, min_tier=3, thinking=False, moe=True, pinch=0.0,
              note="Best open coding model. 671B total / 37B active MoE."),
    ModelSpec(id="qwen3-235b-a22b", ollama_tag="qwen3:235b-a22b",
              hf_slug="Qwen/Qwen3-235B-A22B-GGUF",
              family="Qwen3", total_b=235.0, active_b=22.0, vram_q4_gb=50.0,
              ctx_k=128, min_tier=3, thinking=True, moe=True, pinch=0.0,
              note="Flagship Qwen3 MoE. Competitive with GPT-4o on reasoning."),
    # ── VLM: Vision-Language models ──────────────────────────────────────────
    ModelSpec(id="qwen2.5-vl-2b", ollama_tag="qwen2.5-vl:2b",
              hf_slug="Qwen/Qwen2.5-VL-2B-Instruct",
              family="Qwen2.5-VL", total_b=2.0, active_b=2.0, vram_q4_gb=2.0,
              ctx_k=32, min_tier=1, thinking=False, moe=False, pinch=0.0,
              note="VLM: image+text understanding on T1 (4GB VRAM). analyze_image tool.",
              requires_vlm=True),
    ModelSpec(id="qwen2.5-vl-7b", ollama_tag="qwen2.5-vl:7b",
              hf_slug="Qwen/Qwen2.5-VL-7B-Instruct",
              family="Qwen2.5-VL", total_b=7.0, active_b=7.0, vram_q4_gb=5.0,
              ctx_k=32, min_tier=2, thinking=False, moe=False, pinch=0.0,
              note="VLM: best open multimodal at T2 (8GB+ VRAM). analyze_image tool.",
              requires_vlm=True),
]


def models_for_tier(tier: int) -> list[ModelSpec]:
    return [m for m in REGISTRY if m.min_tier <= tier]


def _discover_ollama_models() -> list[ModelSpec]:
    """
      DYNAMIC MODEL DISCOVERY  — query `ollama list` at runtime.

    Merges locally installed Ollama models into the REGISTRY so that
    newly released models (e.g. Qwen3.5, Llama 4) are usable immediately
    without a Essence update.  Models already in REGISTRY are not duplicated.

    Called automatically by _ensure_ollama_models_discovered() exactly once
    per process lifetime (thread-safe, double-checked locking).  Do not call
    this function directly — use _ensure_ollama_models_discovered() instead.

    Returns list of dynamically discovered ModelSpec entries (min_tier=0 so
    they're always available; vram_q4_gb set to detected size or 0.0).
    """
    known_tags = {m.ollama_tag for m in REGISTRY}
    discovered: list[ModelSpec] = []
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines()[1:]:   # skip header
            parts = line.split()
            if not parts:
                continue
            tag = parts[0]
            if tag in known_tags:
                continue
            # Parse size hint from ollama list output (e.g. "4.9 GB")
            vram = 0.0
            for i, p in enumerate(parts):
                if p in ("GB", "MB") and i > 0:
                    try:
                        vram = float(parts[i-1]) * (1.0 if p == "GB" else 0.001)
                    except ValueError:
                        pass
            discovered.append(ModelSpec(
                id=tag, ollama_tag=tag, hf_slug=tag,
                family="discovered", total_b=0.0, active_b=0.0,
                vram_q4_gb=vram,
                ctx_k=128, min_tier=0, thinking=False, moe=False,
                pinch=0.0, note=f"Auto-discovered via `ollama list`"))
            known_tags.add(tag)
    except Exception as _e:
        log.debug("ollama_discover_error", extra={"error": str(_e)[:120]})
    return discovered


# ── One-time discovery guard ──────────────────────────────────────────────────
# Prevents best_fit() from calling subprocess + REGISTRY.extend() on every
# request. Set to True after the first successful discovery run.
_ollama_discovered: bool = False
_ollama_discover_lock = threading.Lock()


def _ensure_ollama_models_discovered() -> None:
    """
    Run _discover_ollama_models() exactly once per process lifetime.
    Thread-safe: uses a lock so concurrent first calls don't double-extend.
    Subsequent calls return immediately without touching REGISTRY or subprocess.
    """
    global _ollama_discovered
    if _ollama_discovered:
        return
    with _ollama_discover_lock:
        if _ollama_discovered:   # double-checked locking
            return
        discovered = _discover_ollama_models()
        if discovered:
            REGISTRY.extend(discovered)
        _ollama_discovered = True


def best_fit(hw: HardwareProfile) -> ModelSpec:
    # Merge locally installed Ollama models into REGISTRY — runs exactly once.
    _ensure_ollama_models_discovered()
    budget     = hw.effective_gb * 0.85
    candidates = sorted(
        [m for m in models_for_tier(hw.tier) if m.vram_q4_gb <= budget],
        key=lambda m: (m.pinch, m.active_b, m.total_b), reverse=True,
    )
    return candidates[0] if candidates else REGISTRY[0]


def select_model(hw: HardwareProfile,
                 preferred: str = "",
                 interactive: bool = False) -> ModelSpec:
    """
    Dynamic model selector — zero hard-coded reliance.

    Priority order:
      1. ``preferred`` argument (exact id or ollama_tag match)
      2. ``Essence_MODEL`` env var (already captured in _MODEL_OVERRIDE)
      3. Interactive numbered picker when interactive=True and terminal is a TTY
      4. ``best_fit(hw)`` — highest-scoring model within hardware budget

    Returns the chosen ModelSpec so callers can read .ollama_tag, .hf_slug, etc.
    """
    tag = preferred or _MODEL_OVERRIDE
    if tag:
        for m in models_for_tier(hw.tier):
            if m.id == tag or m.ollama_tag == tag or m.hf_slug == tag:
                return m
        # Not in registry — synthesise a minimal spec so arbitrary tags work
        return ModelSpec(
            id=tag, ollama_tag=tag, hf_slug=tag,
            family="custom", total_b=0.0, active_b=0.0, vram_q4_gb=0.0,
            ctx_k=128, min_tier=0, thinking=False, moe=False, pinch=0.0,
            note="User-specified model (not in REGISTRY)",
        )

    candidates = sorted(
        [m for m in models_for_tier(hw.tier)
         if m.vram_q4_gb <= hw.effective_gb * 0.85
         and not m.requires_vlm],
        key=lambda m: (m.pinch, m.active_b, m.total_b), reverse=True,
    )
    if not candidates:
        return REGISTRY[0]

    if interactive and sys.stdin.isatty() and len(candidates) > 1:
        print(f"\n  {bold('Available models for')} {cyan(hw.tier_label)}:")
        for i, m in enumerate(candidates[:10], 1):
            fit_gb = f"{m.vram_q4_gb:.1f}G"
            think  = green("T") if m.thinking else dim("·")
            moe    = green("M") if m.moe      else dim("·")
            print(f"  {dim(str(i)+'.')} {cyan(m.id):<28} {fit_gb:>6}  {think}  {moe}  {m.note[:50]}")
        print(f"  {dim('  (enter to accept default: '+ candidates[0].id + ')')}")
        try:
            raw = input("  Select model [1]: ").strip()
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(candidates):
                    return candidates[idx]
        except (EOFError, KeyboardInterrupt):
            pass

    return candidates[0]


def print_models(hw: HardwareProfile) -> None:
    print(BANNER)
    budget = hw.effective_gb * 0.85
    print(f"  {bold('Registry for')} {cyan(hw.tier_label)} "
          f"{dim(f'(budget ≈ {budget:.0f} GB)')}")
    print(bold(f"  {'ID':<26}{'Total':>7} {'Act':>6} {'VRAM':>6} "
               f"{'Ctx':>6}  Think  MoE  Pinch  Fit"))
    print(f"  {'─'*76}")
    for m in models_for_tier(hw.tier):
        fits  = green("✓") if m.vram_q4_gb <= budget else red("✗")
        think = green("✓") if m.thinking else dim("·")
        moe   = green("M") if m.moe      else dim("·")
        pb    = green(f"{m.pinch:4.1f}") if m.pinch > 0 else dim("  ─ ")
        print(f"  {m.id:<26}{m.total_b:>6.0f}B {m.active_b:>5.0f}B "
              f"{m.vram_q4_gb:>5.1f}G {m.ctx_k:>5}K   {think}    {moe}   {pb}  {fits}")
    print()


# ══════════════════════════════════════════════════════════════════════════════


import dataclasses as _dc  # noqa: E402 (needed by ABModelRouter before _dc alias set below)

# ══════════════════════════════════════════════════════════════════════════════
