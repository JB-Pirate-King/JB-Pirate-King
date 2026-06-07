# team-vault — Notion 자료 미러 (Obsidian 볼트)

> ⚠️ **이 폴더는 자동 생성되는 읽기 전용 미러입니다.**
> 원본(SSOT)은 **Notion `홈 > 자료`** 입니다. 이 폴더 안 파일을 직접 고치지 마세요 — 다음 동기화 때 덮어써집니다.

## 동작
- 발행 PC가 매일 **09:00 / 18:00** Notion `자료` 트리를 Markdown 으로 변환해 이 폴더(`team-vault/`)에 commit → `develop` 에 push.
- PDF / DOCX / PPTX / XLSX 첨부는 자동으로 `.md` 변환되어 본문에 포함됨.
- 이미지는 각 폴더의 `_assets/` 에 저장.

## 폴더 구조
```
team-vault/
  README.md
  SETUP-팀원.md
  자료.md           ← '자료' 페이지 본문
  자료/             ← 하위 페이지 + 첨부 변환본
    OpenCPN plugin API/
    _assets/
```

## 팀원 사용법
코드 repo 를 평소처럼 `develop` 으로 clone/pull 하면 이 폴더도 같이 업데이트됩니다.
Obsidian 에서 **이 `team-vault/` 폴더를 볼트로 열기**만 하면 끝. → [SETUP-팀원.md](SETUP-팀원.md)
