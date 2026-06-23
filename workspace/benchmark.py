"""Benchmark harness."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.core.constants import BANNER, amber, magenta  # noqa: F401

# BENCHMARK
# ══════════════════════════════════════════════════════════════════════════════

def benchmark(hw: HardwareProfile) -> None:
    print(BANNER)
    print(f"{bold('Benchmarking inference backends …')}\n")
    prompt  = [{"role": "user", "content": "Count 1 to 30, one per line."}]
    N_EST   = 90  # approximate expected output tokens

    backends: list[tuple[str, Any]] = []
    if shutil.which("ollama"):
        ob = OllamaBackend()
        if ob.alive(): backends.append(("Ollama",  ob))
    if hw.has_metal:
        mb = MLXBackend()
        if mb.alive(): backends.append(("MLX",     mb))
    if hw.has_cuda:
        vb = OpenAICompatBackend()
        if vb.alive(): backends.append(("vLLM",    vb))

    if not backends:
        print(yellow("  No backends running.  Start Ollama: ollama serve"))
        return

    print(f"  {'Backend':<14} {'tok/s':>8}  {'latency':>10}  Status")
    print(f"  {'─'*14} {'─'*8}  {'─'*10}  ──────")
    for name, bk in backends:
        t0 = time.perf_counter()
        try:
            tokens = list(bk.complete(prompt, model=hw.model,
                                      stream=True, thinking=False))
            el  = time.perf_counter() - t0
            # Count actual characters as proxy for tokens (≈4 chars/tok)
            actual_chars = sum(len(t) for t in tokens)
            actual_toks  = max(actual_chars // 4, 1)
            tps = actual_toks / max(el, .001)
            col = green if tps > 30 else yellow if tps > 8 else red
            print(f"  {name:<14} {col(f'{tps:>7.1f}')} tok/s "
                  f" {el:>9.2f}s  {green('OK')}")
        except Exception as e:
            print(f"  {name:<14} {'':>8}  {'':>10}  {red(str(e)[:50])}")
    print()


# ══════════════════════════════════════════════════════════════════════════════
