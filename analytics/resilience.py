"""ResilienceLayer backbone (self-falsification, contradiction, drift)."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# Resilience Layer RESILIENCE BACKBONE
# ══════════════════════════════════════════════════════════════════════════════

class ResilienceLayer:
    """The analytical immune system of Analytics Engine."""

    @staticmethod
    def r1_data_immunity(df: Any, layer_context: str) -> dict:
        """R1: Data Resilience (Data Immune System)."""
        import pandas as pd
        import numpy as np
        from scipy import stats

        report = {"trust_score": 1.0, "concerns": [], "fabrication_probability": 0.0}

        # 1. Benford's Law check for numeric fabrication
        num_cols = df.select_dtypes("number").columns
        for col in num_cols:
            try:
                first_digits = df[col].astype(str).str.lstrip('0.').str[0].dropna()
                first_digits = first_digits[first_digits.str.isdigit()].astype(int)
                if not first_digits.empty:
                    observed = first_digits.value_counts(normalize=True).sort_index()
                    expected = np.log10(1 + 1/np.arange(1, 10))
                    # Simple chi-square like comparison
                    diff = np.sum(np.abs(observed.reindex(range(1, 10), fill_value=0).values - expected))
                    if diff > 0.5:
                        report["concerns"].append(f"Benford's Law deviation in {col}")
                        report["trust_score"] *= 0.9
            except Exception:
                pass

        # 2. Missing Data classification
        null_pattern = df.isnull().any(axis=1)
        if null_pattern.any():
            report["concerns"].append("Missing data detected")

        return report

    @staticmethod
    def r2_compute_resilience(op_name: str, values: Any = None) -> Any:
        """R2: Compute Resilience."""
        # 1. NUMERICAL_STABILITY_GUARDS
        def safe_div(a, b): return a / max(abs(b), 1e-10)

        if op_name == "safe_div": return safe_div

        # 2. RESOURCE_GUARDING (Simplified)
        if isinstance(values, (list, range)) and len(values) > 50000:
             return "auto_sample"

        return None

    @staticmethod
    def r3_contradiction_engine(findings: list[Finding]) -> list[dict]:
        """R3: Contradiction Engine."""
        contradictions = []
        for i, f1 in enumerate(findings):
            for f2 in findings[i+1:]:
                # Statistical Paradox Detection
                if f1.category == f2.category and f1.sub_category == f2.sub_category:
                    if f1.title == f2.title and f1.description != f2.description:
                        contradictions.append({
                            "type": "STATISTICAL_PARADOX",
                            "finding_a": f1.id, "finding_b": f2.id,
                            "logic": "Opposing conclusions from similar tests"
                        })
                # Simpson's Paradox Detection
                if f1.simpson_risk or f2.simpson_risk:
                     contradictions.append({
                            "type": "SCALE_DEPENDENT",
                            "finding_a": f1.id, "finding_b": f2.id,
                            "logic": "Simpson's Paradox likely"
                        })
        return contradictions

    def analytical_critic_gate(self, action: str, result: str, spine: AnalyticalStateBus) -> list[dict]:
        """v29: Statistically grounded critique against Analytics Engine findings."""
        failures = []
        # Claim extraction (Simplified: looking for numbers).
        # [Fixed: the previous regex r"[\d.]+" matches bare "." (sentence-ending
        # periods, ellipses, abbreviations like "etc.") with no digits at all,
        # and float(".") crashes — this broke the critic gate on virtually any
        # normal LLM response. Require at least one digit, and guard float()
        # defensively since LLM output is never fully trusted input.]
        claims = re.findall(r"\d+\.?\d*|\.\d+", result)
        for claim in claims:
            try:
                val = float(claim)
            except ValueError:
                continue
            for f in spine.active_findings:
                if f.category == "CORRELATIONS" and val > 0.9:
                    if f.calibrated_confidence < 0.7:
                        failures.append({
                            "type": "StatisticalOverclaim",
                            "claim": claim,
                            "finding_id": f.id,
                            "reason": f"Claimed strong correlation ({claim}) but Analytics Engine confidence is low ({f.calibrated_confidence})"
                        })
        return failures

    @staticmethod
    def r4_temporal_resilience(dataset_id: str,
                               current_fingerprint: DatasetFingerprint) -> list[dict]:
        """R4: Temporal Resilience.

        Implements three mechanisms:
        1. CONCEPT_DRIFT_DETECTOR — flags when sparsity or entropy profile
           deviate significantly from the long-term baseline stored in the
           process-level fingerprint cache.
        2. EVIDENCE_DECAY_FUNCTION — attaches a remaining evidence lifetime
           (in hours) based on the dataset's row-count bucket and sparsity.
           Dense, large datasets have longer evidence lifespans.
        3. EDGE_LIFESPAN_TRACKING — checks which cross-column edges (from
           the relationship graph) have evidence that is more than one
           reporting cycle old and should be re-validated.
        """
        _fingerprint_cache: dict = getattr(
            ResilienceLayer, "_fp_cache", {})
        setattr(ResilienceLayer, "_fp_cache", _fingerprint_cache)

        alerts: list[dict] = []
        now = time.time()

        # ── 1. CONCEPT DRIFT DETECTOR ──────────────────────────────────────
        prev = _fingerprint_cache.get(dataset_id)
        if prev is not None:
            # Sparsity drift
            sparsity_delta = abs(current_fingerprint.sparsity - prev.get("sparsity", 0.0))
            if sparsity_delta > 0.1:
                alerts.append({
                    "type": "CONCEPT_DRIFT",
                    "sub_type": "SPARSITY_SHIFT",
                    "dataset_id": dataset_id,
                    "delta": round(sparsity_delta, 4),
                    "severity": "high" if sparsity_delta > 0.25 else "moderate",
                    "recommendation": "Re-run full Analytics Engine analysis; sparsity regime changed.",
                })

            # Entropy profile drift — compare mean entropy
            prev_entropy = prev.get("mean_entropy", 0.0)
            curr_entropy = (
                sum(current_fingerprint.entropy_profile.values()) /
                max(len(current_fingerprint.entropy_profile), 1)
            )
            entropy_delta = abs(curr_entropy - prev_entropy)
            if entropy_delta > 0.3:
                alerts.append({
                    "type": "CONCEPT_DRIFT",
                    "sub_type": "ENTROPY_SHIFT",
                    "dataset_id": dataset_id,
                    "prev_mean_entropy": round(prev_entropy, 4),
                    "curr_mean_entropy": round(curr_entropy, 4),
                    "delta": round(entropy_delta, 4),
                    "severity": "high" if entropy_delta > 0.6 else "moderate",
                    "recommendation": (
                        "Distribution regime changed; prior findings may no longer apply."),
                })

            # Row count bucket change
            if prev.get("n_rows_bucket") != current_fingerprint.n_rows_bucket:
                alerts.append({
                    "type": "CONCEPT_DRIFT",
                    "sub_type": "SIZE_REGIME_CHANGE",
                    "dataset_id": dataset_id,
                    "from": prev.get("n_rows_bucket"),
                    "to": current_fingerprint.n_rows_bucket,
                    "severity": "low",
                    "recommendation": "Recalibrate confidence thresholds for new data volume.",
                })

        # ── 2. EVIDENCE DECAY FUNCTION ─────────────────────────────────────
        # Lifespan heuristic: large+dense data = long lifetime; small+sparse = short.
        lifespan_hours_map = {
            ("large",  False): 168,   # 7 days
            ("large",  True):  48,    # 2 days (sparse large dataset decays faster)
            ("medium", False): 72,
            ("medium", True):  24,
            ("small",  False): 24,
            ("small",  True):  6,
        }
        is_sparse = current_fingerprint.sparsity > 0.2
        lifespan_h = lifespan_hours_map.get(
            (current_fingerprint.n_rows_bucket, is_sparse), 24)

        last_analysed = _fingerprint_cache.get(f"{dataset_id}_last_ts", now)
        age_h = (now - last_analysed) / 3600.0
        remaining_h = max(0.0, lifespan_h - age_h)
        alerts.append({
            "type": "EVIDENCE_DECAY",
            "dataset_id": dataset_id,
            "lifespan_hours": lifespan_h,
            "age_hours": round(age_h, 2),
            "remaining_hours": round(remaining_h, 2),
            "expired": remaining_h == 0.0,
            "recommendation": (
                "Evidence expired; schedule re-analysis." if remaining_h == 0.0
                else f"Evidence valid for ~{remaining_h:.1f}h more."),
        })

        # ── 3. EDGE LIFESPAN TRACKING ──────────────────────────────────────
        edge_registry: dict = getattr(ResilienceLayer, "_edge_registry", {})
        setattr(ResilienceLayer, "_edge_registry", edge_registry)

        stale_edges: list[str] = []
        for edge_key, edge_ts in list(edge_registry.items()):
            if edge_key.startswith(dataset_id + ":"):
                edge_age_h = (now - edge_ts) / 3600.0
                if edge_age_h > lifespan_h:
                    stale_edges.append(edge_key.split(":", 1)[1])
        if stale_edges:
            alerts.append({
                "type": "STALE_EDGES",
                "dataset_id": dataset_id,
                "stale_edge_count": len(stale_edges),
                "stale_edges": stale_edges[:10],
                "recommendation": "Re-validate cross-column edges listed above.",
            })

        # ── Update cache ───────────────────────────────────────────────────
        _fingerprint_cache[dataset_id] = {
            "sparsity": current_fingerprint.sparsity,
            "mean_entropy": (
                sum(current_fingerprint.entropy_profile.values()) /
                max(len(current_fingerprint.entropy_profile), 1)
            ),
            "n_rows_bucket": current_fingerprint.n_rows_bucket,
        }
        _fingerprint_cache[f"{dataset_id}_last_ts"] = now

        return alerts

    @staticmethod
    def r5_self_healing(failures: list[dict]) -> list[dict]:
        """R5: Failure Resilience / Self-Healing.

        Generates concrete retry plans for every known failure type:
          - resource_limit    → retry with progressive sampling (10% → 25% → 50%)
          - import_error      → recommend pip install + graceful degradation
          - data_load_error   → alternative file format or encoding fallback
          - numerical_error   → clip + standardise before retry
          - timeout           → reduce wave depth and rerun lighter layers only
          - insufficient_data → suggest aggregation or resampling
          - StatisticalOverclaim → lower confidence threshold and re-evaluate
          - unknown / fallback → log and continue with partial results
        """
        retry_plans: list[dict] = []

        for fail in failures:
            ftype = fail.get("type", "unknown")
            ctx: dict = {k: v for k, v in fail.items() if k != "type"}

            if ftype == "resource_limit":
                # Progressive sampling ladder
                sample_tried = ctx.get("sample_ratio", 1.0)
                next_ratio = max(0.05, sample_tried * 0.5)
                retry_plans.append({
                    "failure_type": ftype,
                    "action": "retry_with_sampling",
                    "params": {
                        "sample_ratio": next_ratio,
                        "random_state": 42,
                        "stratify": True,
                    },
                    "rationale": (
                        f"Dataset too large; retry with {int(next_ratio*100)}% sample."),
                })

            elif ftype == "import_error":
                missing_lib = ctx.get("library", "unknown")
                fallback_lib = {
                    "sklearn": "scipy",
                    "statsmodels": "numpy",
                    "pandas": None,
                    "numpy": None,
                }.get(missing_lib, None)
                plan: dict = {
                    "failure_type": ftype,
                    "action": "graceful_degradation",
                    "params": {"missing_library": missing_lib},
                    "rationale": f"{missing_lib} not installed; degrading to fallback.",
                }
                if fallback_lib:
                    plan["params"]["fallback_library"] = fallback_lib
                else:
                    plan["action"] = "abort_layer"
                    plan["rationale"] = (
                        f"{missing_lib} is required with no fallback; skip this layer.")
                retry_plans.append(plan)

            elif ftype == "data_load_error":
                retry_plans.append({
                    "failure_type": ftype,
                    "action": "retry_with_encoding_fallback",
                    "params": {
                        "encodings": ["utf-8", "latin-1", "cp1252"],
                        "na_values": ["NA", "N/A", "null", "NULL", "none", ""],
                        "low_memory": False,
                    },
                    "rationale": "File load failed; retry with alternate encodings.",
                })

            elif ftype == "numerical_error":
                retry_plans.append({
                    "failure_type": ftype,
                    "action": "retry_with_numerical_guards",
                    "params": {
                        "clip_percentile": 99.5,
                        "standardise": True,
                        "fill_inf": True,
                        "fill_nan_with": "median",
                    },
                    "rationale": (
                        "Numerical instability detected; clip + standardise before retry."),
                })

            elif ftype == "timeout":
                current_wave = ctx.get("wave_depth", 3)
                retry_plans.append({
                    "failure_type": ftype,
                    "action": "retry_with_reduced_wave",
                    "params": {
                        "max_wave": max(1, current_wave - 1),
                        "layers": ["L0_L1"],   # minimal layer set
                        "skip_layers": ["L6"],  # skip expensive predictive layer
                    },
                    "rationale": (
                        f"Wave-{current_wave} timed out; retry at wave-{max(1, current_wave-1)}."),
                })

            elif ftype == "insufficient_data":
                retry_plans.append({
                    "failure_type": ftype,
                    "action": "retry_with_aggregation",
                    "params": {
                        "min_rows_required": 20,
                        "aggregation_strategy": "weekly" if ctx.get("temporal") else "bin",
                        "interpolate": True,
                    },
                    "rationale": (
                        "Not enough rows; aggregate or resample to meet minimum threshold."),
                })

            elif ftype == "StatisticalOverclaim":
                retry_plans.append({
                    "failure_type": ftype,
                    "action": "recalibrate_confidence",
                    "params": {
                        "finding_id": ctx.get("finding_id"),
                        "new_confidence_cap": 0.70,
                        "require_p_value": True,
                        "apply_bonferroni": True,
                    },
                    "rationale": (
                        "Overclaimed statistical strength; cap confidence and apply "
                        "Bonferroni correction."),
                })

            else:
                # Unknown / generic failure — log and continue
                retry_plans.append({
                    "failure_type": ftype,
                    "action": "log_and_continue",
                    "params": {"context": ctx, "partial_results": True},
                    "rationale": (
                        f"Unrecognised failure '{ftype}'; log details and return "
                        "partial results rather than aborting."),
                })

        return retry_plans

# ══════════════════════════════════════════════════════════════════════════════
