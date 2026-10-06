"""
텔레그램 세션 문자열 발급 (내 컴퓨터에서 한 번만 실행)

  pip install telethon
  python tools/make_tg_session.py

출력된 긴 문자열을 GitHub 저장소 Secrets의 TG_SESSION에 넣으세요.
이 문자열은 계정 로그인 권한과 같습니다. 절대 공유하거나 커밋하지 마세요.
수집 전용 텔레그램 계정을 따로 쓰는 것을 권장합니다.
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API ID (my.telegram.org): ").strip())
api_hash = input("API HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\n아래 문자열을 TG_SESSION 시크릿에 저장하세요:\n")
    print(client.session.save())
