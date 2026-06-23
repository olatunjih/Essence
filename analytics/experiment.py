"""ExperimentTracker: MLflow / W&B / TensorBoard."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# EXPERIMENT TRACKER  (MLflow / W&B / TensorBoard unified interface)
# ══════════════════════════════════════════════════════════════════════════════

class ExperimentTracker:
    """
    Unified experiment logging interface.
    Priority: W&B (WANDB_API_KEY set) → MLflow (MLFLOW_TRACKING_URI set)
              → TensorBoard (tensorboard installed) → local JSONL fallback.
    Always succeeds — degradation is silent so training runs never fail
    due to a missing tracker.
    """
    def __init__(self, workspace: Path):
        self._ws      = workspace
        self._backend = "jsonl"
        self._run_id  = ""
        self._run: Any = None
        self._log_path: Path | None = None
        self._tb_writer: Any = None
        # Detect best available backend
        if os.environ.get("WANDB_API_KEY"):
            try:
                import wandb  # type: ignore  # noqa: F401
                self._backend = "wandb"
            except ImportError: pass
        if self._backend == "jsonl" and os.environ.get("MLFLOW_TRACKING_URI"):
            try:
                import mlflow  # type: ignore  # noqa: F401
                self._backend = "mlflow"
            except ImportError: pass
        if self._backend == "jsonl":
            try:
                from torch.utils.tensorboard import SummaryWriter  # type: ignore  # noqa: F401
                self._backend = "tensorboard"
            except ImportError: pass

    def start_run(self, name: str, tags: dict | None = None) -> str:
        self._run_id = name
        tags         = tags or {}
        try:
            if self._backend == "wandb":
                import wandb  # type: ignore
                self._run = wandb.init(name=name, tags=list(tags.keys()),
                                       config=tags, reinit=True)
            elif self._backend == "mlflow":
                import mlflow  # type: ignore
                mlflow.start_run(run_name=name, tags=tags)
                self._run = True
            elif self._backend == "tensorboard":
                from torch.utils.tensorboard import SummaryWriter  # type: ignore
                tb_dir = self._ws / "runs" / name
                tb_dir.mkdir(parents=True, exist_ok=True)
                self._tb_writer = SummaryWriter(str(tb_dir))
            else:
                self._log_path = self._ws / "experiments" / f"{name}.jsonl"
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            self._backend = "jsonl"
            self._log_path = self._ws / "experiments" / f"{name}.jsonl"
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        return self._run_id

    def log(self, metrics: dict, step: int | None = None) -> None:
        try:
            if self._backend == "wandb":
                import wandb  # type: ignore
                wandb.log(metrics, step=step)
            elif self._backend == "mlflow":
                import mlflow  # type: ignore
                for k, v in metrics.items():
                    mlflow.log_metric(k, float(v), step=step)
            elif self._backend == "tensorboard" and self._tb_writer:
                for k, v in metrics.items():
                    self._tb_writer.add_scalar(k, float(v),
                                               global_step=step or 0)
            else:
                record = json.dumps({"step": step, "ts": time.time(), **metrics})
                if self._log_path:
                    with open(self._log_path, "a", encoding="utf-8") as f:
                        f.write(record + "\n")
        except Exception as _exc:
            log.debug('experiment_tracker_log_error',
                      extra={'error': str(_exc)})

    def log_artifact(self, path: str) -> None:
        try:
            if self._backend == "wandb":
                import wandb  # type: ignore
                wandb.log_artifact(path)
            elif self._backend == "mlflow":
                import mlflow  # type: ignore
                mlflow.log_artifact(path)
        except Exception as _exc:
            log.debug('experiment_tracker_artifact_error',
                      extra={'error': str(_exc)})

    def end_run(self, status: str = "FINISHED") -> None:
        try:
            if self._backend == "wandb":
                import wandb  # type: ignore
                wandb.finish()
            elif self._backend == "mlflow":
                import mlflow  # type: ignore
                mlflow.end_run(status=status)
            elif self._tb_writer:
                self._tb_writer.close()
        except Exception as _exc:
            log.debug('experiment_tracker_end_run_error',
                      extra={'error': str(_exc)})


# ══════════════════════════════════════════════════════════════════════════════
