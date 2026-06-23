""" — crash-restart continuity (journal + replay)."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# CRASH-RESTART CONTINUITY  — resume in-flight workflows
# ══════════════════════════════════════════════════════════════════════════════
# On startup, scans workspace/workflows/ for workflows in "running" status
# and queues them for resumption.
# ENV:  Essence_AUTO_RESUME=1   Auto-resume without prompting (server mode)

_AUTO_RESUME = os.environ.get("Essence_AUTO_RESUME", "0") == "1"


def startup_recovery(engine: "WorkflowEngine",
                     resume_fn: "Callable[[str], str] | None" = None) -> list[str]:
    """
    Scan for in-flight workflows and optionally resume them.
    Returns list of task_ids that were resumed (or queued for resumption).
    Called once at server/agent startup.
    """
    resumed = []
    for wf_id in engine.list_workflows():
        state = engine.resume(wf_id)
        if state is None:
            continue
        if state.status.value in ("running", "pending"):
            if _AUTO_RESUME and resume_fn:
                try:
                    log.info("workflow_auto_resume",
                             extra={"task_id": wf_id, "task": state.task[:80]})
                    resume_fn(state.task)
                    resumed.append(wf_id)
                except Exception as _e:
                    log.warning("workflow_resume_error",
                                extra={"task_id": wf_id, "error": str(_e)[:120]})
            else:
                log.info("workflow_resume_available",
                         extra={"task_id": wf_id, "task": state.task[:80],
                                "hint": "set Essence_AUTO_RESUME=1 to auto-resume"})
    return resumed


# ══════════════════════════════════════════════════════════════════════════════
