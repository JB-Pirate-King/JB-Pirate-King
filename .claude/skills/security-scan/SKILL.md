---
name: security-scan
description: Scan code for security vulnerabilities with Semgrep SAST. Use when the user asks to security-check, find vulnerabilities, run a security scan, audit code for injection / unsafe eval / hardcoded secrets / path traversal, or review a diff for security issues before committing or pushing. Covers Python and C++ in this repo. Complements pyright (types only) and GitHub code review.
---

# Security scan (Semgrep)

Semgrep으로 코드의 보안 취약점을 정적 분석한다. pyright는 타입만 보므로(취약점 미탐지)
이 스킬이 SAST 갭을 메운다. semgrep MCP(`.mcp.json` 의 `semgrep`)가 떠 있으면 그 도구를
직접 호출해도 되고, 아래 CLI를 써도 된다.

## 1. 변경분만 스캔 (커밋/푸시 전 권장)

```powershell
# 변경된 파일 목록 → semgrep 스캔
$files = git diff --name-only HEAD
uvx --from semgrep semgrep scan --config p/security-audit --config p/secrets $files
```

## 2. 특정 경로 / 전체 스캔

```powershell
# 파이썬 파이프라인만
uvx --from semgrep semgrep scan --config p/python --config p/security-audit ml/

# C++ 플러그인
uvx --from semgrep semgrep scan --config p/c ais_ids_pi/src/

# 비밀키/토큰 유출 점검 (공개 레포라 중요)
uvx --from semgrep semgrep scan --config p/secrets .
```

## 3. 룰셋 가이드

| 룰셋 | 용도 |
|---|---|
| `p/security-audit` | 범용 보안 취약점(injection, SSRF, 안전하지 않은 역직렬화 등) |
| `p/secrets` | 하드코딩된 토큰/키/자격증명 |
| `p/python` `p/c` | 언어별 베스트프랙티스 |
| `--config auto` | 언어 자동 감지(레지스트리 룰, 로그인 시 Pro 룰 추가) |

## 4. 결과 해석

- `Findings: N` 줄에서 탐지 건수 확인. 각 finding은 파일:라인 + 룰ID + 설명.
- 오탐이면 해당 라인 위에 `# nosemgrep: <rule-id>` 주석으로 무시.
- JSON 출력: `--json --output result.json` (n8n/스크립트 연동용).

## 참고

- Windows 네이티브에서 동작 확인됨(WSL 불필요, semgrep 1.166+).
- 주간 자동 스캔은 n8n 커맨드센터 워크플로 `JB - Semgrep Weekly` 가 담당
  (결과를 Discord로 요약 전송). 상세: `dev-tooling/README.md`.
- 첫 실행 시 uvx가 semgrep(약 50MB)을 받으므로 수십 초 지연될 수 있다.
