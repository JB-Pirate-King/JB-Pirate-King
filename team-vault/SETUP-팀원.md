# 팀원 1회 셋업 가이드 (코드 repo 한 폴더 = 옵시디언 볼트)

이미 JB-Pirate-King repo를 clone 해서 쓰고 있다면 2번부터 하면 된다.

## 1. repo clone (이미 있으면 생략)

```bash
git clone https://github.com/JB-Pirate-King/JB-Pirate-King.git
cd JB-Pirate-King
git checkout develop
```

## 2. Obsidian에서 team-vault 폴더 열기

Obsidian → Open folder as vault → `JB-Pirate-King/team-vault` 선택.
코드 전체가 아니라 `team-vault` 하위 폴더만 볼트로 연다. 코드 파일은 보이지 않는다.

## 3. 최신 자료 받기 = git pull

`develop`에서 `git pull` 하면 코드와 함께 `team-vault/` 자료도 최신화된다.

### (선택) 자동 pull — obsidian-git

매번 손으로 pull 하기 싫으면:

1. Settings → Community plugins → "Git" (Vinzent03/obsidian-git) 설치 → Enable
2. 설정:
   - Pull updates on startup: 켬
   - Auto pull interval (minutes): `10`
   - Auto commit-and-sync interval: `0` (끔) — 켜 두면 작업 중인 코드가 자동 커밋될 수 있다
   - Commit author / Disable push: push는 본인이 코드 작업할 때만. 자료 폴더는 건드리지 않는다

## 주의

- `team-vault/` 안 파일을 직접 수정하지 않는다. 발행 PC가 매 동기화 때 덮어쓴다 (충돌 위험).
- 개인 메모는 별도 볼트에 둔다.
- 충돌이 나면 `git checkout -- team-vault/` 로 미러 상태를 복구한다.

---

## (참고) 동기화 구조

Notion `홈 > 자료`/`CCIT` → 발행 PC의 `C:\scripts\notion_team_vault_sync.py` (Task Scheduler 09:00/18:00) → `develop`의 `team-vault/`로 push.
노션이 원본, git은 전달 경로, 팀원 옵시디언은 수신 측이다. 팀원은 pull만 한다.
