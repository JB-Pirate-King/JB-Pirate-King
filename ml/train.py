"""
AIS LSTM Autoencoder 학습 스크립트

입력: ais_preprocessed.csv
출력: model.pt, scaler.pkl, threshold.txt
"""

import csv
import math
import pickle
import json
from collections import defaultdict

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from tqdm import tqdm

# ── 설정 ──────────────────────────────────────────────────────────
INPUT_FILE   = "ais_preprocessed.csv"
MODEL_FILE   = "model.pt"
SCALER_FILE  = "scaler.pkl"
THRESHOLD_FILE = "threshold.txt"

FEATURES     = ["sog", "cog", "dt", "dist_km"]
SEQ_LEN      = 10
HIDDEN_SIZE  = 64
NUM_LAYERS   = 2
BATCH_SIZE   = 256
EPOCHS       = 10
LR           = 0.001
THRESHOLD_PERCENTILE = 95
SAMPLE_MMSI  = 100
SEQ_BREAK_DT = 3600  # dt가 1시간 이상이면 시퀀스 끊기

# ── 정규화 (Min-Max Scaler) ───────────────────────────────────────
class MinMaxScaler:
    def __init__(self):
        self.min_ = None
        self.max_ = None

    def fit(self, data):
        self.min_ = [min(row[i] for row in data) for i in range(len(data[0]))]
        self.max_ = [max(row[i] for row in data) for i in range(len(data[0]))]

    def transform(self, data):
        result = []
        for row in data:
            scaled = []
            for i, val in enumerate(row):
                denom = self.max_[i] - self.min_[i]
                scaled.append((val - self.min_[i]) / denom if denom != 0 else 0.0)
            result.append(scaled)
        return result

    def fit_transform(self, data):
        self.fit(data)
        return self.transform(data)

# ── LSTM Autoencoder ─────────────────────────────────────────────
class LSTMAutoencoder(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int):
        super().__init__()
        self.encoder = nn.LSTM(input_size, hidden_size, num_layers,
                               batch_first=True)
        self.decoder = nn.LSTM(hidden_size, hidden_size, num_layers,
                               batch_first=True)
        self.output_layer = nn.Linear(hidden_size, input_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Encode
        _, (hidden, cell) = self.encoder(x)

        # Decoder 입력: hidden state를 시퀀스 길이만큼 반복
        decoder_input = hidden[-1].unsqueeze(1).repeat(1, x.size(1), 1)

        # Decode
        out, _ = self.decoder(decoder_input, (hidden, cell))
        return self.output_layer(out)

# ── 데이터 로드 ───────────────────────────────────────────────────
def load_data(path):
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

    # MMSI 샘플링
    if SAMPLE_MMSI and len(mmsi_data) > SAMPLE_MMSI:
        import random
        sampled_keys = random.sample(list(mmsi_data.keys()), SAMPLE_MMSI)
        mmsi_data = {k: mmsi_data[k] for k in sampled_keys}
        print(f"  샘플링 후 MMSI: {len(mmsi_data):,}")

    return mmsi_data

# ── 시퀀스 생성 (슬라이딩 윈도우) ────────────────────────────────
def make_sequences(mmsi_data):
    dt_idx      = FEATURES.index("dt")
    dist_km_idx = FEATURES.index("dist_km")
    sequences = []

    for mmsi, records in mmsi_data.items():
        # dt가 SEQ_BREAK_DT 이상이면 시퀀스 끊기
        segments = []
        current = [records[0]]
        for rec in records[1:]:
            if rec[dt_idx] >= SEQ_BREAK_DT:
                segments.append(current)
                rec = list(rec)
                rec[dt_idx]      = 0.0  # 새 세그먼트 시작 시 dt 리셋
                rec[dist_km_idx] = 0.0  # 새 세그먼트 시작 시 dist_km 리셋
                current = [rec]
            else:
                current.append(rec)
        segments.append(current)

        # 세그먼트별 슬라이딩 윈도우
        for seg in segments:
            if len(seg) < SEQ_LEN:
                continue
            for i in range(len(seg) - SEQ_LEN + 1):
                sequences.append(seg[i:i + SEQ_LEN])

    print(f"  총 시퀀스: {len(sequences):,}")
    return sequences

# ── 메인 ──────────────────────────────────────────────────────────
def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[디바이스] {device}")

    # 1. 데이터 로드
    print("[1/5] 데이터 로드...")
    mmsi_data = load_data(INPUT_FILE)

    # 2. 시퀀스 생성
    print("[2/5] 시퀀스 생성...")
    sequences = make_sequences(mmsi_data)

    # 3. 정규화
    print("[3/5] 정규화...")
    flat = [row for seq in sequences for row in seq]
    scaler = MinMaxScaler()
    scaler.fit(flat)
    scaled_sequences = [scaler.transform(seq) for seq in sequences]

    with open(SCALER_FILE, "wb") as f:
        pickle.dump(scaler, f)
    print(f"  스케일러 저장: {SCALER_FILE}")

    # C++ 추론용 스케일러 JSON 저장
    SCALER_JSON_FILE = "scaler.json"
    scaler_json = {
        "features": FEATURES,
        "min": scaler.min_,
        "max": scaler.max_,
    }
    with open(SCALER_JSON_FILE, "w") as f:
        json.dump(scaler_json, f, indent=2)
    print(f"  스케일러 JSON 저장: {SCALER_JSON_FILE}")

    # 4. 텐서 변환 및 DataLoader
    print("[4/5] 학습 준비...")
    tensor = torch.tensor(scaled_sequences, dtype=torch.float32)
    dataset = TensorDataset(tensor)
    loader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True)

    # 5. 모델 학습
    print("[5/5] 학습 시작...")
    model = LSTMAutoencoder(
        input_size=len(FEATURES),
        hidden_size=HIDDEN_SIZE,
        num_layers=NUM_LAYERS,
    ).to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    criterion = nn.MSELoss()

    for epoch in range(1, EPOCHS + 1):
        model.train()
        total_loss = 0.0
        pbar = tqdm(loader, desc=f"Epoch {epoch:3d}/{EPOCHS}", leave=True)
        for (batch,) in pbar:
            batch = batch.to(device)
            output = model(batch)
            loss = criterion(output, batch)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            pbar.set_postfix(loss=f"{loss.item():.6f}")

        avg_loss = total_loss / len(loader)
        pbar.set_postfix(avg_loss=f"{avg_loss:.6f}")

    torch.save(model.state_dict(), MODEL_FILE)
    print(f"  모델 저장: {MODEL_FILE}")

    # TorchScript 변환 및 저장
    TORCHSCRIPT_FILE = "model_scripted.pt"
    model.eval()
    scripted = torch.jit.script(model)
    scripted.save(TORCHSCRIPT_FILE)
    print(f"  TorchScript 모델 저장: {TORCHSCRIPT_FILE}")

    # ONNX 변환 및 저장
    ONNX_FILE = "model.onnx"
    model.eval()
    dummy_input = torch.zeros(1, SEQ_LEN, len(FEATURES), dtype=torch.float32).to(device)
    onnx_program = torch.onnx.export(
        model,
        (dummy_input,),
        dynamo=True,
    )
    onnx_program.apply_weights(dict(model.named_parameters()))
    onnx_program.save(ONNX_FILE)
    print(f"  ONNX 모델 저장: {ONNX_FILE}")

    # 6. 임계값 계산
    print("[6/6] 임계값 계산...")
    model.eval()
    errors = []
    with torch.no_grad():
        for (batch,) in loader:
            batch = batch.to(device)
            output = model(batch)
            mse = ((output - batch) ** 2).mean(dim=(1, 2))
            errors.extend(mse.cpu().tolist())

    errors.sort()
    threshold = errors[int(len(errors) * THRESHOLD_PERCENTILE / 100)]
    with open(THRESHOLD_FILE, "w") as f:
        f.write(str(threshold))
    print(f"  임계값: {threshold:.6f} (상위 {100 - THRESHOLD_PERCENTILE}%)")
    print(f"  임계값 저장: {THRESHOLD_FILE}")
    print("완료!")

if __name__ == "__main__":
    main()