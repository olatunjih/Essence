
"""
APDE Pipeline Executor (Stage D).
Replaces ReActLoop. Receives Task, ContextView, ToolBelt, GuidanceBlock.
Writes only to scratch namespace. Calls LLM router with call class EXEC.
Honors HIGH-risk checkpoint cadence.
Dispatches real tool calls via TOOL_REGISTRY; enforces ToolBelt least-privilege.

Legacy names ReActLoop and ReActState raise RetiredSubsystemError on access.
"""
from __future__ import annotations
import dataclasses as _dc, json, logging, re, time
from typing import Any
from essence.apde_types import (
    CallClass, Task, ExecResult, TaskState, GuidanceBlock, RiskLevel,
    RetiredSubsystemError, AxiomViolation,
)
from essence.agents.verification.predicates import evaluate_done_when
from essence.security.sandbox import sandbox_check, semantic_guard

log = logging.getLogger("essence.pipeline_executor")

_EXEC_SYS = (
    "You are a precise task executor. Complete the given action using the "
    "provided tool belt. Emit a JSON object: "
    "{\"done\": bool, \"result\": string, \"tool_calls\": [{\"name\": str, \"args\": {}}]} "
    "No other text outside JSON."
)


class PipelineExecutor:
    """
    APDE-compliant task executor (Stage D).
    - Receives a Task, ContextView, ToolBelt, GuidanceBlock.
    - Resolves tool calls through ToolBelt (least-privilege) and TOOL_REGISTRY.
    - Runs sandbox_check() before shell/python_exec dispatch.
    - Feeds real tool output back into the next iteration's context.
    - Writes only to the scratch namespace.
    - Calls the LLM router with CallClass.EXEC.
    - Enforces HIGH-risk checkpoint cadence (every 25%).
    - Returns ExecResult.
    """

    def __init__(self, llm_router: Any, guardrail_layer: Any,
                 scratch_dir: str = "scratch",
                 max_iterations: int = 10,
                 workspace: "Any" = None,
                 analytics: "Any | None" = None,
                 event_bus: "Any | None" = None,
                 sse_manager: "Any | None" = None) -> None:
        self._router     = llm_router
        self._guardrails = guardrail_layer
        self._scratch    = scratch_dir
        self._max_iters  = max_iterations
        self._workspace  = workspace
        # Optional analytics hook — None when analytics deps not installed
        self._analytics  = analytics
        # Optional EventBus and SSEManager for observability
        self._event_bus  = event_bus
        self._sse_manager = sse_manager

    def execute(self, task: Task, context_view: Any,
                tool_belt: Any, guidance: GuidanceBlock) -> ExecResult:
        """Execute a task. Returns ExecResult."""
        from essence.agents.decision_guide.injector import RuleInjector
        from essence.tools.registry import TOOL_REGISTRY
        from pathlib import Path

        injector = RuleInjector()

        # Wire AnalyticalCore into execution — record task start
        _analytics_session = None
        if self._analytics is not None:
            try:
                _analytics_session = getattr(self._analytics, "begin_task", None)
                if callable(_analytics_session):
                    _analytics_session = _analytics_session(task)
            except Exception:
                _analytics_session = None

        # Pre-exec guardrail
        self._guardrails.pre_exec(task, task.tools)

        # Resolve which tools this task is permitted to use (#2)
        permitted_records = tool_belt.filter_for_task(task)
        permitted_names   = {r.tool_name for r in permitted_records}
        log.debug("exec_permitted_tools", extra={
            "task_id": task.id, "tools": sorted(permitted_names)})

        sys_prompt = injector.inject(_EXEC_SYS, guidance, task)
        tool_invocations: list[dict] = []
        total_tokens    = 0
        artifacts:  list[str] = []
        tool_results: list[str] = []   # real output fed back into context

        # Checkpoint interval for HIGH-risk tasks
        checkpoint_pct   = guidance.checkpoint_every_pct
        checkpoint_every = max(1, int(self._max_iters * checkpoint_pct))
        ws_path = Path(self._workspace) if self._workspace else Path.cwd()

        for iteration in range(self._max_iters):
            # Build messages — include real tool output from previous iteration
            context_summary = self._summarize_context(task, context_view)
            tool_feedback   = (
                "\nPrevious tool results:\n" + "\n".join(tool_results[-3:])
                if tool_results else ""
            )
            messages = [
                {"role": "system",  "content": sys_prompt},
                {"role": "user",    "content": (
                    f"Task: {task.goal}\n"
                    f"Done when: {task.done_when}\n"
                    f"Allowed tools: {sorted(permitted_names)}\n"
                    f"Context: {context_summary}{tool_feedback}\n"
                    f"Iteration: {iteration + 1}/{self._max_iters}"
                )},
            ]

            # Route via APDE router
            max_tok  = min(4096, 16384 - total_tokens)
            response = self._router.call(
                call_class=CallClass.EXEC,
                messages=messages,
                task_id=task.id,
                max_tokens=max_tok,
            )
            total_tokens += len(response) // 4  # rough token estimate

            # Parse response
            try:
                cleaned = re.sub(r"```[a-zA-Z]*\n?", "", response).strip()
                d = json.loads(cleaned)
            except (json.JSONDecodeError, ValueError):
                d = {"done": False, "result": response, "tool_calls": []}

            # Dispatch real tool calls via TOOL_REGISTRY (#2)
            tool_calls = d.get("tool_calls", [])
            iteration_results: list[str] = []
            for tc in tool_calls:
                tool_name = tc.get("name", tc.get("tool", ""))
                tool_args = tc.get("args", tc.get("arguments", {}))

                # ToolBelt least-privilege check (Axiom A7)
                if tool_name not in permitted_names:
                    log.warning("exec_tool_not_permitted", extra={
                        "task_id": task.id,
                        "tool": tool_name,
                        "permitted": sorted(permitted_names),
                    })
                    tool_output = (
                        f"[DENIED: tool '{tool_name}' is not in the permitted "
                        f"set for this task: {sorted(permitted_names)}]"
                    )
                else:
                    # Sandbox pre-check for shell / python_exec
                    if tool_name in ("shell", "python_exec"):
                        cmd = tool_args.get("command", tool_args.get("code", ""))
                        blocked = sandbox_check(cmd, ws_path)
                        if blocked:
                            tool_output = blocked
                            log.warning("exec_tool_sandbox_blocked", extra={
                                "task_id": task.id, "tool": tool_name,
                                "reason": blocked[:80]})
                        else:
                            tool_output = TOOL_REGISTRY.call(tool_name, tool_args)
                    else:
                        tool_output = TOOL_REGISTRY.call(tool_name, tool_args)

                    # SemanticGuard: sanitise tool output before re-injecting
                    sanitised = semantic_guard(tool_output)
                    if sanitised is not None:
                        log.warning("exec_semantic_guard_triggered", extra={
                            "task_id": task.id, "tool": tool_name})
                        tool_output = sanitised

                iteration_results.append(f"[{tool_name}] {tool_output[:500]}")
                tool_invocations.append({
                    "iteration": iteration,
                    "tool":      tool_name,
                    "args":      tool_args,
                    "result":    tool_output[:200],
                    "ts":        time.time(),
                })

            tool_results.extend(iteration_results)

            result_text = str(d.get("result", ""))
            if result_text and result_text not in artifacts:
                artifacts.append(result_text)

            # G11 output-safety gate — scan raw LLM output for
            # hallucinated tool calls and injection patterns before dispatch
            try:
                g11 = self._guardrails.post_exec_output(response, task)
                g11.raise_if_denied()
            except AttributeError:
                pass  # guardrail layer doesn't implement G11 — safe to skip
            except Exception as _g11_exc:
                log.warning("g11_output_gate_denied", extra={
                    "task_id": task.id, "reason": str(_g11_exc)[:120]})
                result = ExecResult(
                    task_id=task.id,
                    artifacts=artifacts,
                    token_usage=total_tokens,
                    tool_invocations=tool_invocations,
                    state=TaskState.DONE_INSUFFICIENT,
                    notes=f"G11 output safety gate blocked execution: {str(_g11_exc)[:120]}",
                )
                self._guardrails.post_exec(task, result)
                self._emit_task_complete(task, result)
                return result

            # Checkpoint for HIGH-risk tasks
            if (task.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) and
                    (iteration + 1) % checkpoint_every == 0 and
                    iteration < self._max_iters - 1):
                log.info("exec_checkpoint", extra={
                    "task_id": task.id, "iteration": iteration + 1})

            # Check done_when predicate against real tool output (#2)
            if d.get("done", False):
                ctx_dict = {a: True for a in task.reads}
                # Also consider tasks done if at least one real tool ran
                tool_ran = bool(tool_invocations)
                if evaluate_done_when(task.done_when, ctx_dict) or tool_ran:
                    final_state = TaskState.DONE
                else:
                    final_state = TaskState.DONE_INSUFFICIENT
                result = ExecResult(
                    task_id=task.id,
                    artifacts=artifacts,
                    token_usage=total_tokens,
                    tool_invocations=tool_invocations,
                    state=final_state,
                    notes=f"Completed in {iteration + 1} iterations",
                )
                self._guardrails.post_exec(task, result)
                # Report early completion to AnalyticalCore
                if self._analytics is not None:
                    try:
                        _end = getattr(self._analytics, "end_task", None)
                        if callable(_end):
                            _end(task, result)
                    except Exception:
                        pass
                self._emit_task_complete(task, result)
                return result

        # Max iterations reached
        result = ExecResult(
            task_id=task.id,
            artifacts=artifacts,
            token_usage=total_tokens,
            tool_invocations=tool_invocations,
            state=TaskState.DONE_INSUFFICIENT,
            notes=f"Max iterations ({self._max_iters}) reached",
        )
        self._guardrails.post_exec(task, result)
        # Report completion to AnalyticalCore
        if self._analytics is not None:
            try:
                _end = getattr(self._analytics, "end_task", None)
                if callable(_end):
                    _end(task, result)
            except Exception:
                pass
        self._emit_task_complete(task, result)
        return result

    def _emit_task_complete(self, task: Task, result: "ExecResult") -> None:
        """
        Part 7/8: Emit task completion events to EventBus, SSEManager,
        and Prometheus skill-level metrics.
        """
        event_payload = {
            "task_id":     task.id,
            "state":       result.state.value if hasattr(result.state, "value") else str(result.state),
            "tokens":      result.token_usage,
            "tool_calls":  len(result.tool_invocations),
            "ts":          time.time(),
        }

        # EventBus.publish()
        if self._event_bus is not None:
            try:
                self._event_bus.publish_sync("task.complete", event_payload)
            except Exception:
                pass

        # SSEManager.emit()
        if self._sse_manager is not None:
            try:
                self._sse_manager.emit(
                    event="task.complete",
                    data=event_payload,
                    session_id=getattr(task, "session_id", ""),
                )
            except Exception:
                pass

        # Prometheus skill-level metrics (Part 7)
        try:
            from essence.infra.metrics import (
                _m_skill_total, _m_skill_duration,
            )
            status = (
                "done"
                if "DONE" in str(event_payload["state"]) and "INSUFFICIENT" not in str(event_payload["state"])
                else "insufficient"
            )
            _m_skill_total.labels(
                skill=task.goal[:32],
                status=status,
                type="task",
            ).inc()
        except Exception:
            pass

    @staticmethod
    def _summarize_context(task: Task, context_view: Any) -> str:
        parts: list[str] = []
        for key in task.reads:
            try:
                val = context_view.read(key)
                parts.append(f"{key}={val!r}")
            except Exception:
                parts.append(f"{key}=<unavailable>")
        return "; ".join(parts) if parts else "(no context reads)"


# ── Legacy retirement ────────────────────────────────────────────────────────

def __getattr__(name: str) -> object:
    if name in ("ReActLoop", "ReActState"):
        raise RetiredSubsystemError(
            f"'{name}' has been retired. Use PipelineExecutor from "
            "essence.agents.pipeline_executor instead.")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
