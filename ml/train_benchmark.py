"""
AIS 선박 이상 탐지 벤치마크 학습 스크립트

정상 AIS 시퀀스로 비지도 학습 후 재구성 오차(MSE) 기반으로 이상 판정.
모든 모델은 동일한 ONNX 인터페이스로 export되어 eval_anomaly.py 및
OpenCPN 플러그인과 호환된다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
지원 모델 (9종)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[시계열 기반]

  usad      USAD — KDD 2020
            Encoder 1개 + Decoder 2개(G1, G2)의 adversarial 구조.
            G1은 G2를 속이도록, G2는 G1의 재구성을 간파하도록 학습.
            이상 시퀀스는 두 decoder 간 재구성 불일치가 커진다.
            입력: (B, T*F) flatten → latent → unflatten

  tranad    TranAD — VLDB 2022
            Transformer Encoder + Decoder 2개(D1, D2).
            D1이 1차 재구성, D2가 D1 출력을 조건으로 2차 재구성(self-conditioning).
            학습 스케줄: epoch 진행에 따라 두 loss의 가중치를 동적으로 조정.
            window=10 기본값을 논문에서 사용 → 본 프로젝트 seq=10과 일치.

  conv1d    Conv1D Autoencoder
            Conv1d(kernel=3) × 2 인코더 + ConvTranspose1d × 2 디코더.
            same padding으로 시퀀스 길이를 유지.
            지역 패턴(급격한 방향/속도 변화) 탐지에 강하고 ONNX 변환이 안정적.

  lstm      LSTM Autoencoder
            Encoder LSTM → hidden state → Decoder LSTM (step-by-step).
            각 스텝에서 이전 출력을 다음 입력으로 사용(autoregressive).
            seq=10 환경에서는 장기 의존성 이점이 제한적.

  tcn       TCN Autoencoder — Bai et al., 2018
            Dilated Causal Conv 블록을 스택하여 다양한 receptive field 확보.
            dilation=[1,2,4]로 최대 7타임스텝까지 커버.
            Conv1D AE보다 시간 패턴 포착력이 강하면서도 경량.

  anomtrans Anomaly Transformer — NeurIPS 2022
            핵심: Association Discrepancy.
            - Series Association: 학습된 self-attention 분포
            - Prior Association: 가우시안 커널 기반 고정 분포
            두 분포의 KL 발산을 극대화하면서 재구성 오차를 최소화.
            이상 시퀀스는 두 분포의 불일치가 커져 MSE가 높아진다.

  dcdetect  DCdetector — KDD 2023
            이중 어텐션 구조:
            - Channel-wise Attention: 12개 피처 간 상관관계 학습
            - Patch-wise Attention: seq를 patch(=2)로 분할 후 패치 간 관계 학습
            두 관점의 표현을 결합하여 재구성.
            정교한 위장 공격(E5-Shadow, F1-FeatSmooth 등)에 강점.

[비시계열 기반 — sklearn 필터링 + Dense AE]

  iforest   IsolationForest-guided AE — Liu et al., 2008
            1단계: IsolationForest로 이상 샘플 탐지 (contamination=5%).
            2단계: 정상으로 판별된 90% 샘플만으로 FlattenAE 학습.
            입력을 (T×F) flatten하여 피처 값의 절대적 분포 기반 이상 탐지.
            scikit-learn 필요: pip install scikit-learn

  ocsvm     One-Class SVM-guided AE — Schölkopf et al., 2001
            RBF 커널 OCSVM으로 정상 경계 추정 후 필터링.
            IForest보다 고차원 피처 공간에서의 경계를 정밀하게 설정.
            학습 시간이 길어질 수 있음 (대규모 데이터에서 O(n²) 복잡도).
            scikit-learn 필요: pip install scikit-learn

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
출력 파일 (모델별 분리)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  model_{name}.onnx      ONNX 모델 (input="x", shape=(1, SEQ_LEN, N_FEAT))
  scaler_{name}.json     Min-Max 스케일러 파라미터 (feature별 min/max)
  threshold_{name}.txt   이상 판정 임계값 (정상 학습 데이터의 95 퍼센타일 MSE)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
사용법
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  python train_benchmark.py --model dcdetect
  python train_benchmark.py --model tranad --epochs 50 --lr 0.0005
  python train_benchmark.py --model all              # 전체 9개 순차 학습
  python train_benchmark.py --model iforest          # scikit-learn 필요

하이퍼파라미터 수정: 파일 내 DEFAULTS 딕셔너리 직접 수정
"""

import argparse
import shutil
import csv
import json
import math
import random
import time
import warnings
from collections import defaultdict

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset, random_split
from tqdm import tqdm

# ── 공통 설정 ─────────────────────────────────────────────────────
FEATURES = [
    "sog", "cog", "heading", "status",
    "dt", "dist_km",
    "cog_hdg_diff", "sog_change",
    "cog_hdg_change",
    "speed_consistency",
    "lat_speed", "lon_speed",
]
SEQ_LEN    = 10
N_FEAT     = len(FEATURES)   # 12
SEED       = 42

random.seed(SEED)
torch.manual_seed(SEED)

INPUT_FILE           = "ais-2025-12-31_preprocessed.csv"
SCALER_FILE          = "scaler.json"       # 모델별 실행 시 덮어씀
THRESHOLD_FILE       = "threshold.txt"    # 모델별 실행 시 덮어씀
SEQ_BREAK_DT         = 600
SAMPLE_MMSI          = 10000
VAL_RATIO            = 0.1
THRESHOLD_PERCENTILE = 95


# ══════════════════════════════════════════════════════════════════
# 데이터 파이프라인 (train.py 와 동일)
# ══════════════════════════════════════════════════════════════════

class MinMaxScaler:
    def __init__(self):
        self.min_ = None
        self.max_ = None

    def fit(self, data: list):
        n = len(data[0])
        self.min_ = [min(row[i] for row in data) for i in range(n)]
        self.max_ = [max(row[i] for row in data) for i in range(n)]

    def transform(self, data: list) -> list:
        result = []
        for row in data:
            scaled = []
            for i, val in enumerate(row):
                denom = self.max_[i] - self.min_[i]
                scaled.append((val - self.min_[i]) / denom if denom != 0 else 0.0)
            result.append(scaled)
        return result

    def fit_transform(self, data: list) -> list:
        self.fit(data)
        return self.transform(data)


def load_and_prepare(input_file: str, scaler_path: str = "scaler.json"):
    """CSV 로드 → 시퀀스 생성 → 스케일러 fit → Tensor 반환"""
    print(f"[데이터] {input_file} 로드 중...")
    mmsi_data = defaultdict(list)

    with open(input_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mmsi = row.get("mmsi", "")
            if not mmsi:
                continue
            try:
                record = [float(row[col]) for col in FEATURES]
                mmsi_data[mmsi].append(record)
            except (ValueError, KeyError):
                continue

    print(f"  고유 MMSI: {len(mmsi_data):,}")
    if SAMPLE_MMSI and len(mmsi_data) > SAMPLE_MMSI:
        keys = random.sample(list(mmsi_data.keys()), SAMPLE_MMSI)
        mmsi_data = {k: mmsi_data[k] for k in keys}
        print(f"  샘플링 후 MMSI: {len(mmsi_data):,}")

    dt_idx      = FEATURES.index("dt")
    dist_km_idx = FEATURES.index("dist_km")
    sequences   = []

    for records in mmsi_data.values():
        segments, current = [], [records[0]]
        for rec in records[1:]:
            if rec[dt_idx] >= SEQ_BREAK_DT:
                segments.append(current)
                rec = list(rec)
                rec[dt_idx] = rec[dist_km_idx] = 0.0
                current = [rec]
            else:
                current.append(rec)
        segments.append(current)
        for seg in segments:
            if len(seg) < SEQ_LEN:
                continue
            for i in range(len(seg) - SEQ_LEN + 1):
                sequences.append(seg[i: i + SEQ_LEN])

    print(f"  총 시퀀스: {len(sequences):,}")

    flat   = [row for seq in sequences for row in seq]
    scaler = MinMaxScaler()
    scaler.fit(flat)
    scaled = [scaler.transform(seq) for seq in sequences]

    with open(scaler_path, "w") as f:
        json.dump({"features": FEATURES, "min": scaler.min_, "max": scaler.max_}, f, indent=2)
    print(f"  스케일러 저장: {scaler_path}")

    tensor = torch.tensor(scaled, dtype=torch.float32)
    return tensor


def make_loaders(tensor: torch.Tensor, batch_size: int):
    dataset = TensorDataset(tensor)
    n_val   = max(1, int(len(dataset) * VAL_RATIO))
    n_train = len(dataset) - n_val
    train_ds, val_ds = random_split(
        dataset, [n_train, n_val],
        generator=torch.Generator().manual_seed(SEED)
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,  drop_last=True)
    val_loader   = DataLoader(val_ds,   batch_size=batch_size, shuffle=False, drop_last=False)
    print(f"  학습: {n_train:,}  검증: {n_val:,}  배치: {batch_size}")
    return train_loader, val_loader


def calc_threshold(model, loader, device, threshold_path: str = "threshold.txt") -> float:
    model.eval()
    errors = []
    with torch.no_grad():
        for (batch,) in loader:
            batch  = batch.to(device)
            output = model(batch)
            mse    = ((output - batch) ** 2).mean(dim=(1, 2))
            errors.extend(mse.cpu().tolist())
    errors.sort()
    idx = int(len(errors) * THRESHOLD_PERCENTILE / 100)
    thr = errors[min(idx, len(errors) - 1)]
    with open(threshold_path, "w") as f:
        f.write(str(thr))
    print(f"  임계값: {thr:.6f}  (상위 {100 - THRESHOLD_PERCENTILE}%)")
    print(f"  임계값 저장: {threshold_path}")
    return thr


def export_onnx(model, device, onnx_path: str):
    model.eval()
    dummy = torch.zeros(1, SEQ_LEN, N_FEAT, dtype=torch.float32).to(device)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        torch.onnx.export(
            model, (dummy,), onnx_path,
            dynamo=False,
            opset_version=14,
            input_names=["x"],
            output_names=["output"],
        )
    print(f"  ONNX 저장: {onnx_path}")


# ══════════════════════════════════════════════════════════════════
# 모델 1: USAD — KDD 2020
# Audibert et al., "USAD: UnSupervised Anomaly Detection on
# Multivariate Time Series"
#
# 핵심 아이디어:
#   두 Decoder(G1, G2)가 공유 Encoder를 두고 adversarial 학습.
#   G1은 정상 재구성에 집중하고, G2는 G1이 틀린 곳을 증폭시켜
#   이상 시점의 MSE가 더 크게 벌어지도록 유도.
#
# 구조:
#   Encoder E  : (B,T,F) flatten → MLP → latent z
#   Decoder G1 : z → MLP → (B,T,F)  (1차 재구성)
#   Decoder G2 : z → MLP → (B,T,F)  (adversarial 증폭)
#
# 손실 (n = epoch/N 스케줄):
#   L(θE,θG1) = (1/n)·MSE(G1(E(x)),x) + (1-1/n)·MSE(G2(G1(E(x))),x)
#   L(θE,θG2) = (1/n)·MSE(G2(E(x)),x) − (1-1/n)·MSE(G2(G1(E(x))),x)
#
# 추론: forward(x) → G1(E(x))  재구성 MSE로 이상 판정
# ══════════════════════════════════════════════════════════════════

class USAD(nn.Module):
    def __init__(self, seq_len: int, n_feat: int, latent_dim: int = 40,
                 hidden_dim: int = 128):
        super().__init__()
        self.seq_len   = seq_len
        self.n_feat    = n_feat
        self.input_dim = seq_len * n_feat

        def mlp_block(in_d, out_d):
            return nn.Sequential(
                nn.Linear(in_d, out_d),
                nn.ReLU(),
            )

        # Encoder
        self.encoder = nn.Sequential(
            mlp_block(self.input_dim, hidden_dim),
            mlp_block(hidden_dim, hidden_dim // 2),
            nn.Linear(hidden_dim // 2, latent_dim),
        )

        # Decoder G1
        self.decoder_g1 = nn.Sequential(
            mlp_block(latent_dim, hidden_dim // 2),
            mlp_block(hidden_dim // 2, hidden_dim),
            nn.Linear(hidden_dim, self.input_dim),
        )

        # Decoder G2
        self.decoder_g2 = nn.Sequential(
            mlp_block(latent_dim, hidden_dim // 2),
            mlp_block(hidden_dim // 2, hidden_dim),
            nn.Linear(hidden_dim, self.input_dim),
        )

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        # (B, T, F) → (B, T*F)
        return self.encoder(x.reshape(x.size(0), -1))

    def decode_g1(self, z: torch.Tensor) -> torch.Tensor:
        return self.decoder_g1(z).reshape(-1, self.seq_len, self.n_feat)

    def decode_g2(self, z: torch.Tensor) -> torch.Tensor:
        return self.decoder_g2(z).reshape(-1, self.seq_len, self.n_feat)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """ONNX 추론: G1 재구성만 반환"""
        return self.decode_g1(self.encode(x))


def train_usad(model: USAD, train_loader, val_loader, device,
               epochs: int, lr: float, patience: int):
    # 논문 식:
    #   L(θE,θG1) = (1/n)*||x-G1(E(x))||² + (1-1/n)*||x-G2(G1(E(x)))||²
    #   L(θE,θG2) = (1/n)*||x-G2(E(x))||² - (1-1/n)*||x-G2(G1(E(x)))||²
    opt_e_g1 = torch.optim.Adam(
        list(model.encoder.parameters()) + list(model.decoder_g1.parameters()), lr=lr)
    opt_e_g2 = torch.optim.Adam(
        list(model.encoder.parameters()) + list(model.decoder_g2.parameters()), lr=lr)

    best_val, best_state, patience_cnt = float("inf"), None, 0

    for epoch in range(1, epochs + 1):
        model.train()
        train_l1 = train_l2 = 0.0
        n = 0

        pbar = tqdm(train_loader, desc=f"Epoch {epoch:3d}/{epochs}", leave=False)
        for (batch,) in pbar:
            batch = batch.to(device)
            n_ep  = epoch / epochs   # n/N

            # ── Phase 1: L(θE, θG1) ──────────────────────────────
            z  = model.encode(batch)
            w1 = model.decode_g1(z)                          # G1(E(x))
            w3 = model.decode_g2(model.encode(w1.detach()))  # G2(G1(E(x)))
            l1 = (1 / n_ep) * F.mse_loss(w1, batch) \
               + (1 - 1 / n_ep) * F.mse_loss(w3, batch)
            opt_e_g1.zero_grad()
            l1.backward()
            opt_e_g1.step()

            # ── Phase 2: L(θE, θG2) ──────────────────────────────
            z2 = model.encode(batch)
            w2 = model.decode_g2(z2)                          # G2(E(x))
            w3b = model.decode_g2(model.encode(
                model.decode_g1(z2).detach()))                # G2(G1(E(x)))
            l2 = (1 / n_ep) * F.mse_loss(w2, batch) \
               - (1 - 1 / n_ep) * F.mse_loss(w3b, batch)
            opt_e_g2.zero_grad()
            l2.backward()
            opt_e_g2.step()

            train_l1 += l1.item(); train_l2 += l2.item(); n += 1
            pbar.set_postfix(l1=f"{l1.item():.5f}", l2=f"{l2.item():.5f}")

        # 검증: G1 재구성 MSE
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                batch = batch.to(device)
                out   = model(batch)
                val_loss += F.mse_loss(out, batch).item()
        val_loss /= len(val_loader)

        print(f"  Epoch {epoch:3d}/{epochs} | "
              f"L1={train_l1/n:.5f} L2={train_l2/n:.5f} | val_mse={val_loss:.6f}")

        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= patience:
                print(f"  조기 종료: {patience} epoch 개선 없음")
                break

    if best_state:
        model.load_state_dict(best_state)
    print(f"  최적 검증 MSE: {best_val:.6f}")


# ══════════════════════════════════════════════════════════════════
# 모델 2: TranAD — VLDB 2022
# Tuli et al., "TranAD: Deep Transformer Networks for Anomaly
# Detection in Multivariate Time Series Data"
#
# 핵심 아이디어:
#   두 Transformer Decoder(D1, D2)로 self-conditioning 학습.
#   D1이 1차 재구성 → D2가 D1 출력을 조건으로 2차 재구성.
#   학습 초반엔 재구성에 집중(1/n 스케일), 후반엔 adversarial
#   증폭(1-1/n 스케일)으로 이상 시점 MSE를 벌려나감.
#   window=10 단기 시퀀스를 염두에 두고 설계된 모델.
#
# 구조:
#   Input Proj + Positional Encoding → Transformer Encoder
#   Decoder D1: memory + x     → 1차 재구성 o1
#   Decoder D2: memory + o1    → 2차 재구성 o2 (self-conditioning)
#
# 손실 (n = epoch/N, L1은 enc+D1, L2는 enc+D2 별도 optimizer):
#   L1(θenc,θD1) = (1/n)·MSE(o1,x) + (1-1/n)·MSE(o2,x)
#   L2(θenc,θD2) = (1/n)·MSE(o1,x) − (1-1/n)·MSE(o2,o1)
#
# 추론: forward(x) → D1 재구성 MSE로 이상 판정
# ══════════════════════════════════════════════════════════════════

class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 64, dropout: float = 0.1):
        super().__init__()
        self.dropout = nn.Dropout(dropout)
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(max_len).unsqueeze(1).float()
        div = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0))   # (1, max_len, d_model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.dropout(x + self.pe[:, :x.size(1)])


class TranAD(nn.Module):
    def __init__(self, seq_len: int, n_feat: int,
                 d_model: int = 64, nhead: int = 4,
                 num_encoder_layers: int = 1,
                 num_decoder_layers: int = 1,
                 dim_feedforward: int = 128,
                 dropout: float = 0.1):
        super().__init__()
        self.seq_len = seq_len
        self.n_feat  = n_feat
        self.d_model = d_model

        self.input_proj  = nn.Linear(n_feat, d_model)
        self.pos_enc     = PositionalEncoding(d_model, max_len=seq_len + 4, dropout=dropout)

        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout, batch_first=True)
        self.encoder = nn.TransformerEncoder(enc_layer, num_layers=num_encoder_layers)

        dec_layer1 = nn.TransformerDecoderLayer(
            d_model=d_model, nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout, batch_first=True)
        self.decoder1 = nn.TransformerDecoder(dec_layer1, num_layers=num_decoder_layers)

        dec_layer2 = nn.TransformerDecoderLayer(
            d_model=d_model, nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout, batch_first=True)
        self.decoder2 = nn.TransformerDecoder(dec_layer2, num_layers=num_decoder_layers)

        self.output_proj1 = nn.Linear(d_model, n_feat)
        self.output_proj2 = nn.Linear(d_model, n_feat)

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        z = self.pos_enc(self.input_proj(x))
        return self.encoder(z)

    def decode1(self, memory: torch.Tensor, tgt: torch.Tensor) -> torch.Tensor:
        tgt_emb = self.pos_enc(self.input_proj(tgt))
        out = self.decoder1(tgt_emb, memory)
        return self.output_proj1(out)

    def decode2(self, memory: torch.Tensor, tgt: torch.Tensor) -> torch.Tensor:
        tgt_emb = self.pos_enc(self.input_proj(tgt))
        out = self.decoder2(tgt_emb, memory)
        return self.output_proj2(out)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """ONNX 추론: D1 재구성만 반환"""
        memory = self.encode(x)
        return self.decode1(memory, x)


def train_tranad(model: TranAD, train_loader, val_loader, device,
                 epochs: int, lr: float, patience: int):
    # 논문 식:
    #   L1(θ_enc, θ_D1) = (1/n)*MSE(D1(z,x), x) + (1-1/n)*MSE(D2(z,D1), x)
    #   L2(θ_enc, θ_D2) = (1/n)*MSE(D1(z,x), x) - (1-1/n)*MSE(D2(z,D1), D1)
    #   → L1은 enc+D1, L2는 enc+D2 별도 업데이트
    opt_d1 = torch.optim.AdamW(
        list(model.encoder.parameters()) +
        list(model.pos_enc.parameters()) +
        list(model.input_proj.parameters()) +
        list(model.decoder1.parameters()) +
        list(model.output_proj1.parameters()),
        lr=lr, weight_decay=1e-4)
    opt_d2 = torch.optim.AdamW(
        list(model.encoder.parameters()) +
        list(model.pos_enc.parameters()) +
        list(model.input_proj.parameters()) +
        list(model.decoder2.parameters()) +
        list(model.output_proj2.parameters()),
        lr=lr, weight_decay=1e-4)

    best_val, best_state, patience_cnt = float("inf"), None, 0

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        n = 0
        n_ep = epoch / epochs

        pbar = tqdm(train_loader, desc=f"Epoch {epoch:3d}/{epochs}", leave=False)
        for (batch,) in pbar:
            batch  = batch.to(device)

            # ── L1: enc + D1 업데이트 ─────────────────────────────
            memory = model.encode(batch)
            o1     = model.decode1(memory, batch)
            o2     = model.decode2(memory.detach(), o1.detach())
            l1 = (1 / n_ep) * F.mse_loss(o1, batch) \
               + (1 - 1 / n_ep) * F.mse_loss(o2, batch)
            opt_d1.zero_grad()
            l1.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt_d1.step()

            # ── L2: enc + D2 업데이트 ─────────────────────────────
            memory2 = model.encode(batch)
            o1b     = model.decode1(memory2.detach(), batch)
            o2b     = model.decode2(memory2, o1b.detach())
            l2 = (1 / n_ep) * F.mse_loss(o1b.detach(), batch) \
               - (1 - 1 / n_ep) * F.mse_loss(o2b, o1b.detach())
            opt_d2.zero_grad()
            l2.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt_d2.step()

            total_loss += (l1.item() + l2.item()); n += 1
            pbar.set_postfix(l1=f"{l1.item():.5f}", l2=f"{l2.item():.5f}")

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                batch = batch.to(device)
                out   = model(batch)
                val_loss += F.mse_loss(out, batch).item()
        val_loss /= len(val_loader)

        print(f"  Epoch {epoch:3d}/{epochs} | "
              f"train={total_loss/n:.5f} | val_mse={val_loss:.6f}")

        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= patience:
                print(f"  조기 종료: {patience} epoch 개선 없음")
                break

    if best_state:
        model.load_state_dict(best_state)
    print(f"  최적 검증 MSE: {best_val:.6f}")


# ══════════════════════════════════════════════════════════════════
# 모델 3: Conv1D Autoencoder — 2011
# Masci et al., "Stacked Convolutional Auto-Encoders for
# Hierarchical Feature Extraction" (ICANN 2011) 기반 시계열 변형
#
# 핵심 아이디어:
#   1D 합성곱으로 시퀀스의 지역적(local) 패턴을 추출.
#   kernel_size=3, same padding으로 시퀀스 길이를 유지하며
#   채널 수를 압축(encoder)했다가 복원(decoder).
#   LSTM보다 빠르고 ONNX 변환이 안정적이며,
#   방향/속도의 급격한 단기 변화 탐지에 강함.
#
# 구조:
#   Encoder: (B,T,F) → permute → Conv1d(F→64, k=3) → BN → ReLU
#                              → Conv1d(64→32, k=3) → BN → ReLU
#   Decoder: ConvTranspose1d(32→64) → ReLU
#          → ConvTranspose1d(64→F)  → permute → (B,T,F)
# ══════════════════════════════════════════════════════════════════

class Conv1DAE(nn.Module):
    def __init__(self, n_feat: int, hidden_ch: int = 32):
        super().__init__()
        # Encoder
        self.enc1 = nn.Conv1d(n_feat, hidden_ch * 2, kernel_size=3, padding=1)
        self.enc2 = nn.Conv1d(hidden_ch * 2, hidden_ch, kernel_size=3, padding=1)
        # Decoder
        self.dec1 = nn.ConvTranspose1d(hidden_ch, hidden_ch * 2, kernel_size=3, padding=1)
        self.dec2 = nn.ConvTranspose1d(hidden_ch * 2, n_feat, kernel_size=3, padding=1)
        self.bn1  = nn.BatchNorm1d(hidden_ch * 2)
        self.bn2  = nn.BatchNorm1d(hidden_ch)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, F) → (B, F, T) for Conv1d
        z = x.permute(0, 2, 1)
        z = F.relu(self.bn1(self.enc1(z)))
        z = F.relu(self.bn2(self.enc2(z)))
        z = F.relu(self.dec1(z))
        z = self.dec2(z)
        return z.permute(0, 2, 1)   # (B, T, F)


def train_standard(model: nn.Module, train_loader, val_loader, device,
                   epochs: int, lr: float, patience: int):
    """표준 MSE 재구성 손실 학습 루프 (Conv1D, LSTM, TCN, DCdetector, FlattenAE 공용)"""
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    best_val, best_state, patience_cnt = float("inf"), None, 0

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        pbar = tqdm(train_loader, desc=f"Epoch {epoch:3d}/{epochs}", leave=False)
        for (batch,) in pbar:
            batch  = batch.to(device)
            output = model(batch)
            loss   = F.mse_loss(output, batch)
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            train_loss += loss.item()
            pbar.set_postfix(loss=f"{loss.item():.6f}")
        train_loss /= len(train_loader)

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                batch = batch.to(device)
                val_loss += F.mse_loss(model(batch), batch).item()
        val_loss /= len(val_loader)
        print(f"  Epoch {epoch:3d}/{epochs} | train={train_loss:.6f} | val={val_loss:.6f}")

        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= patience:
                print(f"  조기 종료: {patience} epoch 개선 없음")
                break

    if best_state:
        model.load_state_dict(best_state)
    print(f"  최적 검증 MSE: {best_val:.6f}")


# ══════════════════════════════════════════════════════════════════
# 모델 4: LSTM Autoencoder — 2015
# Srivastava et al., "Unsupervised Learning of Video Representations
# using LSTMs" (ICML 2015) Seq2Seq 구조를 이상 탐지에 적용
#
# 핵심 아이디어:
#   Encoder LSTM이 시퀀스를 압축해 hidden state를 생성하고,
#   Decoder LSTM이 step-by-step으로 시퀀스를 재구성.
#   정상 패턴을 학습한 후 이상 시점에서 재구성 오차가 커지는
#   원리를 이용.
#   단, 시퀀스 길이=10처럼 짧은 경우 LSTM의 장기 의존성
#   학습 이점이 제한적이며, Conv1D/TCN 대비 성능이 낮을 수 있음.
#
# 구조:
#   Encoder: LSTM(F→hidden, 2 layers) → (hidden, cell)
#   Decoder: LSTM(F→hidden, 2layers) + start_token → step-by-step
#          → Linear(hidden→F) × seq_len → (B,T,F)
# ══════════════════════════════════════════════════════════════════

class LSTMAutoencoder(nn.Module):
    def __init__(self, input_size: int, hidden_size: int = 64, num_layers: int = 2):
        super().__init__()
        self.encoder      = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.decoder      = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.output_layer = nn.Linear(hidden_size, input_size)
        self.start_token  = nn.Parameter(torch.zeros(1, 1, input_size))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size = x.size(0)
        _, (hidden, cell) = self.encoder(x)
        dec_input  = self.start_token.expand(batch_size, 1, -1).clone()
        dec_hidden, dec_cell = hidden, cell
        steps = []
        for _ in range(x.size(1)):
            out, (dec_hidden, dec_cell) = self.decoder(dec_input, (dec_hidden, dec_cell))
            step_out  = self.output_layer(out)
            steps.append(step_out)
            dec_input = step_out
        return torch.cat(steps, dim=1)


# ══════════════════════════════════════════════════════════════════
# 모델 5: TCN Autoencoder — 2018
# Bai et al., "An Empirical Evaluation of Generic Convolutional
# and Recurrent Networks for Sequence Modeling" (arXiv 2018)
#
# 핵심 아이디어:
#   Dilated Causal Convolution으로 receptive field를 지수적으로
#   확장. dilation=[1,2,4]이면 최대 7 스텝 과거를 참조 가능.
#   각 TCNBlock은 residual 연결로 gradient 소실을 방지.
#   seq=10에서 Conv1D AE보다 다양한 시간 스케일 패턴 포착에 유리.
#
# 구조:
#   Input Proj: Conv1d(F→hidden_ch, k=1)
#   Encoder: TCNBlock(dilation=1) → TCNBlock(dilation=2)
#          → TCNBlock(dilation=4)
#   Decoder: TCNBlock(dilation=4) → TCNBlock(dilation=2)
#          → TCNBlock(dilation=1)  (역순 symmetric)
#   Output Proj: Conv1d(hidden_ch→F, k=1)
#   TCNBlock: Conv1d×2 + BN + ReLU + Dropout + residual
# ══════════════════════════════════════════════════════════════════

class TCNBlock(nn.Module):
    def __init__(self, n_ch: int, kernel_size: int, dilation: int, dropout: float = 0.1):
        super().__init__()
        pad = (kernel_size - 1) * dilation
        self.conv1 = nn.Conv1d(n_ch, n_ch, kernel_size, dilation=dilation, padding=pad)
        self.conv2 = nn.Conv1d(n_ch, n_ch, kernel_size, dilation=dilation, padding=pad)
        self.bn1   = nn.BatchNorm1d(n_ch)
        self.bn2   = nn.BatchNorm1d(n_ch)
        self.drop  = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        T = x.size(2)
        z = self.drop(F.relu(self.bn1(self.conv1(x)[:, :, :T])))
        z = self.drop(F.relu(self.bn2(self.conv2(z)[:, :, :T])))
        return x + z


class TCNAE(nn.Module):
    def __init__(self, n_feat: int, hidden_ch: int = 32,
                 kernel_size: int = 3, dilations: list = None):
        super().__init__()
        if dilations is None:
            dilations = [1, 2, 4]
        self.input_proj  = nn.Conv1d(n_feat, hidden_ch, 1)
        self.enc_blocks  = nn.ModuleList([TCNBlock(hidden_ch, kernel_size, d) for d in dilations])
        self.dec_blocks  = nn.ModuleList([TCNBlock(hidden_ch, kernel_size, d) for d in reversed(dilations)])
        self.output_proj = nn.Conv1d(hidden_ch, n_feat, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.input_proj(x.permute(0, 2, 1))
        for block in self.enc_blocks:
            z = block(z)
        for block in self.dec_blocks:
            z = block(z)
        return self.output_proj(z).permute(0, 2, 1)


# ══════════════════════════════════════════════════════════════════
# 모델 6: Anomaly Transformer — NeurIPS 2022
# Xu et al., "Anomaly Transformer: Time Series Anomaly Detection
# with Association Discrepancy"
#
# 핵심 아이디어 (Association Discrepancy):
#   정상 구간: 어텐션이 인접 시점에 집중 → Gaussian prior와 유사
#   이상 구간: 어텐션이 분산되거나 편중 → prior와 크게 차이남
#   이 차이(KL divergence)를 손실에 포함해 두 분포 간 거리를
#   극대화하도록 학습 → 이상 시점의 재구성 오차가 더 커짐
#
# 구조:
#   각 Transformer Layer에 두 종류의 어텐션 내재:
#     Series Association: 학습된 self-attention (B,H,T,T)
#     Prior Association : 학습 가능한 sigma의 Gaussian kernel
#   재구성 손실 + Association Discrepancy 손실(KL)의 합으로 학습
#   loss = MSE(recon, x) − λ·(KL(prior‖series) + KL(series‖prior))
#
# ONNX 추론: forward(x) 는 재구성만 반환 (KL은 학습 전용)
# ══════════════════════════════════════════════════════════════════

class AnomalyAttentionLayer(nn.Module):
    def __init__(self, d_model: int, nhead: int, seq_len: int, dropout: float = 0.1):
        super().__init__()
        self.nhead   = nhead
        self.d_head  = d_model // nhead
        self.seq_len = seq_len
        self.q   = nn.Linear(d_model, d_model)
        self.k   = nn.Linear(d_model, d_model)
        self.v   = nn.Linear(d_model, d_model)
        self.out = nn.Linear(d_model, d_model)
        self.drop = nn.Dropout(dropout)
        # Learnable sigma for Gaussian prior (per head)
        self.sigma = nn.Parameter(torch.ones(nhead) * 0.5)

    def _prior_assoc(self, device) -> torch.Tensor:
        pos  = torch.arange(self.seq_len, dtype=torch.float32, device=device).unsqueeze(0)
        diff = (pos.T - pos) ** 2
        sig  = self.sigma.abs().clamp(min=1e-3)
        p    = torch.exp(-diff.unsqueeze(0) / (2 * sig.view(-1, 1, 1) ** 2))
        return p / (p.sum(-1, keepdim=True) + 1e-9)

    def forward(self, x: torch.Tensor):
        B, T, D = x.shape
        H, d = self.nhead, self.d_head
        Q = self.q(x).view(B, T, H, d).permute(0, 2, 1, 3)
        K = self.k(x).view(B, T, H, d).permute(0, 2, 1, 3)
        V = self.v(x).view(B, T, H, d).permute(0, 2, 1, 3)
        series = F.softmax(Q @ K.transpose(-1, -2) / (d ** 0.5), dim=-1)
        prior  = self._prior_assoc(x.device).unsqueeze(0)
        ctx = self.drop(series) @ V
        out = self.out(ctx.permute(0, 2, 1, 3).reshape(B, T, D))
        return out, series, prior


class AnomalyTransformerLayer(nn.Module):
    def __init__(self, d_model: int, nhead: int, seq_len: int,
                 dim_ff: int = 128, dropout: float = 0.1):
        super().__init__()
        self.attn  = AnomalyAttentionLayer(d_model, nhead, seq_len, dropout)
        self.ff    = nn.Sequential(
            nn.Linear(d_model, dim_ff), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(dim_ff, d_model))
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.drop  = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor):
        a, series, prior = self.attn(x)
        x = self.norm1(x + self.drop(a))
        x = self.norm2(x + self.drop(self.ff(x)))
        return x, series, prior


class AnomalyTransformerAE(nn.Module):
    def __init__(self, seq_len: int, n_feat: int,
                 d_model: int = 64, nhead: int = 4,
                 n_layers: int = 2, dim_ff: int = 128, dropout: float = 0.1):
        super().__init__()
        self.input_proj  = nn.Linear(n_feat, d_model)
        self.pos_enc     = PositionalEncoding(d_model, max_len=seq_len + 4, dropout=dropout)
        self.layers      = nn.ModuleList([
            AnomalyTransformerLayer(d_model, nhead, seq_len, dim_ff, dropout)
            for _ in range(n_layers)])
        self.output_proj = nn.Linear(d_model, n_feat)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """ONNX 추론: 재구성만"""
        z = self.pos_enc(self.input_proj(x))
        for layer in self.layers:
            z, _, _ = layer(z)
        return self.output_proj(z)

    def forward_train(self, x: torch.Tensor):
        z = self.pos_enc(self.input_proj(x))
        series_list, prior_list = [], []
        for layer in self.layers:
            z, s, p = layer(z)
            series_list.append(s); prior_list.append(p)
        return self.output_proj(z), series_list, prior_list


def _assoc_discrepancy(series_list, prior_list):
    loss = 0.0
    for s, p in zip(series_list, prior_list):
        p_ = p.expand_as(s) + 1e-9
        s_ = s + 1e-9
        loss += ((p_ * (p_ / s_).log()).sum(-1).mean() +
                 (s_ * (s_ / p_).log()).sum(-1).mean()) / 50.0
    return loss / len(series_list)


def train_anomtrans(model: AnomalyTransformerAE, train_loader, val_loader, device,
                    epochs: int, lr: float, patience: int):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    best_val, best_state, patience_cnt = float("inf"), None, 0
    for epoch in range(1, epochs + 1):
        model.train()
        t_loss = 0.0
        pbar = tqdm(train_loader, desc=f"Epoch {epoch:3d}/{epochs}", leave=False)
        for (batch,) in pbar:
            batch = batch.to(device)
            recon, series, prior = model.forward_train(batch)
            r = F.mse_loss(recon, batch)
            a = _assoc_discrepancy(series, prior)
            loss = r - a   # maximize discrepancy, minimize recon
            optimizer.zero_grad(); loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            t_loss += r.item()
            pbar.set_postfix(recon=f"{r.item():.5f}", assoc=f"{a.item():.5f}")
        t_loss /= len(train_loader)
        model.eval()
        v_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                v_loss += F.mse_loss(model(batch.to(device)), batch.to(device)).item()
        v_loss /= len(val_loader)
        print(f"  Epoch {epoch:3d}/{epochs} | recon={t_loss:.6f} | val={v_loss:.6f}")
        if v_loss < best_val - 1e-6:
            best_val = v_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= patience:
                print(f"  조기 종료: {patience} epoch 개선 없음"); break
    if best_state: model.load_state_dict(best_state)
    print(f"  최적 검증 MSE: {best_val:.6f}")


# ══════════════════════════════════════════════════════════════════
# 모델 7: DCdetector — KDD 2023
# Yang et al., "DCdetector: Dual Attention Contrastive
# Representation Learning for Time Series Anomaly Detection"
#
# 핵심 아이디어 (Dual Attention):
#   피처 간 관계(Channel-wise)와 시간 패턴(Patch-wise) 두 관점을
#   동시에 학습. 정교하게 위장된 이상(F1-FeatSmooth, E5-Shadow 등)
#   처럼 단일 차원 분석으로 놓치기 쉬운 이상을 포착하는 데 강함.
#
# 구조:
#   1. Channel-wise Attention: (B,T,F) → Multi-head Attn(head=F)
#      → 피처 간 상관 패턴 학습, Add&Norm
#   2. Patchify: (B,T,F) → (B,n_patches, patch_size×F)
#      patch_size=2이면 seq=10 → 5 patches
#   3. Patch-wise Attention: patch embedding → Multi-head Attn
#      → 시퀀스 내 구간 간 패턴 학습
#   4. Decoder: Linear → reshape → (B,T,F)
# ══════════════════════════════════════════════════════════════════

class DCdetector(nn.Module):
    def __init__(self, seq_len: int, n_feat: int,
                 patch_size: int = 2, d_model: int = 64,
                 nhead: int = 4, dropout: float = 0.1):
        super().__init__()
        self.seq_len    = seq_len
        self.n_feat     = n_feat
        self.patch_size = patch_size
        self.n_patches  = seq_len // patch_size
        # Channel-wise attention
        self.ch_attn  = nn.MultiheadAttention(
            n_feat, num_heads=min(nhead, n_feat), dropout=dropout, batch_first=True)
        self.ch_norm  = nn.LayerNorm(n_feat)
        # Patch embedding + attention
        self.patch_embed = nn.Linear(patch_size * n_feat, d_model)
        self.pt_attn     = nn.MultiheadAttention(d_model, nhead, dropout=dropout, batch_first=True)
        self.pt_norm     = nn.LayerNorm(d_model)
        # Decoder
        self.decoder = nn.Linear(d_model, patch_size * n_feat)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, T, F = x.shape
        # Channel-wise
        ch, _ = self.ch_attn(x, x, x)
        x_ch  = self.ch_norm(x + ch)
        # Patchify
        n = T // self.patch_size
        patches  = x_ch[:, :n * self.patch_size, :].reshape(B, n, self.patch_size * F)
        pt_emb   = self.patch_embed(patches)
        pt_out, _= self.pt_attn(pt_emb, pt_emb, pt_emb)
        pt_out   = self.pt_norm(pt_emb + pt_out)
        # Decode
        recon = self.decoder(pt_out).reshape(B, n * self.patch_size, F)
        if recon.size(1) < T:
            recon = torch.cat([recon, x[:, recon.size(1):, :]], dim=1)
        return recon


# ══════════════════════════════════════════════════════════════════
# 모델 8/9: 비시계열 이상치 탐지 기반 Autoencoder
#
# IsolationForest — ICDM 2008
#   Liu et al., "Isolation Forest"
#   랜덤 트리로 샘플을 고립시키는 횟수로 이상도 측정.
#   고립이 빠를수록 이상. 트리 앙상블이라 빠르고 고차원에 강함.
#
# One-Class SVM — NIPS 2001
#   Schölkopf et al., "Estimating the Support of a High-Dimensional
#   Distribution"
#   정상 데이터의 결정 경계를 RBF 커널로 학습.
#   경계 밖 샘플을 이상으로 판정. 데이터가 많으면 느림.
#
# 적용 방식 (sklearn-guided AE):
#   (B,T,F) → flatten → (B,T×F) 벡터로 sklearn 학습
#   sklearn이 이상으로 판별한 하위 10% 샘플을 제거하고
#   나머지 정상 샘플만으로 FlattenAE(MLP AE)를 학습.
#   → sklearn이 정상 분포를 정의, AE가 재구성 기반 스코어 생성.
#
# ONNX: FlattenAE 그대로 export (input "x", shape (1,T,F) 호환)
# ══════════════════════════════════════════════════════════════════

class FlattenAE(nn.Module):
    """비시계열용 MLP AE: flatten → encode → decode → unflatten"""
    def __init__(self, seq_len: int, n_feat: int,
                 hidden_dim: int = 128, latent_dim: int = 32):
        super().__init__()
        self.seq_len   = seq_len
        self.n_feat    = n_feat
        self.input_dim = seq_len * n_feat
        self.encoder = nn.Sequential(
            nn.Linear(self.input_dim, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim // 2), nn.ReLU(),
            nn.Linear(hidden_dim // 2, latent_dim),
        )
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, hidden_dim // 2), nn.ReLU(),
            nn.Linear(hidden_dim // 2, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, self.input_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B = x.size(0)
        return self.decoder(self.encoder(x.reshape(B, -1))).reshape(B, self.seq_len, self.n_feat)


def train_sklearn_ae(sk_name: str, model: FlattenAE, tensor: torch.Tensor,
                     train_loader, val_loader, device,
                     epochs: int, lr: float, patience: int):
    """sklearn으로 정상 샘플 필터링 후 FlattenAE 학습"""
    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.svm import OneClassSVM
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("  ⚠ scikit-learn 없음 → pip install scikit-learn")
        train_standard(model, train_loader, val_loader, device, epochs, lr, patience)
        return

    print(f"  sklearn {sk_name} 학습 중 (flatten shape: {tensor.shape[0]}×{tensor.shape[1]*tensor.shape[2]})...")
    X_flat = tensor.reshape(len(tensor), -1).numpy()
    sc = StandardScaler()
    X_sc = sc.fit_transform(X_flat)

    if sk_name == "iforest":
        sk = IsolationForest(n_estimators=100, contamination=0.05, random_state=SEED, n_jobs=-1)
    else:
        sk = OneClassSVM(kernel="rbf", nu=0.05, gamma="scale")

    sk.fit(X_sc)
    scores = sk.score_samples(X_sc)   # 높을수록 정상
    thr_sk = float(np.percentile(scores, 10))
    mask   = scores >= thr_sk
    print(f"  정상 필터: {mask.sum():,}/{len(mask):,}개 ({mask.mean()*100:.1f}%) 로 AE 학습")

    normal_tensor  = tensor[torch.from_numpy(mask)]
    normal_loader  = DataLoader(TensorDataset(normal_tensor),
                                batch_size=train_loader.batch_size,
                                shuffle=True, drop_last=True)
    train_standard(model, normal_loader, val_loader, device, epochs, lr, patience)

# ══════════════════════════════════════════════════════════════════
# 모델별 하이퍼파라미터 ← 여기서 직접 수정
# ══════════════════════════════════════════════════════════════════
DEFAULTS = {
    #              epochs  lr      batch  patience
    "usad":      dict(epochs=50,  lr=1e-3, batch_size=256, patience=7),
    "tranad":    dict(epochs=50,  lr=1e-3, batch_size=256, patience=7),
    "conv1d":    dict(epochs=30,  lr=1e-3, batch_size=256, patience=5),
    "lstm":      dict(epochs=30,  lr=1e-3, batch_size=256, patience=5),
    "tcn":       dict(epochs=30,  lr=1e-3, batch_size=256, patience=5),
    "anomtrans": dict(epochs=50,  lr=1e-3, batch_size=256, patience=7),
    "dcdetect":  dict(epochs=30,  lr=1e-3, batch_size=256, patience=5),
    "iforest":   dict(epochs=30,  lr=1e-3, batch_size=256, patience=5),
    "ocsvm":     dict(epochs=30,  lr=5e-4, batch_size=256, patience=5),
}


# ══════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════
def run_model(model_name: str, tensor: torch.Tensor,
              epochs: int, lr: float, batch_size: int,
              patience: int, device: torch.device,
              onnx_path: str, scaler_path: str, threshold_path: str,
              full_tensor: torch.Tensor = None):
    # full_tensor: sklearn 모델용 (train/val 분리 전 전체 텐서)
    if full_tensor is None:
        full_tensor = tensor

    print(f"\n{'='*60}")
    print(f"  모델: {model_name.upper()}")
    print(f"  epochs={epochs}  lr={lr}  batch={batch_size}  patience={patience}")
    print(f"{'='*60}")

    train_loader, val_loader = make_loaders(tensor, batch_size)

    if model_name == "usad":
        model = USAD(SEQ_LEN, N_FEAT, latent_dim=40, hidden_dim=128).to(device)
        train_usad(model, train_loader, val_loader, device, epochs, lr, patience)

    elif model_name == "tranad":
        model = TranAD(SEQ_LEN, N_FEAT, d_model=64, nhead=4,
                       num_encoder_layers=1, num_decoder_layers=1,
                       dim_feedforward=128).to(device)
        train_tranad(model, train_loader, val_loader, device, epochs, lr, patience)

    elif model_name == "conv1d":
        model = Conv1DAE(N_FEAT, hidden_ch=32).to(device)
        train_standard(model, train_loader, val_loader, device,
                       epochs, lr, patience)

    elif model_name == "lstm":
        model = LSTMAutoencoder(input_size=N_FEAT, hidden_size=64, num_layers=2).to(device)
        train_standard(model, train_loader, val_loader, device,
                       epochs, lr, patience)

    elif model_name == "tcn":
        model = TCNAE(N_FEAT, hidden_ch=32, kernel_size=3, dilations=[1, 2, 4]).to(device)
        train_standard(model, train_loader, val_loader, device,
                       epochs, lr, patience)

    elif model_name == "anomtrans":
        model = AnomalyTransformerAE(SEQ_LEN, N_FEAT, d_model=64, nhead=4,
                                     n_layers=2, dim_ff=128).to(device)
        train_anomtrans(model, train_loader, val_loader, device, epochs, lr, patience)

    elif model_name == "dcdetect":
        model = DCdetector(SEQ_LEN, N_FEAT, patch_size=2, d_model=64, nhead=4).to(device)
        train_standard(model, train_loader, val_loader, device,
                       epochs, lr, patience)

    elif model_name in ("iforest", "ocsvm"):
        model = FlattenAE(SEQ_LEN, N_FEAT, hidden_dim=128, latent_dim=32).to(device)
        train_sklearn_ae(model_name, model, full_tensor, train_loader, val_loader,
                         device, epochs, lr, patience)
    else:
        raise ValueError(f"Unknown model: {model_name}")

    calc_threshold(model, train_loader, device, threshold_path)
    export_onnx(model, device, onnx_path)
    return model


def main():
    parser = argparse.ArgumentParser(description="AIS 벤치마크 학습 (eval_anomaly.py 호환)")
    parser.add_argument("--model",      type=str, default="usad",
                        choices=["usad","tranad","conv1d","lstm","tcn","anomtrans","dcdetect","iforest","ocsvm","all"],
                        help="학습할 모델 (all: 전체 9개 모델 순차 학습)")
    parser.add_argument("--input",      type=str, default=INPUT_FILE)
    parser.add_argument("--epochs",     type=int, default=None)
    parser.add_argument("--lr",         type=float, default=None)
    parser.add_argument("--batch_size", type=int, default=None)
    parser.add_argument("--patience",   type=int, default=None)
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[디바이스] {device}")
    print(f"[피처 수]  {N_FEAT}  |  시퀀스 길이: {SEQ_LEN}")

    t0 = time.time()
    models_to_run = ["usad","tranad","conv1d","lstm","tcn","anomtrans","dcdetect","iforest","ocsvm"] if args.model == "all" else [args.model]

    # 데이터는 한 번만 로드 (스케일러는 모델별로 저장)
    first_scaler = f"scaler_{models_to_run[0]}.json"
    tensor = load_and_prepare(args.input, scaler_path=first_scaler)

    for name in models_to_run:
        d = DEFAULTS[name]
        epochs     = args.epochs     or d["epochs"]
        lr         = args.lr         or d["lr"]
        batch_size = args.batch_size or d["batch_size"]
        patience   = args.patience   or d["patience"]

        onnx_path      = f"model_{name}.onnx"
        scaler_path    = f"scaler_{name}.json"
        threshold_path = f"threshold_{name}.txt"

        # 첫 모델 외에는 scaler 재저장 (동일 데이터이므로 값은 같음)
        if name != models_to_run[0]:
            shutil.copy(first_scaler, scaler_path)
            print(f"  스케일러 복사: {first_scaler} → {scaler_path}")

        run_model(name, tensor, epochs, lr, batch_size, patience, device,
                  onnx_path, scaler_path, threshold_path, full_tensor=tensor)

    print(f"\n완료! 전체 소요: {time.time() - t0:.1f}s")
    print("\n생성된 파일:")
    for name in models_to_run:
        print(f"  model_{name}.onnx  |  scaler_{name}.json  |  threshold_{name}.txt")

    print("\neval_anomaly.py 사용법:")
    for name in models_to_run:
        print(f"  python eval_anomaly.py --model {name}")


if __name__ == "__main__":
    main()