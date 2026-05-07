"""
ONNX 기반 ML 이상 탐지기

단일 모델 및 가중 앙상블(DCdetector 0.7 + TranAD 0.3) 지원.
ais_ids_pi/src/ais_ml.cpp 의 Python 대응 구현.

모델 파일 위치 (환경변수 MODEL_DIR, 기본 /models):
  단일 모드:
    model.onnx
    scaler.json
    threshold.txt
  앙상블 모드 (ensemble_config.json 존재 시 자동 선택):
    ensemble_config.json
    model_dcdetect.onnx, model_tranad.onnx  (또는 config 지정 파일)
    scaler_dcdetect.json, scaler_tranad.json
    threshold_weighted_ensemble.txt

ensemble_config.json 형식:
  {
    "models":    ["model_dcdetect.onnx", "model_tranad.onnx"],
    "scalers":   ["scaler_dcdetect.json", "scaler_tranad.json"],
    "threshold": "threshold_weighted_ensemble.txt",
    "weights":   [0.7, 0.3]
  }
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
import onnxruntime as ort

from feature import FEATURES, N_FEAT, SEQ_LEN


@dataclass
class _ModelEntry:
    session: ort.InferenceSession
    input_name: str
    mins:    List[float]
    maxs:    List[float]
    weight:  float = 1.0


class MLDetector:
    def __init__(self, model_dir: str):
        self.model_dir  = model_dir
        self._entries:   List[_ModelEntry] = []
        self._threshold: float = 0.0
        self.loaded:     bool  = False
        self.status_msg: str   = "not initialized"

    # ── 공개 메서드 ─────────────────────────────────────────────────

    def load(self) -> bool:
        """
        model_dir 에서 모델 파일을 로드.
        ensemble_config.json 이 있으면 앙상블, 없으면 단일 모델.
        """
        cfg_path = os.path.join(self.model_dir, "ensemble_config.json")
        if os.path.exists(cfg_path):
            return self._load_ensemble(cfg_path)
        return self._load_single()

    def detect(self, seq: List[List[float]]) -> Tuple[bool, float, str]:
        """
        seq: SEQ_LEN × N_FEAT 피처 행렬
        반환: (is_anomaly, score, top_features_desc)
        """
        if not self.loaded:
            return False, 0.0, ""
        if len(seq) != SEQ_LEN or len(seq[0]) != N_FEAT:
            return False, 0.0, ""

        feat_errs: "np.ndarray | None" = None

        if len(self._entries) == 1:
            e = self._entries[0]
            scaled = self._scale(seq, e.mins, e.maxs)
            score, fe = self._infer_mse(e.session, e.input_name, scaled)
            feat_errs = fe
        else:
            score = 0.0
            for e in self._entries:
                scaled = self._scale(seq, e.mins, e.maxs)
                s, fe = self._infer_mse(e.session, e.input_name, scaled)
                score += e.weight * s
                if fe is not None:
                    feat_errs = (feat_errs + e.weight * fe
                                 if feat_errs is not None
                                 else e.weight * fe)

        return score > self._threshold, score, _top_features(feat_errs)

    # ── 내부 로드 ───────────────────────────────────────────────────

    def _load_single(self) -> bool:
        model_p = os.path.join(self.model_dir, "model.onnx")
        scaler_p = os.path.join(self.model_dir, "scaler.json")
        thr_p    = os.path.join(self.model_dir, "threshold.txt")

        for p in (model_p, scaler_p, thr_p):
            if not os.path.exists(p):
                self.status_msg = f"missing file: {p}"
                return False
        try:
            sess = ort.InferenceSession(model_p,
                                        providers=["CPUExecutionProvider"])
            mins, maxs = self._read_scaler(scaler_p)
            self._entries   = [_ModelEntry(session=sess,
                                           input_name=sess.get_inputs()[0].name,
                                           mins=mins,
                                           maxs=maxs, weight=1.0)]
            self._threshold = self._read_threshold(thr_p)
            self.loaded     = True
            self.status_msg = f"single model loaded ({os.path.basename(model_p)})"
            return True
        except Exception as exc:
            self.status_msg = f"single model load failed: {exc}"
            return False

    def _load_ensemble(self, cfg_path: str) -> bool:
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)

            model_names = cfg["models"]
            scaler_names = cfg.get("scalers",
                           [cfg.get("scaler", "")] * len(model_names))
            weights_raw  = cfg.get("weights",
                           [1.0 / len(model_names)] * len(model_names))
            thr_name     = cfg.get("threshold", "threshold_weighted_ensemble.txt")

            if len(scaler_names) != len(model_names):
                raise ValueError("scalers count must match models count")
            if len(weights_raw) != len(model_names):
                raise ValueError("weights count must match models count")

            scaler_names = self._resolve_scalers(scaler_names)
            required = [
                *(os.path.join(self.model_dir, name) for name in model_names),
                *(os.path.join(self.model_dir, name) for name in scaler_names),
                os.path.join(self.model_dir, thr_name),
            ]
            for p in required:
                if not os.path.exists(p):
                    self.status_msg = f"missing file: {p}"
                    return False

            w_sum = sum(float(w) for w in weights_raw) or 1.0
            weights = [float(w) / w_sum for w in weights_raw]

            entries: List[_ModelEntry] = []
            for mn, sn, w in zip(model_names, scaler_names, weights):
                mp = os.path.join(self.model_dir, mn)
                sp = os.path.join(self.model_dir, sn)
                sess = ort.InferenceSession(mp,
                                            providers=["CPUExecutionProvider"])
                mins, maxs = self._read_scaler(sp)
                entries.append(_ModelEntry(session=sess,
                                           input_name=sess.get_inputs()[0].name,
                                           mins=mins,
                                           maxs=maxs, weight=w))

            thr_p = os.path.join(self.model_dir, thr_name)
            self._entries   = entries
            self._threshold = self._read_threshold(thr_p)
            self.loaded     = True
            self.status_msg = (
                f"ensemble loaded ({len(entries)} models, "
                f"weights={[round(w, 3) for w in weights]})"
            )
            return True
        except Exception as exc:
            self.status_msg = f"ensemble load failed: {exc}"
            return False

    # ── 내부 헬퍼 ───────────────────────────────────────────────────

    def _resolve_scalers(self, scaler_names: List[str]) -> List[str]:
        missing = [
            name for name in scaler_names
            if not os.path.exists(os.path.join(self.model_dir, name))
        ]
        if not missing:
            return scaler_names

        available = [
            name for name in os.listdir(self.model_dir)
            if name.lower().startswith("scaler") and name.lower().endswith(".json")
        ]
        if len(available) == 1:
            return [available[0] for _ in scaler_names]
        return scaler_names

    @staticmethod
    def _read_scaler(path: str) -> Tuple[List[float], List[float]]:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        return list(j["min"]), list(j["max"])

    @staticmethod
    def _read_threshold(path: str) -> float:
        with open(path, encoding="utf-8") as f:
            return float(f.readline().split()[0])

    @staticmethod
    def _scale(seq: List[List[float]],
               mins: List[float],
               maxs: List[float]) -> List[List[float]]:
        result = []
        for row in seq:
            scaled_row = []
            for v, mn, mx in zip(row, mins, maxs):
                d = mx - mn
                scaled_row.append((v - mn) / d if d != 0.0 else 0.0)
            result.append(scaled_row)
        return result

    @staticmethod
    def _infer_mse(
        session: ort.InferenceSession,
        input_name: str,
        seq_scaled: List[List[float]],
    ) -> "Tuple[float, np.ndarray | None]":
        x = np.array(seq_scaled, dtype=np.float32)[np.newaxis]   # (1, T, F)
        outputs = session.run(None, {input_name: x})
        out = next((o for o in outputs if getattr(o, "shape", None) == x.shape), outputs[0])
        out_arr = np.asarray(out, dtype=np.float32)
        if out_arr.shape == x.shape or out_arr.size == x.size:
            r = out_arr.reshape(x.shape) if out_arr.size == x.size else out_arr
            diff2 = (r - x) ** 2                        # (1, T, F)
            feat_errs = diff2.mean(axis=(0, 1))          # (F,)
            return float(diff2.mean()), feat_errs
        if out_arr.size == 1:
            return float(out_arr.reshape(-1)[0]), None
        return float(out_arr.mean()), None


def _top_features(feat_errs: "np.ndarray | None", n: int = 3) -> str:
    """피처별 재구성 오류에서 상위 n개 피처 이름을 반환."""
    if feat_errs is None or len(feat_errs) == 0:
        return "anomaly"
    top_idx = np.argsort(feat_errs)[::-1][:n]
    return ", ".join(FEATURES[i] for i in top_idx)
