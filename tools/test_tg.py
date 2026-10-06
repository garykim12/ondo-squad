"""
텔레그램 수집 테스트 (내 컴퓨터에서 실행, GitHub 없이 결과만 화면에 출력)

  pip install telethon requests
  python tools/test_tg.py

처음 실행하면 전화번호와 인증코드를 물어봅니다 (수집 전용 계정 권장).
아무 파일도 고치지 않고, 채널별 결과만 보여줍니다.
끝나면 tools/test.session 파일은 지워도 됩니다 (계정 로그인 정보라 공유 금지).
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import collect as c  # noqa: E402

from telethon import TelegramClient  # noqa: E402


async def main():
    cfg = json.load(open(c.CONFIG_PATH, encoding="utf-8"))
    camp = cfg["campaign"]
    start, end = c.parse_dt(camp["start"]), c.parse_dt(camp["end"])
    api_id = int(input("API ID (my.telegram.org): ").strip())
    api_hash = input("API HASH: ").strip()

    squads = {s["id"]: {"name": s["name"], "posts": 0, "views": 0} for s in cfg["squads"]}
    async with TelegramClient(str(Path(__file__).parent / "test"), api_id, api_hash) as client:
        print(f"\n기간 {camp['start']} ~ {camp['end']}, 키워드 {', '.join(camp['keywords'])}\n")
        for kol in cfg["kols"]:
            ch = (kol.get("telegram") or "").strip().lstrip("@")
            if not ch:
                continue
            codes = [kol["invite_code"]] if kol.get("invite_code") else []
            match = c.make_matcher(camp["keywords"] + codes, camp.get("exclude_phrases", []))
            try:
                entity = await client.get_entity(ch)
            except Exception as e:
                print(f"❌ {kol['name']} (@{ch}): 채널을 찾을 수 없음 — {e}\n")
                continue
            n = v = 0
            found = []
            async for msg in client.iter_messages(entity, offset_date=start, reverse=True):
                if msg.date > end:
                    break
                hits = match(msg.message)
                if hits:
                    n += 1
                    v += msg.views or 0
                    found.append((msg.views or 0, msg.id, (msg.message or "").replace("\n", " ")[:40], hits))
            has_photo = bool(getattr(getattr(entity, "photo", None), "photo_id", None))
            squads[kol["squad"]]["posts"] += n
            squads[kol["squad"]]["views"] += v
            print(f"✅ {kol['name']} (@{ch}, {squads[kol['squad']]['name']}): 키워드 게시물 {n}건, 조회수 {v:,}, 채널 사진 {'있음' if has_photo else '없음'}")
            for views, mid, txt, hits in sorted(found, reverse=True)[:3]:
                print(f"     {views:>9,}  https://t.me/{ch}/{mid}  {txt}  [{', '.join(hits)}]")
            print()
            await asyncio.sleep(1)

    print("=== 스쿼드 합계 (텔레그램만) ===")
    for s in sorted(squads.values(), key=lambda s: -s["views"]):
        print(f"{s['name']}: 게시물 {s['posts']}건 / {camp.get('min_posts', 0)}건, 조회수 {s['views']:,} / {camp.get('min_views', 0):,}")


if __name__ == "__main__":
    asyncio.run(main())
