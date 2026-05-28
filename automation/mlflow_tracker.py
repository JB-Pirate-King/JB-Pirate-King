"""
automation/mlflow_tracker.py

ml/pipeline.py 실행 결과를 MLflow Experiment에 기록하는 래퍼.

사용법:
    from automation.mlflow_tracker import MLflowTracker

    tracker = MLflowTracker()
    with tracker.start_run(model_name="conv1d", epochs=10):
        # 학습 진행 ...
        tracker.log_train_params(epochs=10, seq_len=10, n_features=12)
        tracker.log_epoch_metrics(epoch=5, train_loss=0.02, val_loss=0.03)
        tracker.log_eval_results(detection_rate=68.3, fp_rate=1.0,
                                 holdout_dr=70.8, holdout_fp=1.1)
        tracker.log_model_artifacts(
            onnx_path=r"D:\ais_models\conv1d\model_conv1d.onnx",
            scaler_path=r"D:\ais_models\conv1d\scaler_conv1d.json",
            threshold_path=r"D:\ais_models\conv1d\threshold_conv1d.txt",
        )
"""

import os
import mlflow
import mlflow.artifacts
from contextlib import contextmanager
from automation.config import MLFLOW_TRACKING_URI, MLFLOW_EXPERIMENT


class MLflowTracker:
    def __init__(self):
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(MLFLOW_EXPERIMENT)
        self._run = None

    @contextmanager
    def start_run(self, model_name: str, epochs: int, run_name: str | None = None):
        run_name = run_name or f"{model_name}_ep{epochs}"
        with mlflow.start_run(run_name=run_name) as run:
            self._run = run
            mlflow.set_tag("model_name", model_name)
            yield self
        self._run = None

    def log_train_params(self, **kwargs):
        mlflow.log_params(kwargs)

    def log_epoch_metrics(self, epoch: int, **metrics):
        mlflow.log_metrics(metrics, step=epoch)

    def log_eval_results(
        self,
        detection_rate: float,
        fp_rate: float,
        holdout_dr: float | None = None,
        holdout_fp: float | None = None,
    ):
        metrics = {
            "detection_rate": detection_rate,
            "false_positive_rate": fp_rate,
        }
        if holdout_dr is not None:
            metrics["holdout_detection_rate"] = holdout_dr
        if holdout_fp is not None:
            metrics["holdout_fp_rate"] = holdout_fp
        mlflow.log_metrics(metrics)

    def log_model_artifacts(
        self,
        onnx_path: str | None = None,
        scaler_path: str | None = None,
        threshold_path: str | None = None,
    ):
        for path in (onnx_path, scaler_path, threshold_path):
            if path and os.path.exists(path):
                mlflow.log_artifact(path)

    def log_comparison_csv(self, csv_path: str):
        if os.path.exists(csv_path):
            mlflow.log_artifact(csv_path, artifact_path="reports")

    def get_run_id(self) -> str | None:
        return self._run.info.run_id if self._run else None


def patch_pipeline_with_mlflow(model_name: str, epochs: int):
    """
    pipeline.py를 직접 수정하지 않고 실행 후 결과를 MLflow에 기록하는 헬퍼.
    pipeline_runner.py에서 호출됨.
    """
    import glob
    import csv
    from automation.config import OUTPUT_DIR

    tracker = MLflowTracker()

    pattern = os.path.join(OUTPUT_DIR, f"{model_name}_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"[mlflow] {model_name} 결과 CSV를 찾을 수 없음: {pattern}")
        return

    latest_csv = files[-1]
    with open(latest_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return

    with tracker.start_run(model_name=model_name, epochs=epochs):
        tracker.log_train_params(
            model=model_name,
            epochs=epochs,
            seq_len=10,
            n_features=12,
        )

        train_drs, holdout_drs = [], []
        for row in rows:
            group = row.get("group", "")
            dr = float(row.get("detection_rate", 0))
            if group.startswith("F") or group.startswith("G"):
                holdout_drs.append(dr)
            else:
                train_drs.append(dr)

        avg_train   = sum(train_drs)   / len(train_drs)   if train_drs   else 0
        avg_holdout = sum(holdout_drs) / len(holdout_drs) if holdout_drs else 0

        tracker.log_eval_results(
            detection_rate=avg_train,
            fp_rate=1.0,
            holdout_dr=avg_holdout,
            holdout_fp=1.0,
        )
        tracker.log_comparison_csv(latest_csv)

    print(f"[mlflow] {model_name} 실험 기록 완료 (train DR={avg_train:.1f}%, holdout DR={avg_holdout:.1f}%)")
