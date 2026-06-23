"""SpecialistAgent + specialist pool builder."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.core.registry import REGISTRY  # noqa: F401  [real source bug: used in build_specialist_pool()/_pick() without import]

# SPECIALIST AGENTS
# ══════════════════════════════════════════════════════════════════════════════
# The OrchestratorAgent decomposes tasks and routes each WorkflowStep to the
# cheapest SpecialistAgent that can handle it.  This implements the
# "micro-specialists" pattern:  Planner (deep reasoning, large model),
# Executor (shallow, small model), Critic (medium model), etc.

class AgentRole(_enum.Enum):
    PLANNER              = "planner"
    RESEARCHER           = "researcher"
    CODER                = "coder"
    DATA_ANALYST         = "data_analyst"
    EXECUTOR             = "executor"
    CRITIC               = "critic"
    CONSOLIDATOR         = "consolidator"
    VERIFIER             = "verifier"
    SLEEPTIME_CONSOLIDATOR = "sleeptime_consolidator"  # largest model, runs at idle


@_dc.dataclass
class AgentCapabilities:
    """Tracks agent capabilities, skills, cost, and reliability."""
    role:        AgentRole
    skills:      list[str] = _dc.field(default_factory=list)
    cost_tier:   int       = 1  # 1=Low, 2=Med, 3=High
    reliability: float     = 1.0 # 0.0 to 1.0

class AgentRegistry:
    """Tracks available agents and their metadata."""
    def __init__(self):
        self._registry: dict[AgentRole, AgentCapabilities] = {}

    def register(self, capabilities: AgentCapabilities):
        self._registry[capabilities.role] = capabilities

    def get_capabilities(self, role: AgentRole) -> AgentCapabilities | None:
        return self._registry.get(role)

    def list_roles(self) -> list[AgentRole]:
        return list(self._registry.keys())

class AgentSelector:
    """Intelligently selects best agent(s) for a task based on requirements."""
    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    def select_best_role(self, task_description: str,
                         required_skills: list[str] | None = None) -> AgentRole:
        """Heuristic selector for the most appropriate role."""
        t = task_description.lower()
        if any(k in t for k in ("search", "find", "research", "lookup")):
            return AgentRole.RESEARCHER
        if any(k in t for k in ("code", "python", "script", "refactor")):
            return AgentRole.CODER
        if any(k in t for k in ("analyze", "data", "csv", "plot", "chart")):
            return AgentRole.DATA_ANALYST
        if any(k in t for k in ("plan", "breakdown", "steps")):
            return AgentRole.PLANNER
        return AgentRole.EXECUTOR

class TaskScheduler:
    """Manages priority-based task scheduling and deadline enforcement."""
    def __init__(self):
        self._queue: list[tuple[float, str, dict]] = [] # (priority_val, task_id, task_data)
        self._lock = threading.Lock()

    def schedule(self, task_id: str, task_data: dict, priority: str = "medium", deadline: float | None = None):
        """Adds a task to the priority queue."""
        priority_map = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        val = priority_map.get(priority.lower(), 2)
        with self._lock:
            # We use a simple list sorted by priority value.
            # In production, this would use heapq or a real task queue.
            self._queue.append((val, task_id, {**task_data, "deadline": deadline}))
            self._queue.sort(key=lambda x: x[0])

    def next_task(self) -> tuple[str, dict] | None:
        """Retrieves the next highest-priority task."""
        with self._lock:
            if not self._queue: return None
            _, tid, data = self._queue.pop(0)
            return tid, data

class ResultMerger:
    """Aggregates and synthesizes results from parallel agent executions."""
    def merge(self, results: list[dict]) -> dict:
        """Synthesizes multiple results into a single cohesive response."""
        if not results: return {}
        if len(results) == 1: return results[0]

        merged = {
            "synthesized_output": "",
            "individual_results": results,
            "status": "merged"
        }

        # Simple concatenation for baseline
        outputs = []
        for r in results:
            if "output" in r:
                outputs.append(str(r["output"]))
            elif "result" in r:
                outputs.append(str(r["result"]))

        merged["synthesized_output"] = "\n\n---\n\n".join(outputs)
        return merged

@_dc.dataclass
class SpecialistConfig:
    role:            AgentRole
    model:           str        # ollama_tag of model for this role
    thinking:        bool = False
    budget:          int  = 512
    system_prompt:   str  = ""


_SPECIALIST_PROMPTS: dict[AgentRole, str] = {
    AgentRole.PLANNER: (
        "You are a precise task planner. Break the user's request into numbered steps. "
        "Output ONLY a JSON array: "
        '[{"step":1,"action":"...","tool":"shell|read_file|write_file|'
        'python_exec|web_search|none","args":{}}]. No other text.'
    ),
    AgentRole.EXECUTOR: (
        "You are a precise executor. Complete the given action. "
        'Emit a JSON tool call {"tool":"name","args":{...}} or '
        '{"done":true,"result":"..."}. No other text outside JSON.'
    ),
    AgentRole.CRITIC: (
        "You are a CriticGate validator. Given a step action and its result, "
        "evaluate whether the step completed correctly against the tool constraints. "
        'Respond ONLY with JSON: {"pass": true/false, "category": "...", '
        '"evidence": "...", "fix_hint": "..."}. No other text.'
    ),
    AgentRole.CONSOLIDATOR: (
        "Distil the essential facts from this conversation into ≤400 tokens. "
        "Focus on facts needed to continue the task. Plain text only."
    ),
    AgentRole.SLEEPTIME_CONSOLIDATOR: (
        "You are a sleep-time memory agent. During idle periods you reorganise "
        "long-term memory: merge duplicate facts, remove outdated entries, extract "
        "durable user preferences, and update the UserProfile. "
        "Work through the provided memory dump and output a JSON object with two keys: "
        "'retain' (list of clean, deduplicated fact strings to keep) and "
        "'profile' (UserProfile fields to update: name, occupation, location, "
        "current_projects, preferences, goals, notes). "
        "Be aggressive about pruning — quality over quantity."
    ),
    AgentRole.VERIFIER: (
        "You are a factuality verifier. Given a claim and evidence context, "
        'respond ONLY with JSON: {"verdict": "verified"|"unverified"|"contradicted", '
        '"confidence": 0.0-1.0, "evidence": "brief quote or none"}.'
    ),
}


class SpecialistAgent:
    """
    Lightweight single-role agent.
    Each specialist runs the cheapest model appropriate for its role,
    thinking only when necessary (Planner), saving tokens everywhere else.
    """

    def __init__(self, cfg: SpecialistConfig, provider: Any) -> None:
        self.cfg      = cfg
        self.provider = provider

    def run(self, prompt: str) -> str:
        """Single LLM call with role-specific system prompt."""
        sys_prompt = self.cfg.system_prompt or _SPECIALIST_PROMPTS.get(
            self.cfg.role, "You are a helpful assistant.")
        out = ""
        for tok in self.provider.complete(
            [{"role": "system", "content": sys_prompt},
             {"role": "user",   "content": prompt}],
            model=self.cfg.model, stream=False,
            thinking=self.cfg.thinking, budget=self.cfg.budget,
        ):
            out += tok
    async def arun(self, prompt: str) -> str:
        """Async version of run()."""
        sys_prompt = self.cfg.system_prompt or _SPECIALIST_PROMPTS.get(
            self.cfg.role, "You are a helpful assistant.")
        out = ""
        async for tok in self.provider.acomplete(
            [{"role": "system", "content": sys_prompt},
             {"role": "user",   "content": prompt}],
            model=self.cfg.model, stream=False,
            thinking=self.cfg.thinking, budget=self.cfg.budget,
        ):
            out += tok
        return out.strip()


    async def arun(self, prompt: str) -> str:
        """Async version of run()."""
        sys_prompt = self.cfg.system_prompt or _SPECIALIST_PROMPTS.get(
            self.cfg.role, "You are a helpful assistant.")
        out = ""
        async for tok in self.provider.acomplete(
            [{"role": "system", "content": sys_prompt},
             {"role": "user",   "content": prompt}],
            model=self.cfg.model, stream=False,
            thinking=self.cfg.thinking, budget=self.cfg.budget,
        ):
            out += tok
        return out.strip()
def build_specialist_pool(hw: "HardwareProfile",
                           provider: Any) -> dict[AgentRole, SpecialistAgent]:
    """
    Build a pool of SpecialistAgents sized to the hardware tier.

    T0/T1: Planner uses best available; all others use smallest.
    T2:    Planner uses 14B+; others use 4-8B.
    T3:    Planner uses 70B+; Executor 14B; others 8B.
    """
    budget = hw.effective_gb * 0.85

    def _pick(min_vram: float, max_vram: float,
              prefer_thinking: bool = False) -> str:
        candidates = sorted(
            [m for m in REGISTRY
             if min_vram <= m.vram_q4_gb <= max_vram
             and m.vram_q4_gb <= budget
             and not m.requires_vlm
             and m.min_tier <= hw.tier],
            key=lambda m: (m.pinch, m.active_b), reverse=True,
        )
        if not candidates:
            return hw.model
        return candidates[0].ollama_tag

    if hw.tier >= 3:
        planner_mdl    = _pick(30, 999, True)
        executor_mdl   = _pick(8, 40)
        small_mdl      = _pick(3, 8)
    elif hw.tier == 2:
        planner_mdl    = _pick(9, 999, True)
        executor_mdl   = _pick(3, 12)
        small_mdl      = _pick(1, 5)
    else:  # T0/T1
        planner_mdl    = hw.model
        executor_mdl   = hw.model
        small_mdl      = hw.model

    pool: dict[AgentRole, SpecialistAgent] = {}
    specs: list[tuple[AgentRole, str, bool, int]] = [
        (AgentRole.PLANNER,               planner_mdl,  True,  1024),
        (AgentRole.EXECUTOR,              executor_mdl, False,  512),
        (AgentRole.CRITIC,                executor_mdl, False,  512),
        (AgentRole.CONSOLIDATOR,          small_mdl,    False,  256),
        (AgentRole.VERIFIER,              small_mdl,    False,  256),
        # Sleep-time agent uses the largest available model — latency doesn't matter.
        (AgentRole.SLEEPTIME_CONSOLIDATOR, planner_mdl,  True, 2048),
    ]
    for role, mdl, think, bgt in specs:
        pool[role] = SpecialistAgent(
            SpecialistConfig(role=role, model=mdl,
                             thinking=think, budget=bgt),
            provider)
    return pool


# ══════════════════════════════════════════════════════════════════════════════
