"""
ml/integrations/mlflow_tracker.py

orchestrator.py 의 run / 피처 엔지니어링(FE) 결과를 MLflow Experiment 에 기록.

설계 원칙:
  - 자체완결형: automation.config 등 외부 의존 없음. 설정은 pipeline_config.json
    의 "mlflow" 키 또는 환경변수에서 읽고, 없으면 로컬 sqlite 로 기본 동작.
  - graceful: mlflow 미설치/초기화 실패/로깅 실패 어떤 경우에도 예외를 삼켜
    orchestrator 파이프라인 실행을 절대 막지 않는다.

config 예 (ml/pipeline_config.json):
    "mlflow": {
        "tracking_uri": "sqlite:///mlflow.db",
        "experiment": "ais-anomaly-detection"
    }

환경변수 override: MLFLOW_TRACKING_URI, MLFLOW_EXPERIMENT

사용 예 (orchestrator):
    from ml.integrations.mlflow_tracker import MLflowTracker
    tracker = MLflowTracker()
    with tracker.start_run(run_name=branch,
                           params={"model": "dcdetect", "epochs": 5},
                           tags={"branch": branch}):
        tracker.log_fe_result(baseline_det=56.6, best_det=81.8,
                              det_fp5=88.0, det_fp10=92.0,
                              threshold=0.0123, n_features=24, n_adopted=2)
        tracker.log_scenarios(scenario_fp1)
        tracker.log_artifacts(onnx_path, scaler_path, threshold_path)
"""
import os
import re
import json
from contextlib import contextmanager

try:
    import mlflow
    _HAS_MLFLOW = True
except Exception:  # ImportError 또는 의존성 문제
    _HAS_MLFLOW = False

CONFIG_PATH = "ml/pipeline_config.json"
DEFAULT_URI = "sqlite:///mlflow.db"
DEFAULT_EXPERIMENT = "ais-anomaly-detection"

# MLflow metric 키 허용 문자: 영숫자 _ - . space /
_METRIC_OK = re.compile(r"[^0-9A-Za-z_\-. /]")


def _load_cfg() -> tuple[str, str]:
    uri, exp = DEFAULT_URI, DEFAULT_EXPERIMENT
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            m = json.load(f).get("mlflow", {}) or {}
        uri = m.get("tracking_uri", uri)
        exp = m.get("experiment", exp)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    uri = os.environ.get("MLFLOW_TRACKING_URI", uri)
    exp = os.environ.get("MLFLOW_EXPERIMENT", exp)
    return uri, exp


def _safe_metric_name(name, idx: int) -> str:
    s = _METRIC_OK.sub("", str(name)).strip(" _-./")
    return s if s else f"scen_{idx}"


class MLflowTracker:
    """orchestrator 결과를 MLflow 에 기록하는 graceful 래퍼."""

    def __init__(self):
        self.enabled = _HAS_MLFLOW
        self._run = None
        if not self.enabled:
            print("[mlflow] mlflow 미설치 → 실험 추적 건너뜀")
            return
        uri, exp = _load_cfg()
        try:
            mlflow.set_tracking_uri(uri)
            mlflow.set_experiment(exp)
            self.uri, self.experiment = uri, exp
            print(f"[mlflow] tracking_uri={uri}  experiment={exp}")
        except Exception as e:
            print(f"[mlflow] 초기화 실패 → 추적 비활성화: {e}")
            self.enabled = False

    @contextmanager
    def start_run(self, run_name: str, params: dict | None = None,
                  tags: dict | None = None):
        if not self.enabled:
            yield self
            return
        try:
            with mlflow.start_run(run_name=run_name) as run:
                self._run = run
                if tags:
                    mlflow.set_tags(tags)
                if params:
                    mlflow.log_params({k: v for k, v in params.items()
                                       if v is not None})
                yield self
        except Exception as e:
            print(f"[mlflow] start_run 실패(무시): {e}")
            yield self
        finally:
            self._run = None

    def log_metrics(self, step: int | None = None, **metrics):
        if not self.enabled:
            return
        clean = {}
        for k, v in metrics.items():
            if v is None:
                continue
            try:
                clean[k] = float(v)
            except (TypeError, ValueError):
                continue
        if not clean:
            return
        try:
            if step is not None:
                mlflow.log_metrics(clean, step=step)
            else:
                mlflow.log_metrics(clean)
        except Exception as e:
            print(f"[mlflow] log_metrics 실패(무시): {e}")

    def log_fe_result(self, baseline_det=None, best_det=None, det_fp5=None,
                      det_fp10=None, threshold=None, n_features=None,
                      n_adopted=None):
        """FE 최종 결과 지표 기록."""
        gain = None
        if best_det is not None and baseline_det is not None:
            gain = best_det - baseline_det
        self.log_metrics(
            baseline_det_fp1=baseline_det,
            det_fp1=best_det,
            det_fp1_gain=gain,
            det_fp5=det_fp5,
            det_fp10=det_fp10,
            threshold=threshold,
            n_features=n_features,
            n_adopted=n_adopted,
        )

    def log_scenarios(self, scenarios: dict | None, prefix: str = "scen"):
        """시나리오별 탐지율 기록. 한글 시나리오명은 ASCII 안전키로 변환."""
        if not self.enabled or not scenarios:
            return
        clean = {}
        for i, (name, rate) in enumerate(scenarios.items()):
            key = f"{prefix}/{_safe_metric_name(name, i)}"
            try:
                clean[key] = float(rate)
            except (TypeError, ValueError):
                continue
        if clean:
            self.log_metrics(**clean)

    def log_artifacts(self, *paths):
        if not self.enabled:
            return
        for p in paths:
            if p and os.path.exists(p):
                try:
                    mlflow.log_artifact(p)
                except Exception as e:
                    print(f"[mlflow] log_artifact 실패(무시) {p}: {e}")

    def set_tag(self, key: str, value):
        if not self.enabled:
            return
        try:
            mlflow.set_tag(key, value)
        except Exception:
            pass

    def get_run_id(self) -> str | None:
        return self._run.info.run_id if self._run else None
