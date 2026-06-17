"""파이프라인 구조도 렌더 — 노드 성격별 색상.

LangGraph 가 뽑은 mermaid 소스에 classDef 를 주입해 PNG 로 렌더한다.
NodeStyles(first/last/default 3종)로는 노드별 색이 불가능해 소스 후처리 방식 사용.

실행 (repo 루트에서):
    python -m ml.scripts.render_graph        # → ml/pipeline_langgraph.png 갱신

색/테두리 분류 (테두리가 claude 사용 여부를 표시):
    굵은 보라 테두리 = claude 호출 노드  →  prime(지식주입)·recommend(피처발명)·j_base/j_reco/j_fe(판정)
    얇은 회색 테두리 = claude 미사용     →  나머지 전부(판정 패스스루·결정적 체크 포함)
  채움색은 역할:
    초록 compute · 분홍 recommend · 보라 judge(claude) · 회색 judge_pass/check(비claude)
    노랑 gate · 파랑 log · 빨강 user_stop
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ml.orchestrator import build_graph                     # noqa: E402
from langchain_core.runnables.graph_mermaid import draw_mermaid_png  # noqa: E402

# 노드 성격 분류 (build_graph 와 동기화).
# claude 호출 노드(prime·reco·judge_llm)는 STYLES 에서 굵은 보라 테두리로 표시 — 비claude 와 구분.
GROUPS = {
    # claude 미사용 compute
    "compute":   ["new_branch", "preprocess", "fe_baseline", "reco_again",
                  "fe_train", "build", "release", "chain", "converge"],
    "prime":     ["prime"],                          # claude (도메인 지식 주입)
    "reco":      ["recommend"],                      # claude (피처 발명, opus)
    "judge_llm": ["j_base", "j_reco", "j_fe"],       # claude (실제 판정, sonnet) — 유일한 판정 노드
    "gate":      ["gate_deploy", "gate_release", "gate_converge"],
    "log":       ["log_run_start", "log_fe", "log_run_done", "log_converge"],
    "readme":    ["readme"],                          # claude (README note 한 줄 작성)
    "stop":      ["user_stop"],
}
# claude 호출 = 굵은 보라 테두리(stroke:#7c3aed,stroke-width:4px) / 비claude = 얇은 테두리.
# 무조건 continue 였던 패스스루 판정(j_branch/j_release/j_chain)은 그래프에서 제거됨.
_CLAUDE = "stroke:#7c3aed,stroke-width:4px"
STYLES = {
    "compute":   "fill:#dcfce7,stroke:#16a34a,stroke-width:1px,color:#000000,font-weight:bold",
    "prime":     f"fill:#dcfce7,{_CLAUDE},color:#000000,font-weight:bold",   # compute지만 claude
    "reco":      f"fill:#fce7f3,{_CLAUDE},color:#000000,font-weight:bold",
    "judge_llm": f"fill:#ede9fe,{_CLAUDE},color:#000000,font-weight:bold",
    "gate":      "fill:#fef3c7,stroke:#d97706,stroke-width:1px,color:#000000,font-weight:bold",
    "log":       "fill:#dbeafe,stroke:#2563eb,stroke-width:1px,color:#000000,font-weight:bold",
    "readme":    f"fill:#dbeafe,{_CLAUDE},color:#000000,font-weight:bold",    # log지만 claude
    "stop":      "fill:#fee2e2,stroke:#dc2626,stroke-width:1px,color:#000000,font-weight:bold",
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
