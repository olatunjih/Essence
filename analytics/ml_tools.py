"""ML training +  fine-tuner scaffold."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# ML TRAINING TOOL  (PyTorch / sklearn / end-to-end with Optuna HPO)
# ══════════════════════════════════════════════════════════════════════════════

def _tool_train_model(dataset_path: str, target_col: str,
                       model_type: str = "auto",
                       config: dict | None = None,
                       workspace: Path | None = None,
                       tracker: "ExperimentTracker | None" = None) -> str:
    """
    End-to-end model training.
    model_type: auto | sklearn_rf | sklearn_gbm | sklearn_lr | pytorch_mlp |
                pytorch_cnn | transformers_clf | transformers_ner
    Optuna HPO fires when config.hpo=True and optuna is installed.
    Results written to workspace/models/<run_id>/.
    """
    cfg  = config or {}
    ws   = workspace or Path.cwd()
    models_dir = ws / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"run_{int(time.time())}"
    run_dir = models_dir / run_id
    run_dir.mkdir()

    if tracker:
        tracker.start_run(run_id, tags={"model_type": model_type,
                                         "dataset": dataset_path})

    try:
        import pandas as pd  # type: ignore
        from sklearn.model_selection import train_test_split  # type: ignore
        from sklearn.preprocessing import LabelEncoder, StandardScaler  # type: ignore
        from sklearn.metrics import (accuracy_score, f1_score,      # type: ignore
                                      r2_score, mean_absolute_error)

        df  = pd.read_csv(Path(dataset_path).expanduser())
        y   = df[target_col].dropna()
        X   = df.drop(columns=[target_col]).select_dtypes("number").loc[y.index].fillna(0)
        is_clf = y.dtype == object or y.nunique() < 20
        if is_clf:
            le = LabelEncoder(); y = le.fit_transform(y.astype(str))
        else:
            y = y.values
        sc = StandardScaler()
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42)
        Xtr_s = sc.fit_transform(Xtr); Xte_s = sc.transform(Xte)

        # ── Optuna HPO ──────────────────────────────────────────────────────
        if cfg.get("hpo", False):
            try:
                import optuna  # type: ignore
                optuna.logging.set_verbosity(optuna.logging.WARNING)
                from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor  # type: ignore

                def objective(trial: Any) -> float:
                    n  = trial.suggest_int("n_estimators", 50, 300)
                    md = trial.suggest_int("max_depth", 3, 15)
                    if is_clf:
                        m = RandomForestClassifier(n_estimators=n, max_depth=md,
                                                   random_state=42)
                        m.fit(Xtr_s, ytr)
                        return float(f1_score(yte, m.predict(Xte_s), average="weighted"))
                    else:
                        m = RandomForestRegressor(n_estimators=n, max_depth=md,
                                                  random_state=42)
                        m.fit(Xtr_s, ytr)
                        return float(r2_score(yte, m.predict(Xte_s)))

                study = optuna.create_study(direction="maximize")
                study.optimize(objective, n_trials=cfg.get("n_trials", 20))
                best = study.best_params
                cfg.update(best)
            except ImportError:
                pass

        # ── Model selection and training ─────────────────────────────────────
        from sklearn.ensemble import (RandomForestClassifier,         # type: ignore
                                       RandomForestRegressor,
                                       GradientBoostingClassifier,
                                       GradientBoostingRegressor)
        from sklearn.linear_model import LogisticRegression, Ridge    # type: ignore

        if model_type == "auto":
            model_type = "sklearn_gbm"
        mtype_map = {
            "sklearn_rf":  (RandomForestClassifier if is_clf else RandomForestRegressor),
            "sklearn_gbm": (GradientBoostingClassifier if is_clf else GradientBoostingRegressor),
            "sklearn_lr":  (LogisticRegression if is_clf else Ridge),
        }
        ModelCls = mtype_map.get(model_type)

        # PyTorch MLP path — run in a daemon thread so long training
        # does not block the agent loop. Returns immediately with run_id.
        if model_type == "pytorch_mlp" or ModelCls is None:
            try:
                import torch  # type: ignore  # noqa: F401
            except ImportError:
                return json.dumps({"error": "torch not installed: pip install torch"})

            # Capture training arrays by value before spawning daemon thread
            # to prevent premature GC of the enclosing scope's locals.
            _Xtr_s  = Xtr_s.copy()
            _Xte_s  = Xte_s.copy()
            _ytr    = ytr.copy() if hasattr(ytr, "copy") else list(ytr)
            _yte    = yte.copy() if hasattr(yte, "copy") else list(yte)
            _is_clf = bool(is_clf)
            _cfg_t  = dict(cfg)
            _run_dir_t = run_dir
            _run_id_t  = run_id

            def _pytorch_train() -> None:
                _status = _run_dir_t / "status.json"
                _status.write_text(json.dumps({"status": "running", "run_id": _run_id_t}), encoding="utf-8")
                try:
                    import torch  # type: ignore
                    import torch.nn as nn
                    import numpy as np

                    class MLP(nn.Module):
                        def __init__(self, in_f: int, out_f: int):
                            super().__init__()
                            h = _cfg_t.get("hidden", 128)
                            self.net = nn.Sequential(
                                nn.Linear(in_f, h), nn.ReLU(), nn.Dropout(0.2),
                                nn.Linear(h, h), nn.ReLU(),
                                nn.Linear(h, out_f))
                        def forward(self, x: Any) -> Any:
                            return self.net(x)

                    n_out   = len(set(_ytr.tolist() if hasattr(_ytr,"tolist") else _ytr)) if _is_clf else 1
                    model_t = MLP(_Xtr_s.shape[1], n_out)
                    opt     = torch.optim.Adam(model_t.parameters(),
                                               lr=_cfg_t.get("lr", 1e-3))
                    loss_fn = nn.CrossEntropyLoss() if _is_clf else nn.MSELoss()
                    xt = torch.tensor(_Xtr_s, dtype=torch.float32)
                    yt = torch.tensor(_ytr if isinstance(_ytr, list) else _ytr,
                                      dtype=torch.long if _is_clf else torch.float32)
                    epochs = _cfg_t.get("epochs", 20)
                    for ep in range(epochs):
                        model_t.train()
                        opt.zero_grad()
                        out = model_t(xt)
                        loss = loss_fn(out, yt if _is_clf else yt.unsqueeze(1))
                        loss.backward(); opt.step()
                        if tracker: tracker.log({"loss": float(loss)}, step=ep)
                    torch.save(model_t.state_dict(), str(_run_dir_t / "model.pt"))
                    model_t.eval()
                    with torch.no_grad():
                        xe = torch.tensor(_Xte_s, dtype=torch.float32)
                        pred_t = model_t(xe)
                        if _is_clf:
                            pred_np = pred_t.argmax(dim=1).numpy()
                            score   = float(accuracy_score(_yte, pred_np))
                            metric  = "accuracy"
                        else:
                            pred_np = pred_t.squeeze(1).numpy()
                            score   = float(r2_score(_yte, pred_np))
                            metric  = "r2"
                    if tracker:
                        tracker.log({metric: score})
                        tracker.end_run()
                    _status.write_text(json.dumps({
                        "status": "done", "run_id": _run_id_t,
                        metric: round(score, 4),
                        "saved": str(_run_dir_t / "model.pt")}),
                        encoding="utf-8")
                except ImportError as _ie:
                    _status.write_text(json.dumps(
                        {"status": "error", "error": str(_ie)}), encoding="utf-8")
                except Exception as _te:
                    _status.write_text(json.dumps(
                        {"status": "error", "error": str(_te)}), encoding="utf-8")

            _t = threading.Thread(target=_pytorch_train, daemon=True)
            _t.start()
            return json.dumps({"model_type": "pytorch_mlp", "run_id": _run_id_t,
                               "status": "launched",
                               "monitor": str(_run_dir_t / "status.json")})

        n_est = cfg.get("n_estimators", 100)
        md    = cfg.get("max_depth", None)
        try:
            model = ModelCls(n_estimators=n_est, max_depth=md, random_state=42)
        except TypeError:
            # LogisticRegression / Ridge do not accept n_estimators
            model = ModelCls()
        model.fit(Xtr_s, ytr)
        pred = model.predict(Xte_s)
        if is_clf:
            score  = float(accuracy_score(yte, pred))
            f1     = float(f1_score(yte, pred, average="weighted"))
            metrics = {"accuracy": round(score, 4), "f1_weighted": round(f1, 4)}
        else:
            metrics = {"r2": round(float(r2_score(yte, pred)), 4),
                       "mae": round(float(mean_absolute_error(yte, pred)), 4)}
        if tracker:
            tracker.log(metrics)
            tracker.end_run()
        # Save model
        try:
            import joblib  # type: ignore
            joblib.dump(model, str(run_dir / "model.joblib"))
            saved = str(run_dir / "model.joblib")
        except ImportError:
            saved = str(run_dir)
        return json.dumps({"model_type": model_type, "run_id": run_id,
                            "metrics": metrics, "saved": saved})
    except ImportError as e:
        return json.dumps({"error": f"Missing dependency: {e}"})
    except Exception as e:
        if tracker:
            try: tracker.end_run("FAILED")
            except Exception: pass
        return json.dumps({"error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════

# FINE-TUNER SCAFFOLD  (Llama3 / Qwen2.5 / Mistral / Phi via unsloth/PEFT)
# ══════════════════════════════════════════════════════════════════════════════

def _tool_finetune(base_model: str, dataset_path: str,
                    output_dir: str = "",
                    config: dict | None = None,
                    workspace: Path | None = None) -> str:
    """
    Fine-tune an LLM on a local dataset.
    Generates a training script and launches it as a managed subprocess.
    Supported backends: unsloth (T2/T3, fastest), PEFT LoRA (T2+), full-param (T3 only).
    Dataset format: JSONL with {"prompt": "...", "response": "..."} or
                    {"instruction": "...", "input": "...", "output": "..."} (Alpaca).

    Security: all parameters are written to a JSON sidecar file and loaded by
    the training script — never interpolated as f-string literals. This prevents
    script injection via paths containing quotes, newlines, or backslashes.
    """
    cfg      = config or {}
    ws       = workspace or Path.cwd()
    out_dir  = Path(output_dir) if output_dir else ws / "models" / f"ft_{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)
    script_path = out_dir / "finetune.py"

    # ── Write params to a JSON sidecar (no f-string injection) ───────────────
    params = {
        "base_model":   base_model,
        "dataset_path": dataset_path,
        "output_dir":   str(out_dir),
        "epochs":       cfg.get("epochs", 3),
        "lr":           cfg.get("lr", 2e-4),
        "batch_size":   cfg.get("batch_size", 4),
        "max_seq_len":  cfg.get("max_seq_len", 2048),
        "lora_r":       cfg.get("lora_r", 16),
        "bf16":         cfg.get("bf16", True),
    }
    params_path = out_dir / "finetune_params.json"
    params_path.write_text(json.dumps(params, indent=2), encoding="utf-8")

    script = textwrap.dedent("""\
        #!/usr/bin/env python3
        # Essence auto-generated fine-tune script — params loaded from sidecar JSON
        import os, json, sys
        from pathlib import Path

        # Load all params from the sidecar — no inline f-string injection
        _params_path = Path(__file__).parent / "finetune_params.json"
        _p = json.loads(_params_path.read_text(encoding="utf-8"))
        BASE_MODEL   = _p["base_model"]
        DATASET_PATH = _p["dataset_path"]
        OUTPUT_DIR   = _p["output_dir"]
        EPOCHS       = _p["epochs"]
        LR           = _p["lr"]
        BATCH        = _p["batch_size"]
        MAX_SEQ      = _p["max_seq_len"]
        LORA_R       = _p["lora_r"]
        USE_BF16     = _p["bf16"]

        # ── Try unsloth first (2-4x faster, T2+) ──────────────────────────────
        try:
            from unsloth import FastLanguageModel  # type: ignore
            model, tokenizer = FastLanguageModel.from_pretrained(
                model_name=BASE_MODEL, max_seq_length=MAX_SEQ,
                dtype=None, load_in_4bit=True)
            model = FastLanguageModel.get_peft_model(
                model, r=LORA_R, target_modules=["q_proj","k_proj","v_proj","o_proj"],
                lora_alpha=16, lora_dropout=0, bias="none",
                use_gradient_checkpointing="unsloth")
            backend = "unsloth"
        except ImportError:
            # ── PEFT LoRA fallback ─────────────────────────────────────────
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig  # type: ignore
            from peft import get_peft_model, LoraConfig, TaskType  # type: ignore
            import torch
            bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16)
            tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
            model     = AutoModelForCausalLM.from_pretrained(
                BASE_MODEL, quantization_config=bnb, device_map="auto")
            lora_cfg  = LoraConfig(r=LORA_R, lora_alpha=16, task_type=TaskType.CAUSAL_LM,
                                    target_modules=["q_proj","v_proj"])
            model     = get_peft_model(model, lora_cfg)
            backend   = "peft_lora"

        # ── Dataset loading ────────────────────────────────────────────────
        from datasets import load_dataset  # type: ignore
        ds_path = Path(DATASET_PATH)
        if ds_path.suffix == ".jsonl":
            ds = load_dataset("json", data_files=str(ds_path), split="train")
        elif ds_path.suffix == ".csv":
            ds = load_dataset("csv", data_files=str(ds_path), split="train")
        else:
            ds = load_dataset(DATASET_PATH, split="train")

        def _format(ex):
            if "prompt" in ex and "response" in ex:
                return {"text": f"{ex['prompt']}\
\
{ex['response']}<|endoftext|>"}
            elif "instruction" in ex:
                inp = ex.get("input","")
                return {"text": (f"### Instruction:\
{ex['instruction']}\
"
                                 f"### Input:\
{inp}\
"
                                 f"### Response:\
{ex.get('output','')}{tokenizer.eos_token}")}
            return {"text": str(ex)}

        ds = ds.map(_format, remove_columns=ds.column_names)

        # ── Trainer ────────────────────────────────────────────────────────
        from transformers import TrainingArguments  # type: ignore
        from trl import SFTTrainer  # type: ignore
        args = TrainingArguments(
            output_dir=OUTPUT_DIR, num_train_epochs=EPOCHS,
            per_device_train_batch_size=BATCH, learning_rate=LR,
            fp16=False, bf16=USE_BF16,
            logging_steps=10, save_strategy="epoch",
            report_to=["mlflow"] if os.environ.get("MLFLOW_TRACKING_URI") else ["none"],
        )
        trainer = SFTTrainer(
            model=model, tokenizer=tokenizer, train_dataset=ds,
            dataset_text_field="text", max_seq_length=MAX_SEQ, args=args)
        trainer.train()
        model.save_pretrained(OUTPUT_DIR)
        tokenizer.save_pretrained(OUTPUT_DIR)
        print(f"[finetune] backend={backend} done -> {OUTPUT_DIR}")
    """)
    script_path.write_text(script, encoding="utf-8")

    # Launch training as a detached subprocess — training can take hours;
    # blocking proc.wait() would deadlock long agent tasks.
    log_path  = out_dir / "finetune.log"
    pid_file  = out_dir / "status.json"
    try:
        with open(log_path, "w", encoding="utf-8") as log_f:
            proc = subprocess.Popen(
                [sys.executable, str(script_path)],
                stdout=log_f, stderr=subprocess.STDOUT,
                cwd=str(ws))
        pid_file.write_text(json.dumps({
            "pid": proc.pid, "status": "running",
            "script": str(script_path), "log": str(log_path),
            "params": str(params_path), "output_dir": str(out_dir)
        }), encoding="utf-8")
        return (f"[finetune] Training launched (pid={proc.pid}).\n"
                f"Script : {script_path}\n"
                f"Params : {params_path}\n"
                f"Log    : {log_path}\n"
                f"Status : {pid_file}\n"
                f"Monitor: tail -f {log_path}")
    except Exception as e:
        return (f"[finetune] Script written to {script_path}.\n"
                f"Run manually: python {script_path}\nError launching: {e}")


# ══════════════════════════════════════════════════════════════════════════════
