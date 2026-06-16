# n8n 워크플로 (참조 사본)

이 디렉토리는 n8n 커맨드센터 워크플로 정의의 **버전관리용 참조 사본**이다.
실제로 동작하는 워크플로는 운영 머신의 `C:\scripts\n8n_workflows\` 에서 n8n(`C:\Users\pc\.n8n`)으로
임포트되어 돈다. 운영 상세 문서: 운영 머신의 `C:\scripts\README-commandcenter.md`.

머신 고유 경로(`C:\scripts\*.bat`)를 포함하므로 다른 머신에서 그대로 쓰진 못하고, 구조 참고용이다.

## 워크플로

| 파일 | 스케줄(KST) | Execute Command | 동작 |
|---|---|---|---|
| `jb_notion_vault_sync.json` | 매일 09:00, 18:00 | `notion_team_vault_sync.bat` | Notion 자료 → git team-vault 미러 |
| `jb_daily_pm_snapshot.json` | 매일 18:00 | `daily_pm_snapshot.bat` | Sheets → Obsidian Daily Summary + Discord |
| `jb_ops_nightly.json` | 매일 21:30 | `ops_nightly.bat` (ops_graph.py) | 야간 운영 점검 → Discord |
| `jb_semgrep_weekly.json` | 매주 월 09:00 | `semgrep_scan.bat` | 레포 보안 스캔(Semgrep) → Discord 요약 |

각 워크플로 = Schedule Trigger(Asia/Seoul) → Execute Command(기존 .bat 호출).

## 핵심 운영 메모

- n8n은 pm2가 데몬으로 상시 구동(`pm2 status` / `pm2 restart n8n`). 콘솔 비의존.
- 부팅/로그온 복구: Task Scheduler `JB-n8n` → `pm2 resurrect`.
- 환경변수: `NODES_EXCLUDE=[]`(Execute Command 노드 허용), `GENERIC_TIMEZONE=Asia/Seoul`,
  `N8N_USER_FOLDER=C:/Users/pc`.

## 재임포트

```
set PATH=C:\Program Files\nodejs;C:\Users\pc\AppData\Roaming\npm;%PATH%
set NODES_EXCLUDE=[]
n8n import:workflow --separate --input="C:\scripts\n8n_workflows"
n8n publish:workflow --id=<id>   (list:workflow 로 확인)
```
