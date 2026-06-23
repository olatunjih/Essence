"""LearningEngine: self-learning strategy optimizer."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# Learning Engine SELF-LEARNING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class LearningEngine:
    """The recursive self-learning engine of Analytics Engine (Tier 2 Memory)."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.archetype_library = {} # arch_id -> stats
        self.anomaly_atlas = []     # List of confirmed anomalies
        self.strategy_pool = {}     # arch_id -> optimal_params
        self._path = workspace / "memory" / "prism_genesis.json"
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self.archetype_library = data.get("archetypes", {})
                self.anomaly_atlas = data.get("anomalies", [])
            except Exception:
                pass

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({
                "archetypes": self.archetype_library,
                "anomalies": self.anomaly_atlas
            }, indent=2), encoding="utf-8")
        except Exception:
            pass

    def update_pattern_memory(self, fingerprint: DatasetFingerprint, findings: list[Finding]):
        """4.1 Pattern Memory: Archive patterns and discover archetypes."""
        arch_id = f"{fingerprint.n_rows_bucket}_{fingerprint.n_cols_bucket}_{fingerprint.temporal}"
        if arch_id not in self.archetype_library:
            self.archetype_library[arch_id] = {"runs": 0, "avg_impact": 0.0}

        lib = self.archetype_library[arch_id]
        lib["runs"] += 1
        avg_impact = sum(f.impact for f in findings) / max(len(findings), 1)
        lib["avg_impact"] = (lib["avg_impact"] * (lib["runs"]-1) + avg_impact) / lib["runs"]

        for f in findings:
            if f.category == "ANOMALIES":
                self.anomaly_atlas.append({
                    "type": f.sub_category,
                    "context": arch_id,
                    "severity": f.impact
                })

    def strategy_optimizer(self, arch_id: str) -> dict:
        """4.2 Strategy Optimizer: Adaptive layer prioritization."""
        # Multi-armed bandit for test selection (Simplified)
        return {
            "mode": "EXPLORATION" if arch_id not in self.archetype_library else "EXPLOITATION",
            "layer_order": ["L0", "L1", "L1.5", "L2", "L3", "L6", "L7"],
            "epsilon": 0.1
        }

    def confidence_calibrator(self, findings: list[Finding]) -> list[Finding]:
        """4.3 Confidence Calibrator: Isotonic correction of raw scores."""
        # Simplified: linear scaling based on evidence
        for f in findings:
            if f.p_value is not None:
                # p-value based boost
                if f.p_value < 0.01: f.calibrated_confidence = min(1.0, f.confidence * 1.1)
                elif f.p_value > 0.05: f.calibrated_confidence = f.confidence * 0.8
            else:
                f.calibrated_confidence = f.confidence
        return findings

    def evolution_governor(self, mutations: dict) -> dict:
        """4.4 Evolution Governor: Bounded recursive self-improvement.

        Enforces four safety constraints (Analytics Engine Axiom 5):
        1. MAX_CHANGE_RATE  — no single mutation may alter a value by more
           than 20 % of its current magnitude in one cycle.
        2. MONOTONICITY_GUARD — numeric mutations that flip sign (positive→
           negative or vice versa) are clamped to zero-crossing ±ε to prevent
           oscillatory instability.
        3. SAFETY_FLOOR — critical metrics (e.g. trust_score, min_confidence)
           are never allowed to drop below their documented lower bounds.
        4. AUDIT_TRAIL — every applied mutation is recorded with its original
           value, proposed value, clamped value, and the rule that fired.
        """
        SAFETY_FLOORS: dict[str, float] = {
            "trust_score": 0.1,
            "min_confidence": 0.3,
            "epsilon": 0.01,
            "sample_ratio": 0.05,
        }
        MAX_CHANGE_RATE = 0.20   # 20 % per cycle
        SIGN_EPSILON = 1e-6

        guarded: dict = {}
        audit: list[dict] = []

        for key, proposed in mutations.items():
            # Only enforce numeric mutations
            if not isinstance(proposed, (int, float)):
                guarded[key] = proposed
                continue

            # Look up current value from strategy_pool or archetype_library
            current: float | None = None
            for pool in (self.strategy_pool, self.archetype_library):
                if key in pool:
                    raw = pool[key]
                    if isinstance(raw, (int, float)):
                        current = float(raw)
                    elif isinstance(raw, dict):
                        for sub_key in ("value", "avg_impact", "epsilon"):
                            if sub_key in raw and isinstance(raw[sub_key], (int, float)):
                                current = float(raw[sub_key])
                                break
                    break

            proposed_f = float(proposed)
            clamped_f = proposed_f
            rule_fired: str = "none"

            if current is not None:
                # 1. MAX_CHANGE_RATE clamp
                max_delta = abs(current) * MAX_CHANGE_RATE
                if max_delta > 0 and abs(proposed_f - current) > max_delta:
                    direction = 1.0 if proposed_f > current else -1.0
                    clamped_f = current + direction * max_delta
                    rule_fired = "MAX_CHANGE_RATE"

                # 2. MONOTONICITY_GUARD — prevent sign flip
                if current * clamped_f < 0:
                    clamped_f = SIGN_EPSILON * (1.0 if proposed_f > 0 else -1.0)
                    rule_fired = "MONOTONICITY_GUARD"

            # 3. SAFETY_FLOOR
            floor = SAFETY_FLOORS.get(key)
            if floor is not None and clamped_f < floor:
                clamped_f = floor
                rule_fired = "SAFETY_FLOOR"

            guarded[key] = clamped_f

            audit.append({
                "key": key,
                "current": current,
                "proposed": proposed_f,
                "applied": clamped_f,
                "rule": rule_fired,
                "changed": abs(clamped_f - proposed_f) > 1e-9,
            })

        # Persist audit trail
        if audit:
            audit_path = self.workspace / "memory" / "prism_genesis_audit.jsonl"
            try:
                audit_path.parent.mkdir(parents=True, exist_ok=True)
                with audit_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "ts": time.time(),
                        "cycle_audit": audit,
                    }) + "\n")
            except Exception:
                pass

        return guarded

# ══════════════════════════════════════════════════════════════════════════════
