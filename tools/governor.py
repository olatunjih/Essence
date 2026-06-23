
"""
Resource Governor — sole authority granting tokens to the LLM router.
Wired as pre-call gate on the router (NN-8 step 8).
"""
from __future__ import annotations
import threading, time
from essence.apde_types import CallClass, TokenGrant, ResourceGovernorError

_CLASS_DEFAULTS = {
    CallClass.PLAN:   8192,
    CallClass.EXEC:   16384,
    CallClass.VERIFY: 4096,
}


class ResourceGovernor:
    """
    Token budget governor.
    Maintains per-task token usage and enforces per-class limits.
    The router must call grant() before every LLM call; failure raises.
    """

    def __init__(self,
                 plan_max:   int = 8192,
                 exec_max:   int = 16384,
                 verify_max: int = 4096,
                 global_max: int = 0) -> None:
        self._limits = {
            CallClass.PLAN:   plan_max,
            CallClass.EXEC:   exec_max,
            CallClass.VERIFY: verify_max,
        }
        self._global_max = global_max
        self._usage:  dict[str, int] = {}   # task_id -> total tokens used
        self._global: int            = 0
        self._lock = threading.Lock()

    def grant(self, call_class: CallClass, task_id: str,
              requested_tokens: int) -> TokenGrant:
        """
        Request a token grant.
        Raises ResourceGovernorError if the request exceeds limits.
        Returns a TokenGrant on success.
        """
        if call_class not in self._limits:
            raise ResourceGovernorError(
                f"Unknown call class: {call_class}")

        class_limit = self._limits[call_class]

        with self._lock:
            task_used = self._usage.get(task_id, 0)

            if requested_tokens > class_limit:
                raise ResourceGovernorError(
                    f"Token request {requested_tokens} exceeds {call_class.value} "
                    f"limit {class_limit} for task {task_id}")

            if self._global_max > 0:
                if self._global + requested_tokens > self._global_max:
                    raise ResourceGovernorError(
                        f"Global token budget exhausted: "
                        f"{self._global}/{self._global_max}")

            self._usage[task_id] = task_used + requested_tokens
            self._global        += requested_tokens

        return TokenGrant(
            call_class=call_class,
            max_tokens=requested_tokens,
            task_id=task_id,
            granted_at=time.time(),
        )

    def record_actual(self, task_id: str, actual_tokens: int) -> None:
        """Record actual tokens used (may differ from grant)."""
        with self._lock:
            self._usage[task_id] = self._usage.get(task_id, 0) + actual_tokens

    def usage(self, task_id: str) -> int:
        return self._usage.get(task_id, 0)

    def remaining(self, call_class: CallClass) -> int:
        if self._global_max <= 0:
            return self._limits.get(call_class, 0)
        return max(0, self._global_max - self._global)
