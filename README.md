# KOL 스쿼드 배틀 트래커

텔레그램·X에서 KOL이 올린 키워드 게시물의 조회수를 30분마다 자동으로 모으고, 스쿼드 순위 대시보드로 보여줍니다.

```
GitHub Actions (30분마다)
  └─ collector/collect.py ── 텔레그램(Telethon) + X API v2
        └─ docs/data.json 갱신 후 커밋
              └─ GitHub Pages: docs/index.html 대시보드가 5분마다 다시 읽음
```

## 1. 저장소 만들기

이 폴더 전체를 새 GitHub 저장소에 올립니다. 무료 플랜에서 GitHub Pages를 쓰려면 **공개(Public) 저장소**여야 합니다. 그러면 config.json(핸들, 키워드)과 data.json도 공개되니, 공개돼도 괜찮은 정보만 넣으세요. 비밀번호·토큰은 절대 파일에 넣지 말고 3번의 Secrets에만 넣습니다.

## 2. config.json 수정

| 항목 | 설명 |
|---|---|
| `start` / `end` | 이 기간에 올린 게시물만 집계 (`+09:00`은 한국 시간) |
| `lock` | (선택) 게시 마감 후 조회수만 더 쌓을 유예 기간이 필요할 때 넣는 확정 시각. 없으면 `end`에 게시와 조회수 집계가 함께 마감 |
| `keywords` | 하나라도 들어 있으면 집계. 대소문자 무시. 영문은 단어 앞부분 기준이라 `Ondo`가 London·condo에는 안 걸리고 $ONDO·#OndoPerps·OndoFinance에는 걸림. 공백 유무 무관(Ondo Perps = OndoPerps) |
| `exclude_phrases` | 이 표현은 지우고 키워드를 찾음. "온도"가 기온 뜻으로 쓰인 글을 거르기 위한 목록(체감온도, 온도차 등). 필요하면 추가 |
| `excluded_posts` | 캠페인과 무관한데 잡힌 게시물의 URL을 넣으면 다음 수집부터 집계와 대시보드에서 빠짐 |
| `x_refresh_minutes` | X API 호출 간격(분). 늘리면 API 비용이 줄어듦 |
| `x_include_replies` | X 답글도 집계할지 |
| `spike_views_per_hour` | 시간당 이 이상 조회수가 늘면 대시보드에 "급증 감지" 표시 (0이면 끔). 집계에서 빼지는 않음 |
| `min_posts` / `min_views` / `min_volume` | 스쿼드 최소 조건(콘텐츠 50건, 조회수 30만, 거래량 $10M). 대시보드에 진행도로 표시 |
| `squads` | 스쿼드 id, 이름, 색 |
| `kols` | 이름, 소속 스쿼드 id, 텔레그램 채널 username, X 핸들 (@ 없이), `invite_code`(Ondo Perps 초대코드). 초대코드가 들어간 글은 키워드가 없어도 그 KOL의 콘텐츠로 집계 |

## 3. API 키 발급 후 Secrets 등록

저장소 Settings → Secrets and variables → Actions → New repository secret

**텔레그램 (무료)**
1. 수집 전용 텔레그램 계정을 하나 준비합니다 (개인 계정 비추천).
2. https://my.telegram.org → API development tools에서 `api_id`, `api_hash` 발급
3. 내 컴퓨터에서 한 번 실행:
   ```
   pip install telethon
   python tools/make_tg_session.py
   ```
   전화번호·인증코드 입력 후 나오는 긴 문자열이 세션입니다. **계정 로그인 권한과 같으니 절대 공유·커밋 금지.**
4. Secrets: `TG_API_ID`, `TG_API_HASH`, `TG_SESSION`

**X (유료)**
1. https://developer.x.com 에서 앱을 만들고 Bearer Token 발급
2. Secrets: `X_BEARER_TOKEN`
3. 요금은 플랜·사용량에 따라 달라지니 개발자 포털에서 현재 요금을 꼭 확인하세요. 한 번 수집할 때 호출은 대략 (X 계정 수 + 키워드 게시물 100개당 1회)이고, 타임라인에서 읽는 새 게시물 수만큼 읽기량이 늘어납니다. 기본값은 60분마다 수집이며 `x_refresh_minutes`로 조절하세요.

## 4. 대시보드 켜기

Settings → Pages → Source: Deploy from a branch → `main` / `/docs` → Save
몇 분 뒤 `https://<계정>.github.io/<저장소>/` 에서 열립니다. 이 주소를 KOL·클라이언트에게 공유하면 됩니다.

## 5. 첫 실행

Actions 탭 → collect-views → Run workflow. 성공하면 docs/data.json이 생기고 이후 30분마다 자동 실행됩니다.
실패하거나 일부 채널을 못 읽으면 Actions 로그에 경고가 뜨고, 대시보드 하단에도 표시됩니다.

## 탭 내용 수정하기 (GitHub 웹에서 파일 열고 연필 아이콘으로 편집)

| 탭 | 파일 | 방법 |
|---|---|---|
| 조회수 순위 | docs/data.json | 자동 수집. 직접 고치지 마세요 |
| 거래량·레퍼럴 | docs/trading.csv | 아래 형식으로 수치 입력 후 저장 |
| 조회수(유튜브·인스타 등, 선택) | docs/manual_posts.csv | 대시보드 합산을 원할 때만 한 줄씩 입력. 비워두면 텔레그램·X만 집계 |
| 캠페인 룰 | docs/rules.md | 일반 글로 작성. `#`는 제목, `-`는 목록(앞에 공백 2칸이면 하위 목록), 빈 줄로 문단 구분 |
| 콘텐츠 가이드 | docs/content-guide.md | 위와 같음 |
| 이용약관 | docs/terms.md | 위와 같음 |

trading.csv 형식:

```
# 기준: 10월 6일 18:00 (KST)
kol,volume_usd,referral_volume_usd,referrals
KOL 01,"1,250,000",480000,34
KOL 02,830000,120000,12
```

- `volume_usd`는 KOL 본인 거래량, `referral_volume_usd`는 그 KOL 레퍼럴 유저들의 거래량입니다. 순위와 $10M 최소 조건은 둘을 더한 값으로 계산합니다.

- 첫 줄 `# 기준:` 뒤에 집계 기준 시각을 적으면 대시보드에 그대로 표시됩니다. 비워두면 파일을 마지막으로 수정한 시각이 표시돼요.
- `kol` 열은 config.json의 KOL 이름과 **글자 하나까지 똑같아야** 합니다. 다르면 대시보드에 경고가 뜹니다.
- 거래량은 달러 기준 숫자. 쉼표를 넣으려면 큰따옴표로 감싸세요. 스프레드시트에서 CSV로 내보내 붙여넣어도 됩니다.
- 저장하면 1~2분 뒤(Pages 반영 시간) 대시보드에 반영됩니다.

manual_posts.csv 형식 (유튜브, 인스타그램 등):

```
kol,platform,url,views,date,note
KOL 01,유튜브,https://youtube.com/watch?v=xxxx,"12,300",2026-10-03,Ondo Perps 사용 후기 영상
```

- 조회수는 확인할 때마다 숫자를 고쳐 주세요. 다음 자동 수집(최대 30분) 때 순위에 반영됩니다.
- 줄을 지우면 집계에서도 빠집니다.

## 집계 규칙과 한계

- 점수 = 키워드 게시물 조회수 합계. 텔레그램은 **채널 게시물 조회수**, X는 **노출수(impressions)**. X 앱에 보이는 조회수와 같은 값입니다.
- 텔레그램 **그룹 채팅 메시지에는 조회수가 없어** 집계되지 않습니다. KOL에게 채널에 올리도록 안내하세요.
- 삭제됐거나 수정해서 키워드가 빠진 게시물은 다음 수집 때 집계에서 빠집니다.
- 같은 콘텐츠를 X와 텔레그램에 각각 올리면 2건으로 집계합니다.
- KOL 프로필 사진은 텔레그램 채널 사진을 자동으로 가져와 docs/avatars/에 저장합니다. 채널 사진이 바뀌면 다음 수집 때 갱신됩니다.
- X 리트윗은 제외됩니다. 인용 트윗은 본인 글로 집계됩니다.
- GitHub 예약 실행은 붐빌 때 수십 분씩 늦어질 수 있습니다. 실시간에 가깝게 필요하면 자체 서버에서 같은 스크립트를 cron으로 돌리면 됩니다.
- 조회수가 봇으로 부풀려졌는지는 자동 판별이 어렵습니다. "급증 감지"가 뜬 게시물은 직접 확인하세요.

## 로컬에서 테스트

```
pip install -r collector/requirements.txt
export TG_API_ID=... TG_API_HASH=... TG_SESSION=... X_BEARER_TOKEN=...
python collector/collect.py
cd docs && python -m http.server 8000   # http://localhost:8000
```
