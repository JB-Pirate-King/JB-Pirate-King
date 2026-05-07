from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"
DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
API_BASE = "http://localhost:5000"


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    kwargs = {
        "cwd": ROOT,
        "text": True,
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
    return subprocess.run(cmd, check=check, **kwargs)


def docker_ready() -> bool:
    try:
        proc = run(["docker", "ps", "--format", "{{.ID}}"], check=False, capture=True)
        return proc.returncode == 0
    except FileNotFoundError:
        return False


def ensure_docker_cli() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("docker CLI not found. Install Docker Desktop first.")


def start_docker_desktop() -> bool:
    if not DOCKER_DESKTOP.exists():
        return False
    subprocess.Popen([str(DOCKER_DESKTOP)])
    return True


def wait_for_docker(timeout_sec: int) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if docker_ready():
            return True
        time.sleep(3)
    return False


def try_switch_context() -> None:
    proc = run(["docker", "context", "ls", "--format", "{{.Name}}"], check=False, capture=True)
    if proc.returncode != 0:
        return

    contexts = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    if "desktop-linux" not in contexts:
        return

    current = run(["docker", "context", "show"], check=False, capture=True)
    if current.returncode == 0 and current.stdout.strip() == "desktop-linux":
        return

    switched = run(["docker", "context", "use", "desktop-linux"], check=False, capture=True)
    if switched.returncode != 0:
        print("warning: could not switch docker context to desktop-linux automatically")
        if switched.stdout:
            print(switched.stdout.strip())


def inspect_models() -> dict:
    MODELS_DIR.mkdir(exist_ok=True)
    cfg_path = MODELS_DIR / "ensemble_config.json"

    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            model_names = list(cfg.get("models", []))
            scaler_names = list(cfg.get("scalers") or [cfg.get("scaler", "")] * len(model_names))
            threshold = cfg.get("threshold", "threshold_weighted_ensemble.txt")
            notes = []

            available_scalers = sorted(path.name for path in MODELS_DIR.glob("scaler*.json"))
            missing_scalers = [name for name in scaler_names if not (MODELS_DIR / name).exists()]
            if missing_scalers and len(available_scalers) == 1:
                scaler_names = [available_scalers[0] for _ in scaler_names]
                notes.append(f"using shared scaler: {available_scalers[0]}")

            required = model_names + scaler_names + [threshold]
            missing = [name for name in required if not (MODELS_DIR / name).exists()]
            if not model_names:
                missing.append("models[] in ensemble_config.json")
            if len(scaler_names) != len(model_names):
                missing.append("scalers[] count must match models[]")
            return {"mode": f"ensemble ({len(model_names)} models)", "ready": not missing, "missing": missing, "notes": notes}
        except Exception as exc:
            return {"mode": "ensemble", "ready": False, "missing": [f"invalid ensemble_config.json: {exc}"], "notes": []}

    required = ["model.onnx", "scaler.json", "threshold.txt"]
    missing = [name for name in required if not (MODELS_DIR / name).exists()]
    return {"mode": "single", "ready": not missing, "missing": missing, "notes": []}


def print_model_summary() -> None:
    model = inspect_models()
    print(f"Model mode: {model['mode']}")
    if model["ready"]:
        print("Model files: ready")
        for item in model.get("notes", []):
            print(f"  - {item}")
        return
    print("Model files: missing. Server will start, but ML detection will be disabled.")
    for item in model["missing"]:
        print(f"  - {item}")


def fetch_status() -> dict | None:
    try:
        with urllib.request.urlopen(f"{API_BASE}/status", timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None


def wait_for_status(timeout_sec: int = 30) -> dict | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        status = fetch_status()
        if status:
            return status
        time.sleep(2)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Start AIS IDS with Docker Compose")
    parser.add_argument("--timeout", type=int, default=120, help="seconds to wait for Docker engine")
    parser.add_argument("--skip-compose", action="store_true", help="only verify Docker availability")
    args = parser.parse_args()

    ensure_docker_cli()

    if not docker_ready():
        print("Docker engine is not reachable. Trying to start Docker Desktop...")
        launched = start_docker_desktop()
        if not launched:
            print(f"Docker Desktop not found at: {DOCKER_DESKTOP}")

        if not wait_for_docker(args.timeout):
            print(f"Docker engine did not become ready within {args.timeout} seconds.")
            print("Check Docker Desktop, WSL2/Hyper-V backend, and admin permissions.")
            return 1

    try_switch_context()
    print_model_summary()

    print("Docker engine is ready.")
    run(["docker", "ps"], check=True)

    if args.skip_compose:
        return 0

    run(["docker", "compose", "up", "--build", "-d"], check=True)
    run(["docker", "compose", "ps"], check=True)
    status = wait_for_status()
    if status:
        stats = status.get("stats", {})
        print("API status: online")
        print(f"ML loaded: {status.get('ml_loaded')} ({status.get('ml_status')})")
        print(
            "Traffic: "
            f"sentences={stats.get('rx_sentences', 0)}, "
            f"targets={stats.get('rx_targets', 0)}, "
            f"alerts={status.get('alert_count', 0)}"
        )
    else:
        print("API status: not ready yet")
    print(f"Status endpoint: {API_BASE}/status")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"command failed: {' '.join(exc.cmd)}")
        return_code = exc.returncode if isinstance(exc.returncode, int) else 1
        raise SystemExit(return_code)
