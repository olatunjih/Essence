"""Hardware probe: tier, GPU, RAM, model budget."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.core.constants import BANNER, amber, magenta  # noqa: F401

# HARDWARE PROBE & TIER CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════
#
#  Tier map:
#
#  T0  IoT / SBC                   CPU-only  ≤ 4 GB RAM   llama.cpp  0.6–1.7B
#  T1  Laptop / consumer PC         iGPU      4–12 GB      Ollama     3B–8B Q4
#  T2  Workstation / M-series Mac   dGPU/M   12–40 GB      MLX/Ollama 14B–32B
#  T3  Server / multi-GPU / cloud    48 GB+   VRAM         vLLM       70B–685B
#
#  VRAM budgeting uses *active_params* for MoE models, not total params.
#  Nemotron 3 Super: 120B total / 12B active → fits a single A100 80GB.

class HardwareProfile(BaseModel):
    """Pydantic v2 model — all fields validated and serialisable."""
    model_config = ConfigDict(frozen=False)  # allow hw.model = override

    os_name:    str       # Linux | Darwin | Windows
    arch:       str       # x86_64 | arm64 | aarch64
    cpu_cores:  int       = Field(default=1)
    ram_gb:     float     = Field(default=4.0)
    gpu_vendor: str       # nvidia | amd | apple | intel | none
    vram_gb:    float     = Field(default=0.0)  # 0 = no discrete GPU
    has_cuda:   bool      = False
    has_metal:  bool      = False  # Apple Silicon
    has_rocm:   bool      = False
    has_vulkan: bool      = False
    tier:       int       = 0    # 0–3
    tier_label: str       = 'T0·IoT'
    backend:    str       = 'ollama'
    model:      str       = 'qwen3:0.6b'

    @field_validator('vram_gb', mode='before')
    @classmethod
    def _clamp_vram(cls, v: Any) -> float:
        return max(0.0, float(v))

    @field_validator('cpu_cores', mode='before')
    @classmethod
    def _clamp_cores(cls, v: Any) -> int:
        return max(1, int(v))

    @field_validator('ram_gb', mode='before')
    @classmethod
    def _clamp_ram(cls, v: Any) -> float:
        return max(0.5, float(v))

    @property
    def effective_gb(self) -> float:
        """VRAM if available, else ~60% of RAM (accounting for OS overhead)."""
        return self.vram_gb if self.vram_gb > 0 else self.ram_gb * 0.60

    @property
    def is_apple(self) -> bool:
        return self.gpu_vendor == 'apple'


def _sh(cmd: list[str], timeout: int = 5) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, check=False).stdout.strip()
    except Exception:
        return ""


def _ram_gb() -> float:
    s = platform.system()
    try:
        if s == "Darwin":
            return int(_sh(["sysctl", "-n", "hw.memsize"])) / 1e9
        if s == "Linux":
            for line in open("/proc/meminfo", encoding="utf-8"):
                if line.startswith("MemTotal"):
                    return int(line.split()[1]) / 1e6
        if s == "Windows":
            for part in _sh(["wmic", "ComputerSystem", "get",
                              "TotalPhysicalMemory", "/value"]).split():
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k == "TotalPhysicalMemory" and v.isdigit():
                        return int(v) / 1e9
    except Exception as _e:
        log.debug("ram_detect_error", extra={"error": str(_e)[:120]})
    return 4.0


def _nvidia_vram_gb() -> float:
    out = _sh(["nvidia-smi", "--query-gpu=memory.total",
               "--format=csv,noheader,nounits"])
    try:
        return sum(float(x.strip()) for x in out.splitlines() if x.strip()) / 1024
    except (ValueError, AttributeError):
        return 0.0


def _amd_vram_gb() -> float:
    out = _sh(["rocm-smi", "--showmeminfo", "vram", "--json"])
    try:
        data = json.loads(out)
        total = 0
        for v in data.values():
            # rocm-smi key changed across versions
            total += int(v.get("VRAM Total Memory (B)",
                         v.get("VRAM Total Memory(B)",
                         v.get("vram_total_memory_B", 0))))
        return total / 1e9
    except Exception:
        return 0.0


def probe_hardware() -> HardwareProfile:
    os_name    = platform.system()
    arch       = platform.machine().lower()
    cores      = os.cpu_count() or 1
    ram_gb     = _ram_gb()
    is_apple   = os_name == "Darwin" and arch in ("arm64", "aarch64")
    has_metal  = is_apple
    has_cuda   = bool(shutil.which("nvidia-smi"))
    has_rocm   = bool(shutil.which("rocm-smi"))
    has_vulkan = bool(shutil.which("vulkaninfo"))
    gpu_vendor = "none"
    vram_gb    = 0.0

    if is_apple:
        gpu_vendor = "apple"
        try:   vram_gb = int(_sh(["sysctl", "-n", "hw.memsize"])) / 1e9
        except Exception: vram_gb = ram_gb
    elif has_cuda:
        gpu_vendor, vram_gb = "nvidia", _nvidia_vram_gb()
    elif has_rocm:
        gpu_vendor, vram_gb = "amd",    _amd_vram_gb()

    eff = vram_gb if vram_gb > 0 else ram_gb * 0.60

    # Tier + model: ranked by quality that fits the budget.
    # nemotron-3-super-120b-a12b is the top recommendation at T3 ≥ 60 GB
    # (PinchBench 85.6%, best open agentic model as of 2026-03-13).
    if   eff <  4.0:
        tier, lbl, bk, mdl = 0, "T0·IoT",         "llamacpp", "qwen3:0.6b"
    elif eff <  8.0:
        tier, lbl, bk, mdl = 1, "T1·Consumer",    "ollama",   "qwen3:4b"
    elif eff < 14.0:
        tier, lbl, bk, mdl = 1, "T1·Consumer",    "ollama",   "qwen3:8b"
    elif eff < 24.0:
        tier, lbl = 2, "T2·Workstation"
        bk  = "mlx" if is_apple else "ollama"
        mdl = "qwen3:14b"
    elif eff < 40.0:
        tier, lbl = 2, "T2·Workstation"
        bk  = "mlx" if is_apple else "ollama"
        mdl = "qwen3:30b-a3b"   # 30B total / 3B active — near-32B quality at 8 GB VRAM
    elif eff < 60.0:
        tier, lbl = 3, "T3·Server"
        bk  = "vllm" if has_cuda else ("mlx" if is_apple else "ollama")
        mdl = "qwen3:32b"
    else:
        tier, lbl = 3, "T3·Server"
        bk  = "vllm" if has_cuda else ("mlx" if is_apple else "ollama")
        # nemotron-3-super-120b-a12b: 85.6% PinchBench; 1M ctx; 12B active / 120B total
        mdl = ("nvidia/nemotron-3-super-120b-a12b"
               if eff >= 80 else "qwen3:30b-a3b")

    return HardwareProfile(
        os_name=os_name, arch=arch, cpu_cores=cores, ram_gb=ram_gb,
        gpu_vendor=gpu_vendor, vram_gb=vram_gb,
        has_cuda=has_cuda, has_metal=has_metal,
        has_rocm=has_rocm, has_vulkan=has_vulkan,
        tier=tier, tier_label=lbl,
        # env-var overrides: zero hard-coded reliance
        backend=_BACKEND_OVERRIDE or bk,
        model=_MODEL_OVERRIDE   or mdl,
    )


def print_probe(hw: HardwareProfile) -> None:
    print(BANNER)
    tc    = [red, yellow, green, magenta][hw.tier]
    accel = " ".join(filter(None, [
        green("CUDA")   if hw.has_cuda   else "",
        green("Metal")  if hw.has_metal  else "",
        green("ROCm")   if hw.has_rocm   else "",
        green("Vulkan") if hw.has_vulkan else "",
    ])) or dim("CPU-only")
    rows = [
        ("Tier",    tc(hw.tier_label)),
        ("OS/Arch", f"{hw.os_name} / {hw.arch}"),
        ("CPU",     f"{hw.cpu_cores} cores  |  RAM {hw.ram_gb:.1f} GB"),
        ("GPU",     f"{hw.gpu_vendor.upper()} / {hw.vram_gb:.1f} GB"
                    if hw.vram_gb else hw.gpu_vendor),
        ("Accel",   accel),
        ("Budget",  f"{hw.effective_gb:.0f} GB effective"),
        ("Backend", cyan(hw.backend)),
        ("Model",   cyan(hw.model)),
    ]
    for k, v in rows:
        print(f"  {bold(k):<18}{v}")
    print()


# ══════════════════════════════════════════════════════════════════════════════
