"""ValueAlignmentOracle +  NeuromorphicEventBus."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# VALUE ALIGNMENT ORACLE
# ══════════════════════════════════════════════════════════════════════════════

class ValueAlignmentOracle:
    """Dedicated LLM judge for ethical constraint verification."""
    PRINCIPLES = [
        "Safety: No harm to humans, animals, or critical infrastructure.",
        "Privacy: No exposure of PII, credentials, or private data.",
        "Integrity: No deceptive, manipulative, or illegal actions.",
        "Alignment: Strict adherence to user intent and safety constraints."
    ]

    def __init__(self, provider: Any, model: str):
        self.provider = provider
        self.model = model

    async def check(self, task: str, plan: list[dict], context: str = "") -> tuple[bool, str]:
        """Verify proposed plan against ethical principles."""
        prompt = (
            f"Evaluate this multi-agent plan against the following ethical principles:\n" +
            "\n".join(f"- {p}" for p in self.PRINCIPLES) +
            f"\n\nTask: {task}\nPlan: {json.dumps(plan)}\nContext: {context}\n\n"
            "Respond ONLY with a JSON object: {\"safe\": bool, \"rationale\": \"...\"}"
        )
        try:
            raw = ""
            async for tok in self.provider.acomplete(
                [{"role": "user", "content": prompt}],
                model=self.model, stream=False, thinking=False
            ): raw += tok
            d = json.loads(re.sub(r"```[a-zA-Z]*", "", raw).strip())
            return d.get("safe", False), d.get("rationale", "Blocked: oracle parse failure.")  # fail-safe default
        except Exception as e:
            if _ALIGNMENT_FAILOPEN:
                return True, f"Safety check failed ({e}), assuming safe (fail-open)."
            return False, f"Safety check unavailable: {e}. Plan blocked pending manual review."

# ══════════════════════════════════════════════════════════════════════════════

# NEUROMORPHIC EVENT BUS
# ══════════════════════════════════════════════════════════════════════════════

class NeuromorphicEventBus:
    """Async-native signal distributor for internal coordination."""
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = {}

    def subscribe(self, event_type: str, handler: Callable):
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    async def emit(self, event_type: str, data: Any):
        if event_type not in self._subscribers: return
        tasks = []
        for handler in self._subscribers[event_type]:
            try:
                if asyncio.iscoroutinefunction(handler):
                    tasks.append(asyncio.create_task(handler(data)))
                else:
                    handler(data)
            except Exception as e:
                log.debug("event_handler_error", extra={"type": event_type, "error": str(e)})
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)


# ══════════════════════════════════════════════════════════════════════════════
