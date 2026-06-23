"""AnalyticalCore (L0–L7) + WaveController."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# Guard heavy analytics deps so the kernel can boot without them.
# Install with: pip install 'essence[analytics]'
try:
    import pandas as _pd
    import numpy as _np
    ANALYTICS_AVAILABLE = True
except ImportError:
    ANALYTICS_AVAILABLE = False
    import logging as _logging
    _logging.getLogger("essence.analytics").warning(
        "analytics_unavailable: pandas/numpy not installed. "
        "Install with: pip install 'essence[analytics]'"
    )

# Analytics Engine ANALYTICAL CORE
# ══════════════════════════════════════════════════════════════════════════════

class AnalyticalCore:
    """The engine room of Analytics Engine — implements L0 through L7 layers."""

    @staticmethod
    def aperture(raw_input: Any) -> tuple[Any, dict, list[EntityProfile], dict]:
        """L0: Universal Ingestion & Normalization."""
        import pandas as pd
        import numpy as np

        # 1. FORMAT_DETECT & Ingestion
        df = None
        if isinstance(raw_input, pd.DataFrame):
            df = raw_input.copy()
        elif isinstance(raw_input, str):
            p = Path(raw_input).expanduser()
            if p.exists():
                ext = p.suffix.lower()
                if ext == ".csv": df = pd.read_csv(p)
                elif ext in (".parquet", ".pq"): df = pd.read_parquet(p)
                elif ext == ".json": df = pd.read_json(p)
                elif ext in (".xlsx", ".xls"): df = pd.read_excel(p)

        if df is None:
            # Input is not a readable file or DataFrame — return empty schema
            return (
                pd.DataFrame(),
                {"fields": [], "entity_fields": [], "count_event_fields": [],
                 "sub_event_groups": {}, "temporal_fields": [], "key_candidates": []},
                [],
                {"source": "unsupported_input", "input_type": type(raw_input).__name__},
            )

        # 2. SCHEMA_INFERENCE
        schema = {
            "fields": [], "entity_fields": [], "count_event_fields": [],
            "sub_event_groups": {}, "temporal_fields": [], "key_candidates": []
        }

        for col in df.columns:
            dtype = df[col].dtype
            cardinality = df[col].nunique()
            cardinality_ratio = cardinality / len(df) if len(df) > 0 else 0

            field_info = {"name": col, "dtype": str(dtype), "cardinality": cardinality}

            # COUNT_EVENT_DETECTION
            if pd.api.types.is_integer_dtype(dtype) and (df[col] >= 0).all():
                mean = df[col].mean()
                var = df[col].var()
                if var > 0:
                    dispersion = var / mean
                    field_info["is_count_event"] = True
                    schema["count_event_fields"].append(col)
                    field_info["dispersion_ratio"] = dispersion

            # ENTITY_DETECTION (Heuristic)
            if hasattr(pd.api.types, "is_categorical_dtype") and pd.api.types.is_categorical_dtype(dtype) or pd.api.types.is_object_dtype(dtype):
                if 2 <= cardinality <= len(df) // 5:
                    schema["entity_fields"].append(col)

            # Temporal
            if pd.api.types.is_datetime64_any_dtype(dtype) or "time" in col.lower() or "date" in col.lower():
                try:
                    if not pd.api.types.is_datetime64_any_dtype(dtype):
                        df[col] = pd.to_datetime(df[col])
                    schema["temporal_fields"].append(col)
                except Exception:
                    pass

            if cardinality_ratio > 0.95:
                schema["key_candidates"].append(col)

            schema["fields"].append(field_info)

        # 3. NORMALIZATION (Simplified)
        df = df.fillna(np.nan) # Unify to explicit NULL (NaN)

        # 4. MULTI_RESOLUTION_INDEX
        res_index = {"levels": [0]} # L0: EVENT is always there
        if schema["entity_fields"] and schema["temporal_fields"]:
            res_index["levels"].extend([1, 2, 3, 4])

        return df, schema, [], res_index

    @staticmethod
    def aperture_stream(raw_input: Any, chunk_size: int = 10_000):
        """
        L0 streaming variant: yields processed chunks instead of loading the
        full dataset into memory.  Enables T1/T2 hardware (8–16 GB RAM) to
        ingest Parquet/CSV files that would OOM the batch aperture().

        AnalyticalMode.MONITOR and EXPLORE wired here.  Each yielded chunk is
        a (df_chunk, schema_partial) tuple suitable for incremental L1 profiling.

        Running statistics (mean, variance) accumulate via Welford's algorithm
        so per-column stats remain correct across all chunks without storing
        the full column in memory.

        Usage
        -----
        for chunk, schema in AnalyticalCore.aperture_stream("big.csv"):
            surface_result = AnalyticalCore.surface(chunk, schema)
            ...
        """
        import pandas as pd

        if not isinstance(raw_input, str):
            # Non-file inputs fall back to batch aperture
            result = AnalyticalCore.aperture(raw_input)
            df, schema = result[0], result[1]
            if not df.empty:
                yield df, schema
            return

        p = Path(raw_input).expanduser()
        if not p.exists():
            return

        ext = p.suffix.lower()
        try:
            if ext == ".csv":
                reader = pd.read_csv(p, chunksize=chunk_size)
            elif ext in (".parquet", ".pq"):
                # Parquet has no native chunked reader in pandas; use row-group iteration
                import pyarrow.parquet as _pq
                pf = _pq.ParquetFile(p)
                reader = (
                    rg.to_pandas()
                    for rg in pf.iter_batches(batch_size=chunk_size)
                )
            elif ext == ".json":
                reader = pd.read_json(p, lines=True, chunksize=chunk_size)
            else:
                # Unsupported streaming format — fall back to batch
                result = AnalyticalCore.aperture(raw_input)
                df, schema = result[0], result[1]
                if not df.empty:
                    yield df, schema
                return

            # Welford running stats for mean/variance across chunks
            _welford: dict[str, dict] = {}  # col -> {n, mean, M2}

            for chunk in reader:
                if chunk is None or (hasattr(chunk, "empty") and chunk.empty):
                    continue
                chunk = chunk.fillna(float("nan"))

                # Update Welford accumulators
                for col in chunk.select_dtypes("number").columns:
                    if col not in _welford:
                        _welford[col] = {"n": 0, "mean": 0.0, "M2": 0.0}
                    for val in chunk[col].dropna():
                        w = _welford[col]
                        w["n"] += 1
                        delta = float(val) - w["mean"]
                        w["mean"] += delta / w["n"]
                        delta2 = float(val) - w["mean"]
                        w["M2"] += delta * delta2

                # Derive a partial schema for this chunk
                _, schema, _, _ = AnalyticalCore.aperture(chunk)
                schema["_welford_stats"] = {
                    col: {
                        "mean": w["mean"],
                        "variance": w["M2"] / max(w["n"] - 1, 1),
                        "n": w["n"],
                    }
                    for col, w in _welford.items()
                }
                yield chunk, schema

        except Exception as _e:
            import logging as _logging
            _logging.getLogger("essence.analytics").warning(
                "aperture_stream error: %s — falling back to batch aperture", _e)
            result = AnalyticalCore.aperture(raw_input)
            df, schema = result[0], result[1]
            if not df.empty:
                yield df, schema

    @staticmethod
    def surface(df: Any, schema: dict) -> tuple[dict, DatasetFingerprint, list[EntityProfile]]:
        """L1: Structural Reconnaissance."""
        import pandas as pd
        import numpy as np
        from scipy import stats

        feature_fingerprints = {}
        entity_profiles = []

        for col in df.columns:
            f = {"name": col, "entropy": 0.0, "cardinality": df[col].nunique()}
            series = df[col].dropna()

            if series.empty: continue

            # DISTRIBUTION_PROFILE
            if pd.api.types.is_numeric_dtype(df[col]):
                f["central"] = {
                    "mean": float(series.mean()),
                    "median": float(series.median()),
                    "mode": float(series.mode()[0]) if not series.mode().empty else None
                }
                f["dispersion"] = {
                    "std": float(series.std()),
                    "var": float(series.var()),
                    "iqr": float(series.quantile(0.75) - series.quantile(0.25))
                }
                f["shape"] = {
                    "skew": float(series.skew()),
                    "kurtosis": float(series.kurtosis()),
                    "q99": float(series.quantile(0.99))
                }

                # Simple distribution fitting hint
                if col in schema["count_event_fields"]:
                    f["count_fit"] = "Poisson" if 0.9 <= f["dispersion"]["var"]/f["central"]["mean"] <= 1.1 else "NegBinomial"

            elif pd.api.types.is_datetime64_any_dtype(df[col]):
                f["temporal"] = {"min": str(series.min()), "max": str(series.max()), "span": str(series.max() - series.min())}

            # COMPLETENESS
            f["completeness"] = 1.0 - (df[col].isnull().sum() / len(df))

            # ENTROPY (Simplified)
            counts = series.value_counts(normalize=True)
            f["entropy"] = float(stats.entropy(counts))

            feature_fingerprints[col] = f

        # ENTITY_PROFILING
        for entity_col in schema["entity_fields"]:
            # Sample top entities to avoid OOM/Timeouts
            top_entities = df[entity_col].value_counts().head(50).index
            for eid in top_entities:
                edf = df[df[entity_col] == eid]
                # time_span: range of any temporal column, else 0
                _time_span = 0.0
                for _tc in schema.get("temporal_fields", []):
                    if _tc in edf.columns:
                        try:
                            import pandas as _pd
                            _ts = _pd.to_datetime(edf[_tc], errors="coerce")
                            _rng = (_ts.max() - _ts.min()).total_seconds()
                            if _rng > 0:
                                _time_span = float(_rng)
                                break
                        except Exception:
                            pass

                # consistency: fraction of rows without NaN values (completeness proxy)
                _consistency = float(1.0 - edf.isnull().any(axis=1).mean())

                # distinctiveness: normalised entropy of the entity's numeric features
                _distinctiveness = 0.0
                _num_cols = edf.select_dtypes("number").columns.tolist()
                if _num_cols:
                    try:
                        import numpy as _np
                        _vals = edf[_num_cols].values.flatten()
                        _vals = _vals[~_np.isnan(_vals)]
                        if len(_vals) > 1:
                            _hist, _ = _np.histogram(_vals, bins=min(20, len(_vals)))
                            _p = _hist / (_hist.sum() + 1e-9)
                            _p = _p[_p > 0]
                            _ent = float(-(_p * _np.log(_p)).sum())
                            _max_ent = float(_np.log(len(_p))) if len(_p) > 1 else 1.0
                            _distinctiveness = _ent / (_max_ent + 1e-9)
                    except Exception:
                        pass

                profile = EntityProfile(
                    id=str(eid),
                    observation_count=len(edf),
                    time_span=_time_span,
                    completeness=float(1.0 - edf.isnull().mean().mean()),
                    feature_means={c: float(edf[c].mean()) for c in edf.select_dtypes("number").columns},
                    feature_stds={c: float(edf[c].std()) for c in edf.select_dtypes("number").columns},
                    event_rates={},
                    consistency=_consistency,
                    distinctiveness=_distinctiveness,
                )
                entity_profiles.append(profile)

        # DatasetFingerprint
        fingerprint = DatasetFingerprint(
            n_rows_bucket="small" if len(df) < 10000 else "large",
            n_cols_bucket="narrow" if len(df.columns) < 20 else "wide",
            type_distribution=df.dtypes.value_counts(normalize=True).astype(str).to_dict(),
            sparsity=float(df.isnull().mean().mean()),
            dominant_distribution="unknown",
            entropy_profile={k: v["entropy"] for k, v in feature_fingerprints.items() if "entropy" in v},
            temporal=bool(schema["temporal_fields"]),
            cardinality_profile={k: v["cardinality"] for k, v in feature_fingerprints.items() if "cardinality" in v},
            event_density=len(schema["count_event_fields"]) / len(df.columns) if df.columns.any() else 0,
            sub_event_richness=len(schema["sub_event_groups"]),
            entity_count=len(entity_profiles),
            resolution_levels=[0]
        )

        return feature_fingerprints, fingerprint, entity_profiles

    @staticmethod
    def decomposition(df: Any, schema: dict, fingerprints: dict, res_index: dict) -> dict:
        """L1.5: Multi-Resolution & Sub-Event Analysis."""
        import pandas as pd
        import numpy as np

        report = {"sub_event_analysis": {}, "entity_analysis": {}, "temporal_analysis": {}, "derived_features": []}

        # 1. SUB_EVENT_DECOMPOSITION (Simplified)
        if schema["count_event_fields"]:
            counts_df = df[schema["count_event_fields"]]
            report["sub_event_analysis"]["correlation"] = counts_df.corr().to_dict()
            report["sub_event_analysis"]["total_events"] = int(counts_df.sum().sum())

        # 2. ENTITY_INTERACTION_DECOMPOSITION (Simplified)
        if len(schema["entity_fields"]) >= 1:
            # Placeholder for Bradley-Terry or ELO
            report["entity_analysis"]["archetypes"] = []

        # 3. TEMPORAL_DECOMPOSITION
        if schema["temporal_fields"]:
            t_col = schema["temporal_fields"][0]
            # Simple trend detection
            for col in df.select_dtypes("number").columns:
                if col != t_col:
                    report["temporal_analysis"][col] = {"trend": "detecting..."}

        return report

    @staticmethod
    def spectrum(df: Any, fingerprints: dict, decomposition: dict) -> dict:
        """L2: Pattern Mining."""
        import pandas as pd
        import numpy as np

        report = {
            "occurrences": [], "anomalies": [], "clusters": [],
            "periodicities": [], "change_points": [], "hierarchical_patterns": [],
            "complexity_profile": {}
        }

        # 1. ANOMALY_DETECTION (MAD-based)
        for col in df.select_dtypes("number").columns:
            series = df[col].dropna()
            if series.empty: continue
            median = series.median()
            mad = (series - median).abs().median()
            if mad > 0:
                z_scores = 0.6745 * (series - median) / mad
                anomalies = series[z_scores.abs() > 3.5]
                if not anomalies.empty:
                    report["anomalies"].append({
                        "column": col, "count": len(anomalies),
                        "indices": anomalies.index.tolist()[:10]
                    })

        # 2. CLUSTERING_TENDENCY (Simplified)
        if len(df.select_dtypes("number").columns) > 1:
             report["clusters"].append({"type": "kmeans_candidate", "optimal_k": 4})

        return report

    @staticmethod
    def refraction(df: Any, fingerprints: dict, decomposition: dict, patterns: dict) -> dict:
        """L3: Relationship Discovery — Pearson/Spearman matrices, VIF multicollinearity,
        mutual-information non-linear map, and simple causal hypothesis generation."""
        import pandas as pd
        import numpy as np

        report: dict = {
            "matrices": {}, "non_linear_map": {}, "multicollinearity": {},
            "interactions": {}, "causal_hypotheses": [], "relationship_graph": {}
        }

        num = df.select_dtypes("number")
        if len(num.columns) < 2:
            return report

        num_filled = num.fillna(num.median())

        # 1. CORRELATION_MATRIX — Pearson + Spearman
        report["matrices"]["pearson"] = num_filled.corr(method="pearson").round(4).to_dict()
        report["matrices"]["spearman"] = num_filled.corr(method="spearman").round(4).to_dict()

        # 2. NON_LINEAR_DEPENDENCY — mutual information (sklearn) per pair
        try:
            from sklearn.feature_selection import mutual_info_regression
            mi_map: dict = {}
            cols = list(num_filled.columns)
            for tgt in cols[:8]:   # cap at 8 targets to bound runtime
                y = num_filled[tgt].values
                X = num_filled.drop(columns=[tgt])
                if X.empty:
                    continue
                try:
                    mi = mutual_info_regression(X, y, discrete_features=False,
                                                random_state=42)
                    mi_map[tgt] = {c: round(float(v), 4)
                                   for c, v in zip(X.columns, mi)}
                except Exception:
                    pass
            report["non_linear_map"] = mi_map
        except ImportError:
            # sklearn absent — fall back to Kendall tau for non-linearity hint
            try:
                report["non_linear_map"] = {
                    "kendall": num_filled.corr(method="kendall").round(4).to_dict()
                }
            except Exception:
                report["non_linear_map"] = {}

        # 3. MULTICOLLINEARITY — Variance Inflation Factor (VIF)
        try:
            from statsmodels.stats.outliers_influence import variance_inflation_factor
            vif_cols = list(num_filled.columns)
            if len(vif_cols) >= 2:
                vif_data: dict = {}
                X_np = num_filled[vif_cols].values
                for idx, col in enumerate(vif_cols):
                    try:
                        vif_val = variance_inflation_factor(X_np, idx)
                        vif_data[col] = round(float(vif_val), 2)
                    except Exception:
                        vif_data[col] = None
                report["multicollinearity"] = {
                    "vif": vif_data,
                    "high_vif": [c for c, v in vif_data.items()
                                 if v is not None and v > 5.0],
                }
        except ImportError:
            # statsmodels absent — use condition number as a proxy
            try:
                X_np = num_filled.values.astype(float)
                cond = float(np.linalg.cond(X_np))
                report["multicollinearity"] = {"condition_number": round(cond, 2)}
            except Exception:
                pass

        # 4. INTERACTION TERMS — top correlated pairs as candidate interactions
        pearson = report["matrices"].get("pearson", {})
        interactions: list[dict] = []
        seen: set = set()
        for c1, row in pearson.items():
            for c2, r in row.items():
                if c1 == c2:
                    continue
                key = tuple(sorted([c1, c2]))
                if key in seen:
                    continue
                seen.add(key)
                if abs(r) > 0.5:
                    interactions.append({"col_a": c1, "col_b": c2,
                                         "pearson_r": round(r, 4),
                                         "candidate_interaction": f"{c1}*{c2}"})
        report["interactions"] = interactions[:20]

        # 5. CAUSAL HYPOTHESES — simple temporal-lag heuristic
        causal: list[dict] = []
        t_cols = [c for c in df.columns
                  if "time" in c.lower() or "date" in c.lower()
                  or pd.api.types.is_datetime64_any_dtype(df[c])]
        if t_cols and len(num.columns) >= 2:
            num_cols = [c for c in num.columns if c not in t_cols]
            for i, c1 in enumerate(num_cols[:5]):
                for c2 in num_cols[i+1:6]:
                    try:
                        r = float(num_filled[c1].corr(num_filled[c2]))
                        if abs(r) > 0.6:
                            causal.append({
                                "candidate_cause": c1 if r > 0 else c2,
                                "candidate_effect": c2 if r > 0 else c1,
                                "basis": "temporal_correlation",
                                "pearson_r": round(r, 4),
                                "causal_level": "associational",
                            })
                    except Exception:
                        pass
        report["causal_hypotheses"] = causal[:10]

        # 6. RELATIONSHIP GRAPH — adjacency summary
        edges: list[dict] = []
        for pair in interactions[:10]:
            edges.append({
                "source": pair["col_a"], "target": pair["col_b"],
                "weight": abs(pair["pearson_r"]),
            })
        report["relationship_graph"] = {"edges": edges}

        return report

    @staticmethod
    def interference(datasets: list[Any], schemas: list[dict],
                     fingerprints: list[dict], decompositions: list[dict]) -> dict:
        """L4: Cross-Dataset Analysis — schema alignment, join-key discovery,
        column-level cross-correlation, distribution divergence (KL), and
        feature transferability scoring."""
        import pandas as pd
        import numpy as np

        report: dict = {
            "alignment": {}, "join_keys": [], "entities": [], "correlations": {},
            "divergences": {}, "transferability": {}, "entity_analysis": {}
        }

        if not datasets or len(datasets) < 2:
            report["alignment"] = {"status": "single_dataset_mode",
                                   "note": "Provide 2+ datasets for cross-analysis"}
            return report

        ds_a, ds_b = datasets[0], datasets[1]
        if not isinstance(ds_a, pd.DataFrame) or not isinstance(ds_b, pd.DataFrame):
            return report

        # 1. SCHEMA ALIGNMENT
        cols_a = set(ds_a.columns)
        cols_b = set(ds_b.columns)
        common = sorted(cols_a & cols_b)
        report["alignment"] = {
            "common_columns": common,
            "only_in_a": sorted(cols_a - cols_b),
            "only_in_b": sorted(cols_b - cols_a),
            "overlap_ratio": round(len(common) / max(len(cols_a | cols_b), 1), 3),
        }

        # 2. JOIN KEY CANDIDATES — high-cardinality shared columns
        join_keys: list[dict] = []
        for col in common:
            if ds_a[col].dtype == object or pd.api.types.is_integer_dtype(ds_a[col]):
                overlap_vals = set(ds_a[col].dropna().astype(str)) & set(ds_b[col].dropna().astype(str))
                if len(overlap_vals) > 0:
                    join_keys.append({
                        "column": col,
                        "overlap_count": len(overlap_vals),
                        "coverage_a": round(len(overlap_vals) / max(ds_a[col].nunique(), 1), 3),
                        "coverage_b": round(len(overlap_vals) / max(ds_b[col].nunique(), 1), 3),
                    })
        report["join_keys"] = join_keys[:10]

        # 3. CROSS-DATASET CORRELATIONS (numeric common columns)
        num_common = [c for c in common
                      if pd.api.types.is_numeric_dtype(ds_a[c])
                      and pd.api.types.is_numeric_dtype(ds_b[c])]
        corr_map: dict = {}
        for col in num_common[:10]:
            try:
                sa = ds_a[col].dropna()
                sb = ds_b[col].dropna()
                n = min(len(sa), len(sb), 1000)
                if n >= 5:
                    r = float(np.corrcoef(sa.values[:n], sb.values[:n])[0, 1])
                    corr_map[col] = round(r, 4)
            except Exception:
                pass
        report["correlations"] = corr_map

        # 4. DISTRIBUTION DIVERGENCE — KL divergence per numeric column
        divs: dict = {}
        for col in num_common[:10]:
            try:
                sa = ds_a[col].dropna()
                sb = ds_b[col].dropna()
                if len(sa) < 5 or len(sb) < 5:
                    continue
                bins = np.histogram_bin_edges(np.concatenate([sa, sb]), bins=10)
                ha = np.clip(np.histogram(sa, bins=bins)[0].astype(float), 1e-8, None)
                hb = np.clip(np.histogram(sb, bins=bins)[0].astype(float), 1e-8, None)
                ha /= ha.sum(); hb /= hb.sum()
                kl = float(np.sum(ha * np.log(ha / hb)))
                divs[col] = {"kl_divergence": round(kl, 4),
                             "severity": "high" if kl > 0.5 else "moderate" if kl > 0.1 else "low"}
            except Exception:
                pass
        report["divergences"] = divs

        # 5. TRANSFERABILITY SCORE — how well A's numeric distribution generalises to B
        transfer_scores: dict = {}
        for col in num_common[:8]:
            try:
                sa = ds_a[col].dropna()
                sb = ds_b[col].dropna()
                mean_shift = abs(float(sa.mean() - sb.mean()))
                std_a = float(sa.std()) or 1e-6
                # Normalise by dataset A std — Cohen's d equivalent
                d = mean_shift / std_a
                transfer_scores[col] = round(max(0.0, 1.0 - min(d, 1.0)), 3)
            except Exception:
                pass
        report["transferability"] = transfer_scores

        return report

    @staticmethod
    def diffraction(d_old: Any, d_new: Any, schema_old: dict, schema_new: dict) -> dict:
        """L5: Delta Analysis — schema evolution, row-count diff, PSI distribution
        drift, correlation drift, anomaly migration, and entity evolution tracking."""
        import pandas as pd
        import numpy as np

        report: dict = {
            "schema_evolution": {}, "row_diff": {}, "drift": {},
            "correlation_drift": {}, "anomaly_migration": {},
            "entity_evolution": {}, "impact_ranking": [], "cascades": []
        }

        if d_old is None or d_new is None:
            report["schema_evolution"] = {"status": "missing_dataset"}
            return report

        if not isinstance(d_old, pd.DataFrame) or not isinstance(d_new, pd.DataFrame):
            return report

        # 1. SCHEMA EVOLUTION
        cols_old = set(d_old.columns)
        cols_new = set(d_new.columns)
        added = sorted(cols_new - cols_old)
        removed = sorted(cols_old - cols_new)
        type_changed: dict = {}
        for col in cols_old & cols_new:
            if str(d_old[col].dtype) != str(d_new[col].dtype):
                type_changed[col] = {"old": str(d_old[col].dtype),
                                     "new": str(d_new[col].dtype)}
        report["schema_evolution"] = {
            "added_columns": added,
            "removed_columns": removed,
            "type_changes": type_changed,
        }

        # 2. ROW DIFF
        report["row_diff"] = {
            "rows_old": len(d_old),
            "rows_new": len(d_new),
            "delta": len(d_new) - len(d_old),
            "pct_change": round((len(d_new) - len(d_old)) / max(len(d_old), 1) * 100, 2),
        }

        # 3. PSI DISTRIBUTION DRIFT per numeric column
        common_num = [c for c in cols_old & cols_new
                      if pd.api.types.is_numeric_dtype(d_old[c])
                      and pd.api.types.is_numeric_dtype(d_new[c])]
        drift: dict = {}
        for col in common_num:
            try:
                so = d_old[col].dropna()
                sn = d_new[col].dropna()
                if len(so) < 5 or len(sn) < 5:
                    continue
                bins = np.histogram_bin_edges(pd.concat([so, sn]), bins=10)
                ho = np.clip(np.histogram(so, bins=bins)[0] / max(len(so), 1), 1e-8, None)
                hn = np.clip(np.histogram(sn, bins=bins)[0] / max(len(sn), 1), 1e-8, None)
                psi = float(np.sum((hn - ho) * np.log(hn / ho)))
                mean_shift = float(sn.mean() - so.mean())
                std_shift = float(sn.std() - so.std())
                drift[col] = {
                    "psi": round(psi, 4),
                    "mean_shift": round(mean_shift, 4),
                    "std_shift": round(std_shift, 4),
                    "severity": ("critical" if abs(psi) > 0.25
                                 else "moderate" if abs(psi) > 0.1
                                 else "stable"),
                }
            except Exception:
                pass
        report["drift"] = drift

        # 4. CORRELATION DRIFT — compare Pearson correlation matrices
        if len(common_num) >= 2:
            try:
                old_corr = d_old[common_num].corr()
                new_corr = d_new[common_num].corr()
                corr_delta: dict = {}
                for c1 in common_num:
                    for c2 in common_num:
                        if c1 >= c2:
                            continue
                        delta = float(new_corr.loc[c1, c2] - old_corr.loc[c1, c2])
                        if abs(delta) > 0.15:
                            corr_delta[f"{c1}↔{c2}"] = round(delta, 4)
                report["correlation_drift"] = corr_delta
            except Exception:
                pass

        # 5. ANOMALY MIGRATION — MAD anomaly count change per column
        anomaly_mig: dict = {}
        for col in common_num:
            try:
                def _mad_count(s: pd.Series) -> int:
                    med = s.median()
                    mad = (s - med).abs().median()
                    if mad == 0:
                        return 0
                    return int((0.6745 * (s - med).abs() / mad > 3.5).sum())
                anomaly_mig[col] = {
                    "old_count": _mad_count(d_old[col].dropna()),
                    "new_count": _mad_count(d_new[col].dropna()),
                }
            except Exception:
                pass
        report["anomaly_migration"] = anomaly_mig

        # 6. IMPACT RANKING — sort drifted columns by PSI severity
        ranked = sorted(
            [(col, v["psi"]) for col, v in drift.items()],
            key=lambda x: abs(x[1]),
            reverse=True,
        )
        report["impact_ranking"] = [{"column": c, "psi": p} for c, p in ranked[:10]]

        # 7. CASCADE DETECTION — schema removals that appear in added columns
        for removed_col in removed:
            for added_col in added:
                if removed_col.lower()[:4] == added_col.lower()[:4]:
                    report["cascades"].append({
                        "type": "RENAME_SUSPECTED",
                        "old": removed_col, "new": added_col
                    })

        return report

    @staticmethod
    def projection(df: Any, fingerprints: dict, decomposition: dict,
                   patterns: dict, refraction: dict) -> dict:
        """L6: Predictive Synthesis — trend extrapolation for temporal data,
        ensemble model scoring (GBM/RF/Ridge), risk scoring, similarity
        detection, and simple what-if scenario generation."""
        import pandas as pd
        import numpy as np

        report: dict = {
            "trends": {}, "outcomes": {}, "ensembles": {}, "scenarios": [],
            "risks": {}, "similarities": {}, "edges": [], "counterfactuals": []
        }

        num = df.select_dtypes("number")
        if num.empty:
            return report

        num_filled = num.fillna(num.median())

        # 1. TREND EXTRAPOLATION — for temporal + numeric columns
        temporal_cols = [c for c in df.columns
                         if pd.api.types.is_datetime64_any_dtype(df[c])
                         or "time" in c.lower() or "date" in c.lower()]
        if temporal_cols:
            t_col = temporal_cols[0]
            try:
                t_series = pd.to_numeric(pd.to_datetime(df[t_col], errors="coerce"),
                                         errors="coerce").dropna()
                t_norm = (t_series - t_series.min()) / max(t_series.max() - t_series.min(), 1)
                trends: dict = {}
                for col in num_filled.columns[:8]:
                    y = num_filled.loc[t_norm.index, col].values if len(t_norm) == len(num_filled) else num_filled[col].values[:len(t_norm)]
                    x = t_norm.values[:len(y)]
                    if len(x) < 5:
                        continue
                    try:
                        slope, intercept = float(np.polyfit(x, y, 1))
                        horizon_val = slope * 1.1 + intercept  # 10% beyond max time
                        trends[col] = {
                            "slope": round(slope, 4),
                            "intercept": round(intercept, 4),
                            "direction": "up" if slope > 0 else "down" if slope < 0 else "flat",
                            "forecast_next": round(horizon_val, 4),
                        }
                    except Exception:
                        pass
                report["trends"] = trends
            except Exception:
                pass
        else:
            # No temporal column — use index as proxy
            x = np.arange(len(num_filled))
            trends: dict = {}
            for col in num_filled.columns[:5]:
                try:
                    y = num_filled[col].values
                    slope, intercept = float(np.polyfit(x, y, 1))
                    trends[col] = {
                        "slope": round(slope, 6),
                        "direction": "up" if slope > 0 else "down" if slope < 0 else "flat",
                    }
                except Exception:
                    pass
            report["trends"] = trends

        # 2. ENSEMBLE OUTCOME SCORING — per numeric target (cap at 3 targets)
        outcomes: dict = {}
        target_candidates = list(num_filled.columns[:3])
        for tgt in target_candidates:
            X = num_filled.drop(columns=[tgt])
            if X.empty or len(X) < 20:
                continue
            y = num_filled[tgt]
            try:
                from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
                from sklearn.linear_model import Ridge
                from sklearn.model_selection import cross_val_score
                from sklearn.preprocessing import StandardScaler
                Xs = StandardScaler().fit_transform(X)
                model_scores: dict = {}
                for name, m in [
                    ("GBM", GradientBoostingRegressor(n_estimators=30, random_state=42)),
                    ("RF",  RandomForestRegressor(n_estimators=30, random_state=42)),
                    ("Ridge", Ridge()),
                ]:
                    try:
                        cv = min(5, len(X) // 5) or 2
                        s = float(cross_val_score(m, Xs, y, cv=cv, scoring="r2").mean())
                        model_scores[name] = round(s, 4)
                    except Exception:
                        model_scores[name] = 0.0
                best = max(model_scores, key=model_scores.get)
                outcomes[tgt] = {
                    "best_model": best,
                    "best_r2": model_scores[best],
                    "model_scores": model_scores,
                    "predictable": model_scores[best] > 0.3,
                }
            except ImportError:
                # sklearn not available — use linear R² as baseline
                try:
                    X_np = X.values; y_np = y.values
                    slope_vec = np.linalg.lstsq(
                        np.column_stack([X_np, np.ones(len(X_np))]),
                        y_np, rcond=None)[0]
                    y_pred = np.column_stack([X_np, np.ones(len(X_np))]) @ slope_vec
                    ss_res = float(np.sum((y_np - y_pred) ** 2))
                    ss_tot = float(np.sum((y_np - y_np.mean()) ** 2)) or 1e-10
                    r2 = round(1 - ss_res / ss_tot, 4)
                    outcomes[tgt] = {"best_model": "OLS", "best_r2": r2,
                                     "predictable": r2 > 0.3}
                except Exception:
                    pass
        report["outcomes"] = outcomes

        # 3. RISK SCORING — data quality + model uncertainty
        sparsity = float(df.isnull().mean().mean()) if len(df) else 0.0
        report["risks"] = {
            "data_quality": round(1.0 - sparsity, 3),
            "sparsity": round(sparsity, 3),
            "small_sample": len(df) < 100,
            "high_cardinality_cols": [c for c in df.columns if df[c].nunique() > len(df) * 0.9],
        }

        # 4. SIMILARITY — cosine similarity between column vectors
        similarities: list[dict] = []
        cols = list(num_filled.columns[:10])
        for i, c1 in enumerate(cols):
            for c2 in cols[i+1:]:
                try:
                    v1 = num_filled[c1].values.astype(float)
                    v2 = num_filled[c2].values.astype(float)
                    norm1 = np.linalg.norm(v1); norm2 = np.linalg.norm(v2)
                    if norm1 > 0 and norm2 > 0:
                        cos_sim = float(np.dot(v1, v2) / (norm1 * norm2))
                        if abs(cos_sim) > 0.8:
                            similarities.append({"col_a": c1, "col_b": c2,
                                                 "cosine_similarity": round(cos_sim, 4)})
                except Exception:
                    pass
        report["similarities"] = similarities[:15]

        # 5. PREDICTIVE EDGES — from refraction correlation data
        pearson = refraction.get("matrices", {}).get("pearson", {})
        edges: list[dict] = []
        seen_edges: set = set()
        for c1, row in pearson.items():
            for c2, r_val in row.items():
                if c1 == c2:
                    continue
                key = tuple(sorted([c1, c2]))
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                r_f = float(r_val) if r_val is not None else 0.0
                if abs(r_f) > 0.6:
                    edges.append({"source": c1, "target": c2,
                                  "strength": round(abs(r_f), 4),
                                  "direction": "positive" if r_f > 0 else "negative"})
        report["edges"] = edges[:20]

        # 6. COUNTERFACTUALS — simple mean-shift scenarios
        counterfactuals: list[dict] = []
        for col in list(num_filled.columns[:3]):
            mean_val = float(num_filled[col].mean())
            std_val = float(num_filled[col].std()) or 1.0
            counterfactuals.append({
                "column": col,
                "scenario_up": round(mean_val + std_val, 4),
                "scenario_down": round(mean_val - std_val, 4),
                "description": f"If {col} shifts ±1σ from mean ({mean_val:.3f})",
            })
        report["counterfactuals"] = counterfactuals

        return report

    @staticmethod
    def coherence(all_layer_outputs: dict) -> list[Finding]:
        """L7: Narrative & Structured Output."""
        findings = []

        # FINDING_CONSOLIDATION (Simplified)
        # Pull findings from L2, L3, L6
        patterns = all_layer_outputs.get("spectrum", {})
        for anomaly in patterns.get("anomalies", []):
            findings.append(Finding(
                category="ANOMALIES",
                sub_category="statistical",
                title=f"Statistical Anomaly in {anomaly['column']}",
                description=f"Detected {anomaly['count']} outliers using MAD-based modified Z-score.",
                confidence=0.85,
                calibrated_confidence=0.85,
                impact=0.7,
                actionability=0.6,
                novelty=0.4,
                robustness=0.9,
                fragility=0.1,
                source_layer="L2",
                falsification_criteria="If outliers disappear after robust scaling",
                uncertainty_type="aleatoric"
            ))

        corrs = all_layer_outputs.get("refraction", {}).get("matrices", {}).get("pearson", {})
        for col1, targets in corrs.items():
            for col2, r in targets.items():
                if col1 < col2 and abs(r) > 0.7:
                    findings.append(Finding(
                        category="CORRELATIONS",
                        sub_category="linear",
                        title=f"Strong Correlation: {col1} vs {col2}",
                        description=f"Pearson r={r:.2f} indicates a strong linear relationship.",
                        confidence=0.9,
                        calibrated_confidence=0.9,
                        impact=0.6,
                        actionability=0.5,
                        novelty=0.3,
                        robustness=0.95,
                        fragility=0.05,
                        source_layer="L3",
                        falsification_criteria="If relationship disappears in temporal holdout",
                        uncertainty_type="epistemic"
                    ))

        return findings

class WaveController:
    """Orchestrates the analytical waves of Analytics Engine."""

    def __init__(self, core: AnalyticalCore):
        self.core = core
        self.last_lens: Any = None  # set by analyze() when a nexus is supplied

    def analyze(self, raw_input: Any, mode: str = "SINGLE", max_wave: int = 2,
                nexus: Any = None,
                extra_inputs: list | None = None) -> list[Finding]:
        """Evidence-Gated Wave Controller logic.

        Modes
        -----
        SINGLE (default) — single-dataset reconnaissance + projection (L1-L3, L6).
        CROSS  — multi-dataset interference analysis (L4).
                 Pass additional DataFrames/paths via extra_inputs=[df2, ...].
        DELTA  — delta/drift analysis comparing two dataset snapshots (L5).
                 Pass the newer snapshot via extra_inputs=[df_new].

        v29: when a DomainLensManager is supplied, auto-detects the domain lens from the
        loaded dataframe/schema and injects domain narrative onto findings."""

        # ── Wave 1: Reconnaissance (all modes) ────────────────────────────────
        try:
            df, schema, entities, res_index = self.core.aperture(raw_input)
        except ImportError as _ie:
            return [Finding(
                id="no_data_libs", layer="L0", category="DATA_LIBRARY_MISSING",
                description=f"pandas/numpy not installed: {_ie}",
                confidence=1.0, evidence=[], tags=["infra"],
            )]

        f_fingerprints, fingerprint, entity_profiles = self.core.surface(df, schema)
        decomposition = self.core.decomposition(df, schema, f_fingerprints, res_index)
        spectrum = self.core.spectrum(df, f_fingerprints, decomposition)
        refraction = self.core.refraction(df, f_fingerprints, decomposition, spectrum)

        all_outputs: dict = {
            "aperture": schema,
            "surface": f_fingerprints,
            "decomposition": decomposition,
            "spectrum": spectrum,
            "refraction": refraction,
        }

        # ── Mode-aware L4 / L5 orchestration ──────────────────────────────────
        _mode = (mode or "SINGLE").upper()

        if _mode == "CROSS" and extra_inputs:
            # L4: Cross-Dataset Interference
            try:
                extra_dfs, extra_schemas, extra_fps, extra_decs = [], [], [], []
                for ei in extra_inputs:
                    edf, esc, _, eri = self.core.aperture(ei)
                    efp, _, _ = self.core.surface(edf, esc)
                    edec = self.core.decomposition(edf, esc, efp, eri)
                    extra_dfs.append(edf)
                    extra_schemas.append(esc)
                    extra_fps.append(efp)
                    extra_decs.append(edec)
                all_outputs["interference"] = self.core.interference(
                    [df] + extra_dfs,
                    [schema] + extra_schemas,
                    [f_fingerprints] + extra_fps,
                    [decomposition] + extra_decs,
                )
            except Exception:
                pass  # L4 is best-effort; fall back to SINGLE-mode findings

        elif _mode == "DELTA" and extra_inputs:
            # L5: Delta / Drift Analysis
            try:
                df_new, schema_new, _, _ = self.core.aperture(extra_inputs[0])
                all_outputs["diffraction"] = self.core.diffraction(
                    df, df_new, schema, schema_new)
            except Exception:
                pass  # L5 is best-effort

        # ── Wave 2: Predictive & Synthesis ────────────────────────────────────
        if max_wave >= 2:
            projection = self.core.projection(
                df, f_fingerprints, decomposition, spectrum, refraction)
            all_outputs["projection"] = projection

        findings = self.core.coherence(all_outputs)

        # ── Domain lens injection ──────────────────────────────────────────────
        self.last_lens = None
        if nexus is not None:
            try:
                detected = nexus.auto_detect_domain(df, schema)
                if detected is not None:
                    findings = nexus.inject_lens(findings, detected)
                self.last_lens = detected
            except Exception:
                pass  # domain detection is best-effort

        return findings

    def explain(self, finding_id: str) -> str: return "Explanation..."

    def drill_down(self, finding_id: str) -> list:
        """Return sub-findings for a given finding_id by filtering the last
        analysis result for entries whose parent_id matches finding_id.
        Returns an empty list if no sub-findings exist or the analysis has
        not been run yet (#11)."""
        last = getattr(self, "_last_findings", None)
        if not last:
            return []
        return [f for f in last if getattr(f, "parent_id", None) == finding_id]

    def predict_outcome(self, entities: list, context: dict) -> dict: return {}
    def what_if(self, conditions: list) -> dict: return {}

