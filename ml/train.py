"""
AIS LSTM Autoencoder 학습 스크립트

입력: ais_preprocessed.csv
출력: model.pt, model.onnx, scaler.json, threshold.txt

피처 (12개):
    sog, cog, heading, status,
    dt, dist_km,
    cog_hdg_diff, sog_change, cog_hdg_change,
    speed_consistency,
    lat_speed, lon_speed
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
INPUT_FILE     = "ais-2025-12-31_preprocessed.csv"
ONNX_FILE      = "model.onnx"
PT_FILE        = "model.pt"
SCALER_FILE    = "scaler.json"
THRESHOLD_FILE = "threshold.txt"

FEATURES = [
    "sog", "cog", "heading", "status",
    "dt", "dist_km",
    "cog_hdg_diff", "sog_change",
    "cog_hdg_change",
    "speed_consistency",
    "lat_speed", "lon_speed",
]

SEQ_LEN      = 10
HIDDEN_SIZE  = 64
NUM_LAYERS   = 2
BATCH_SIZE   = 256
EPOCHS       = 30
LR           = 0.001
PATIENCE     = 5
VAL_RATIO    = 0.1
THRESHOLD_PERCENTILE = 95
SAMPLE_MMSI  = 10000
SEQ_BREAK_DT = 600
SEED         = 42

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
class LSTMAutoencoder(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers  = num_layers

        self.encoder = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.decoder = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.output_layer = nn.Linear(hidden_size, input_size)
        self.start_token = nn.Parameter(torch.zeros(1, 1, input_size))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size = x.size(0)

        _, (hidden, cell) = self.encoder(x)

        dec_input  = self.start_token.expand(batch_size, 1, -1).clone()
        dec_hidden = hidden
        dec_cell   = cell

        steps = []
        for _t in range(x.size(1)):
            out, (dec_hidden, dec_cell) = self.decoder(
                dec_input, (dec_hidden, dec_cell)
            )
            step_out  = self.output_layer(out)
            steps.append(step_out)
            dec_input = step_out

        return torch.cat(steps, dim=1)


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


# ── .pt 저장 ─────────────────────────────────────────────────────
def save_pt(model):
    torch.save(model.state_dict(), PT_FILE)
    print(f"  .pt 저장: {PT_FILE}")


# ── ONNX export ──────────────────────────────────────────────────
def export_onnx(model, device):
    import warnings
    model.eval()
    dummy = torch.zeros(1, SEQ_LEN, len(FEATURES), dtype=torch.float32).to(device)

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
    print(f"[피처 수]  {len(FEATURES)}")

    _t1 = time.time()
    print("[1/6] 데이터 로드...")
    mmsi_data = load_data(INPUT_FILE)

    _t2 = time.time()
    print("[2/6] 시퀀스 생성...")
    sequences = make_sequences(mmsi_data)

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

    _t5 = time.time()
    print("[5/6] 학습 시작...")
    model = LSTMAutoencoder(
        input_size=len(FEATURES),
        hidden_size=HIDDEN_SIZE,
        num_layers=NUM_LAYERS,
    ).to(device)
    train(model, train_loader, val_loader, device)
    save_pt(model)
    export_onnx(model, device)

    _t6 = time.time()
    print("[6/6] 임계값 계산...")
    threshold = calc_threshold(model, train_loader, device)
    with open(THRESHOLD_FILE, "w") as f:
        f.write(str(threshold))
    print(f"  임계값: {threshold:.6f}  (상위 {100 - THRESHOLD_PERCENTILE}%)")
    print(f"  임계값 저장: {THRESHOLD_FILE}")

    t_end = time.time()
    print("완료!")
    print(f"\n  [소요 시간]")
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