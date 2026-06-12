"""파이프라인 구조도 렌더 — 노드 성격별 색상.

LangGraph 가 뽑은 mermaid 소스에 classDef 를 주입해 PNG 로 렌더한다.
NodeStyles(first/last/default 3종)로는 노드별 색이 불가능해 소스 후처리 방식 사용.

실행 (repo 루트에서):
    python -m ml.scripts.render_graph        # → ml/pipeline_langgraph.png 갱신

색 분류:
    초록  compute (실행 노드)        분홍  recommend (claude 피처 발명)
    보라  judge (claude 판정)        노랑  gate (승인)
    파랑  log (Sheets/README 기록)   빨강  user_stop (중단)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ml.orchestrator import build_graph                     # noqa: E402
from langchain_core.runnables.graph_mermaid import draw_mermaid_png  # noqa: E402

# 노드 성격 분류 (build_graph 와 동기화)
GROUPS = {
    "compute": ["new_branch", "preprocess", "fe_baseline", "reco_again",
                "fe_train", "build", "release", "chain", "converge"],
    "reco":    ["recommend"],
    "judge":   ["j_branch", "j_base", "j_reco", "j_fe", "j_build", "j_release", "j_chain"],
    "gate":    ["gate_deploy", "gate_release", "gate_converge"],
    "log":     ["log_run_start", "log_fe", "log_run_done", "log_converge", "readme"],
    "stop":    ["user_stop"],
}
STYLES = {
    "compute": "fill:#dcfce7,stroke:#16a34a,color:#000000,font-weight:bold",
    "reco":    "fill:#fce7f3,stroke:#db2777,color:#000000,font-weight:bold",
    "judge":   "fill:#ede9fe,stroke:#7c3aed,color:#000000,font-weight:bold",
    "gate":    "fill:#fef3c7,stroke:#d97706,color:#000000,font-weight:bold",
    "log":     "fill:#dbeafe,stroke:#2563eb,color:#000000,font-weight:bold",
    "stop":    "fill:#fee2e2,stroke:#dc2626,color:#000000,font-weight:bold",
}


def colored_mermaid() -> str:
    g = build_graph().get_graph()
    mer = g.draw_mermaid()
    # 그래프 노드가 빠짐없이 분류됐는지 검증 (새 노드 추가 시 여기서 잡힘)
    known = {n for ns in GROUPS.values() for n in ns} | {"__start__", "__end__"}
    missing = [n for n in g.nodes if n not in known]
    if missing:
        print(f"[경고] 미분류 노드 (기본색): {missing}")
    lines = [f"\tclassDef {grp} {style}" for grp, style in STYLES.items()]
    lines += [f"\tclass {','.join(ns)} {grp}" for grp, ns in GROUPS.items()]
    return mer + "\n" + "\n".join(lines) + "\n"


def main(out: str = "ml/pipeline_langgraph.png"):
    mer = colored_mermaid()
    png = draw_mermaid_png(mermaid_syntax=mer)
    Path(out).write_bytes(png)
    print(f"렌더 완료: {out} ({len(png):,} bytes)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ml/pipeline_langgraph.png")
