---
name: repomix-pack
description: Produce a compact, signature-only map of a code subtree without reading every file. Use when you need a quick architectural overview of a directory (for example ml/core or ais_ids_pi/src) before editing, to save context tokens instead of opening many files one by one. Runs the repomix CLI via npx (Node).
---

# repomix-pack

큰 디렉토리를 일일이 열지 않고, 압축된 코드 지도(시그니처 위주)를 1파일로 만들어 컨텍스트 토큰을 아낀다.

## 사용

```powershell
# 서브트리를 압축(함수 본문 제거, 시그니처/구조만 — 약 70% 토큰 절감)해서 1파일로
npx -y repomix --compress --style markdown --output repomix-out.md ml/core

# 저장소 전체 구조 빠르게 훑기 (압축)
npx -y repomix --compress --style markdown --output repomix-out.md .
```

- `.gitignore` + 루트 `.repomixignore`(onnxruntime/·opencpn-libs/·data/)를 자동 존중한다.
- 출력 끝에 파일별/전체 토큰 추정치가 표시된다. 다 읽은 뒤 `repomix-out.md`는 삭제(임시 산출물, gitignore됨).
- **개요 파악용**이지 정확한 라인 편집용이 아니다(압축은 함수 본문을 버린다). 특정 파일을 고칠 때는 그 파일을 직접 Read.
- Node 필요(이 머신에 node v22 설치됨). 최초 실행 시 npx가 repomix를 받아온다.
- 공개 GitHub 레포를 클론 없이 패킹: `npx -y repomix --remote owner/repo --compress`.
