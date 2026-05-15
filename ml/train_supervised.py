"""
AIS 이상 탐지 지도 학습 스크립트 (5개 최신 모델)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
지원 모델 (5종)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  patchtst   PatchTST — NeurIPS 2023
             패치 토큰화 + Transformer 인코더 + 분류 헤드.
             시계열을 겹치지 않는 패치로 분할 후 각 패치를 토큰화.

  itrans     iTransformer — ICLR 2024
             입력 전치 (피처=토큰, 시간=임베딩 차원).
             피처 간 어텐션으로 다변량 의존성 학습.

  tsmixer    TSMixer — 2023
             시간 축 MLP + 피처 축 MLP 교차 적용.
             경량이면서 Transformer 급 성능.

  moderntcn  ModernTCN — ICLR 2024
             ConvNeXt 스타일의 대형 커널 Depthwise Conv.
             지역 패턴 포착 + 긴 의존성 학습.

  mamba      Mamba SSM — NeurIPS 2023 (순수 PyTorch 구현)
             선택적 상태 공간 모델. ONNX 호환을 위해
             시간 축 for-loop 방식으로 구현 (T=10 고정).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
데이터 파이프라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  정상 데이터: ais_preprocessed.csv 실제 데이터만 사용 (CSV 없으면 오류)
  이상 데이터: eval_anomaly.SCENARIO_MAKERS 중 is_anom=True, is_holdout=False 시나리오 × n_anom개
  스케일러: scaler_dcdetect.json 재사용 (재학습 없음)
  클래스 균형: 다운샘플링으로 1:1 맞춤

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
사용법
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  python train_supervised.py --model patchtst
  python train_supervised.py --model all
  python train_supervised.py --model mamba --epochs 50 --lr 0.001
  python train_supervised.py --model itrans --n_anom 1000 --n_normal 20000
  python train_supervised.py --model all --max_mmsi 200   # MMSI 200개로 제한

입력 데이터:
  data/*.csv                  정상 AIS 데이터 (CSV)

출력 파일:
  output/scaler_sup.json               지도학습 전용 스케일러 (CSV에서 자동 계산)
  output/sup_{name}/model_sup_{name}.onnx      ONNX 모델
  output/sup_{name}/threshold_sup_{name}.txt   최적 판정 임계값 (Youden's J 기준)
"""

import argparse
import csv
import json
import math
import os
import random
import time
import sys
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
SEQ_LEN        = 10
N_FEAT         = len(FEATURES)   # 12
SEED           = 42
SEQ_BREAK_DT   = 600
SCALER_SUP     = "output/scaler_sup.json"   # 지도 학습 전용 스케일러
OUTPUT_DIR     = "output"   # 모델·임계값 출력 디렉터리
DATA_DIR       = "data"     # CSV 입력 데이터 디렉터리

random.seed(SEED)
torch.manual_seed(SEED)
np.random.seed(SEED)

DEFAULTS = {
    "epochs":    40,
    "lr":        3e-4,
    "batch":     128,
    "n_anom":    None,   # 시나리오당 이상 시퀀스 수 (None=정상 수 기준 자동)
    "n_normal":  15000,  # 정상 시퀀스 최대 수
    "val_ratio": 0.15,
    "d_model":   64,
    "n_heads":   4,
    "n_layers":  2,
    "dropout":   0.1,
    "weight_decay": 1e-4,
}


# ══════════════════════════════════════════════════════════════════
# 스케일러
# ══════════════════════════════════════════════════════════════════

def load_scaler(path: str):
    with open(path) as f:
        j = json.load(f)
    return j["min"], j["max"]

def compute_scaler(raw_seqs: list):
    """raw 시퀀스 리스트에서 피처별 min/max 계산"""
    all_rows = [row for seq in raw_seqs for row in seq]
    arr = np.array(all_rows, dtype=np.float32)
    mins = arr.min(axis=0).tolist()
    maxs = arr.max(axis=0).tolist()
    return mins, maxs

def save_scaler(mins, maxs, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"min": mins, "max": maxs}, f, indent=2)

def scale_seq(seq, mins, maxs) -> list:
    out = []
    for row in seq:
        scaled = []
        for i, v in enumerate(row):
            d = maxs[i] - mins[i]
            s = (v - mins[i]) / d if d != 0 else 0.0
            scaled.append(max(0.0, min(1.0, s)))
        out.append(scaled)
    return out


# ══════════════════════════════════════════════════════════════════
# 데이터 파이프라인
# ══════════════════════════════════════════════════════════════════

def load_normal_from_csv(path: str, max_seqs: int = 15000,
                         max_mmsi: int = None) -> list:
    """CSV에서 raw(스케일링 전) 정상 시퀀스 로드"""
    if not os.path.exists(path):
        return []
    print(f"  [정상] CSV 로드: {path}")
    mmsi_data = defaultdict(list)
    with open(path, encoding="utf-8") as f:
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

    mmsis = list(mmsi_data.keys())
    if max_mmsi is not None:
        mmsis = mmsis[:max_mmsi]

    dt_idx = FEATURES.index("dt")
    all_seqs = []
    for mmsi in mmsis:
        records = mmsi_data[mmsi]
        seg, current = [], [records[0]]
        for rec in records[1:]:
            if rec[dt_idx] >= SEQ_BREAK_DT:
                seg.append(current)
                current = [rec]
            else:
                current.append(rec)
        seg.append(current)
        for s in seg:
            if len(s) < SEQ_LEN:
                continue
            for i in range(len(s) - SEQ_LEN + 1):
                all_seqs.append(s[i:i + SEQ_LEN])

    random.shuffle(all_seqs)
    sampled = all_seqs[:max_seqs]
    mmsi_info = f"{len(mmsis):,}개 MMSI" + (f" (전체 {len(mmsi_data):,}개 중)" if max_mmsi else "")
    print(f"    → {len(sampled):,}개 ({mmsi_info}, 전체 풀: {len(all_seqs):,})")
    return sampled


def build_dataset(args):
    from eval_anomaly import SCENARIO_MAKERS

    # ── 정상 시퀀스: CSV 실데이터 로드 (raw) ────────────────────
    csv_candidates = [
        f"{DATA_DIR}/ais_preprocessed.csv",
        f"{DATA_DIR}/ais-2024-01-01_preprocessed.csv",
        f"{DATA_DIR}/ais-2025-01-25_preprocessed.csv",
        f"{DATA_DIR}/ais-2025-12-31_preprocessed.csv",
        # 현재 디렉터리 폴백 (이전 방식 호환)
        "ais_preprocessed.csv",
        "ais-2025-01-25_preprocessed.csv",
    ]
    raw_normal = []
    for cand in csv_candidates:
        raw_normal = load_normal_from_csv(
            cand,
            max_seqs=args.n_normal,
            max_mmsi=args.max_mmsi,
        )
        if raw_normal:
            break

    if not raw_normal:
        print("  [오류] 정상 CSV 데이터를 찾을 수 없습니다. CSV 파일을 ml/data/ 폴더에 확인하세요.")
        sys.exit(1)

    # ── 스케일러: 정상 데이터에서 직접 계산 후 저장 ─────────────
    mins, maxs = compute_scaler(raw_normal)
    save_scaler(mins, maxs, SCALER_SUP)
    print(f"  [스케일러] 정상 데이터 기반으로 계산 → {SCALER_SUP}")

    normal_seqs = [scale_seq(seq, mins, maxs) for seq in raw_normal]

    # ── 이상 시퀀스 생성 (홀드아웃 제외, 정상 수에 비례) ─────────
    anom_makers = [(name, maker) for name, maker, is_anom, is_holdout
                   in SCENARIO_MAKERS if is_anom and not is_holdout]
    holdout_cnt = sum(1 for _, _, ia, ih in SCENARIO_MAKERS if ia and ih)

    # --n_anom 미지정 시 정상 수 기준으로 자동 계산
    n_anom = args.n_anom if args.n_anom is not None else \
             max(1, math.ceil(len(normal_seqs) / len(anom_makers)))

    print(f"  [이상] {len(anom_makers)}개 시나리오 × {n_anom}개 = "
          f"{len(anom_makers)*n_anom:,}개 목표  "
          f"(홀드아웃 {holdout_cnt}개 제외)")
    anom_seqs = []
    for name, maker in tqdm(anom_makers, desc="  이상 시나리오", leave=False):
        for _ in range(n_anom):
            try:
                seq = maker()
                anom_seqs.append(scale_seq(seq, mins, maxs))
            except Exception:
                pass
    print(f"    → {len(anom_seqs):,}개 생성")

    # ── 클래스 균형 (1:1 다운샘플) ──────────────────────────────
    n = min(len(normal_seqs), len(anom_seqs))
    random.shuffle(normal_seqs)
    random.shuffle(anom_seqs)
    normal_seqs = normal_seqs[:n]
    anom_seqs   = anom_seqs[:n]
    print(f"  [균형] 정상 {n:,}  이상 {n:,}  합계 {n*2:,}")

    X = normal_seqs + anom_seqs
    y = [0.0] * len(normal_seqs) + [1.0] * len(anom_seqs)

    combined = list(zip(X, y))
    random.shuffle(combined)
    X, y = zip(*combined)

    X_t = torch.tensor(X, dtype=torch.float32)   # (N, T, F)
    y_t = torch.tensor(y, dtype=torch.float32).unsqueeze(1)  # (N, 1)
    return X_t, y_t


def make_loaders(X_t, y_t, batch_size: int, val_ratio: float):
    dataset = TensorDataset(X_t, y_t)
    n_val   = max(1, int(len(dataset) * val_ratio))
    n_train = len(dataset) - n_val
    train_ds, val_ds = random_split(
        dataset, [n_train, n_val],
        generator=torch.Generator().manual_seed(SEED)
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=True)
    val_loader   = DataLoader(val_ds,   batch_size=batch_size, shuffle=False)
    return train_loader, val_loader


# ══════════════════════════════════════════════════════════════════
# ONNX 래퍼: sigmoid 포함 (출력 0~1 확률)
# ══════════════════════════════════════════════════════════════════

class SigmoidWrapper(nn.Module):
    def __init__(self, model: nn.Module):
        super().__init__()
        self.model = model

    def forward(self, x):
        return torch.sigmoid(self.model(x))


# ══════════════════════════════════════════════════════════════════
# 모델 1: PatchTST (NeurIPS 2023)
# ══════════════════════════════════════════════════════════════════

class PatchTST(nn.Module):
    """
    채널 독립 패치 어텐션.
    각 피처를 독립적으로 패치 분할 → 패치 임베딩 → Transformer → 집계 → 분류.
    """
    def __init__(self, seq_len=SEQ_LEN, n_feat=N_FEAT,
                 patch_len=2, stride=2,
                 d_model=64, n_heads=4, n_layers=2, dropout=0.1):
        super().__init__()
        self.seq_len   = seq_len
        self.n_feat    = n_feat
        self.patch_len = patch_len
        self.stride    = stride

        n_patches = (seq_len - patch_len) // stride + 1
        self.n_patches = n_patches

        self.patch_embed = nn.Linear(patch_len, d_model)
        self.cls_token   = nn.Parameter(torch.zeros(1, 1, d_model))
        self.pos_embed   = nn.Parameter(torch.randn(1, n_patches + 1, d_model) * 0.02)

        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout, batch_first=True, norm_first=True
        )
        self.transformer = nn.TransformerEncoder(enc_layer, num_layers=n_layers)
        self.norm  = nn.LayerNorm(d_model)
        self.head  = nn.Linear(d_model * n_feat, 1)

    def forward(self, x):
        # x: (B, T, F)
        B, T, F = x.shape
        # 채널 독립: (B*F, T)
        x = x.permute(0, 2, 1).reshape(B * F, T)

        # 패치 추출: (B*F, n_patches, patch_len)
        patches = x.unfold(dimension=1, size=self.patch_len, step=self.stride)

        # 임베딩: (B*F, n_patches, d_model)
        p = self.patch_embed(patches)

        # CLS 토큰 추가
        cls = self.cls_token.expand(B * F, -1, -1)
        p   = torch.cat([cls, p], dim=1)           # (B*F, n_patches+1, d_model)
        p   = p + self.pos_embed

        out = self.transformer(p)                  # (B*F, n_patches+1, d_model)
        out = self.norm(out[:, 0])                 # CLS 토큰: (B*F, d_model)

        # 피처별 CLS 토큰을 concat
        out = out.reshape(B, F * out.shape[-1])    # (B, F*d_model)
        return self.head(out)                       # (B, 1)


# ══════════════════════════════════════════════════════════════════
# 모델 2: iTransformer (ICLR 2024)
# ══════════════════════════════════════════════════════════════════

class iTransformer(nn.Module):
    """
    입력 전치: 피처를 토큰으로, 시간을 임베딩 차원으로.
    피처 간 어텐션을 통해 다변량 의존성을 명시적으로 학습.
    """
    def __init__(self, seq_len=SEQ_LEN, n_feat=N_FEAT,
                 d_model=64, n_heads=4, n_layers=2, dropout=0.1):
        super().__init__()
        # 각 피처의 시계열(길이 T)을 d_model로 투영
        self.input_proj = nn.Linear(seq_len, d_model)

        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout, batch_first=True, norm_first=True
        )
        self.transformer = nn.TransformerEncoder(enc_layer, num_layers=n_layers)
        self.norm = nn.LayerNorm(d_model)
        self.head = nn.Linear(d_model * n_feat, 1)
        self.n_feat = n_feat

    def forward(self, x):
        # x: (B, T, F) → (B, F, T)
        x = x.permute(0, 2, 1)
        # 각 피처 시계열 투영: (B, F, d_model)
        x = self.input_proj(x)
        # 피처 간 어텐션 (F개의 토큰)
        out = self.transformer(x)          # (B, F, d_model)
        out = self.norm(out)
        out = out.reshape(out.shape[0], -1)  # (B, F*d_model)
        return self.head(out)               # (B, 1)


# ══════════════════════════════════════════════════════════════════
# 모델 3: TSMixer (2023)
# ══════════════════════════════════════════════════════════════════

class TSMixerBlock(nn.Module):
    def __init__(self, seq_len, n_feat, dropout=0.1):
        super().__init__()
        # Time mixing: 각 피처에 동일한 MLP 적용 (across time)
        self.time_norm = nn.LayerNorm(seq_len)
        self.time_mlp  = nn.Sequential(
            nn.Linear(seq_len, seq_len * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(seq_len * 2, seq_len),
            nn.Dropout(dropout),
        )
        # Feature mixing: 각 시간 스텝에 MLP 적용 (across features)
        self.feat_norm = nn.LayerNorm(n_feat)
        self.feat_mlp  = nn.Sequential(
            nn.Linear(n_feat, n_feat * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(n_feat * 2, n_feat),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        # x: (B, T, F)
        # Time mixing: transpose to (B, F, T), apply MLP, transpose back
        r = x.permute(0, 2, 1)                # (B, F, T)
        r = self.time_mlp(self.time_norm(r))
        x = x + r.permute(0, 2, 1)            # residual

        # Feature mixing: apply MLP along feature dim
        r = self.feat_mlp(self.feat_norm(x))
        x = x + r
        return x


class TSMixer(nn.Module):
    def __init__(self, seq_len=SEQ_LEN, n_feat=N_FEAT,
                 n_layers=4, dropout=0.1, d_hidden=64):
        super().__init__()
        self.proj_in = nn.Linear(n_feat, d_hidden)
        self.blocks  = nn.ModuleList(
            [TSMixerBlock(seq_len, d_hidden, dropout) for _ in range(n_layers)]
        )
        self.norm = nn.LayerNorm(d_hidden)
        self.head = nn.Linear(d_hidden, 1)

    def forward(self, x):
        # x: (B, T, F) → project to d_hidden
        x = self.proj_in(x)             # (B, T, d_hidden)
        for blk in self.blocks:
            x = blk(x)
        x = self.norm(x)
        x = x.mean(dim=1)              # global average pool over time
        return self.head(x)            # (B, 1)


# ══════════════════════════════════════════════════════════════════
# 모델 4: ModernTCN (ICLR 2024)
# ══════════════════════════════════════════════════════════════════

class ModernTCNBlock(nn.Module):
    """ConvNeXt 스타일: DWConv(대형 커널) → LN → PWConv × 2"""
    def __init__(self, d_model, kernel_size=7, dropout=0.1):
        super().__init__()
        # Depthwise large-kernel conv (across time)
        self.dw_conv = nn.Conv1d(
            d_model, d_model,
            kernel_size=kernel_size,
            padding=kernel_size // 2,
            groups=d_model
        )
        self.norm  = nn.LayerNorm(d_model)
        self.pw1   = nn.Linear(d_model, d_model * 4)
        self.act   = nn.GELU()
        self.pw2   = nn.Linear(d_model * 4, d_model)
        self.drop  = nn.Dropout(dropout)
        self.gamma = nn.Parameter(torch.ones(d_model) * 1e-6)

    def forward(self, x):
        # x: (B, T, d_model)
        r = x.permute(0, 2, 1)            # (B, d_model, T)
        r = self.dw_conv(r)
        r = r.permute(0, 2, 1)            # (B, T, d_model)
        r = self.norm(r)
        r = self.pw2(self.drop(self.act(self.pw1(r))))
        return x + self.gamma * r


class ModernTCN(nn.Module):
    def __init__(self, seq_len=SEQ_LEN, n_feat=N_FEAT,
                 d_model=64, n_layers=3, kernel_size=7, dropout=0.1):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Linear(n_feat, d_model),
            nn.LayerNorm(d_model),
        )
        self.blocks = nn.ModuleList(
            [ModernTCNBlock(d_model, kernel_size, dropout) for _ in range(n_layers)]
        )
        self.norm = nn.LayerNorm(d_model)
        self.head = nn.Linear(d_model, 1)

    def forward(self, x):
        # x: (B, T, F)
        x = self.stem(x)                   # (B, T, d_model)
        for blk in self.blocks:
            x = blk(x)
        x = self.norm(x)
        x = x.mean(dim=1)                  # global average pool
        return self.head(x)                # (B, 1)


# ══════════════════════════════════════════════════════════════════
# 모델 5: Mamba SSM (NeurIPS 2023) — 순수 PyTorch, ONNX 호환
# ══════════════════════════════════════════════════════════════════

class MambaBlock(nn.Module):
    """
    선택적 상태 공간 모델 블록 (simplified Mamba).
    ONNX 호환을 위해 고정 T=10 시간 축 for-loop 사용.
    B, C, Δ는 입력에 의존 (selective mechanism).
    """
    def __init__(self, d_model, d_state=16, d_conv=4, expand=2, dropout=0.1, seq_len=SEQ_LEN):
        super().__init__()
        self.d_model = d_model
        self.d_state = d_state
        self.d_inner = d_model * expand
        self.seq_len = seq_len

        self.norm = nn.LayerNorm(d_model)

        # 입력 투영: x → z, u (inner dim)
        self.in_proj = nn.Linear(d_model, self.d_inner * 2, bias=False)

        # Depthwise conv (local context, 순방향)
        self.conv1d = nn.Conv1d(
            self.d_inner, self.d_inner,
            kernel_size=d_conv, padding=d_conv - 1,
            groups=self.d_inner, bias=True
        )

        # SSM 파라미터
        self.x_proj = nn.Linear(self.d_inner, d_state * 2 + 1, bias=False)  # B_proj, C_proj, Δ_raw
        self.dt_proj = nn.Linear(1, self.d_inner, bias=True)

        # A: 고정 log-spacing (학습 가능)
        A = torch.arange(1, d_state + 1, dtype=torch.float32).unsqueeze(0).expand(self.d_inner, -1)
        self.A_log = nn.Parameter(torch.log(A))
        self.D     = nn.Parameter(torch.ones(self.d_inner))

        self.out_proj = nn.Linear(self.d_inner, d_model, bias=False)
        self.drop = nn.Dropout(dropout)

    def forward(self, x):
        # x: (B, T, d_model)
        B_b, T, _ = x.shape
        res = x

        x = self.norm(x)
        xz = self.in_proj(x)                           # (B, T, d_inner*2)
        u, z = xz.chunk(2, dim=-1)                    # each (B, T, d_inner)

        # Depthwise conv (causal: truncate right padding)
        u_conv = self.conv1d(u.permute(0, 2, 1))       # (B, d_inner, T + pad)
        u_conv = u_conv[..., :T].permute(0, 2, 1)      # (B, T, d_inner)
        u_conv = F.silu(u_conv)

        # SSM 파라미터 계산 (선택적)
        bcd = self.x_proj(u_conv)                      # (B, T, 2*d_state + 1)
        B_proj = bcd[..., :self.d_state]               # (B, T, d_state)
        C_proj = bcd[..., self.d_state:2*self.d_state] # (B, T, d_state)
        dt_raw = bcd[..., -1:]                         # (B, T, 1)

        dt = F.softplus(self.dt_proj(dt_raw))          # (B, T, d_inner)
        A  = -torch.exp(self.A_log)                    # (d_inner, d_state)

        # 이산화 (ZOH): Ā = exp(A * Δ), B̄ = Δ * B
        # A_bar: (B, T, d_inner, d_state)
        dt_exp  = dt.unsqueeze(-1)                     # (B, T, d_inner, 1)
        A_bar   = torch.exp(dt_exp * A)                # (B, T, d_inner, d_state)
        B_bar   = dt_exp * B_proj.unsqueeze(2)         # (B, T, d_inner, d_state)

        # SSM 스캔 (고정 T for-loop — ONNX 호환)
        h = torch.zeros(B_b, self.d_inner, self.d_state, device=x.device)
        ys = []
        for t in range(T):
            h = A_bar[:, t] * h + B_bar[:, t] * u_conv[:, t].unsqueeze(-1)
            y_t = (h * C_proj[:, t].unsqueeze(1)).sum(-1)  # (B, d_inner)
            ys.append(y_t)
        y = torch.stack(ys, dim=1)                     # (B, T, d_inner)
        y = y + self.D * u_conv

        # Gate
        y = y * F.silu(z)
        y = self.out_proj(y)                           # (B, T, d_model)
        return res + self.drop(y)


class Mamba(nn.Module):
    def __init__(self, seq_len=SEQ_LEN, n_feat=N_FEAT,
                 d_model=64, d_state=16, n_layers=3, dropout=0.1):
        super().__init__()
        self.proj_in = nn.Linear(n_feat, d_model)
        self.blocks  = nn.ModuleList(
            [MambaBlock(d_model, d_state=d_state, dropout=dropout, seq_len=seq_len)
             for _ in range(n_layers)]
        )
        self.norm = nn.LayerNorm(d_model)
        self.head = nn.Linear(d_model, 1)

    def forward(self, x):
        x = self.proj_in(x)             # (B, T, d_model)
        for blk in self.blocks:
            x = blk(x)
        x = self.norm(x)
        x = x[:, -1]                   # 마지막 타임스텝
        return self.head(x)            # (B, 1)


# ══════════════════════════════════════════════════════════════════
# 학습 루프
# ══════════════════════════════════════════════════════════════════

def train_epoch(model, loader, optimizer, device):
    model.train()
    total_loss, correct, total = 0.0, 0, 0
    criterion = nn.BCEWithLogitsLoss()
    for X, y in loader:
        X, y = X.to(device), y.to(device)
        optimizer.zero_grad()
        logits = model(X)
        loss   = criterion(logits, y)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        total_loss += loss.item() * len(X)
        preds = (torch.sigmoid(logits) > 0.5).float()
        correct += (preds == y).sum().item()
        total   += len(X)
    return total_loss / total, correct / total


@torch.no_grad()
def eval_epoch(model, loader, device):
    model.eval()
    total_loss, correct, total = 0.0, 0, 0
    criterion = nn.BCEWithLogitsLoss()
    all_probs, all_labels = [], []
    for X, y in loader:
        X, y = X.to(device), y.to(device)
        logits = model(X)
        loss   = criterion(logits, y)
        probs  = torch.sigmoid(logits)
        total_loss += loss.item() * len(X)
        preds = (probs > 0.5).float()
        correct += (preds == y).sum().item()
        total   += len(X)
        all_probs.append(probs.cpu())
        all_labels.append(y.cpu())
    all_probs  = torch.cat(all_probs).squeeze(1).numpy()
    all_labels = torch.cat(all_labels).squeeze(1).numpy()
    return total_loss / total, correct / total, all_probs, all_labels


def find_best_threshold(probs, labels):
    """Youden's J 기준 최적 임계값 (TPR - FPR 최대화)"""
    best_thr, best_j = 0.5, -1.0
    for thr in np.linspace(0.1, 0.9, 81):
        preds = (probs >= thr).astype(float)
        tp = ((preds == 1) & (labels == 1)).sum()
        fp = ((preds == 1) & (labels == 0)).sum()
        fn = ((preds == 0) & (labels == 1)).sum()
        tn = ((preds == 0) & (labels == 0)).sum()
        tpr = tp / (tp + fn + 1e-9)
        fpr = fp / (fp + tn + 1e-9)
        j   = tpr - fpr
        if j > best_j:
            best_j, best_thr = j, float(thr)
    return best_thr, best_j


def export_onnx(model, name: str, device):
    model.eval()
    wrapped = SigmoidWrapper(model).to(device)
    wrapped.eval()
    dummy = torch.zeros(1, SEQ_LEN, N_FEAT, device=device)
    model_out_dir = os.path.join(OUTPUT_DIR, f"sup_{name}")
    os.makedirs(model_out_dir, exist_ok=True)
    path  = os.path.join(model_out_dir, f"model_sup_{name}.onnx")
    torch.onnx.export(
        wrapped, dummy, path,
        input_names=["x"],
        output_names=["output"],
        opset_version=18,
        dynamic_axes={"x": {0: "batch"}, "output": {0: "batch"}},
    )
    return path


def run_model(name: str, model: nn.Module, train_loader, val_loader, args, device):
    print(f"\n{'='*60}")
    print(f"  모델: {name.upper()}")
    print(f"{'='*60}")

    model = model.to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=args.epochs, eta_min=args.lr * 0.01
    )

    best_val_loss = float("inf")
    best_state    = None
    t0 = time.time()

    for epoch in range(1, args.epochs + 1):
        tr_loss, tr_acc = train_epoch(model, train_loader, optimizer, device)
        va_loss, va_acc, _, _ = eval_epoch(model, val_loader, device)
        scheduler.step()

        if va_loss < best_val_loss:
            best_val_loss = va_loss
            best_state    = {k: v.clone() for k, v in model.state_dict().items()}

        if epoch % 10 == 0 or epoch == 1:
            lr_now = scheduler.get_last_lr()[0]
            print(f"  epoch {epoch:3d}/{args.epochs} | "
                  f"tr_loss={tr_loss:.4f} tr_acc={tr_acc:.3f} | "
                  f"va_loss={va_loss:.4f} va_acc={va_acc:.3f} | "
                  f"lr={lr_now:.2e}")

    elapsed = time.time() - t0
    print(f"  학습 완료: {elapsed:.1f}s")

    # 최적 가중치 복원
    if best_state is not None:
        model.load_state_dict(best_state)

    # 검증 데이터로 임계값 계산
    _, val_acc, probs, labels = eval_epoch(model, val_loader, device)
    best_thr, best_j = find_best_threshold(probs, labels)
    print(f"  최적 임계값: {best_thr:.3f}  (Youden J={best_j:.3f})")
    print(f"  검증 정확도: {val_acc:.3f}")

    # F1 계산
    preds = (probs >= best_thr).astype(float)
    tp = ((preds == 1) & (labels == 1)).sum()
    fp = ((preds == 1) & (labels == 0)).sum()
    fn = ((preds == 0) & (labels == 1)).sum()
    precision = tp / (tp + fp + 1e-9)
    recall    = tp / (tp + fn + 1e-9)
    f1        = 2 * precision * recall / (precision + recall + 1e-9)
    print(f"  Precision={precision:.3f}  Recall={recall:.3f}  F1={f1:.3f}")

    # ONNX export
    onnx_path = export_onnx(model, name, device)
    print(f"  ONNX 저장: {onnx_path}")

    # 임계값 저장
    model_out_dir = os.path.join(OUTPUT_DIR, f"sup_{name}")
    os.makedirs(model_out_dir, exist_ok=True)
    thr_path = os.path.join(model_out_dir, f"threshold_sup_{name}.txt")
    with open(thr_path, "w") as f:
        f.write(f"{best_thr:.6f}\n")
        f.write(f"# model: {name}\n")
        f.write(f"# Youden_J: {best_j:.4f}\n")
        f.write(f"# F1: {f1:.4f}\n")
        f.write(f"# val_acc: {val_acc:.4f}\n")
    print(f"  임계값 저장: {thr_path}")

    return {"name": name, "f1": f1, "precision": precision,
            "recall": recall, "threshold": best_thr}


# ══════════════════════════════════════════════════════════════════
# 모델 팩토리
# ══════════════════════════════════════════════════════════════════

def build_model(name: str, args) -> nn.Module:
    d = args.d_model
    h = args.n_heads
    l = args.n_layers
    dr = args.dropout
    if name == "patchtst":
        return PatchTST(SEQ_LEN, N_FEAT, patch_len=2, stride=2,
                        d_model=d, n_heads=h, n_layers=l, dropout=dr)
    elif name == "itrans":
        return iTransformer(SEQ_LEN, N_FEAT, d_model=d, n_heads=h,
                            n_layers=l, dropout=dr)
    elif name == "tsmixer":
        return TSMixer(SEQ_LEN, N_FEAT, n_layers=max(l, 4), dropout=dr, d_hidden=d)
    elif name == "moderntcn":
        return ModernTCN(SEQ_LEN, N_FEAT, d_model=d, n_layers=l,
                         kernel_size=7, dropout=dr)
    elif name == "mamba":
        return Mamba(SEQ_LEN, N_FEAT, d_model=d, d_state=16,
                     n_layers=l, dropout=dr)
    else:
        raise ValueError(f"알 수 없는 모델: {name}")


ALL_MODELS = ["patchtst", "itrans", "tsmixer", "moderntcn", "mamba"]


# ══════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AIS 지도 학습 이상 탐지")
    parser.add_argument("--model", type=str, default="all",
                        choices=ALL_MODELS + ["all"],
                        help="학습할 모델 (default: all)")
    parser.add_argument("--epochs",       type=int,   default=DEFAULTS["epochs"])
    parser.add_argument("--lr",           type=float, default=DEFAULTS["lr"])
    parser.add_argument("--batch",        type=int,   default=DEFAULTS["batch"])
    parser.add_argument("--n_anom",       type=int,   default=DEFAULTS["n_anom"],
                        help="시나리오당 이상 시퀀스 수 (기본: 정상 수 기준 자동 계산)")
    parser.add_argument("--n_normal",     type=int,   default=DEFAULTS["n_normal"],
                        help="정상 시퀀스 최대 수")
    parser.add_argument("--max_mmsi",     type=int,   default=None,
                        help="학습에 사용할 최대 MMSI 수 (기본: 전체)")
    parser.add_argument("--val_ratio",    type=float, default=DEFAULTS["val_ratio"])
    parser.add_argument("--d_model",      type=int,   default=DEFAULTS["d_model"])
    parser.add_argument("--n_heads",      type=int,   default=DEFAULTS["n_heads"])
    parser.add_argument("--n_layers",     type=int,   default=DEFAULTS["n_layers"])
    parser.add_argument("--dropout",      type=float, default=DEFAULTS["dropout"])
    parser.add_argument("--weight_decay", type=float, default=DEFAULTS["weight_decay"])
    parser.add_argument("--device",       type=str,   default="auto",
                        help="cuda / cpu / auto")
    args = parser.parse_args()

    # 출력/데이터 디렉터리 생성
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(DATA_DIR,   exist_ok=True)

    # 디바이스 설정
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    print(f"[디바이스] {device}")

    # 데이터 준비 (스케일러는 CSV에서 자동 계산)
    print("\n[데이터 준비]")
    X_t, y_t = build_dataset(args)
    train_loader, val_loader = make_loaders(X_t, y_t, args.batch, args.val_ratio)
    print(f"  train: {len(train_loader.dataset):,}  val: {len(val_loader.dataset):,}")

    # 학습 대상 모델 목록
    targets = ALL_MODELS if args.model == "all" else [args.model]

    results = []
    for name in targets:
        model = build_model(name, args)
        n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
        print(f"\n  파라미터 수: {n_params:,}")
        res = run_model(name, model, train_loader, val_loader, args, device)
        results.append(res)

    # 최종 요약
    print(f"\n{'='*60}")
    print("  최종 성능 요약")
    print(f"{'='*60}")
    print(f"  {'모델':<12} {'F1':>8} {'Precision':>10} {'Recall':>8} {'Threshold':>10}")
    print(f"  {'-'*50}")
    for r in sorted(results, key=lambda x: -x["f1"]):
        print(f"  {r['name']:<12} {r['f1']:>8.3f} {r['precision']:>10.3f} "
              f"{r['recall']:>8.3f} {r['threshold']:>10.3f}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
