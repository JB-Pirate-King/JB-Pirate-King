"""
AIS LSTM Autoencoder 학습 스크립트

입력: ais_preprocessed.csv
출력: model.onnx, scaler.json, threshold.txt

변경 이력:
  v2 - Decoder 구조 개선 (teacher forcing → autoregressive)
     - ONNX export: dynamo=True 제거, 고정 입출력 이름 지정
     - 검증 셋 분리 및 조기 종료 추가
     - 재현성을 위한 시드 고정
"""

import csv
import json
import random
import time
from collections import defaultdict

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset, random_split
from tqdm import tqdm

# ── 설정 ──────────────────────────────────────────────────────────
INPUT_FILE     = "ais_preprocessed.csv"
ONNX_FILE      = "model.onnx"
SCALER_FILE    = "scaler.json"
THRESHOLD_FILE = "threshold.txt"

FEATURES     = ["sog", "cog", "heading", "status", "dt", "dist_km",
                "expected_dist_km", "bearing_cog_diff", "cog_hdg_diff",
                "sog_change", "cog_change", "status_sog_product", "dist_expected_ratio"]
SEQ_LEN      = 10       # C++ 쪽 ML_SEQ_LEN 과 반드시 일치
HIDDEN_SIZE  = 64
NUM_LAYERS   = 2
BATCH_SIZE   = 256
EPOCHS       = 30
LR           = 0.001
PATIENCE     = 5        # 조기 종료: 검증 손실이 N epoch 개선 없으면 중단
VAL_RATIO    = 0.1      # 검증 셋 비율
THRESHOLD_PERCENTILE = 95
SAMPLE_MMSI  = 500
SEQ_BREAK_DT = 3600     # dt 1시간 이상이면 시퀀스 분리
SEED         = 42

# ── 재현성 시드 ───────────────────────────────────────────────────
random.seed(SEED)
torch.manual_seed(SEED)


# ── 정규화 (Min-Max Scaler) ───────────────────────────────────────
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


# ── LSTM Autoencoder ─────────────────────────────────────────────
#
# [이전 구조의 문제]
#   decoder_input = hidden[-1].unsqueeze(1).repeat(1, seq_len, 1)
#   → encoder hidden state 복사본을 decoder 입력으로 사용
#   → 모든 타임스텝이 동일한 입력을 받으므로, decoder가
#     시간적 순서를 전혀 학습하지 못함
#   → 재구성 오차가 전 시퀀스에 걸쳐 균일하게 분포 →
#     이상 탐지 민감도 저하
#
# [개선된 구조]
#   encoder: x → (hidden, cell) 로 시퀀스를 압축
#   decoder: 첫 입력으로 learned start token 사용,
#            이후 자신의 출력을 다음 타임스텝 입력으로 사용
#            (autoregressive / teacher forcing 없음)
#   → 시간 순서를 유지하면서 재구성하므로, 정상 패턴과의
#     오차가 더 의미있는 이상 신호가 됨
#
# [ONNX 호환성]
#   autoregressive loop 는 ONNX 정적 그래프로 변환이 어려우므로,
#   루프를 torch.jit.script 친화적인 방식으로 작성하고
#   legacy torch.onnx.export (opset 12) 를 사용
# ─────────────────────────────────────────────────────────────────
class LSTMAutoencoder(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers  = num_layers

        self.encoder = nn.LSTM(input_size, hidden_size, num_layers,
                               batch_first=True)
        # decoder 입력 크기: input_size (이전 타임스텝 출력)
        self.decoder = nn.LSTM(input_size, hidden_size, num_layers,
                               batch_first=True)
        self.output_layer = nn.Linear(hidden_size, input_size)

        # 시퀀스 시작 토큰 (학습 가능한 파라미터)
        self.start_token = nn.Parameter(torch.zeros(1, 1, input_size))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        x      : (batch, seq_len, input_size)
        returns: (batch, seq_len, input_size)

        jit.trace 호환:
          - Python list + torch.cat 대신 pre-allocated output tensor 사용
          - seq_len 은 x.size(1) 에서 읽으므로 고정 shape 에서 unroll 됨
        """
        batch_size = x.size(0)
        seq_len    = x.size(1)

        # ── Encode ────────────────────────────────────────────────
        _, (hidden, cell) = self.encoder(x)

        # ── Decode (autoregressive) ───────────────────────────────
        dec_input  = self.start_token.expand(batch_size, 1, -1).clone()
        dec_hidden = hidden
        dec_cell   = cell

        steps = []
        for _t in range(x.size(1)):
            out, (dec_hidden, dec_cell) = self.decoder(
                dec_input, (dec_hidden, dec_cell)
            )
            step_out  = self.output_layer(out)   # (B, 1, input_size)
            steps.append(step_out)
            dec_input = step_out

        return torch.cat(steps, dim=1)           # (B, seq_len, input_size)


# ── 데이터 로드 ───────────────────────────────────────────────────
def load_data(path: str) -> dict:
    print(f"  데이터 로드 중: {path}")
    mmsi_data = defaultdict(list)

    with open(path, "r", encoding="utf-8") as f:
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
        sampled_keys = random.sample(list(mmsi_data.keys()), SAMPLE_MMSI)
        mmsi_data = {k: mmsi_data[k] for k in sampled_keys}
        print(f"  샘플링 후 MMSI: {len(mmsi_data):,}")

    return mmsi_data


# ── 시퀀스 생성 (슬라이딩 윈도우) ────────────────────────────────
def make_sequences(mmsi_data: dict) -> list:
    dt_idx      = FEATURES.index("dt")
    dist_km_idx = FEATURES.index("dist_km")
    sequences   = []

    for records in mmsi_data.values():
        segments = []
        current  = [records[0]]
        for rec in records[1:]:
            if rec[dt_idx] >= SEQ_BREAK_DT:
                segments.append(current)
                rec = list(rec)
                rec[dt_idx]      = 0.0
                rec[dist_km_idx] = 0.0
                current = [rec]
            else:
                current.append(rec)
        segments.append(current)

        for seg in segments:
            if len(seg) < SEQ_LEN:
                continue
            for i in range(len(seg) - SEQ_LEN + 1):
                sequences.append(seg[i : i + SEQ_LEN])

    print(f"  총 시퀀스: {len(sequences):,}")
    return sequences


# ── 학습 루프 ─────────────────────────────────────────────────────
def train(model, loader, val_loader, device):
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    criterion = nn.MSELoss()

    best_val_loss = float("inf")
    best_state    = None
    patience_cnt  = 0

    for epoch in range(1, EPOCHS + 1):
        # Train
        model.train()
        train_loss = 0.0
        pbar = tqdm(loader, desc=f"Epoch {epoch:3d}/{EPOCHS}", leave=False)
        for (batch,) in pbar:
            batch  = batch.to(device)
            output = model(batch)
            loss   = criterion(output, batch)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            train_loss += loss.item()
            pbar.set_postfix(loss=f"{loss.item():.6f}")

        train_loss /= len(loader)

        # Validation
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                batch    = batch.to(device)
                output   = model(batch)
                val_loss += criterion(output, batch).item()
        val_loss /= len(val_loader)

        print(f"  Epoch {epoch:3d}/{EPOCHS} | "
              f"train={train_loss:.6f} | val={val_loss:.6f}")

        # 조기 종료
        if val_loss < best_val_loss - 1e-6:
            best_val_loss = val_loss
            best_state    = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt  = 0
        else:
            patience_cnt += 1
            if patience_cnt >= PATIENCE:
                print(f"  조기 종료: {PATIENCE} epoch 동안 개선 없음 → 최적 가중치 복원")
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    print(f"  최적 검증 손실: {best_val_loss:.6f}")


# ── ONNX export ──────────────────────────────────────────────────
#
# PyTorch 2.11 에서 torch.onnx.export 는 dynamo 경로가 기본이지만,
# torch.jit.trace 로 먼저 변환하면 legacy(TorchScript) 경로가 강제되어
# opset_version / input_names / output_names 가 안정적으로 동작한다.
#
# jit.trace 주의사항:
#   - dummy 입력 shape 에 고정됨 (1, SEQ_LEN, F) → C++ 추론도 동일 shape
#   - autoregressive loop 는 SEQ_LEN 만큼 unroll 되어 정적 그래프로 캡처
# ─────────────────────────────────────────────────────────────────
def export_onnx(model, device):
    import warnings
    model.eval()
    dummy = torch.zeros(1, SEQ_LEN, len(FEATURES), dtype=torch.float32).to(device)

    # PyTorch 2.11: dynamo=False 로 legacy TorchScript 경로 강제
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        torch.onnx.export(
            model,
            (dummy,),
            ONNX_FILE,
            dynamo=False,
            opset_version=14,
            input_names=["x"],
            output_names=["output"],
        )
    print(f"  ONNX 저장: {ONNX_FILE}")

# ── 임계값 계산 ───────────────────────────────────────────────────
def calc_threshold(model, loader, device) -> float:
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
    return errors[idx]


# ── 메인 ──────────────────────────────────────────────────────────
def main():
    t_start = time.time()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[디바이스] {device}")

    # 1. 데이터 로드
    _t1 = time.time()
    print("[1/6] 데이터 로드...")
    mmsi_data = load_data(INPUT_FILE)

    # 2. 시퀀스 생성
    _t2 = time.time()
    print("[2/6] 시퀀스 생성...")
    sequences = make_sequences(mmsi_data)

    # 3. 정규화
    _t3 = time.time()
    print("[3/6] 정규화...")
    flat   = [row for seq in sequences for row in seq]
    scaler = MinMaxScaler()
    scaler.fit(flat)
    scaled = [scaler.transform(seq) for seq in sequences]

    with open(SCALER_FILE, "w") as f:
        json.dump({"features": FEATURES, "min": scaler.min_, "max": scaler.max_},
                  f, indent=2)
    print(f"  스케일러 저장: {SCALER_FILE}")

    # 4. Dataset / DataLoader (train / val 분리)
    _t4 = time.time()
    print("[4/6] 학습 준비...")
    tensor   = torch.tensor(scaled, dtype=torch.float32)
    dataset  = TensorDataset(tensor)
    n_val    = max(1, int(len(dataset) * VAL_RATIO))
    n_train  = len(dataset) - n_val
    train_ds, val_ds = random_split(
        dataset, [n_train, n_val],
        generator=torch.Generator().manual_seed(SEED)
    )
    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
    val_loader   = DataLoader(val_ds,   batch_size=BATCH_SIZE, shuffle=False)
    print(f"  학습: {n_train:,}  검증: {n_val:,}")

    # 5. 학습 + ONNX 변환
    _t5 = time.time()
    print("[5/6] 학습 시작...")
    model = LSTMAutoencoder(
        input_size=len(FEATURES),
        hidden_size=HIDDEN_SIZE,
        num_layers=NUM_LAYERS,
    ).to(device)
    train(model, train_loader, val_loader, device)
    export_onnx(model, device)

    # 6. 임계값 계산 (train 셋 오차 분포 기준, 검증 셋 누수 방지)
    _t6 = time.time()
    print("[6/6] 임계값 계산...")
    threshold = calc_threshold(model, train_loader, device)
    with open(THRESHOLD_FILE, "w") as f:
        f.write(str(threshold))
    print(f"  임계값: {threshold:.6f}  (상위 {100 - THRESHOLD_PERCENTILE}%)")
    print(f"  임계값 저장: {THRESHOLD_FILE}")
    t_end = time.time()
    print("완료!")
    print("")
    print(f"  [소요 시간]")
    print(f"  데이터 로드:   {_t2-_t1:6.1f}s")
    print(f"  시퀀스 생성:   {_t3-_t2:6.1f}s")
    print(f"  정규화:        {_t4-_t3:6.1f}s")
    print(f"  학습 준비:     {_t5-_t4:6.1f}s")
    print(f"  학습+ONNX:     {_t6-_t5:6.1f}s")
    print(f"  임계값 계산:   {t_end-_t6:6.1f}s")
    print(f"  ─────────────────────")
    print(f"  전체:          {t_end-t_start:6.1f}s")


if __name__ == "__main__":
    main()