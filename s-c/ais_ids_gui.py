from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText


def _fmt_ts(ts_str: str) -> str:
    """'2026-05-07T12:03:34.155322+00:00' → '05/07 12:03:34'"""
    try:
        return ts_str[:19].replace("T", " ")[5:].replace("-", "/", 1)
    except Exception:
        return ts_str


def _fmt_score(score) -> str:
    try:
        return f"{float(score):.6g}"
    except Exception:
        return str(score)


ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"
LOGS_DIR = ROOT / "logs"
DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
API_BASE = "http://localhost:5000"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_cmd(args: list[str], timeout: int | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return proc.returncode, proc.stdout.strip()
    except FileNotFoundError:
        return 127, f"command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"command timed out: {' '.join(args)}"


def docker_ready() -> bool:
    code, _ = run_cmd(["docker", "ps", "--format", "{{.ID}}"], timeout=10)
    return code == 0


def fetch_json(path: str, timeout: int = 3) -> tuple[dict | list | None, str | None]:
    try:
        with urllib.request.urlopen(f"{API_BASE}{path}", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return None, str(exc)


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))


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
            mode = f"ensemble ({len(model_names)} models)"
            if not model_names:
                missing.append("models[] in ensemble_config.json")
            if len(scaler_names) != len(model_names):
                missing.append("scalers[] count must match models[]")
            return {"mode": mode, "ready": not missing, "missing": missing, "notes": notes}
        except Exception as exc:
            return {"mode": "ensemble", "ready": False, "missing": [f"invalid ensemble_config.json: {exc}"], "notes": []}

    required = ["model.onnx", "scaler.json", "threshold.txt"]
    missing = [name for name in required if not (MODELS_DIR / name).exists()]
    return {"mode": "single", "ready": not missing, "missing": missing, "notes": []}


def container_state() -> str:
    code, out = run_cmd(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            "ais_ids_server",
        ],
        timeout=10,
    )
    if code != 0:
        return "not found"
    status, _, health = out.partition("|")
    return f"{status} ({health})" if health and health != "none" else status


def try_switch_context() -> str:
    code, out = run_cmd(["docker", "context", "ls", "--format", "{{.Name}}"], timeout=10)
    if code != 0 or "desktop-linux" not in out.splitlines():
        return ""

    code, current = run_cmd(["docker", "context", "show"], timeout=10)
    if code == 0 and current.strip() == "desktop-linux":
        return ""

    code, switched = run_cmd(["docker", "context", "use", "desktop-linux"], timeout=10)
    if code != 0:
        return f"Could not switch docker context automatically.\n{switched}"
    return switched


def ensure_docker_engine(timeout_sec: int, log) -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker CLI is not installed or not on PATH.")

    if docker_ready():
        return

    log("Docker engine is not reachable. Trying to start Docker Desktop...")
    if DOCKER_DESKTOP.exists():
        subprocess.Popen([str(DOCKER_DESKTOP)])
    else:
        log(f"Docker Desktop was not found at {DOCKER_DESKTOP}")

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if docker_ready():
            return
        time.sleep(3)

    raise RuntimeError(
        "Docker engine did not become ready. Open Docker Desktop, wait for Engine running, "
        "or run this GUI as Administrator if your Windows policy requires it."
    )


class AisIdsGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AIS IDS Control Center")
        self.geometry("1120x760")
        self.minsize(980, 640)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.refreshing = False
        self.status_vars: dict[str, tk.StringVar] = {}

        self._build_style()
        self._build_ui()
        self.after(100, self._process_events)
        self.after(250, self.refresh_all)
        self.after(5000, self._auto_refresh)

    def _build_style(self) -> None:
        self.configure(bg="#f3f5f7")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5f7")
        style.configure("Panel.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("TLabel", background="#f3f5f7", font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background="#ffffff", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 18), background="#f3f5f7")
        style.configure("Muted.TLabel", foreground="#637083", background="#f3f5f7")
        style.configure("PanelTitle.TLabel", background="#ffffff", font=("Segoe UI Semibold", 11))
        style.configure("TButton", font=("Segoe UI", 10), padding=(10, 6))
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root)
        header.pack(fill=tk.X)

        title_box = ttk.Frame(header)
        title_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(title_box, text="AIS IDS Control Center", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            title_box,
            text="Local desktop controller for Docker, model files, API status, AIS traffic, alerts, and logs.",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))

        self.badge = tk.Label(
            header,
            text="Checking...",
            font=("Segoe UI Semibold", 11),
            padx=16,
            pady=8,
            bg="#dbeafe",
            fg="#1e3a8a",
        )
        self.badge.pack(side=tk.RIGHT)

        controls = ttk.Frame(root)
        controls.pack(fill=tk.X, pady=(14, 12))
        ttk.Button(controls, text="Start / Build", command=self.start_service).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="Restart", command=self.restart_service).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="Stop", command=self.stop_service).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="Refresh", command=self.refresh_all).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="Models Folder", command=lambda: open_folder(MODELS_DIR)).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(controls, text="Logs Folder", command=lambda: open_folder(LOGS_DIR)).pack(side=tk.RIGHT)

        summary = ttk.Frame(root)
        summary.pack(fill=tk.X)
        for key in ["Docker", "Container", "API", "ML", "AIS Sentences", "AIS Targets", "Alerts", "Vessels"]:
            self._add_status_card(summary, key)

        body = ttk.Frame(root)
        body.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = ttk.Frame(body, width=330)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        right.pack_propagate(False)

        self.tabs = ttk.Notebook(left)
        self.tabs.pack(fill=tk.BOTH, expand=True)
        self._build_alerts_tab()
        self._build_vessels_tab()
        self._build_logs_tab()

        self._build_model_panel(right)
        self._build_action_log(right)

    def _add_status_card(self, parent: ttk.Frame, key: str) -> None:
        card = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        ttk.Label(card, text=key, style="PanelTitle.TLabel").pack(anchor=tk.W)
        value = tk.StringVar(value="-")
        self.status_vars[key] = value
        ttk.Label(card, textvariable=value, style="Panel.TLabel", wraplength=140).pack(anchor=tk.W, pady=(6, 0))

    def _build_alerts_tab(self) -> None:
        frame = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(frame, text="Alerts")
        cols = ("timestamp", "mmsi", "score", "description")
        self.alert_tree = ttk.Treeview(frame, columns=cols, show="headings")
        for col, width, anchor, stretch in [
            ("timestamp",   148, tk.W, False),
            ("mmsi",        110, tk.E, False),
            ("score",        88, tk.E, False),
            ("description",  10, tk.W, True),
        ]:
            self.alert_tree.heading(col, text=col)
            self.alert_tree.column(col, width=width, anchor=anchor, stretch=stretch)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.alert_tree.yview)
        self.alert_tree.configure(yscrollcommand=sb.set)
        self.alert_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_vessels_tab(self) -> None:
        frame = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(frame, text="Vessels")
        cols = ("mmsi", "lat", "lon", "sog", "cog", "nav_status", "history_len")
        self.vessel_tree = ttk.Treeview(frame, columns=cols, show="headings")
        for col, width, anchor in [
            ("mmsi",        110, tk.E),
            ("lat",         100, tk.E),
            ("lon",         100, tk.E),
            ("sog",          72, tk.E),
            ("cog",          72, tk.E),
            ("nav_status",   88, tk.E),
            ("history_len",  88, tk.E),
        ]:
            self.vessel_tree.heading(col, text=col)
            self.vessel_tree.column(col, width=width, anchor=anchor, stretch=False)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.vessel_tree.yview)
        self.vessel_tree.configure(yscrollcommand=sb.set)
        self.vessel_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_logs_tab(self) -> None:
        frame = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(frame, text="Logs")
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(toolbar, text="Compose Logs", command=self.refresh_logs).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="Alert File", command=self.refresh_alert_file).pack(side=tk.LEFT)
        self.log_text = ScrolledText(frame, wrap=tk.WORD, height=18, font=("Consolas", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_model_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        panel.pack(fill=tk.X)
        ttk.Label(panel, text="Model Files", style="PanelTitle.TLabel").pack(anchor=tk.W)
        self.model_text = tk.Text(panel, height=8, wrap=tk.WORD, font=("Consolas", 9), relief=tk.FLAT)
        self.model_text.pack(fill=tk.X, pady=(8, 0))
        self.model_text.configure(state=tk.DISABLED)

    def _build_action_log(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        panel.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        ttk.Label(panel, text="Action Log", style="PanelTitle.TLabel").pack(anchor=tk.W)
        self.action_log = ScrolledText(panel, wrap=tk.WORD, height=12, font=("Consolas", 9))
        self.action_log.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

    def _run_background(self, name: str, func) -> None:
        if self.busy:
            messagebox.showinfo("AIS IDS", "Another action is already running.")
            return
        self.busy = True
        self._set_badge("Working...", "busy")
        self._append_action(f"{name} started")

        def worker() -> None:
            try:
                func()
                self.events.put(("log", f"{name} finished"))
            except Exception as exc:
                self.events.put(("error", f"{name} failed: {exc}"))
            finally:
                self.events.put(("busy", False))
                self.events.put(("refresh", None))

        threading.Thread(target=worker, daemon=True).start()

    def start_service(self) -> None:
        def task() -> None:
            ensure_docker_engine(120, lambda msg: self.events.put(("log", msg)))
            context_msg = try_switch_context()
            if context_msg:
                self.events.put(("log", context_msg))
            code, out = run_cmd(["docker", "compose", "up", "--build", "-d"], timeout=300)
            self.events.put(("log", out or "docker compose up finished"))
            if code != 0:
                raise RuntimeError("docker compose up failed")

        self._run_background("Start / Build", task)

    def stop_service(self) -> None:
        def task() -> None:
            code, out = run_cmd(["docker", "compose", "down"], timeout=120)
            self.events.put(("log", out or "docker compose down finished"))
            if code != 0:
                raise RuntimeError("docker compose down failed")

        self._run_background("Stop", task)

    def restart_service(self) -> None:
        def task() -> None:
            code, out = run_cmd(["docker", "compose", "restart", "ais-ids-server"], timeout=120)
            self.events.put(("log", out or "docker compose restart finished"))
            if code != 0:
                raise RuntimeError("docker compose restart failed")

        self._run_background("Restart", task)

    def refresh_all(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True

        def worker() -> None:
            state = self._collect_state()
            self.events.put(("state", state))
            self.events.put(("refreshing", False))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_logs(self) -> None:
        def task() -> None:
            code, out = run_cmd(["docker", "compose", "logs", "--tail", "200", "ais-ids-server"], timeout=30)
            if code != 0:
                raise RuntimeError(out)
            self.events.put(("main_log", out))

        self._run_background("Refresh compose logs", task)

    def refresh_alert_file(self) -> None:
        path = LOGS_DIR / "ais_ids_alerts.log"
        if not path.exists():
            self._set_main_log("No alert log file yet.")
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        self._set_main_log("\n".join(lines))

    def _collect_state(self) -> dict:
        docker_ok = docker_ready()
        status, status_error = fetch_json("/status")
        alerts, _ = fetch_json("/alerts?n=100")
        vessels, _ = fetch_json("/vessels")
        model = inspect_models()

        return {
            "docker": docker_ok,
            "container": container_state() if docker_ok else "docker unavailable",
            "status": status if isinstance(status, dict) else None,
            "status_error": status_error,
            "alerts": alerts if isinstance(alerts, list) else [],
            "vessels": vessels if isinstance(vessels, dict) else {},
            "model": model,
        }

    def _apply_state(self, state: dict) -> None:
        status = state["status"] or {}
        stats = status.get("stats", {})
        model = state["model"]

        self.status_vars["Docker"].set("ready" if state["docker"] else "not ready")
        self.status_vars["Container"].set(state["container"])
        self.status_vars["API"].set("online" if state["status"] else "offline")
        self.status_vars["ML"].set("loaded" if status.get("ml_loaded") else "disabled")
        self.status_vars["AIS Sentences"].set(str(stats.get("rx_sentences", 0)))
        self.status_vars["AIS Targets"].set(str(stats.get("rx_targets", 0)))
        self.status_vars["Alerts"].set(str(status.get("alert_count", 0)))
        self.status_vars["Vessels"].set(str(status.get("tracked_vessels", 0)))

        if state["status"]:
            if status.get("ml_loaded"):
                self._set_badge("Running", "ok")
            else:
                self._set_badge("Running without ML", "warn")
        elif state["docker"]:
            self._set_badge("Docker ready", "busy")
        else:
            self._set_badge("Docker offline", "bad")

        self._set_model_text(model, status.get("ml_status", state.get("status_error", "")))
        self._set_alerts(state["alerts"])
        self._set_vessels(state["vessels"])

    def _set_model_text(self, model: dict, server_msg: str) -> None:
        lines = [
            f"Mode: {model['mode']}",
            f"Local files: {'ready' if model['ready'] else 'missing files'}",
        ]
        if model["missing"]:
            lines.append("")
            lines.append("Missing:")
            lines.extend(f"- {item}" for item in model["missing"])
        if model.get("notes"):
            lines.append("")
            lines.append("Notes:")
            lines.extend(f"- {item}" for item in model["notes"])
        if server_msg:
            lines.append("")
            lines.append(f"Server: {server_msg}")

        self.model_text.configure(state=tk.NORMAL)
        self.model_text.delete("1.0", tk.END)
        self.model_text.insert(tk.END, "\n".join(lines))
        self.model_text.configure(state=tk.DISABLED)

    def _set_alerts(self, alerts: list) -> None:
        self.alert_tree.delete(*self.alert_tree.get_children())
        for item in alerts:
            self.alert_tree.insert(
                "",
                tk.END,
                values=(
                    _fmt_ts(item.get("timestamp", "")),
                    item.get("mmsi", ""),
                    _fmt_score(item.get("score", "")),
                    item.get("description", ""),
                ),
            )

    def _set_vessels(self, vessels: dict) -> None:
        self.vessel_tree.delete(*self.vessel_tree.get_children())
        for _, item in sorted(vessels.items()):
            self.vessel_tree.insert(
                "",
                tk.END,
                values=(
                    item.get("mmsi", ""),
                    item.get("lat", ""),
                    item.get("lon", ""),
                    item.get("sog", ""),
                    item.get("cog", ""),
                    item.get("nav_status", ""),
                    item.get("history_len", ""),
                ),
            )

    def _set_main_log(self, text: str) -> None:
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, text)

    def _append_action(self, text: str) -> None:
        self.action_log.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self.action_log.see(tk.END)

    def _set_badge(self, text: str, kind: str) -> None:
        colors = {
            "ok": ("#dcfce7", "#166534"),
            "warn": ("#fef3c7", "#92400e"),
            "bad": ("#fee2e2", "#991b1b"),
            "busy": ("#dbeafe", "#1e3a8a"),
        }
        bg, fg = colors.get(kind, colors["busy"])
        self.badge.configure(text=text, bg=bg, fg=fg)

    def _process_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._append_action(str(payload))
            elif kind == "error":
                self._append_action(str(payload))
                self._set_badge("Action failed", "bad")
            elif kind == "busy":
                self.busy = bool(payload)
            elif kind == "refreshing":
                self.refreshing = bool(payload)
            elif kind == "refresh":
                self.refresh_all()
            elif kind == "state":
                self._apply_state(payload)
            elif kind == "main_log":
                self._set_main_log(str(payload))

        self.after(100, self._process_events)

    def _auto_refresh(self) -> None:
        self.refresh_all()
        self.after(5000, self._auto_refresh)


def main() -> None:
    app = AisIdsGui()
    app.mainloop()


if __name__ == "__main__":
    main()
