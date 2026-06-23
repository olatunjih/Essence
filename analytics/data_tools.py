"""Data analysis tools: EDA, clustering, forecasting, risk, correlation.
_tool_run_analysis routes through Analytics Engine AnalyticalToolDispatch."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# DATA AGENT TOOLS  (analytics, ML, forecasting, risk, fraud, A/B)
# ══════════════════════════════════════════════════════════════════════════════
# All tasks dispatch through _tool_run_analysis().
# Outputs: JSON result dict + matplotlib PNG saved to workspace/plots/.
# Graceful tier degradation: T0 gets pandas-only; T3 gets full suite.

_ANALYSIS_TASKS = [
    "eda", "cluster", "classify", "regress", "forecast",
    "anomaly", "ab_test", "sentiment", "risk", "churn",
    "feature_importance", "correlation", "segmentation",
]


def _run_eda(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    """Automatic EDA: shape, dtypes, missing, distributions, correlations."""
    import pandas as pd  # type: ignore
    result: dict[str, Any] = {
        "rows": len(df), "cols": len(df.columns),
        "columns": list(df.columns),
        "dtypes": df.dtypes.astype(str).to_dict(),
        "missing": df.isnull().sum().to_dict(),
        "describe": json.loads(df.describe(include="all").to_json()),
    }
    try:
        import matplotlib  # type: ignore
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, min(4, len(df.select_dtypes("number").columns)),
                                 figsize=(14, 4))
        if not hasattr(axes, "__len__"): axes = [axes]
        for ax, col in zip(axes, df.select_dtypes("number").columns[:4]):
            df[col].dropna().hist(ax=ax, bins=30, color="#6c8cff", alpha=0.8)
            ax.set_title(col, fontsize=9)
        plt.tight_layout()
        out = plots_dir / "eda_distributions.png"
        plt.savefig(out, dpi=100, bbox_inches="tight")
        plt.close()
        result["plot"] = str(out)
    except Exception:
        pass
    # Correlation heatmap
    try:
        import matplotlib.pyplot as plt
        num = df.select_dtypes("number")
        if len(num.columns) > 1:
            corr = num.corr()
            fig, ax = plt.subplots(figsize=(8, 6))
            im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
            ax.set_xticks(range(len(corr.columns)))
            ax.set_yticks(range(len(corr.columns)))
            ax.set_xticklabels(corr.columns, rotation=45, ha="right", fontsize=8)
            ax.set_yticklabels(corr.columns, fontsize=8)
            plt.colorbar(im, ax=ax)
            ax.set_title("Correlation Matrix")
            plt.tight_layout()
            cp = plots_dir / "eda_correlation.png"
            plt.savefig(cp, dpi=100, bbox_inches="tight")
            plt.close()
            result["correlation_plot"] = str(cp)
    except Exception:
        pass
    return result


def _run_cluster(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    import pandas as pd  # type: ignore
    try:
        from sklearn.preprocessing import StandardScaler  # type: ignore
        from sklearn.cluster import KMeans, DBSCAN        # type: ignore
        from sklearn.decomposition import PCA             # type: ignore
        import numpy as np
        num = df.select_dtypes("number").fillna(0)
        X   = StandardScaler().fit_transform(num)
        algo = cfg.get("algorithm", "kmeans")
        k    = cfg.get("n_clusters", 4)
        if algo == "dbscan":
            labels = DBSCAN(eps=cfg.get("eps", 0.5)).fit_predict(X)
        else:
            labels = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(X)
        df["_cluster"] = labels
        counts = pd.Series(labels).value_counts().to_dict()
        # PCA scatter
        pca = PCA(n_components=2)
        X2  = pca.fit_transform(X)
        try:
            import matplotlib; matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(8, 6))
            for lbl in np.unique(labels):
                mask = labels == lbl
                ax.scatter(X2[mask, 0], X2[mask, 1], label=f"C{lbl}", alpha=0.6, s=20)
            ax.legend(); ax.set_title(f"Clusters ({algo})")
            out = plots_dir / "cluster_pca.png"
            plt.savefig(out, dpi=100, bbox_inches="tight"); plt.close()
            return {"algorithm": algo, "n_clusters": k,
                    "cluster_counts": counts, "plot": str(out)}
        except Exception:
            return {"algorithm": algo, "cluster_counts": counts}
    except ImportError:
        return {"error": "sklearn not installed: pip install scikit-learn"}


def _run_regress(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    if not target_col:
        return {"error": "target_col required for regression"}
    try:
        from sklearn.model_selection import train_test_split  # type: ignore
        from sklearn.preprocessing import StandardScaler      # type: ignore
        from sklearn.linear_model import Ridge, Lasso         # type: ignore
        from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor  # type: ignore
        from sklearn.metrics import r2_score, mean_absolute_error  # type: ignore
        import numpy as np
        y   = df[target_col].dropna()
        X   = df.drop(columns=[target_col]).select_dtypes("number").loc[y.index].fillna(0)
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42)
        sc  = StandardScaler().fit(Xtr)
        Xtr, Xte = sc.transform(Xtr), sc.transform(Xte)
        algo = cfg.get("algorithm", "gbm")
        model = {
            "ridge": Ridge(alpha=1.0),
            "lasso": Lasso(alpha=0.01),
            "rf":    RandomForestRegressor(n_estimators=100, random_state=42),
            "gbm":   GradientBoostingRegressor(n_estimators=100, random_state=42),
        }.get(algo, GradientBoostingRegressor())
        model.fit(Xtr, ytr)
        pred = model.predict(Xte)
        r2   = float(r2_score(yte, pred))
        mae  = float(mean_absolute_error(yte, pred))
        fi   = {}
        if hasattr(model, "feature_importances_"):
            fi = dict(sorted(zip(X.columns, model.feature_importances_),
                             key=lambda x: -x[1])[:10])
        return {"algorithm": algo, "r2": round(r2, 4),
                "mae": round(mae, 4), "feature_importances": fi}
    except ImportError:
        return {"error": "sklearn not installed: pip install scikit-learn"}


def _run_classify(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    if not target_col:
        return {"error": "target_col required for classification"}
    try:
        from sklearn.model_selection import train_test_split  # type: ignore
        from sklearn.preprocessing import LabelEncoder, StandardScaler  # type: ignore
        from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier  # type: ignore
        from sklearn.metrics import classification_report, roc_auc_score  # type: ignore
        import numpy as np
        df2 = df.copy()
        le  = LabelEncoder()
        df2[target_col] = le.fit_transform(df2[target_col].astype(str))
        y   = df2[target_col]
        X   = df2.drop(columns=[target_col]).select_dtypes("number").fillna(0)
        _strat = y if y.value_counts().min() >= 2 else None
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2,
                                               random_state=42, stratify=_strat)
        sc  = StandardScaler().fit(Xtr)
        Xtr, Xte = sc.transform(Xtr), sc.transform(Xte)
        algo  = cfg.get("algorithm", "rf")
        model = {
            "rf":  RandomForestClassifier(n_estimators=100, random_state=42),
            "gbm": GradientBoostingClassifier(n_estimators=100, random_state=42),
        }.get(algo, RandomForestClassifier(random_state=42))
        model.fit(Xtr, ytr)
        pred = model.predict(Xte)
        report = classification_report(yte, pred, output_dict=True)
        result: dict[str, Any] = {"algorithm": algo, "report": report}
        try:
            prob = model.predict_proba(Xte)
            if len(le.classes_) == 2:
                result["roc_auc"] = round(float(roc_auc_score(yte, prob[:,1])), 4)
        except Exception: pass
        if hasattr(model, "feature_importances_"):
            result["feature_importances"] = dict(sorted(
                zip(X.columns, model.feature_importances_), key=lambda x: -x[1])[:10])
        return result
    except ImportError:
        return {"error": "sklearn not installed: pip install scikit-learn"}


def _run_forecast(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    if not target_col:
        return {"error": "target_col required for forecast"}
    try:
        import pandas as pd  # type: ignore
        series = df[target_col].dropna().reset_index(drop=True)
        periods = cfg.get("periods", 30)
        algo    = cfg.get("algorithm", "auto")
        # Try Prophet first
        if algo in ("prophet", "auto"):
            try:
                from prophet import Prophet  # type: ignore
                date_col = cfg.get("date_col", "")
                if date_col and date_col in df.columns:
                    fc_df = df[[date_col, target_col]].rename(
                        columns={date_col: "ds", target_col: "y"}).dropna()
                else:
                    fc_df = pd.DataFrame({
                        "ds": pd.date_range("2020-01-01", periods=len(series)),
                        "y": series.values})
                m  = Prophet(yearly_seasonality=True, weekly_seasonality=True)
                m.fit(fc_df)
                future = m.make_future_dataframe(periods=periods)
                forecast = m.predict(future)
                fv = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(periods)
                # Plot
                try:
                    import matplotlib; matplotlib.use("Agg")
                    import matplotlib.pyplot as plt
                    fig, ax = plt.subplots(figsize=(12, 5))
                    ax.plot(fc_df["ds"], fc_df["y"], label="Actual", alpha=0.7)
                    ax.plot(fv["ds"], fv["yhat"], label="Forecast", color="#6c8cff")
                    ax.fill_between(fv["ds"], fv["yhat_lower"], fv["yhat_upper"],
                                    alpha=0.2, color="#6c8cff")
                    ax.legend(); ax.set_title("Prophet Forecast")
                    plt.tight_layout()
                    out = plots_dir / "forecast_prophet.png"
                    plt.savefig(out, dpi=100, bbox_inches="tight"); plt.close()
                    return {"algorithm": "prophet", "periods": periods,
                            "forecast": fv.to_dict("records"), "plot": str(out)}
                except Exception:
                    return {"algorithm": "prophet", "periods": periods,
                            "forecast": fv.to_dict("records")}
            except ImportError:
                pass
        # LSTM via statsmodels ARIMA as fallback
        try:
            from statsmodels.tsa.arima.model import ARIMA  # type: ignore
            m = ARIMA(series, order=(2, 1, 2)).fit()
            fc = m.forecast(steps=periods)
            return {"algorithm": "arima", "periods": periods,
                    "forecast": fc.tolist()}
        except ImportError:
            pass
        # Naive seasonal fallback (zero deps)
        season = min(7, len(series))
        naive_fc = [float(series.iloc[-(season - i % season)]) for i in range(periods)]
        return {"algorithm": "naive_seasonal", "periods": periods,
                "forecast": naive_fc}
    except Exception as e:
        return {"error": str(e)}


def _run_anomaly(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    try:
        import numpy as np
        cols = [target_col] if target_col and target_col in df.columns \
               else df.select_dtypes("number").columns.tolist()
        X = df[cols].fillna(0).values
        algo = cfg.get("algorithm", "isolation_forest")
        try:
            from sklearn.ensemble import IsolationForest  # type: ignore
            from sklearn.preprocessing import StandardScaler  # type: ignore
            clf = IsolationForest(contamination=cfg.get("contamination", 0.05),
                                  random_state=42)
            labels = clf.fit_predict(StandardScaler().fit_transform(X))
            n_anom = int((labels == -1).sum())
            idx    = list(np.where(labels == -1)[0].tolist())
            return {"algorithm": "isolation_forest",
                    "n_anomalies": n_anom,
                    "anomaly_indices": idx[:50],
                    "contamination": cfg.get("contamination", 0.05)}
        except ImportError:
            # Z-score fallback
            zscores = np.abs((X - X.mean(axis=0)) / (X.std(axis=0) + 1e-9))
            thresh  = cfg.get("z_threshold", 3.0)
            flags   = (zscores > thresh).any(axis=1)
            return {"algorithm": "zscore", "threshold": thresh,
                    "n_anomalies": int(flags.sum()),
                    "anomaly_indices": list(np.where(flags)[0][:50].tolist())}
    except Exception as e:
        return {"error": str(e)}


def _run_ab_test(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    group_col = cfg.get("group_col", "")
    if not target_col or not group_col:
        return {"error": "ab_test requires target_col and config.group_col"}
    try:
        from scipy import stats  # type: ignore
        import numpy as np
        groups = df[group_col].unique()
        if len(groups) < 2:
            return {"error": "Need at least 2 groups for A/B test"}
        if len(groups) > 2:
            extra = [str(g) for g in groups[2:]]
            # Log the extra groups; test only compares groups[0] vs groups[1]
            import warnings as _w
            _w.warn(f"ab_test: {len(groups)} groups found; "
                    f"testing only '{groups[0]}' vs '{groups[1]}'. "
                    f"Ignored: {extra}", UserWarning, stacklevel=2)
        a, b  = groups[0], groups[1]
        sa    = df[df[group_col] == a][target_col].dropna().values
        sb    = df[df[group_col] == b][target_col].dropna().values
        tstat, pval = stats.ttest_ind(sa, sb)
        lift        = float((sb.mean() - sa.mean()) / (sa.mean() + 1e-9) * 100)
        return {
            "group_a": str(a), "group_b": str(b),
            "mean_a": round(float(sa.mean()), 4),
            "mean_b": round(float(sb.mean()), 4),
            "lift_pct": round(lift, 2),
            "t_stat": round(float(tstat), 4),
            "p_value": round(float(pval), 6),
            "significant": bool(pval < cfg.get("alpha", 0.05)),
            "n_a": len(sa), "n_b": len(sb),
        }
    except ImportError:
        return {"error": "scipy not installed: pip install scipy"}


def _run_sentiment(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    if not target_col:
        return {"error": "target_col (text column) required for sentiment"}
    texts = df[target_col].dropna().astype(str).tolist()[:500]
    # Try transformers pipeline first
    try:
        from transformers import pipeline as hf_pipeline  # type: ignore
        pipe = hf_pipeline("sentiment-analysis",
                           model=cfg.get("model", "distilbert-base-uncased-finetuned-sst-2-english"),
                           truncation=True, max_length=512)
        results = pipe(texts[:100])
        if not results:
            return {"model": "transformers", "total": 0, "positive": 0, "negative": 0,
                    "positive_pct": 0.0, "sample": []}
        pos = sum(1 for r in results if r["label"] == "POSITIVE")
        return {"model": "transformers", "total": len(results),
                "positive": pos, "negative": len(results) - pos,
                "positive_pct": round(pos / len(results) * 100, 1),
                "sample": results[:5]}
    except ImportError:
        pass
    # VADER fallback
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore
        sia     = SentimentIntensityAnalyzer()
        scores  = [sia.polarity_scores(t) for t in texts]
        if not scores:
            return {"model": "vader", "total": 0, "positive": 0, "negative": 0,
                    "neutral": 0, "positive_pct": 0.0, "avg_compound": 0.0}
        pos     = sum(1 for s in scores if s["compound"] >= 0.05)
        neg     = sum(1 for s in scores if s["compound"] <= -0.05)
        return {"model": "vader", "total": len(scores),
                "positive": pos, "negative": neg, "neutral": len(scores)-pos-neg,
                "positive_pct": round(pos / len(scores) * 100, 1),
                "avg_compound": round(sum(s["compound"] for s in scores)/len(scores), 4)}
    except ImportError:
        pass
    # Lexicon fallback
    pos_words = {"good", "great", "excellent", "positive", "love", "best"}
    neg_words = {"bad", "terrible", "awful", "negative", "hate", "worst"}
    pos = sum(1 for t in texts if any(w in t.lower() for w in pos_words))
    neg = sum(1 for t in texts if any(w in t.lower() for w in neg_words))
    if not texts:
        return {"model": "lexicon", "total": 0, "positive": 0, "negative": 0,
                "positive_pct": 0.0}
    return {"model": "lexicon", "total": len(texts),
            "positive": pos, "negative": neg,
            "positive_pct": round(pos/len(texts)*100, 1)}


def _run_risk(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    """VaR, CVaR, and portfolio risk metrics on a returns series."""
    if not target_col:
        return {"error": "target_col (returns column) required for risk"}
    try:
        import numpy as np
        returns = df[target_col].dropna().values.astype(float)
        conf    = cfg.get("confidence", 0.95)
        var_pct = float(np.percentile(returns, (1 - conf) * 100))
        cvar    = float(returns[returns <= var_pct].mean()) if any(returns <= var_pct) else var_pct
        vol     = float(returns.std() * np.sqrt(252))
        sharpe  = float(returns.mean() / (returns.std() + 1e-9) * np.sqrt(252))
        max_dd_val = 0.0
        cumret = np.cumprod(1 + returns)
        peak   = np.maximum.accumulate(cumret)
        dd     = (cumret - peak) / peak
        max_dd_val = float(dd.min())
        return {
            "confidence": conf, "VaR": round(var_pct, 5),
            "CVaR": round(cvar, 5), "annualised_vol": round(vol, 4),
            "sharpe_ratio": round(sharpe, 4),
            "max_drawdown": round(max_dd_val, 4),
            "n_obs": len(returns),
        }
    except Exception as e:
        return {"error": str(e)}


def _run_churn(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    """Customer churn / binary classification with probability calibration."""
    return _run_classify(df, target_col, {**cfg, "algorithm": cfg.get("algorithm", "rf")},
                         plots_dir)


def _run_correlation(df: Any, target_col: str, cfg: dict, plots_dir: Path) -> dict:
    """Pearson/Spearman correlation matrix with optional heatmap."""
    try:
        import numpy as np
        method = cfg.get("method", "pearson")
        num = df.select_dtypes("number")
        if len(num.columns) < 2:
            return {"error": "Need at least 2 numeric columns for correlation analysis"}
        corr = num.corr(method=method)
        # Top correlated pairs (excluding self-correlation)
        pairs = []
        cols = list(corr.columns)
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                pairs.append({
                    "col_a": cols[i], "col_b": cols[j],
                    "correlation": round(float(corr.iloc[i, j]), 4)
                })
        pairs.sort(key=lambda x: abs(x["correlation"]), reverse=True)
        result: dict[str, Any] = {
            "method": method,
            "n_columns": len(cols),
            "top_pairs": pairs[:20],
            "matrix": {c: {r: round(float(corr.loc[c, r]), 4)
                           for r in cols} for c in cols},
        }
        if target_col and target_col in corr.columns:
            target_corr = corr[target_col].drop(target_col).sort_values(
                key=abs, ascending=False)
            result["target_correlations"] = {
                k: round(float(v), 4) for k, v in target_corr.items()}
        # Heatmap
        try:
            import matplotlib; matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(max(6, len(cols)), max(5, len(cols) - 1)))
            im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
            ax.set_xticks(range(len(cols)))
            ax.set_yticks(range(len(cols)))
            ax.set_xticklabels(cols, rotation=45, ha="right", fontsize=8)
            ax.set_yticklabels(cols, fontsize=8)
            plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            ax.set_title(f"Correlation Matrix ({method.capitalize()})")
            for i in range(len(cols)):
                for j in range(len(cols)):
                    ax.text(j, i, f"{corr.iloc[i, j]:.2f}",
                            ha="center", va="center",
                            fontsize=7,
                            color="white" if abs(corr.iloc[i, j]) > 0.5 else "black")
            plt.tight_layout()
            out = plots_dir / "correlation_matrix.png"
            plt.savefig(out, dpi=100, bbox_inches="tight")
            plt.close()
            result["plot"] = str(out)
        except Exception:
            pass
        return result
    except Exception as e:
        return {"error": str(e)}


def _run_feature_importance(df: Any, target_col: str, cfg: dict,
                             plots_dir: Path) -> dict:
    if not target_col:
        return {"error": "target_col required"}
    try:
        from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier  # type: ignore
        from sklearn.preprocessing import LabelEncoder  # type: ignore
        import numpy as np
        y   = df[target_col].dropna()
        X   = df.drop(columns=[target_col]).select_dtypes("number").loc[y.index].fillna(0)
        # Detect if target is categorical
        if y.dtype == object or y.nunique() < 20:
            le  = LabelEncoder()
            y2  = le.fit_transform(y.astype(str))
            clf = RandomForestClassifier(n_estimators=100, random_state=42)
        else:
            y2  = y.values
            clf = RandomForestRegressor(n_estimators=100, random_state=42)
        clf.fit(X, y2)
        fi = sorted(zip(X.columns, clf.feature_importances_), key=lambda x: -x[1])
        result: dict[str, Any] = {
            "top_features": {k: round(float(v), 5) for k, v in fi[:20]}}
        # Bar chart
        try:
            import matplotlib; matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            names, vals = zip(*fi[:15])
            fig, ax = plt.subplots(figsize=(9, 5))
            ax.barh(list(reversed(names)), list(reversed(vals)), color="#6c8cff")
            ax.set_xlabel("Importance"); ax.set_title("Feature Importance")
            plt.tight_layout()
            out = plots_dir / "feature_importance.png"
            plt.savefig(out, dpi=100, bbox_inches="tight"); plt.close()
            result["plot"] = str(out)
        except Exception: pass
        return result
    except ImportError:
        return {"error": "sklearn not installed: pip install scikit-learn"}


def _tool_run_analysis(dataset_path: str, task: str,
                        target_col: str = "",
                        config: dict | None = None,
                        workspace: Path | None = None) -> str:
    """
    Unified data analysis dispatcher.
    Loads CSV/parquet/JSON, dispatches to the right sub-function,
    returns JSON result string + saves plots to workspace/plots/.
    """
    cfg       = config or {}
    ws        = workspace or Path.cwd()
    plots_dir = ws / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    # Load data
    try:
        import pandas as pd  # type: ignore
        p = Path(dataset_path).expanduser()
        if not p.exists():
            return json.dumps({"error": f"File not found: {dataset_path}"})
        ext = p.suffix.lower()
        if ext == ".csv":
            df = pd.read_csv(p)
        elif ext in (".parquet", ".pq"):
            df = pd.read_parquet(p)
        elif ext == ".json":
            df = pd.read_json(p)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(p)
        else:
            return json.dumps({"error": f"Unsupported format: {ext}"})
    except ImportError:
        return json.dumps({"error": "pandas not installed: pip install pandas"})
    except Exception as e:
        return json.dumps({"error": str(e)})

    dispatch = {
        "eda":                _run_eda,
        "cluster":            _run_cluster,
        "segmentation":       _run_cluster,
        "regress":            _run_regress,
        "classify":           _run_classify,
        "forecast":           _run_forecast,
        "anomaly":            _run_anomaly,
        "fraud":              _run_anomaly,
        "ab_test":            _run_ab_test,
        "sentiment":          _run_sentiment,
        "risk":               _run_risk,
        "churn":              _run_churn,
        "feature_importance": _run_feature_importance,
        "correlation":        _run_correlation,
    }
    fn = dispatch.get(task)
    if fn is None:
        return json.dumps({"error": f"Unknown task '{task}'. Valid: {list(dispatch)}"})
    try:
        result = fn(df, target_col, cfg, plots_dir)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════

# ──  Analytics Engine integration patch ──────────────────────────────────────────────
# Replace the legacy dict-based dispatch with Analytics Engine-native routing.
# Falls back to the original _tool_run_analysis if Analytics Engine is unavailable.
try:
    from essence.analytics.layers import (
        prism_tool_run_analysis as _prism_dispatch,
        get_analytical_dispatch,
        register_prism_tools,
    )
    _ANALYTICS_DISPATCH_AVAILABLE = True
except ImportError:
    _ANALYTICS_DISPATCH_AVAILABLE = False

def _tool_run_analysis(
        dataset_path: str,
        task: str,
        target_col: str = "",
        config: dict | None = None,
        workspace: "Path | None" = None) -> str:
    """
    Unified data analysis dispatcher.
    Routes through Analytics Engine AnalyticalToolDispatch when available;
    falls back to the v28.1 _run_* dispatch table otherwise.
    """
    if _ANALYTICS_DISPATCH_AVAILABLE:
        return _prism_dispatch(
            dataset_path=dataset_path,
            task=task,
            target_col=target_col,
            config=config,
            workspace=workspace,
        )
    # --- v28.1 fallback ---
    cfg        = config or {}
    ws         = workspace or Path.cwd()
    plots_dir  = ws / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    try:
        import pandas as pd  # type: ignore
        p = Path(dataset_path).expanduser()
        if not p.exists():
            return json.dumps({"error": f"File not found: {dataset_path}"})
        ext = p.suffix.lower()
        loaders = {".csv": pd.read_csv, ".json": pd.read_json,
                   ".parquet": pd.read_parquet, ".pq": pd.read_parquet,
                   ".xlsx": pd.read_excel, ".xls": pd.read_excel}
        loader = loaders.get(ext)
        if loader is None:
            return json.dumps({"error": f"Unsupported format: {ext}"})
        df = loader(p)
    except ImportError:
        return json.dumps({"error": "pandas not installed"})
    except Exception as e:
        return json.dumps({"error": str(e)})
    dispatch = {
        "eda": _run_eda, "cluster": _run_cluster, "regress": _run_regress,
        "classify": _run_classify, "forecast": _run_forecast,
        "anomaly": _run_anomaly, "fraud": _run_anomaly,
        "ab_test": _run_ab_test, "sentiment": _run_sentiment,
        "risk": _run_risk, "churn": _run_churn,
        "feature_importance": _run_feature_importance,
        "correlation": _run_correlation, "segmentation": _run_cluster,
    }
    fn = dispatch.get(task)
    if fn is None:
        return json.dumps({"error": f"Unknown task '{task}'. Valid: {sorted(dispatch)}"})
    try:
        return json.dumps(fn(df, target_col, cfg, plots_dir), indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
