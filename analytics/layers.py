# essence.analytics.analytical_core — Essence v29.0 Analytical Intelligence Core
# Auto-placed by build pipeline; hand-written content (not auto-generated).
"""
 AnalyticalStateBus ·  AnalyticalIntentLayer ·  AnalyticalToolDispatch

The three core Analytics Engine components that wire the analytical spine into the
modular package.  Every other sub-package imports from here via
    from essence.analytics.spine import AnalyticalStateBus, ...
"""
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

import dataclasses as _dc
import enum
import hashlib
import math
import secrets
import threading
import time
import json
import re
from pathlib import Path
from typing import Any

_log = _setup_logging("essence.analytics.analytical_core")

try:
    from essence.analytics.models import (
        DatasetFingerprint, EntityProfile, DomainLens, Finding
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# ANALYTICAL SPINE
# ─────────────────────────────────────────────────────────────────────────────

class AnalyticalMode(str, enum.Enum):
    EXPLORE   = "EXPLORE"
    CORRELATE = "CORRELATE"
    PREDICT   = "PREDICT"
    COMPARE   = "COMPARE"
    DIAGNOSE  = "DIAGNOSE"
    MONITOR   = "MONITOR"
    COMPOSE   = "COMPOSE"
    PROFILE   = "PROFILE"
    NONE      = "NONE"


class AnalyticalWave(int, enum.Enum):
    QUICK      = 1
    STANDARD   = 2
    DEEP       = 3
    EXHAUSTIVE = 4


@_dc.dataclass
class CalibrationState:
    ece: float = 0.0
    brier: float = 0.0
    n_samples: int = 0
    last_updated: float = _dc.field(default_factory=time.time)


@_dc.dataclass
class StrategyState:
    archetype: str = "unknown"
    layer_ranking: list = _dc.field(default_factory=list)
    efficacy_map: dict = _dc.field(default_factory=dict)
    exploration_epsilon: float = 0.15
    generation: int = 0


@_dc.dataclass
class ResolutionIndex:
    levels: list = _dc.field(default_factory=lambda: [0])
    entity_resolution: bool = False
    temporal_resolution: bool = False
    sub_event_resolution: bool = False


@_dc.dataclass
class DriftAlert:
    feature: str = ""
    psi: float = 0.0
    detected_at: float = _dc.field(default_factory=time.time)
    severity: str = "moderate"


@_dc.dataclass
class Contradiction:
    finding_id_a: str = ""
    finding_id_b: str = ""
    description: str = ""
    cycles_unresolved: int = 0
    status: str = "OPEN"


class AnalyticalStateBus:
    """
     — The Analytical Spine (v29.0 full implementation).
    Thread-safe; write contracts enforced per subsystem.
    """
    def __init__(self) -> None:
        self._lock                 = threading.RLock()
        self.active_lens: Any      = None
        self.active_archetype: str = "unknown"
        self.active_fingerprint: Any = None
        self.active_entities: list = []
        self.active_findings: list = []
        self.active_edges: list    = []
        self.confidence_state      = CalibrationState()
        self.genesis_strategy      = StrategyState()
        self.resolution_index      = ResolutionIndex()
        self.trust_score: float    = 1.0
        self.drift_alerts: list    = []
        self.contradiction_log: list = []
        self.analytical_mode       = AnalyticalMode.NONE
        self.prism_config: dict    = {}
        self._snapshots: list      = []

    # write contracts ─────────────────────────────────────────────────────────
    def update_from_prism(self, *, fingerprint=None, entities=None,
                          findings=None, resolution_index=None,
                          archetype=None, trust_score=None, lens=None) -> None:
        with self._lock:
            if fingerprint     is not None: self.active_fingerprint = fingerprint
            if entities        is not None: self.active_entities    = entities
            if findings        is not None: self.active_findings    = findings
            if resolution_index is not None: self.resolution_index  = resolution_index
            if archetype       is not None: self.active_archetype   = archetype
            if trust_score     is not None: self.trust_score        = float(trust_score)
            if lens            is not None: self.active_lens        = lens

    def update_from_genesis(self, *, archetype=None, strategy=None,
                            calibration=None) -> None:
        with self._lock:
            if archetype    is not None: self.active_archetype  = archetype
            if strategy     is not None: self.genesis_strategy  = strategy
            if calibration  is not None: self.confidence_state  = calibration

    def update_from_aegis(self, *, trust_score=None, drift_alert=None,
                          contradiction=None) -> None:
        with self._lock:
            if trust_score   is not None:
                self.trust_score = max(0.0, min(1.0, trust_score))
            if drift_alert   is not None: self.drift_alerts.append(drift_alert)
            if contradiction is not None: self.contradiction_log.append(contradiction)

    def update_from_intent(self, *, mode=None, prism_config=None,
                           lens=None) -> None:
        with self._lock:
            if mode         is not None: self.analytical_mode = mode
            if prism_config is not None: self.prism_config    = prism_config
            if lens         is not None: self.active_lens     = lens

    def extend_findings(self, new_findings: list) -> None:
        with self._lock:
            existing = {getattr(f, "id", id(f)) for f in self.active_findings}
            for f in new_findings:
                fid = getattr(f, "id", id(f))
                if fid not in existing:
                    self.active_findings.append(f)
                    existing.add(fid)

    # snapshot / diff ─────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "ts": time.time(),
                "archetype": self.active_archetype,
                "trust_score": self.trust_score,
                "analytical_mode": self.analytical_mode.value,
                "finding_count": len(self.active_findings),
                "entity_count": len(self.active_entities),
                "drift_alert_count": len(self.drift_alerts),
                "contradiction_count": len(self.contradiction_log),
                "ece": self.confidence_state.ece,
            }

    def save_snapshot(self) -> None:
        s = self.snapshot()
        with self._lock:
            self._snapshots.append(s)
            if len(self._snapshots) > 500:
                self._snapshots = self._snapshots[-500:]

    def diff(self, prev: dict) -> dict:
        cur = self.snapshot()
        return {k: {"before": prev.get(k), "after": cur.get(k)}
                for k in cur if cur.get(k) != prev.get(k)}

    def to_analytical_context(self, max_chars: int = 4000) -> str:
        with self._lock:
            parts = [
                "[Analytics Engine Analytical Context]",
                f"Archetype:  {self.active_archetype}",
                f"Trust:      {self.trust_score:.2f}",
                f"Mode:       {self.analytical_mode.value}",
            ]
            if self.active_lens is not None:
                parts.append(f"Domain:     {getattr(self.active_lens,'name',str(self.active_lens))}")
            findings = sorted(self.active_findings,
                              key=lambda f: getattr(f, "impact", 0), reverse=True)
            if findings:
                parts.append(f"\nTop findings ({len(findings)} total):")
                for f in findings[:5]:
                    c = getattr(f, "calibrated_confidence", getattr(f, "confidence", 0))
                    parts.append(f"  • [{c:.0%}] {getattr(f,'title',str(f))}")
            if self.active_entities:
                names = [getattr(e, "id", str(e)) for e in self.active_entities[:8]]
                parts.append(f"\nEntities:  {', '.join(names)}")
            if self.drift_alerts:
                parts.append(f"\nDrift alerts: {len(self.drift_alerts)}")
            ctx = "\n".join(parts)
            return ctx[:max_chars] + ("\n[…truncated]" if len(ctx) > max_chars else "")


# ─────────────────────────────────────────────────────────────────────────────
# ANALYTICAL INTENT LAYER
# ─────────────────────────────────────────────────────────────────────────────

_EXPLICIT: dict[AnalyticalMode, list[str]] = {
    AnalyticalMode.DIAGNOSE:  ["why","cause","root cause","explain","diagnose","attribute","causal"],
    AnalyticalMode.COMPOSE:   ["breakdown","composition","decompose","what makes up","components"],
    AnalyticalMode.PROFILE:   ["profile","tell me about","entity","who is","details about"],
    AnalyticalMode.COMPARE:   ["compare","diff","difference","contrast","versus"," vs ","delta"],
    AnalyticalMode.MONITOR:   ["monitor","alert","watch","track changes","notify","drift"],
    AnalyticalMode.CORRELATE: ["correlate","correlation","related to","what drives","linked to"],
    AnalyticalMode.PREDICT:   ["predict","forecast","project","will","probability","model"],
    AnalyticalMode.EXPLORE:   ["analyze","analyse","explore","describe","show me","overview","eda"],
}

_IMPLICIT_RE = re.compile(
    r"\.(csv|parquet|xlsx|json|pq|tsv)\b|\bdataset\b|\bfeatures?\b|\bKPI\b|\btrend\b",
    re.IGNORECASE)

_WAVE_HINTS: list[tuple[list[str], AnalyticalWave]] = [
    (["quick","summary","brief","fast","tldr","overview"],    AnalyticalWave.QUICK),
    (["deep dive","thorough","comprehensive","everything"],   AnalyticalWave.DEEP),
    (["validate","prove","exhaustive","rigorous","verify"],   AnalyticalWave.EXHAUSTIVE),
]

_FOCUS_MAP: dict[AnalyticalMode, list[str]] = {
    AnalyticalMode.EXPLORE:   ["DISTRIBUTIONS","ENTITY_PROFILES","CORRELATIONS","ANOMALIES"],
    AnalyticalMode.CORRELATE: ["CORRELATIONS","FEATURE_IMPORTANCE"],
    AnalyticalMode.PREDICT:   ["PREDICTIONS","EDGES"],
    AnalyticalMode.COMPARE:   ["DELTA","CORRELATIONS"],
    AnalyticalMode.DIAGNOSE:  ["CAUSAL","DELTA","CHANGE_POINTS","COUNTERFACTUALS"],
    AnalyticalMode.MONITOR:   ["DELTA","DISTRIBUTIONS"],
    AnalyticalMode.COMPOSE:   ["COMPOSITIONS","ENTITY_PROFILES"],
    AnalyticalMode.PROFILE:   ["ENTITY_PROFILES","PREDICTIONS"],
    AnalyticalMode.NONE:      [],
}


class AnalyticalIntentLayer:
    """Analytics Engine-aware intent classification wrapping the v28.1 Intent Layer."""

    def __init__(self, spine: AnalyticalStateBus | None = None,
                 provider: Any = None, model: str = "") -> None:
        self.spine    = spine or AnalyticalStateBus()
        self.provider = provider
        self.model    = model

    def classify(self, text: str, context: dict | None = None) -> dict:
        tl = text.lower()
        mode = self._detect_mode(tl)
        wave = self._detect_wave(tl)
        is_analytical = (mode is not None or bool(_IMPLICIT_RE.search(text))
                         or self.spine.analytical_mode != AnalyticalMode.NONE)
        mode = mode or (AnalyticalMode.EXPLORE if is_analytical else AnalyticalMode.NONE)
        focus = _FOCUS_MAP.get(mode, [])
        target_features = self._extract_features(text)
        target_entities = self._extract_entities(text)
        domain_hint = getattr(self.spine.active_lens, "name", None) if self.spine.active_lens else None
        cfg = {
            "mode": mode.value, "max_wave": wave.value,
            "focus_categories": focus, "target_features": target_features,
            "target_entities": target_entities, "domain_hint": domain_hint,
        }
        return {"mode": mode, "wave": wave, "focus": focus,
                "target_features": target_features, "target_entities": target_entities,
                "domain_hint": domain_hint, "is_analytical": is_analytical,
                "prism_config": cfg}

    def enhance_task_spec(self, task_spec: Any, text: str,
                          context: dict | None = None) -> Any:
        c = self.classify(text, context)
        is_none = (c["mode"] == AnalyticalMode.NONE)
        if hasattr(task_spec, "analytical_mode"):
            # AnalyticalMode.NONE.value == "NONE", a non-empty string that
            # would be truthy at call sites doing `if task_spec.analytical_mode:`
            # (e.g. agent.py's Analytics Engine-recon gate) — use real None instead so
            # "nothing analytical detected" stays falsy as the field's
            # `str | None = None` declaration intends.
            task_spec.analytical_mode = None if is_none else c["mode"].value
        if hasattr(task_spec, "prism_config") and not is_none:
            task_spec.prism_config = c["prism_config"]
        if hasattr(task_spec, "context") and isinstance(task_spec.context, dict):
            task_spec.context["_prism_spine"] = self.spine.to_analytical_context(1200)
        self.spine.update_from_intent(mode=c["mode"], prism_config=c["prism_config"])
        return task_spec

    def _detect_mode(self, tl: str) -> AnalyticalMode | None:
        for mode, signals in _EXPLICIT.items():
            for s in signals:
                if re.search(r"\b" + re.escape(s) + r"\b", tl, re.IGNORECASE):
                    return mode
        return None

    def _detect_wave(self, tl: str) -> AnalyticalWave:
        for kws, w in _WAVE_HINTS:
            if any(k in tl for k in kws):
                return w
        return AnalyticalWave.STANDARD

    def _extract_features(self, text: str) -> list[str]:
        fp = self.spine.active_fingerprint
        if fp is None:
            return []
        fields = (list(fp.entropy_profile.keys())
                  if hasattr(fp, "entropy_profile") else
                  (fp.get("fields", []) if isinstance(fp, dict) else []))
        return [f for f in fields if f.lower() in text.lower()][:10]

    def _extract_entities(self, text: str) -> list[str]:
        known = {getattr(e, "id", "").lower() for e in self.spine.active_entities}
        caps  = re.findall(r"(?<![.!?]\s)\b([A-Z][a-zA-Z0-9_-]+)\b", text)
        seen: set[str] = set()
        out: list[str] = []
        for c in caps:
            if c not in seen and (c.lower() in known or len(out) < 5):
                out.append(c); seen.add(c)
        return out[:8]


# ─────────────────────────────────────────────────────────────────────────────
# ANALYTICAL TOOL DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

@_dc.dataclass
class SpectrumReport:
    report_id:    str   = _dc.field(default_factory=lambda: hashlib.md5(secrets.token_bytes(8)).hexdigest()[:12])
    dataset_path: str   = ""
    task:         str   = ""
    mode:         str   = AnalyticalMode.EXPLORE.value
    wave_depth:   int   = 1
    findings:     list  = _dc.field(default_factory=list)
    layer_results: dict = _dc.field(default_factory=dict)
    trust_score:  float = 1.0
    total_findings: int = 0
    high_impact_count: int = 0
    execution_ms: float = 0.0
    generated_at: float = _dc.field(default_factory=time.time)
    archetype:    str   = "unknown"
    narrative:    str   = ""

    def all_findings(self) -> list:
        return [f for f in self.findings if getattr(f, "status", "ACTIVE") == "ACTIVE"]

    def to_json(self) -> str:
        return json.dumps({
            "report_id": self.report_id, "dataset_path": self.dataset_path,
            "task": self.task, "mode": self.mode, "wave_depth": self.wave_depth,
            "trust_score": self.trust_score, "total_findings": self.total_findings,
            "high_impact_count": self.high_impact_count, "execution_ms": self.execution_ms,
            "archetype": self.archetype, "narrative": self.narrative,
            "finding_summaries": [
                {"id": getattr(f,"id",""), "title": getattr(f,"title",str(f)),
                 "confidence": getattr(f,"calibrated_confidence",getattr(f,"confidence",0)),
                 "impact": getattr(f,"impact",0), "category": getattr(f,"category",""),
                 "status": getattr(f,"status","ACTIVE")}
                for f in self.findings
            ],
        }, default=str, indent=2)


def _make_finding(*, category, sub_category, title, description,
                  confidence, impact, source_layer, **kw) -> Any:
    fid = hashlib.md5(secrets.token_bytes(8)).hexdigest()[:16]
    if _MODELS_AVAILABLE:
        try:
            return Finding(
                id=fid, category=category, sub_category=sub_category,
                title=title, description=description,
                confidence=confidence, calibrated_confidence=confidence,
                impact=impact, actionability=kw.get("actionability", 0.5),
                novelty=kw.get("novelty", 0.5), robustness=kw.get("robustness", 0.5),
                fragility=kw.get("fragility", 0.3), source_layer=source_layer,
                statistical_test=kw.get("statistical_test"),
                test_statistic=kw.get("test_statistic"), p_value=kw.get("p_value"),
                effect_size=kw.get("effect_size"),
                causal_level=kw.get("causal_level", "associational"),
                falsification_criteria=kw.get("falsification_criteria", "Not specified"),
                uncertainty_type=kw.get("uncertainty_type", "epistemic"),
                status="ACTIVE", wave_discovered=1, wave_last_refined=1,
            )
        except Exception:
            pass
    import types
    return types.SimpleNamespace(
        id=fid, category=category, sub_category=sub_category,
        title=title, description=description,
        confidence=confidence, calibrated_confidence=confidence,
        impact=impact, source_layer=source_layer,
        causal_level=kw.get("causal_level","associational"),
        status="ACTIVE", wave_discovered=1, wave_last_refined=1, **{
            k: v for k, v in kw.items()
            if k in ("actionability","novelty","robustness","fragility",
                     "statistical_test","test_statistic","p_value","effect_size",
                     "falsification_criteria","uncertainty_type")
        }
    )


# Layer runners (L0/L1, L2, L3, L5, L6) ─────────────────────────────────────

def _L0_L1(df: Any, cfg: dict, plots_dir: Path) -> dict:
    r: dict = {"layer":"L0_L1","rows":len(df),"cols":len(df.columns),
               "columns":list(df.columns),"dtypes":df.dtypes.astype(str).to_dict(),
               "missing":df.isnull().sum().to_dict(),
               "missing_pct":(df.isnull().mean()*100).round(2).to_dict(),
               "distribution_tags":{},"skew":{},"kurtosis":{},
               "temporal_cols":[],"entity_cols":[],"count_event_cols":[]}
    try:
        import scipy.stats as sp, numpy as np, pandas as pd
        for col in df.select_dtypes("number").columns:
            s = df[col].dropna()
            if len(s) < 10: continue
            sk, ku = float(sp.skew(s)), float(sp.kurtosis(s))
            r["skew"][col] = round(sk,4); r["kurtosis"][col] = round(ku,4)
            if (s>=0).all() and s.dtype in (int,"int64","int32"):
                r["count_event_cols"].append(col)
            r["distribution_tags"][col] = ("fat_tailed" if abs(ku)>3 else
                                           "skewed" if abs(sk)>1.5 else "near_normal")
    except Exception: pass
    try:
        import pandas as pd
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]) or any(
                    k in col.lower() for k in ("date","time","ts","timestamp")):
                r["temporal_cols"].append(col)
        n = len(df)
        for col in df.select_dtypes(["object","category"]).columns:
            if 2 <= df[col].nunique() <= max(10, n//5):
                r["entity_cols"].append(col)
    except Exception: pass
    return r


def _L2(df: Any, cfg: dict, plots_dir: Path) -> dict:
    r: dict = {"layer":"L2","change_points":{},"clusters":{}}
    try:
        import numpy as np, pandas as pd
        for col in df.select_dtypes("number").columns[:8]:
            s = df[col].dropna()
            if len(s) < 40: continue
            w = max(10, len(s)//10)
            rm = s.rolling(w).mean().dropna(); rs = s.rolling(w).std().dropna()
            if rs.mean() == 0: continue
            z = abs((rm - rm.mean()) / rs.mean())
            peaks = z[z > 2.5]
            if peaks.empty: continue
            r["change_points"][col] = {"detected":True,
                "at_index": int(peaks.index[0]), "n_change_points": len(peaks)}
    except Exception: pass
    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
        num = df.select_dtypes("number").iloc[:,:10].dropna()
        if len(num) >= 20 and len(num.columns) >= 2:
            k = min(5, len(num)//10)
            X = StandardScaler().fit_transform(num)
            km = KMeans(n_clusters=k, random_state=42, n_init=10).fit(X)
            r["clusters"] = {"n_clusters":k,"inertia":float(km.inertia_),
                "sizes":{str(i):int((km.labels_==i).sum()) for i in range(k)}}
    except Exception: pass
    return r


def _L3(df: Any, cfg: dict) -> dict:
    r: dict = {"layer":"L3","strong_pairs":[],"threshold_relationships":[]}
    try:
        import numpy as np; from scipy import stats as sp
        num = df.select_dtypes("number")
        if len(num.columns) < 2: return r
        method = cfg.get("correlation_method","pearson")
        corr = num.corr(method=method); cols = list(corr.columns)
        bonf = 0.05 / max(1, len(cols)*(len(cols)-1)//2)
        for i in range(len(cols)):
            for j in range(i+1, len(cols)):
                rv = float(corr.iloc[i,j])
                if abs(rv) < 0.3 or math.isnan(rv): continue
                idx = num[cols[i]].dropna().index.intersection(num[cols[j]].dropna().index)
                if len(idx) < 10: continue
                _, p = sp.pearsonr(num[cols[i]][idx], num[cols[j]][idx])
                if p > bonf: continue
                r["strong_pairs"].append({"col_a":cols[i],"col_b":cols[j],
                    "r":round(rv,4),"p_bonferroni":round(p,6),"n":len(idx),
                    "strength":"strong" if abs(rv)>0.6 else "moderate"})
    except Exception as e:
        r["error"] = str(e)
    return r


def _L5(df_old: Any, df_new: Any, cfg: dict) -> dict:
    r: dict = {"layer":"L5","psi_scores":{},"mean_shifts":{},"std_shifts":{}}
    if df_old is None or df_new is None: return r
    try:
        import numpy as np, pandas as pd
        for col in set(df_old.columns) & set(df_new.columns):
            if not pd.api.types.is_numeric_dtype(df_old[col]): continue
            os_, ns_ = df_old[col].dropna(), df_new[col].dropna()
            if len(os_)<5 or len(ns_)<5: continue
            bins = np.histogram_bin_edges(pd.concat([os_,ns_]), bins=10)
            oh = np.clip(np.histogram(os_,bins=bins)[0]/max(1,len(os_)),1e-6,None)
            nh = np.clip(np.histogram(ns_,bins=bins)[0]/max(1,len(ns_)),1e-6,None)
            r["psi_scores"][col] = round(float(np.sum((nh-oh)*np.log(nh/oh))),4)
            r["mean_shifts"][col] = round(float(ns_.mean()-os_.mean()),4)
            r["std_shifts"][col]  = round(float(ns_.std()-os_.std()),4)
    except Exception as e:
        r["error"] = str(e)
    return r


def _L6(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    r: dict = {"layer":"L6","predictions":[],"edges":[]}
    if not target_col or target_col not in df.columns: return r
    try:
        import numpy as np
        from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
        from sklearn.linear_model import Ridge
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import StandardScaler
        y = df[target_col].dropna()
        X = df.drop(columns=[target_col]).select_dtypes("number").loc[y.index].fillna(0)
        if len(X)<20 or not len(X.columns): return r
        Xs = StandardScaler().fit_transform(X)
        models = {"GBM":GradientBoostingRegressor(n_estimators=50,random_state=42),
                  "RF":RandomForestRegressor(n_estimators=50,random_state=42),
                  "Ridge":Ridge()}
        scores = {}
        for name, m in models.items():
            try: scores[name]=float(cross_val_score(m,Xs,y,cv=min(5,len(X)//5),scoring="r2").mean())
            except Exception: scores[name]=0.0
        best = max(scores,key=scores.get)
        r["predictions"].append({"target":target_col,"best_model":best,
            "r2_cv":round(scores[best],4),"model_scores":{k:round(v,4) for k,v in scores.items()},
            "ensemble_r2":round(float(sum(scores.values())/len(scores)),4)})
        from scipy.stats import pearsonr
        for col in list(X.columns)[:10]:
            try:
                _, p = pearsonr(X[col].values, y.values)
                if p < 0.01:
                    r["edges"].append({"feature":col,"target":target_col,"p_value":round(float(p),6),
                        "direction":"positive" if X[col].corr(y)>0 else "negative"})
            except Exception:
                pass
    except Exception as e:
        r["error"] = str(e)
    return r


def _build_findings(layer_results: dict, mode: str, target_col: str) -> list:
    out: list = []
    l1 = layer_results.get("L0_L1", {})
    for col, tag in (l1.get("distribution_tags") or {}).items():
        if tag in ("fat_tailed","skewed"):
            out.append(_make_finding(
                category="DISTRIBUTION", sub_category=tag.upper(),
                title=f"{col}: {tag.replace('_',' ')} distribution",
                description=f"'{col}' shows {tag.replace('_',' ')} (skew={l1.get('skew',{}).get(col,'?')}, kurt={l1.get('kurtosis',{}).get(col,'?')}).",
                confidence=0.85, impact=0.5, source_layer="L1", uncertainty_type="aleatoric",
                falsification_criteria="Retest with larger sample; fit formal distribution."))
    for col, cp in (layer_results.get("L2",{}).get("change_points") or {}).items():
        if cp.get("detected"):
            out.append(_make_finding(
                category="CHANGE_POINT", sub_category="STRUCTURAL_BREAK",
                title=f"{col}: structural change at idx {cp.get('at_index')}",
                description=f"Structural break detected in '{col}' ({cp.get('n_change_points')} point(s)).",
                confidence=0.75, impact=0.8, source_layer="L2", uncertainty_type="epistemic",
                falsification_criteria="Apply formal PELT / BOCPD test."))
    for pair in (layer_results.get("L3",{}).get("strong_pairs") or []):
        s = pair.get("strength","moderate")
        out.append(_make_finding(
            category="CORRELATION", sub_category=f"{s.upper()}_CORRELATION",
            title=f"{pair['col_a']} ↔ {pair['col_b']} (r={pair['r']:+.3f})",
            description=(f"{'Strong' if s=='strong' else 'Moderate'} correlation r={pair['r']}, "
                         f"p={pair['p_bonferroni']:.4f} (Bonferroni), n={pair['n']}."),
            confidence=0.85 if s=="strong" else 0.70, impact=0.7 if s=="strong" else 0.5,
            source_layer="L3", statistical_test="Pearson (Bonferroni)",
            test_statistic=pair["r"], p_value=pair["p_bonferroni"], effect_size=abs(pair["r"]),
            causal_level="associational", falsification_criteria="Spearman cross-check on holdout."))
    for col, psi in (layer_results.get("L5",{}).get("psi_scores") or {}).items():
        if psi < 0.1: continue
        sev = "critical" if psi > 0.25 else "moderate"
        out.append(_make_finding(
            category="DRIFT", sub_category=f"PSI_{sev.upper()}",
            title=f"{col}: distribution drift PSI={psi:.3f}",
            description=f"'{col}' drifted (PSI={psi:.3f}, {sev}). Models may degrade.",
            confidence=0.90, impact=0.85 if sev=="critical" else 0.60,
            source_layer="L5", falsification_criteria="Re-measure after 3 cycles.",
            uncertainty_type="aleatoric"))
    for pred in (layer_results.get("L6",{}).get("predictions") or []):
        r2 = pred.get("r2_cv",0)
        if r2 < 0.1: continue
        out.append(_make_finding(
            category="PREDICTION", sub_category="ENSEMBLE_FORECAST",
            title=f"{pred['target']}: predictable R²={r2:.3f}",
            description=f"CV R²={r2:.3f} (best: {pred['best_model']}).",
            confidence=min(0.95,0.5+r2*0.4), impact=0.75, source_layer="L6",
            statistical_test="5-fold CV R²", test_statistic=r2, causal_level="associational",
            falsification_criteria="Evaluate on held-out test set.", uncertainty_type="mixed"))
    for edge in (layer_results.get("L6",{}).get("edges") or [])[:5]:
        out.append(_make_finding(
            category="EDGE", sub_category="PREDICTIVE_EDGE",
            title=f"{edge['feature']} → {edge['target']}",
            description=f"Predictive edge p={edge['p_value']:.4f} ({edge['direction']}).",
            confidence=0.75, impact=0.7, source_layer="L6",
            p_value=edge["p_value"], causal_level="associational",
            falsification_criteria="Validate on holdout.", uncertainty_type="epistemic"))
    return out


_VERB_MAP: dict[str, tuple[str, list[str], int]] = {
    "explore":    ("EXPLORE",   ["L0_L1","L2","L3"], 2),
    "eda":        ("EXPLORE",   ["L0_L1","L2","L3"], 2),
    "describe":   ("EXPLORE",   ["L0_L1"],            1),
    "correlate":  ("CORRELATE", ["L0_L1","L3"],       2),
    "correlation":("CORRELATE", ["L0_L1","L3"],       2),
    "forecast":   ("PREDICT",   ["L0_L1","L6"],       2),
    "predict":    ("PREDICT",   ["L0_L1","L6"],       2),
    "compare":    ("COMPARE",   ["L5"],               2),
    "diff":       ("COMPARE",   ["L5"],               2),
    "anomaly":    ("EXPLORE",   ["L0_L1","L2"],       2),
    "fraud":      ("EXPLORE",   ["L0_L1","L2"],       2),
    "cluster":    ("EXPLORE",   ["L0_L1","L2"],       2),
    "segment":    ("EXPLORE",   ["L0_L1","L2"],       2),
    "risk":       ("DIAGNOSE",  ["L0_L1","L3","L6"],  3),
    "churn":      ("PREDICT",   ["L0_L1","L6"],       2),
    "regress":    ("PREDICT",   ["L0_L1","L6"],       2),
    "classify":   ("PREDICT",   ["L0_L1","L6"],       2),
    "ab_test":    ("COMPARE",   ["L5"],               2),
    "sentiment":  ("EXPLORE",   ["L0_L1"],            1),
    "feature_importance": ("CORRELATE", ["L0_L1","L3","L6"], 2),
    "profile":    ("PROFILE",   ["L0_L1","L2"],       1),
    "entity":     ("PROFILE",   ["L0_L1","L2"],       2),
    "composition":("COMPOSE",   ["L0_L1","L2"],       2),
}

ANALYTICS_DERIVED_TOOLS: list[dict] = [
    {"name": "prism_analyze",
     "description": "Analytics Engine v29.0 analytical intelligence — routes through AnalyticalToolDispatch.",
     "parameters": {"type":"object","properties":{
         "dataset_path":{"type":"string"},"task":{"type":"string","enum":sorted(_VERB_MAP)},
         "target_col":{"type":"string"},"max_wave":{"type":"integer","minimum":1,"maximum":4}},
         "required":["dataset_path","task"]}},
    {"name": "prism_delta",
     "description": "Analytics Engine delta analysis: compare two dataset snapshots.",
     "parameters": {"type":"object","properties":{
         "dataset_path_old":{"type":"string"},"dataset_path_new":{"type":"string"}},
         "required":["dataset_path_old","dataset_path_new"]}},
]


class AnalyticalToolDispatch:
    """Analytics Engine-native tool dispatch (replaces  _run_* table)."""

    def __init__(self, spine: AnalyticalStateBus | None = None,
                 workspace: Path | None = None, nexus: Any = None) -> None:
        self.spine     = spine or AnalyticalStateBus()
        self.workspace = workspace or Path.cwd()
        self.nexus     = nexus
        self._plots    = self.workspace / "plots"
        self._plots.mkdir(parents=True, exist_ok=True)
        self._df_cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def dispatch(self, dataset_path: str, task: str, target_col: str = "",
                 config: dict | None = None, df_old: Any = None,
                 prism_config: dict | None = None) -> SpectrumReport:
        t0  = time.perf_counter()
        cfg = {**(config or {}), **(prism_config or {})}
        info = _VERB_MAP.get(task.lower(), ("EXPLORE", ["L0_L1","L2","L3"], 2))
        mode, layers, wave = info
        if prism_config and "max_wave" in prism_config:
            wave = min(int(prism_config["max_wave"]), 4)
        if not target_col and prism_config and prism_config.get("target_features"):
            target_col = prism_config["target_features"][0]
        df, err = self._load(dataset_path)
        if err:
            return SpectrumReport(dataset_path=dataset_path, task=task, mode=mode,
                                  narrative=f"Load error: {err}")
        lr: dict = {}
        if "L0_L1" in layers:            lr["L0_L1"] = _L0_L1(df, cfg, self._plots)
        if "L2"    in layers and wave>=2: lr["L2"]    = _L2(df, cfg, self._plots)
        if "L3"    in layers and wave>=2: lr["L3"]    = _L3(df, cfg)
        if "L5"    in layers:
            df_old = df_old or self._prev(dataset_path)
            lr["L5"] = _L5(df_old, df, cfg)
        if "L6"    in layers and wave>=2: lr["L6"]    = _L6(df, target_col, cfg, self._plots)
        with self._lock:
            self._df_cache[dataset_path] = df
        findings = _build_findings(lr, mode, target_col)
        trust    = self._trust(df, lr)
        arch     = self._archetype(lr)
        detected_lens = None
        if self.nexus is not None:
            try:
                detected_lens = self.nexus.auto_detect_domain(df, {})
                if detected_lens is not None:
                    findings = self.nexus.inject_lens(findings, detected_lens)
            except Exception:
                detected_lens = None
        self.spine.update_from_prism(findings=findings, trust_score=trust, archetype=arch,
                                     lens=detected_lens)
        hi = [f for f in findings if getattr(f,"impact",0) >= 0.7]
        narr = f"Analytics Engine {mode} | archetype={arch} | {len(findings)} findings | trust={trust:.2f}"
        return SpectrumReport(
            dataset_path=dataset_path, task=task, mode=mode, wave_depth=wave,
            findings=findings, layer_results=lr, trust_score=trust,
            total_findings=len(findings), high_impact_count=len(hi),
            execution_ms=round((time.perf_counter()-t0)*1000,1),
            archetype=arch, narrative=narr)

    def dispatch_to_json(self, *a, **kw) -> str:
        return self.dispatch(*a, **kw).to_json()

    def _load(self, path: str) -> tuple[Any, str | None]:
        with self._lock:
            if path in self._df_cache:
                return self._df_cache[path], None
        try:
            import pandas as pd
            p = Path(path).expanduser()
            if not p.exists(): return None, f"Not found: {path}"
            ldr = {".csv":pd.read_csv,".json":pd.read_json,".parquet":pd.read_parquet,
                   ".pq":pd.read_parquet,".xlsx":pd.read_excel,".xls":pd.read_excel}
            fn = ldr.get(p.suffix.lower())
            return (fn(p), None) if fn else (None, f"Unsupported: {p.suffix}")
        except ImportError: return None, "pandas not installed"
        except Exception as e: return None, str(e)

    def _prev(self, path: str) -> Any:
        with self._lock: return self._df_cache.get(path)

    def _trust(self, df: Any, lr: dict) -> float:
        try:
            l1 = lr.get("L0_L1", {}); n = l1.get("rows", len(df))
            mv = list(l1.get("missing_pct", {}).values())
            t  = 1.0 - min(0.4, sum(mv)/max(1,len(mv))/100)
            if n < 50: t -= 0.3
            elif n < 200: t -= 0.1
            return max(0.1, round(t, 3))
        except Exception:
            return 0.8

    def _archetype(self, lr: dict) -> str:
        l1   = lr.get("L0_L1", {})
        tags = list((l1.get("distribution_tags") or {}).values())
        parts = ["large" if l1.get("rows",0)>100000 else
                 "medium" if l1.get("rows",0)>5000 else "small"]
        if l1.get("temporal_cols"): parts.append("temporal")
        if l1.get("entity_cols"):   parts.append("entity")
        if tags.count("fat_tailed") > len(tags)*0.3: parts.append("fat_tailed")
        if l1.get("count_event_cols"): parts.append("poisson")
        return "_".join(parts) if len(parts)>1 else "generic"


# ── module-level singletons ───────────────────────────────────────────────────
_spine:    AnalyticalStateBus | None    = None
_dispatch: AnalyticalToolDispatch | None = None
_intent:   AnalyticalIntentLayer | None  = None
_nexus:    Any = None

def get_analytical_spine() -> AnalyticalStateBus:
    global _spine
    if _spine is None: _spine = AnalyticalStateBus()
    return _spine

def set_analytical_spine(spine: AnalyticalStateBus) -> None:
    """Register a pre-built AnalyticalStateBus as the process-wide singleton.
    Called by Agent.__init__ so /api/status and /api/prism/findings always
    read the same instance the live agent is writing to (fixes the
    Agent↔API spine split identified in the system review)."""
    global _spine
    _spine = spine

def get_nexus() -> Any:
    """process-wide DomainLensManager with the illustrative default lenses
    pre-registered (trade/sports/medical). register_lens() to add more."""
    global _nexus
    if _nexus is None:
        from essence.analytics.domain_lens import DomainLensManager, register_default_lenses
        _nexus = DomainLensManager()
        register_default_lenses(_nexus)
    return _nexus

def get_analytical_dispatch(workspace: Path | None = None) -> AnalyticalToolDispatch:
    global _dispatch
    if _dispatch is None:
        _dispatch = AnalyticalToolDispatch(spine=get_analytical_spine(), workspace=workspace, nexus=get_nexus())
    return _dispatch

def get_intent_layer(provider: Any = None, model: str = "") -> AnalyticalIntentLayer:
    global _intent
    if _intent is None:
        _intent = AnalyticalIntentLayer(spine=get_analytical_spine(), provider=provider, model=model)
    return _intent

def prism_tool_run_analysis(dataset_path: str, task: str, target_col: str = "",
                            config: dict | None = None,
                            workspace: Path | None = None) -> str:
    return get_analytical_dispatch(workspace).dispatch_to_json(
        dataset_path=dataset_path, task=task, target_col=target_col, config=config)

def register_prism_tools(tool_registry: Any = None) -> None:
    if tool_registry is None:
        try:
            from essence.tools.registry import TOOL_REGISTRY as tool_registry
        except ImportError: return
    for schema in ANALYTICS_DERIVED_TOOLS:
        try:
            if hasattr(tool_registry, "register_schema"):
                tool_registry.register_schema(schema)
            elif isinstance(tool_registry, list):
                if not any(t.get("name")==schema["name"] for t in tool_registry):
                    tool_registry.append(schema)
        except Exception: pass
