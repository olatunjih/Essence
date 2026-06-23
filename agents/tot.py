"""TreeOfThought multi-path planning + CognitiveReflector.
v29.0: Analytics Engine reconnaissance + Learning Engine empirical branch scoring; see wiring doc """
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

from essence.analytics.spine import get_analytical_spine  # noqa: F401
# TREE-OF-THOUGHT REASONING
# ══════════════════════════════════════════════════════════════════════════════

_TOT_BRANCHES = int(os.environ.get("Essence_TOT_BRANCHES", "3"))
_TOT_ENABLED  = os.environ.get("Essence_TOT_ENABLED", "0") == "1"
_LEARNING_SHRINK_RUNS = int(os.environ.get("ESSENCE_LEARNING_SHRINK_RUNS", "5"))


def _genesis_empirical_score(genesis: "LearningEngine | None", arch_id: str) -> float:
    """empirical branch grounding: read real avg_impact for this archetype from
    Learning Engine Tier-2 memory (essence/prism/genesis.py LearningEngine.archetype_library),
    rather than a fixed constant. Confidence is shrunk toward the 0.5 prior until
    ESSENCE_LEARNING_SHRINK_RUNS observations have accumulated (cold-start safe)."""
    if not genesis or not arch_id:
        return 0.5
    lib = getattr(genesis, "archetype_library", {}).get(arch_id)
    if not lib or lib.get("runs", 0) <= 0:
        return 0.5
    runs = lib["runs"]
    shrinkage = min(1.0, runs / max(_LEARNING_SHRINK_RUNS, 1))
    avg_impact = lib.get("avg_impact", 0.5)
    return 0.5 + shrinkage * (avg_impact - 0.5)


@_dc.dataclass
class ThoughtBranch:
    "A single candidate plan with its score and rationale."
    branch_id:   int
    plan:        list[dict]
    score:       float = 0.0
    rationale:   str   = ""
    tokens_used: int   = 0

class TreeOfThought:
    """
    Multi-path reasoning: expand N plans, score, prune, select best.
    Falls back to single-plan when _TOT_ENABLED is False.
    """
    SCORE_THRESHOLD = 0.4

    def __init__(self, provider: Any, model: str, n_branches: int = 0):
        self._prov = provider; self._model = model
        self._branches = n_branches or _TOT_BRANCHES

    def expand(self, goal: str, context: str = "", plan_sys: str = "") -> list[ThoughtBranch]:
        "Generate N candidate plans via separate LLM calls."
        branches: list[ThoughtBranch] = []
        sys_prompt = plan_sys or _PLAN_SYS
        for i in range(self._branches):
            diversity = "" if i == 0 else f" Branch {i+1}/{self._branches} — use a DIFFERENT approach."
            messages = [{"role": "system", "content": sys_prompt + diversity},
                        {"role": "user", "content": goal}]
            if context:
                messages.insert(1, {"role": "system", "content": context})
            try:
                raw = "".join(self._prov.complete(messages, model=self._model, stream=True, thinking=True))
                plan = self._parse_plan(raw)
                branches.append(ThoughtBranch(branch_id=i, plan=plan, tokens_used=len(raw) // 4))
            except Exception as _e:
                log.debug("tot_expand_error", extra={"branch": i, "error": str(_e)[:80]})
        return branches or [ThoughtBranch(branch_id=0, plan=[])]

    async def aexpand(self, goal: str, context: str = "", plan_sys: str = "") -> list[ThoughtBranch]:
        "Async version of expand()."
        sys_prompt = plan_sys or _PLAN_SYS

        async def _ex_one(i):
            diversity = "" if i == 0 else f" Branch {i+1}/{self._branches} — use a DIFFERENT approach."
            messages = [{"role": "system", "content": sys_prompt + diversity},
                        {"role": "user", "content": goal}]
            if context:
                messages.insert(1, {"role": "system", "content": context})
            try:
                _toks = []
                async for tok in self._prov.acomplete(messages, model=self._model, stream=True, thinking=True):
                    _toks.append(tok)
                raw = "".join(_toks)
                plan = self._parse_plan(raw)
                return ThoughtBranch(branch_id=i, plan=plan, tokens_used=len(raw) // 4)
            except Exception:
                return ThoughtBranch(branch_id=i, plan=[])

        tasks = [asyncio.create_task(_ex_one(i)) for i in range(self._branches)]
        branches = await asyncio.gather(*tasks)
        return list(branches)

    def score(self, branches: list[ThoughtBranch], goal: str, genesis: LearningEngine | None = None, arch_id: str = "") -> list[ThoughtBranch]:
        "Score each branch via a cheap judge call + empirical grounding."
        judge_sys = ('Rate this plan 0.0-1.0. Consider completeness, efficiency, safety. '
                     'Respond ONLY with JSON: {"score": 0.0-1.0, "rationale": "..."}')
        for branch in branches:
            if not branch.plan:
                branch.score = 0.0; continue
            plan_text = json.dumps(branch.plan, indent=2)[:2000]
            user_msg = "Goal: " + goal + "\nPlan:\n" + plan_text
            messages = [{"role": "system", "content": judge_sys},
                        {"role": "user", "content": user_msg}]
            try:
                raw = "".join(self._prov.complete(messages, model=self._model, stream=True, thinking=False))
                m = re.search(r'"score"\s*:\s*([\d.]+)', raw)
                llm_score = float(m.group(1)) if m else 0.5
                m2 = re.search(r'"rationale"\s*:\s*"([^"]*)"', raw)
                branch.rationale = m2.group(1) if m2 else ""

                # Empirical Branch Scoring — grounded in Learning Engine archetype_library,
                # shrunk toward the 0.5 prior until enough runs have accumulated (cold-start safe).
                branch.score = 0.7 * llm_score + 0.3 * _genesis_empirical_score(genesis, arch_id)
            except Exception:
                branch.score = 0.5
        return branches

    async def ascore(self, branches: list[ThoughtBranch], goal: str, genesis: LearningEngine | None = None, arch_id: str = "") -> list[ThoughtBranch]:
        "Async version of score() with empirical grounding."
        judge_sys = ('Rate this plan 0.0-1.0. Consider completeness, efficiency, safety. '
                     'Respond ONLY with JSON: {"score": 0.0-1.0, "rationale": "..."}')

        async def _sc_one(branch):
            if not branch.plan:
                branch.score = 0.0; return branch
            plan_text = json.dumps(branch.plan, indent=2)[:2000]
            user_msg = "Goal: " + goal + "\nPlan:\n" + plan_text
            messages = [{"role": "system", "content": judge_sys},
                        {"role": "user", "content": user_msg}]
            try:
                _toks = []
                async for tok in self._prov.acomplete(messages, model=self._model, stream=True, thinking=False):
                    _toks.append(tok)
                raw = "".join(_toks)
                m = re.search(r'"score"\s*:\s*([\d.]+)', raw)
                llm_score = float(m.group(1)) if m else 0.5
                m2 = re.search(r'"rationale"\s*:\s*"([^"]*)"', raw)
                branch.rationale = m2.group(1) if m2 else ""

                branch.score = 0.7 * llm_score + 0.3 * _genesis_empirical_score(genesis, arch_id)
            except Exception:
                branch.score = 0.5
            return branch

        tasks = [asyncio.create_task(_sc_one(b)) for b in branches]
        await asyncio.gather(*tasks)
        return branches

    def prune(self, branches: list[ThoughtBranch]) -> list[ThoughtBranch]:
        surviving = [b for b in branches if b.score >= self.SCORE_THRESHOLD]
        return surviving or [max(branches, key=lambda b: b.score)]

    def select(self, branches: list[ThoughtBranch]) -> ThoughtBranch:
        return max(branches, key=lambda b: b.score)

    def reason(self, goal: str, context: str = "", plan_sys: str = "", genesis: LearningEngine | None = None, arch_id: str = "") -> tuple[list[dict], ThoughtBranch]:
        "Full pipeline: expand -> score -> prune -> select."
        if not _TOT_ENABLED or self._branches <= 1:
            branches = self.expand(goal, context, plan_sys)
            b = branches[0] if branches else ThoughtBranch(0, []); b.score = 1.0
            return b.plan, b
        branches = self.expand(goal, context, plan_sys)
        branches = self.score(branches, goal, genesis=genesis, arch_id=arch_id)
        branches = self.prune(branches)
        winner = self.select(branches)
        log.info("tot_selected", extra={"branches": len(branches), "winner": winner.branch_id, "score": winner.score})
        return winner.plan, winner

    async def areason(self, goal: str, context: str = "", plan_sys: str = "", genesis: LearningEngine | None = None, arch_id: str = "") -> tuple[list[dict], ThoughtBranch]:
        """Async version of reason()."""
        if not _TOT_ENABLED or self._branches <= 1:
            branches = await self.aexpand(goal, context, plan_sys)
            b = branches[0] if branches else ThoughtBranch(0, []); b.score = 1.0
            return b.plan, b
        branches = await self.aexpand(goal, context, plan_sys)
        branches = await self.ascore(branches, goal, genesis=genesis, arch_id=arch_id)
        branches = self.prune(branches)
        winner = self.select(branches)
        return winner.plan, winner

    @staticmethod
    def _parse_plan(raw: str) -> list[dict]:
        m = re.search(r'\[\s*\{.*\}\s*\]', raw, re.DOTALL)
        if m:
            try: return json.loads(m.group(0))
            except json.JSONDecodeError: pass
        return []


# ══════════════════════════════════════════════════════════════════════════════
# AGENT CORE  (Nemotron + TaskPipeline + task management design)
# ══════════════════════════════════════════════════════════════════════════════
# Architecture:
#   Planner   (thinking ON)   — decomposes task to JSON step plan
#   Executor                  — dispatches tools or LLM
#   Critic    (CriticGate)  — validates against constraints; one retry
#
# "Thinking tax" mitigation from Nemotron 3 Super design:
#   → thinking ON only for Planner; OFF for Executor (saves tokens on subtasks)
#   → multi-agent systems generate 15× tokens of standard chat; don't reason
#     at every subtask
#
# Memory distillation fires at `memory_window` turns to stay inside ctx window.
# SOUL.md + IDENTITY.md + MEMORY.md + skills injected into every system prompt.

_PLAN_SYS = (
    "You are a precise task planner. Break the user's request into numbered steps. "
    "Output ONLY a JSON array (no markdown, no prose): "
    '[{"step":1,"action":"...","tool":"shell|read_file|write_file|'
    'python_exec|web_search|heartbeat_add|none","args":{}}]. '
    "No other text."
)
_EXEC_SYS = (
    "You are a precise executor. Complete the given action using tools when appropriate. "
    'Emit a JSON tool call {"tool":"name","args":{...}} or completion '
    '{"done":true,"result":"..."}. No other text outside JSON.'
)
_MEM_SYS = (
    "Distil the essential facts from this conversation into ≤400 tokens. "
    "Focus on facts needed to continue the task. Plain text only."
)



# ══════════════════════════════════════════════════════════════════════════════
