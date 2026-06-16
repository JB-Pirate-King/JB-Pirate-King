# 협업 연동 (Slack / Discord) — 활성화 가이드

목표(로드맵 3번): 슬랙/디스코드 채팅에서 Claude를 팀원처럼 구동, 또는 Claude가 채팅을
읽고/쓰게 한다. 둘 다 무료·MIT이며, 본인/팀원의 토큰만 넣으면 활성화된다(설치는 npx라 사전설치 불필요).

두 가지 보완적 선택지가 있다. 용도가 다르니 필요에 맞춰 고른다.

## A. korotovsky/slack-mcp-server — Claude가 Slack을 읽고/쓰기

Claude Code가 Slack 채널을 읽고 메시지를 보내는 MCP 서버. 워크스페이스 관리자 승인 불필요
(브라우저 토큰 사용). 두 팀원이 각자 본인 토큰을 넣으면 된다.

### 토큰 추출(브라우저)
1. 브라우저로 Slack 워크스페이스(1hc0.slack.com) 로그인.
2. 개발자도구(F12) → Application/Storage:
   - Cookies 에서 `d` 쿠키 값 = `SLACK_MCP_XOXD_TOKEN` (xoxd-...)
   - Local Storage 또는 콘솔 `JSON.parse(localStorage.localConfig_v2).teams` 에서 `token`(xoxc-...) = `SLACK_MCP_XOXC_TOKEN`

### .mcp.json 항목(추가)
```json
"slack": {
  "command": "npx",
  "args": ["-y", "slack-mcp-server@latest", "--transport", "stdio"],
  "env": {
    "SLACK_MCP_XOXC_TOKEN": "xoxc-...",
    "SLACK_MCP_XOXD_TOKEN": "xoxd-..."
  }
}
```
주의: 브라우저 토큰은 로그아웃/비번변경/세션만료 시 무효 → 갱신 필요.

## B. OpenACP — 채팅에서 Claude Code를 "구동"

Slack/Discord/Telegram 스레드에서 Claude Code(및 28+ ACP 에이전트)를 돌리는 자체호스팅
브리지(MIT). "클라우드 릴레이 없음 — 내 머신, 내 키". 채팅이 곧 에이전트 제어판이 된다.

### 설치/실행
```powershell
npx -y @openacp/cli
# 최초 실행 시 대화형 설정: 플랫폼(Slack/Discord) 선택 + 봇 토큰 입력
```

### 필요한 것
- Discord: Discord Developer Portal에서 봇 생성 → 봇 토큰 + 채널에 초대. (스레드 세션·슬래시커맨드 Stable)
- Slack: Socket Mode 앱 토큰. (채널/스레드 세션 Stable)

상세 설정은 https://openacp.ai/ 참조. 봇 토큰만 발급하면 된다.

## 어느 걸 쓰나
- "Claude가 우리 Slack을 읽고 알림/요약을 올리게" → A(slack-mcp).
- "폰/채팅에서 Claude Code에게 작업을 시키고 결과를 받기" → B(OpenACP).
- 둘은 병행 가능(용도가 다름).

## 현재 상태
- 미설치(npx 실행형이라 설치 불필요). 토큰 미입력 → 비활성.
- 기존 Discord 웹훅(`C:\scripts\discord_webhook.txt`)은 알림 송신용으로 계속 동작 중.
- 메모리상 Slack 워크스페이스 채널 `C0B6RF2H79R` 존재.
