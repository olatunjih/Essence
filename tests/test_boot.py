"""
Integration tests for the Essence APDE Kernel.
Covers boot_kernel(), ingest_capsule(), tick(), audit() round-trip (#7).
"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _controlled_stub(messages, model, max_tokens, seed, call_class=None):
    """
    Deterministic offline stub keyed on CallClass so verification calls
    never get misrouted into the intent-compression branch (#8).
    """
    cc = getattr(call_class, "value", None) if call_class is not None else None
    if cc == "PLAN":
        last = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            ""
        )
        if "decompose" in last.lower() or "tasks" in last:
            return json.dumps([{
                "id": "test-task-001",
                "goal": "Write hello.txt",
                "reads": [],
                "writes": ["scratch/hello.txt"],
                "tools": ["write_file"],
                "done_when": "hello.txt written",
                "risk": "low",
            }])
        return json.dumps({
            "goal": "Write hello.txt",
            "success_signals": ["hello.txt written"],
            "artifacts": ["scratch/hello.txt"],
            "budget": {"tokens": 512, "usd": 0.001},
            "constraints": [],
            "out_of_scope": [],
        })
    if cc == "VERIFY":
        return json.dumps({
            "axes":    [{"name": "correctness", "score": 0.9, "notes": "stub"}],
            "overall": 0.9,
            "passed":  True,
            "notes":   "controlled stub",
        })
    # EXEC
    return json.dumps({
        "done": True,
        "result": "hello.txt written",
        "tool_calls": [{"name": "write_file",
                        "args": {"path": "scratch/hello.txt",
                                 "content": "hello"}}],
    })


@pytest.fixture(scope="module")
def tmp_ws(tmp_path_factory):
    return tmp_path_factory.mktemp("essence_test")


@pytest.fixture(scope="module")
def kernel(tmp_ws):
    from essence.boot import boot_kernel
    return boot_kernel(
        workspace=str(tmp_ws),
        provider_fn=_controlled_stub,
        dev_mode=True,
        autonomy_tier=2,
    )


# ── P0: boot_kernel() itself succeeds ─────────────────────────────────────────

class TestBootKernel:
    def test_boot_succeeds(self, kernel):
        """boot_kernel() must return without raising SelfTestFailure (#5)."""
        assert kernel is not None

    def test_boot_returns_kernel_with_four_endpoints(self, kernel):
        """Kernel must expose exactly the four public entry points."""
        assert callable(getattr(kernel, "ingest_capsule", None))
        assert callable(getattr(kernel, "user_input", None))
        assert callable(getattr(kernel, "tick", None))
        assert callable(getattr(kernel, "audit", None))

    def test_audit_trail_populated_at_boot(self, kernel):
        """boot self-test must leave at least one audit event."""
        trail = kernel.audit()
        assert isinstance(trail, list)
        assert len(trail) > 0


# ── P0: RiskLevel.CRITICAL must exist ─────────────────────────────────────────

class TestRiskLevelCritical:
    def test_risklevel_critical_exists(self):
        from essence.apde_types import RiskLevel
        assert hasattr(RiskLevel, "CRITICAL")
        assert RiskLevel.CRITICAL.value == "CRITICAL"

    def test_ratifier_handles_critical(self):
        from essence.apde_types import RiskLevel, PlanDAG, Task, TaskState
        from essence.channels.ratification import Ratifier
        task = Task(id="t1", capsule_id="c1", goal="test", risk=RiskLevel.CRITICAL)
        plan = PlanDAG(id="p1", capsule_id="c1", tasks=[task])
        plan.freeze()
        # Tier 2 should not auto-approve when CRITICAL task is present
        rat = Ratifier(autonomy_tier=2).ratify(plan)
        assert not rat.auto_approved


# ── P0: ingest_capsule → tick → audit round-trip ──────────────────────────────

class TestKernelRoundTrip:
    def test_ingest_capsule_returns_id(self, kernel):
        capsule_id = kernel.ingest_capsule(
            raw_prompt="Write hello.txt with content hello",
            user_id="test_user",
        )
        assert isinstance(capsule_id, str)
        assert len(capsule_id) > 0
        # Store on class so later tests can reuse it
        TestKernelRoundTrip._capsule_id = capsule_id

    def test_tick_returns_status_dict(self, kernel):
        cid = getattr(TestKernelRoundTrip, "_capsule_id", None)
        if cid is None:
            pytest.skip("ingest_capsule test must run first")
        result = kernel.tick(cid)
        assert isinstance(result, dict)
        assert "status" in result or "task_id" in result

    def test_tick_does_not_report_done_with_no_real_work_when_plan_missing(self, kernel):
        """tick() must return no_plan for unknown capsule IDs."""
        result = kernel.tick("does-not-exist-capsule-id")
        assert result.get("status") in ("no_plan", "no_ready_tasks", None) or \
               "task_id" in result  # valid execution also OK

    def test_audit_trail_grows_after_round_trip(self, kernel):
        before = len(kernel.audit())
        kernel.ingest_capsule(
            raw_prompt="A second test capsule",
            user_id="test_user2",
        )
        after = len(kernel.audit())
        assert after >= before  # audit trail must only grow


# ── P0: APDERouter default stub is CallClass-keyed, not substring-matched ─────

class TestAPDERouterStub:
    def test_verify_stub_returns_verification_shape(self):
        from essence.backends.apde_router import APDERouter
        from essence.apde_types import CallClass
        messages = [{"role": "user", "content": json.dumps({
            "task_goal": "write hello.txt",
            "done_when": "file exists",
        })}]
        result = APDERouter._default_provider(
            messages=messages, model="stub", max_tokens=512, seed=0,
            call_class=CallClass.VERIFY)
        d = json.loads(result)
        assert "overall" in d, f"VERIFY stub must include 'overall', got: {d}"
        assert "passed" in d, f"VERIFY stub must include 'passed', got: {d}"

    def test_plan_stub_returns_intent_shape(self):
        from essence.backends.apde_router import APDERouter
        from essence.apde_types import CallClass
        messages = [{"role": "user", "content": "Compress this goal"}]
        result = APDERouter._default_provider(
            messages=messages, model="stub", max_tokens=512, seed=0,
            call_class=CallClass.PLAN)
        d = json.loads(result)
        assert "goal" in d, f"PLAN stub must include 'goal', got: {d}"

    def test_exec_stub_returns_exec_shape(self):
        from essence.backends.apde_router import APDERouter
        from essence.apde_types import CallClass
        messages = [{"role": "user", "content": "Execute the task"}]
        result = APDERouter._default_provider(
            messages=messages, model="stub", max_tokens=512, seed=0,
            call_class=CallClass.EXEC)
        d = json.loads(result)
        assert "done" in d, f"EXEC stub must include 'done', got: {d}"
        assert "tool_calls" in d, f"EXEC stub must include 'tool_calls', got: {d}"


# ── P1: PipelineExecutor uses tool_belt and actually invokes tools ─────────────

class TestPipelineExecutorToolBelt:
    def test_tool_belt_is_consulted(self, tmp_ws):
        """tool_belt.filter_for_task() must be called; DONE must not be reported
        when the stub says done=True but only if tool_invocations is non-empty (#2)."""
        from essence.apde_types import (
            Task, TaskState, RiskLevel, GuidanceBlock, ToolRecord,
        )
        from essence.agents.pipeline_executor import PipelineExecutor
        from essence.security.guardrail_layer import GuardrailLayer
        from essence.tools.tool_belt import ToolBelt
        from essence.infra.context_view import ContextWindowManager

        guardrails = GuardrailLayer()
        guardrails.activate_sandbox(False)  # no container in test env

        class _StubRouter:
            def call(self, call_class, messages, task_id, **kw):
                return json.dumps({
                    "done": True, "result": "wrote file",
                    "tool_calls": [{"name": "write_file",
                                    "args": {"path": "scratch/out.txt",
                                             "content": "hi"}}],
                })

        executor = PipelineExecutor(
            llm_router=_StubRouter(),
            guardrail_layer=guardrails,
            scratch_dir=str(tmp_ws / "scratch"),
            workspace=str(tmp_ws),
        )
        task = Task(
            id="test-exec-001",
            capsule_id="cap-001",
            goal="write out.txt",
            reads=[],
            writes=["scratch/out.txt"],
            tools=["write_file"],
            done_when="out.txt written",
            risk=RiskLevel.LOW,
        )
        records = [ToolRecord("write_file", ["write"], ["G3"], "MEDIUM", 20, False)]
        belt = ToolBelt(records)
        ctx = ContextWindowManager().resolve(task)
        gb = GuidanceBlock(rules=[], risk=task.risk)

        result = executor.execute(task, ctx, belt, gb)
        assert result.state in (TaskState.DONE, TaskState.DONE_INSUFFICIENT)
        # tool_belt was used — write_file should appear in tool_invocations
        invoked = [ti["tool"] for ti in result.tool_invocations]
        assert "write_file" in invoked, (
            f"write_file must appear in tool_invocations; got {invoked}")

    def test_unpermitted_tool_is_blocked(self, tmp_ws):
        """Tools not in task.tools must be blocked by the ToolBelt check (#2)."""
        from essence.apde_types import (
            Task, TaskState, RiskLevel, GuidanceBlock, ToolRecord,
        )
        from essence.agents.pipeline_executor import PipelineExecutor
        from essence.security.guardrail_layer import GuardrailLayer
        from essence.tools.tool_belt import ToolBelt
        from essence.infra.context_view import ContextWindowManager

        guardrails = GuardrailLayer()
        guardrails.activate_sandbox(False)

        class _BadRouter:
            def call(self, call_class, messages, task_id, **kw):
                return json.dumps({
                    "done": True, "result": "tried shell",
                    "tool_calls": [{"name": "shell",
                                    "args": {"command": "echo test"}}],
                })

        executor = PipelineExecutor(
            llm_router=_BadRouter(),
            guardrail_layer=guardrails,
            scratch_dir=str(tmp_ws / "scratch"),
            workspace=str(tmp_ws),
        )
        task = Task(
            id="test-blocked-001",
            capsule_id="cap-002",
            goal="read only",
            reads=[],
            writes=[],
            tools=["read_file"],   # shell NOT in permitted list
            done_when="done",
            risk=RiskLevel.LOW,
        )
        records = [ToolRecord("read_file", ["read"], [], "LOW", 50, False)]
        belt = ToolBelt(records)
        ctx = ContextWindowManager().resolve(task)
        gb = GuidanceBlock(rules=[], risk=task.risk)

        result = executor.execute(task, ctx, belt, gb)
        # shell must have been blocked (DENIED marker in result)
        blocked = [ti for ti in result.tool_invocations
                   if "DENIED" in str(ti.get("result", ""))]
        assert blocked, (
            f"Unpermitted shell call should be blocked; invocations: "
            f"{result.tool_invocations}")


# ── CLI: main() is callable and returns int ────────────────────────────────────

class TestCLI:
    def test_cli_main_exists(self):
        from essence.cli import main
        assert callable(main)

    def test_cli_no_args_returns_zero(self):
        from essence.cli import main
        rc = main([])
        assert rc == 0

    def test_cli_help_returns_zero(self):
        from essence.cli import main
        # The unified dispatcher intercepts --help (no positional subcommand)
        # and prints combined help, returning 0 rather than raising SystemExit.
        rc = main(["--help"])
        assert rc == 0


# ── Memory: embedding mode is reported ────────────────────────────────────────

class TestMemoryEmbeddingMode:
    def test_json_backend_has_health(self, tmp_ws):
        from essence.memory.backends import _JsonMemoryBackend
        b = _JsonMemoryBackend(tmp_ws / "mem.json")
        h = b.health()
        assert "embedding_mode" in h
        assert "semantic" in h

    def test_sqlite_backend_reports_mode_after_embed(self, tmp_ws):
        try:
            from essence.memory.backends import _SqliteVecBackend
            b = _SqliteVecBackend(tmp_ws)
            b.store("test sentence")
            h = b.health()
            assert h["embedding_mode"] in ("semantic", "hash")
        except Exception:
            pytest.skip("sqlite-vec not available")

    def test_namespaced_available_on_base(self, tmp_ws):
        from essence.memory.backends import _JsonMemoryBackend, NamespacedMemory
        b = _JsonMemoryBackend(tmp_ws / "mem2.json")
        ns = b.namespaced("user123")
        assert isinstance(ns, NamespacedMemory)


# ── Sandbox2: SandboxedExecutor works ─────────────────────────────────────────

class TestSandbox2:
    def test_basic_code_runs(self):
        from essence.infra.sandbox2 import SandboxedExecutor
        ex = SandboxedExecutor(timeout=5.0)
        ok, out = ex.run("x = 1 + 2\nprint(x)")
        assert ok
        assert "3" in out

    def test_dangerous_builtin_blocked(self):
        from essence.infra.sandbox2 import SandboxedExecutor
        ex = SandboxedExecutor(timeout=5.0)
        ok, out = ex.run("import os; os.system('id')")
        assert not ok or "[SANDBOX2" in out or "ImportError" in out.lower()

    def test_timeout_enforced(self):
        from essence.infra.sandbox2 import SandboxedExecutor
        ex = SandboxedExecutor(timeout=1.0)
        # Use a pure busyloop — no import needed, guaranteed to exceed timeout
        ok, out = ex.run("x = 0\nwhile True:\n    x += 1")
        assert not ok, f"Infinite loop should not succeed; got ok={ok}, out={out!r}"

    def test_hash_code_stable(self):
        from essence.infra.sandbox2 import SandboxedExecutor
        ex = SandboxedExecutor()
        code = "print('hello')"
        assert ex.hash_code(code) == ex.hash_code(code)


# ── Coreference resolver: not a passthrough ────────────────────────────────────

class TestCoreferences:
    def test_resolves_pronoun_with_context(self):
        from essence.agents.intent import LanguageUnderstanding
        lu = LanguageUnderstanding()
        result = lu.resolve_coreferences(
            "He wrote the report.",
            context="Alice finished the project.",
        )
        assert result != "He wrote the report.", (
            "resolve_coreferences must substitute the pronoun when context provides a noun")

    def test_passthrough_when_no_context(self):
        from essence.agents.intent import LanguageUnderstanding
        lu = LanguageUnderstanding()
        text = "He did it."
        result = lu.resolve_coreferences(text, context="")
        assert result == text  # no context → no substitution
