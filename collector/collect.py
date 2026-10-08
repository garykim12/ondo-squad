#!/usr/bin/env python3
"""
KOL 스쿼드 대결 조회수 수집기

- 텔레그램 채널(Telethon)과 X(공식 API v2)에서 키워드가 들어간 게시물을 찾고 조회수를 갱신합니다.
- 결과는 docs/data.json 에 저장되고, GitHub Pages 대시보드(docs/index.html)가 이 파일을 읽습니다.
- 점수 = 키워드 게시물 조회수 단순 합계 (텔레그램 조회수 + X 노출수)
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_PATH = ROOT / "docs" / "data.json"
MANUAL_PATH = ROOT / "docs" / "manual_posts.csv"
AVATAR_DIR = ROOT / "docs" / "avatars"
EXCLUDED_PATH = ROOT / "docs" / "excluded_posts.txt"
INCLUDED_PATH = ROOT / "docs" / "included_posts.txt"
EXPORT_DIR = ROOT / "docs" / "export"
KST = timezone(timedelta(hours=9))
X_API = "https://api.x.com/2"
HISTORY_MAX = 3000  # 30분 간격 기준 약 2개월


# ---------- 공통 유틸 ----------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{iso(utcnow())}] {msg}", flush=True)


def load_json(path: Path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def make_matcher(keywords, exclude_phrases=()):
    """
    대소문자 무시, 키워드가 하나라도 있으면 매칭.
    - 영문 키워드는 앞이 영문/숫자면 매칭 안 함 (Ondo가 London, condo에 걸리지 않게).
      뒤쪽은 열어둬서 OndoFinance, #OndoPerps, $ONDO 등은 매칭.
    - 키워드 속 공백은 있어도 없어도 매칭 (Ondo Perps = OndoPerps).
    - exclude_phrases에 있는 표현은 먼저 지운 뒤 매칭 (예: 체감온도).
    """
    pats = []
    for k in keywords:
        k = (k or "").strip()
        if not k:
            continue
        pat = re.escape(k.lower()).replace(r"\ ", r"\s*")
        if re.match(r"[a-z0-9]", k.lower()):
            pat = r"(?<![a-z0-9])" + pat
        pats.append((k, re.compile(pat)))
    excl = [e.lower() for e in exclude_phrases if e and e.strip()]

    def match(text):
        t = (text or "").lower()
        for e in excl:
            t = t.replace(e, " ")
        return [k for k, p in pats if p.search(t)]

    return match


def upsert_post(data, key, rec, now, spike_per_hour):
    """게시물 저장/갱신 + 시간당 조회수 급증 감지."""
    posts = data["posts"]
    old = posts.get(key)
    rec = dict(rec)
    if old:
        delta = rec.get("views", old.get("views", 0)) - old.get("views", 0)
        rec["last_delta"] = delta
        spike = old.get("spike", False)
        if spike_per_hour and delta > 0 and old.get("updated_at"):
            hours = max((now - parse_dt(old["updated_at"])).total_seconds() / 3600, 0.25)
            if delta / hours >= spike_per_hour:
                spike = True
                rec["spike_at"] = iso(now)
        rec["spike"] = spike
    else:
        rec["last_delta"] = 0
        rec["spike"] = False
        rec["first_seen"] = iso(now)
    rec["deleted"] = False
    rec["updated_at"] = iso(now)
    posts[key] = {**(old or {}), **rec}


# ---------- 수동 추가 게시물 (키워드가 없어도 집계) ----------

def read_link_list(path):
    """한 줄에 링크 하나. 빈 줄과 # 줄은 무시, 링크 뒤 공백 이후는 메모."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.split()[0])
    return out


def included_targets():
    """included_posts.txt → ({(텔레그램채널 소문자, 글번호)}, {X 글 ID})"""
    tg, xs = set(), set()
    for u in read_link_list(INCLUDED_PATH):
        n = norm_url(u)
        m = re.match(r"t\.me/(?:c/)?([^/]+)/(\d+)", n)
        if m:
            tg.add((m.group(1), int(m.group(2))))
            continue
        m = re.match(r"x\.com/status/(\d+)", n)
        if m:
            xs.add(m.group(1))
    return tg, xs


# ---------- 텔레그램 ----------

async def save_avatar(client, entity, kol, data):
    """텔레그램 채널 프로필 사진 저장. 사진이 바뀌었을 때만 다시 받음."""
    pid = getattr(getattr(entity, "photo", None), "photo_id", None)
    avatars = data["state"].setdefault("tg_avatars", {})
    if not pid:
        avatars.pop(kol["name"], None)
        return
    slug = re.sub(r"[^a-z0-9_]", "", (getattr(entity, "username", None) or str(entity.id)).lower())
    path = AVATAR_DIR / f"{slug}.jpg"
    if avatars.get(kol["name"], {}).get("id") == pid and path.exists():
        return
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    saved = await client.download_profile_photo(entity, file=str(path), download_big=False)
    if saved:
        avatars[kol["name"]] = {"id": pid, "file": f"avatars/{slug}.jpg?v={pid}"}
        log(f"텔레그램 @{slug}: 채널 사진 저장")


async def collect_telegram(cfg, data, matcher_for, start, end, now, errors, forced_tg=frozenset()):
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    session = os.environ.get("TG_SESSION")
    if not (api_id and api_hash and session):
        log("텔레그램: 시크릿(TG_API_ID, TG_API_HASH, TG_SESSION)이 없어 건너뜁니다")
        return

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    spike = cfg["campaign"].get("spike_views_per_hour", 0)
    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            errors.append("텔레그램: 세션이 만료됐습니다. tools/make_tg_session.py로 새로 발급해 TG_SESSION을 교체하세요.")
            return

        for kol in cfg["kols"]:
            channel = (kol.get("telegram") or "").strip().lstrip("@")
            if not channel:
                continue
            match = matcher_for(kol)
            try:
                entity = await client.get_entity(channel)
                username = getattr(entity, "username", None) or channel
                try:
                    await save_avatar(client, entity, kol, data)
                except Exception as e:
                    errors.append(f"텔레그램 {kol['name']} 채널 사진: {e}")
                seen = set()
                fwd = 0
                # 캠페인 시작 시각 이후 게시물을 오래된 순으로 훑음
                async for msg in client.iter_messages(entity, offset_date=start, reverse=True):
                    if msg.date > end:
                        break
                    forced = (username.lower(), msg.id) in forced_tg or (channel.lower(), msg.id) in forced_tg
                    if msg.fwd_from and not forced:  # 다른 채널 글을 전달(포워딩)한 건 집계 제외
                        if match(msg.message):
                            fwd += 1
                        continue
                    hits = match(msg.message)
                    if not hits and forced:
                        hits = ["수동 추가"]
                    if not hits:
                        continue
                    key = f"tg:{username.lower()}:{msg.id}"
                    seen.add(key)
                    upsert_post(data, key, {
                        "platform": "telegram",
                        "kol": kol["name"],
                        "url": f"https://t.me/{username}/{msg.id}",
                        "text": (msg.message or "")[:280],
                        "created_at": iso(msg.date),
                        "views": msg.views or 0,
                        "forwards": msg.forwards or 0,
                        "keywords": hits,
                    }, now, spike)

                # 이번에 안 보인 게시물 = 삭제됐거나 키워드가 빠짐 → 집계 제외
                for key, p in data["posts"].items():
                    if p.get("platform") == "telegram" and p.get("kol") == kol["name"] and key not in seen:
                        p["deleted"] = True
                log(f"텔레그램 @{username}: 키워드 게시물 {len(seen)}개 (포워딩 {fwd}개 제외)")
            except Exception as e:  # 한 채널 실패가 전체를 멈추지 않도록
                errors.append(f"텔레그램 {kol['name']} (@{channel}): {e}")
            await asyncio.sleep(1)
    finally:
        await client.disconnect()


# ---------- X (트위터) ----------

class XClient:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        self.calls = 0

    def get(self, path, params):
        for _ in range(3):
            r = self.s.get(X_API + path, params=params, timeout=30)
            self.calls += 1
            if r.status_code == 429:
                reset = int(r.headers.get("x-rate-limit-reset", 0) or 0)
                wait = (reset - time.time()) if reset else 60
                if wait > 180:
                    raise RuntimeError(f"요청 한도 초과, {int(wait)}초 뒤 재설정")
                log(f"X: 요청 한도 도달, {int(max(wait, 5))}초 대기")
                time.sleep(max(wait, 5) + 1)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        raise RuntimeError("요청 한도 초과가 계속됨")


def tweet_text(t):
    # 긴 글(note tweet)은 전체 본문이 note_tweet에 들어옴
    return (t.get("note_tweet") or {}).get("text") or t.get("text") or ""


def tweet_metrics(t):
    m = t.get("public_metrics") or {}
    return {
        "views": m.get("impression_count", 0) or 0,
        "likes": m.get("like_count", 0) or 0,
        "reposts": (m.get("retweet_count", 0) or 0) + (m.get("quote_count", 0) or 0),
        "replies": m.get("reply_count", 0) or 0,
    }


def collect_x(cfg, data, matcher_for, start, end, now, errors, final=False):
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        log("X: 시크릿(X_BEARER_TOKEN)이 없어 건너뜁니다")
        return

    camp = cfg["campaign"]
    state = data["state"]

    # 새 글 찾기 간격 (읽은 글 수만큼 과금되므로 간격을 늘려도 비용은 거의 같음)
    interval = camp.get("x_discover_minutes", camp.get("x_refresh_minutes", 60))
    last = state.get("x_last_run")
    if not final and last and (now - parse_dt(last)).total_seconds() < interval * 60 - 120:
        last_k = parse_dt(last).astimezone(timezone(timedelta(hours=9)))
        next_k = last_k + timedelta(minutes=interval - 2)
        log(f"X: 마지막 X 수집 {last_k:%H:%M}(KST) 후 {interval}분이 안 지나 이번엔 건너뜁니다. "
            f"다음 X 수집은 {next_k:%H:%M}(KST) 이후 실행 때. 직전 결과: {state.get('x_last_summary', '-')}")
        return
    reads = 0  # 이번 실행에서 읽은 게시물 수 (과금 기준)

    spike = camp.get("spike_views_per_hour", 0)
    x = XClient(token)
    fields = "created_at,public_metrics,note_tweet"

    handles = {}
    for kol in cfg["kols"]:
        h = (kol.get("x") or "").strip().lstrip("@").lower()
        if h:
            handles[h] = kol

    # 1) 핸들 → 사용자 ID (한 번 조회 후 캐시)
    ids = state.setdefault("x_user_ids", {})
    missing = [h for h in handles if h not in ids]
    try:
        for i in range(0, len(missing), 100):
            res = x.get("/users/by", {"usernames": ",".join(missing[i:i + 100])})
            for u in res.get("data", []):
                ids[u["username"].lower()] = u["id"]
            for err in res.get("errors", []):
                errors.append(f"X 계정 조회 실패: {err.get('value')} ({err.get('detail', '')})")
    except Exception as e:
        errors.append(f"X 계정 조회: {e}")
        return

    # 2) 새 게시물 찾기 (각 KOL 타임라인, 지난번 이후 것만)
    since = state.setdefault("x_since_id", {})
    exclude = ["retweets"] + ([] if camp.get("x_include_replies") else ["replies"])
    fresh = set()
    for h, kol in handles.items():
        uid = ids.get(h)
        if not uid:
            continue
        match = matcher_for(kol)
        params = {"max_results": 100, "tweet.fields": fields, "exclude": ",".join(exclude)}
        if since.get(h):
            params["since_id"] = since[h]
        else:
            params["start_time"] = iso(start)
        if end < now:
            params["end_time"] = iso(end)
        try:
            newest, found, page = None, 0, None
            while True:
                if page:
                    params["pagination_token"] = page
                res = x.get(f"/users/{uid}/tweets", params)
                meta = res.get("meta", {})
                newest = newest or meta.get("newest_id")
                reads += len(res.get("data", []))
                for t in res.get("data", []):
                    created = parse_dt(t["created_at"])
                    if created < start or created > end:
                        continue
                    hits = match(tweet_text(t))
                    if not hits:
                        continue
                    key = f"x:{t['id']}"
                    upsert_post(data, key, {
                        "platform": "x",
                        "kol": kol["name"],
                        "url": f"https://x.com/{h}/status/{t['id']}",
                        "text": tweet_text(t)[:280],
                        "created_at": iso(created),
                        "keywords": hits,
                        **tweet_metrics(t),
                    }, now, spike)
                    fresh.add(key)
                    found += 1
                page = meta.get("next_token")
                if not page:
                    break
            if newest:
                since[h] = newest
            log(f"X @{h}: 새 키워드 게시물 {found}개")
        except Exception as e:
            errors.append(f"X {kol['name']} (@{h}): {e}")

    # 3) 이미 찾은 게시물 조회수 갱신 (100개씩 묶어서)
    # 비용 절약: 올린 지 2일 이내 글은 x_refresh_recent_hours마다,
    # 그보다 오래된 글은 x_refresh_old_days마다, 캠페인 마감 직후 전체 1회 최종 갱신
    recent_h = camp.get("x_refresh_recent_hours", 6)
    old_d = camp.get("x_refresh_old_days", 7)

    def needs_refresh(p):
        if final:
            return True
        last = p.get("updated_at")
        if not last:
            return True
        since_upd = (now - parse_dt(last)).total_seconds()
        if (now - parse_dt(p["created_at"])).total_seconds() <= 2 * 86400:
            return since_upd >= recent_h * 3600 - 600
        return since_upd >= old_d * 86400 - 600

    known = [k[2:] for k, p in data["posts"].items()
             if p.get("platform") == "x" and not p.get("deleted") and k not in fresh and needs_refresh(p)]
    for i in range(0, len(known), 100):
        batch = known[i:i + 100]
        try:
            res = x.get("/tweets", {"ids": ",".join(batch), "tweet.fields": fields})
        except Exception as e:
            errors.append(f"X 조회수 갱신: {e}")
            break
        reads += len(res.get("data", []))
        for t in res.get("data", []):
            upsert_post(data, f"x:{t['id']}", tweet_metrics(t), now, spike)
        for err in res.get("errors", []):  # 삭제·비공개 전환된 글
            rid = err.get("resource_id") or err.get("value")
            if rid in batch:
                data["posts"][f"x:{rid}"]["deleted"] = True

    state["x_last_run"] = iso(now)
    state["x_last_summary"] = f"새 키워드 글 {len(fresh)}개, 조회수 갱신 {len(known)}개"
    # 일별 읽기 수 기록 (비용 확인용, 한국시간 기준 날짜)
    day = (now + timedelta(hours=9)).strftime("%Y-%m-%d")
    usage = state.setdefault("x_reads_by_day", {})
    usage[day] = usage.get(day, 0) + reads
    log(f"X: API 호출 {x.calls}회, 게시물 {reads}건 읽음 (약 ${reads * 0.005:.2f}). "
        f"오늘 누적 {usage[day]}건 (약 ${usage[day] * 0.005:.2f})")



def collect_x_included(cfg, data, forced_x, start, end, now, errors):
    """included_posts.txt의 X 글 중 아직 집계에 없는 것만 바로 가져옴 (1시간 제한 무관)."""
    # 목록에서 지운 수동 추가 X 글은 다시 빠짐
    for k, p in data["posts"].items():
        if k.startswith("x:") and p.get("keywords") == ["수동 추가"] and k[2:] not in forced_x:
            p["deleted"] = True
    token = os.environ.get("X_BEARER_TOKEN")
    new_ids = [i for i in forced_x if f"x:{i}" not in data["posts"] or data["posts"][f"x:{i}"].get("deleted")]
    if not token or not new_ids:
        return
    by_handle = {(k.get("x") or "").strip().lstrip("@").lower(): k for k in cfg["kols"] if k.get("x")}
    x = XClient(token)
    for i in range(0, len(new_ids), 100):
        batch = new_ids[i:i + 100]
        try:
            res = x.get("/tweets", {"ids": ",".join(batch), "tweet.fields": "created_at,public_metrics,note_tweet,author_id",
                                    "expansions": "author_id", "user.fields": "username"})
        except Exception as e:
            errors.append(f"X 수동 추가: {e}")
            return
        users = {u["id"]: u["username"].lower() for u in (res.get("includes") or {}).get("users", [])}
        for t in res.get("data", []):
            h = users.get(t.get("author_id"), "")
            kol = by_handle.get(h)
            if not kol:
                errors.append(f"included_posts.txt: X 글 {t['id']}의 작성자 @{h}는 KOL 목록에 없어 추가하지 않았습니다")
                continue
            created = parse_dt(t["created_at"])
            if created < start or created > end:
                errors.append(f"included_posts.txt: X 글 {t['id']}은 캠페인 기간 밖이라 추가하지 않았습니다")
                continue
            upsert_post(data, f"x:{t['id']}", {
                "platform": "x", "kol": kol["name"],
                "url": f"https://x.com/{h}/status/{t['id']}",
                "text": tweet_text(t)[:280], "created_at": iso(created),
                "keywords": ["수동 추가"], **tweet_metrics(t),
            }, now, cfg["campaign"].get("spike_views_per_hour", 0))
            log(f"X 수동 추가: {kol['name']} 글 {t['id']}")
        for err in res.get("errors", []):
            errors.append(f"included_posts.txt: X 글 {err.get('resource_id') or err.get('value')}을 찾을 수 없습니다 (삭제·비공개)")


# ---------- 수동 입력 게시물 (유튜브, 인스타그램 등) ----------

def parse_local_dt(s):
    """'2026-10-03' 또는 '2026-10-03 18:00' → 시간대 없으면 한국시간으로 간주."""
    s = (s or "").strip()
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1) if "T" not in s else s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone(timedelta(hours=9)))


def load_manual_posts(cfg, data, now, errors):
    names = {k["name"] for k in cfg["kols"]}
    seen = set()
    if MANUAL_PATH.exists():
        with open(MANUAL_PATH, encoding="utf-8-sig") as f:
            lines = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
        for r in csv.DictReader(lines):
            kol = (r.get("kol") or "").strip()
            url = (r.get("url") or "").strip()
            if not kol and not url:
                continue
            if kol not in names:
                errors.append(f"manual_posts.csv: KOL 이름 '{kol}'이 config.json에 없습니다 ({url})")
                continue
            if not url:
                errors.append(f"manual_posts.csv: {kol}의 게시물에 url이 없습니다")
                continue
            try:
                created = parse_local_dt(r.get("date")) or now
            except ValueError:
                errors.append(f"manual_posts.csv: 날짜 형식 오류 '{r.get('date')}' ({url}) — 예: 2026-10-03")
                created = now
            key = "m:" + url.rstrip("/")
            seen.add(key)
            upsert_post(data, key, {
                "platform": (r.get("platform") or "기타").strip(),
                "kol": kol,
                "url": url,
                "text": (r.get("note") or "").strip()[:280],
                "created_at": iso(created),
                "views": int(re.sub(r"[^0-9]", "", r.get("views") or "") or 0),
                "keywords": [],
                "manual": True,
            }, now, 0)
    # 파일에서 지운 줄은 집계에서도 삭제
    for key in [k for k in data["posts"] if k.startswith("m:") and k not in seen]:
        del data["posts"][key]
    if seen:
        log(f"수동 입력 게시물 {len(seen)}개")

def norm_url(u):
    """링크 표기 차이 무시: http/https, www, 대소문자, ?뒤, t.me/s/, twitter.com, x.com/아이디/status."""
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^(www\.|mobile\.)", "", u)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    u = u.replace("twitter.com/", "x.com/").replace("t.me/s/", "t.me/")
    m = re.match(r"x\.com/.*?status/(\d+)", u)
    return f"x.com/status/{m.group(1)}" if m else u


# ---------- 집계 ----------

def aggregate(cfg, data):
    kols = {}
    for k in cfg["kols"]:
        kols[k["name"]] = {
            "name": k["name"], "squad": k["squad"],
            "telegram": k.get("telegram", ""), "x": k.get("x", ""),
            "avatar": data["state"].get("tg_avatars", {}).get(k["name"], {}).get("file", ""),
            "posts": 0, "tg_views": 0, "x_views": 0, "other_views": 0, "views": 0,
        }
    for p in data["posts"].values():
        k = kols.get(p.get("kol"))
        if not k or p.get("deleted") or p.get("excluded"):
            continue
        v = p.get("views", 0)
        k["posts"] += 1
        k["views"] += v
        plat = p.get("platform")
        k["tg_views" if plat == "telegram" else "x_views" if plat == "x" else "other_views"] += v

    kol_list = sorted(kols.values(), key=lambda k: -k["views"])
    for i, k in enumerate(kol_list):
        k["rank"] = i + 1

    squads = []
    for s in cfg["squads"]:
        members = [k for k in kol_list if k["squad"] == s["id"]]
        squads.append({
            **s,
            "views": sum(m["views"] for m in members),
            "tg_views": sum(m["tg_views"] for m in members),
            "x_views": sum(m["x_views"] for m in members),
            "other_views": sum(m["other_views"] for m in members),
            "posts": sum(m["posts"] for m in members),
            "members": [m["name"] for m in members],
        })
    squads.sort(key=lambda s: -s["views"])
    for i, s in enumerate(squads):
        s["rank"] = i + 1
    return kol_list, squads


def kst(s):
    return parse_dt(s).astimezone(KST).strftime("%Y-%m-%d %H:%M") if s else ""


def write_exports(cfg, data, kols, squads):
    """구글 시트 IMPORTDATA용 CSV (docs/export/*.csv)."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    camp = cfg["campaign"]
    min_views = camp.get("min_views", 0)
    updated = kst(data.get("updated_at"))
    plat = {"telegram": "Telegram", "x": "X"}
    sq_en = lambda sid: f"Squad {sid}"
    kcfg = {k["name"]: k for k in cfg["kols"]}

    def ident(name):
        """조회수 앞에 공통으로 붙는 KOL 정보 열."""
        k = kcfg.get(name, {})
        tg = (k.get("telegram") or "").lstrip("@")
        x = (k.get("x") or "").lstrip("@")
        return [k.get("geo") or camp.get("geographic", "Korea"), sq_en(k.get("squad", "")), name,
                k.get("aid", ""), f"https://t.me/{tg}" if tg else "", f"https://x.com/{x}" if x else ""]

    ident_head = ["Geographic", "Squad", "Name", "AID", "Channels (TG)", "Channels (X)"]

    def save(name, header, rows):
        with open(EXPORT_DIR / name, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    # 스쿼드: A, B, C 순
    save("squads.csv",
         ["Squad", "순위", "총 조회수", "텔레그램 조회수", "X 조회수", "게시물 수", "조회수 최소 조건", "달성 여부", "업데이트(KST)"],
         [[sq_en(s["id"]), s["rank"], s["views"], s["tg_views"], s["x_views"], s["posts"], min_views,
           "달성" if min_views and s["views"] >= min_views else "미달", updated]
          for s in sorted(squads, key=lambda s: str(s["id"]))])

    # KOL: config.json에 적힌 순서 그대로 고정
    order = {k["name"]: i for i, k in enumerate(cfg["kols"])}
    save("kols.csv",
         ident_head + ["총 조회수", "텔레그램 조회수", "X 조회수", "게시물 수", "전체 순위"],
         [ident(k["name"]) + [k["views"], k["tg_views"], k["x_views"], k["posts"], k["rank"]]
          for k in sorted(kols, key=lambda k: order.get(k["name"], 999))])

    # 게시물: 최신순
    posts = [p for p in data["posts"].values()
             if not p.get("deleted") and not p.get("excluded") and p.get("kol") in kcfg]
    posts.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    save("posts.csv",
         ident_head + ["게시일(KST)", "플랫폼", "조회수", "링크", "본문 미리보기"],
         [ident(p["kol"]) + [kst(p.get("created_at")), plat.get(p.get("platform"), p.get("platform", "")),
                             p.get("views", 0), p.get("url", ""), " ".join((p.get("text") or "").split())[:100]]
          for p in posts])


def main() -> int:
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        print("config.json이 없습니다", file=sys.stderr)
        return 1

    camp = cfg["campaign"]
    start, end = parse_dt(camp["start"]), parse_dt(camp["end"])
    lock = parse_dt(camp.get("lock") or camp["end"])
    now = utcnow()

    data = load_json(DATA_PATH, {})
    data.setdefault("posts", {})
    data.setdefault("history", [])
    data.setdefault("state", {})
    errors = []

    if now > lock and data.get("final"):
        log("최종 확정된 캠페인입니다. 더 이상 갱신하지 않습니다.")
        return 0

    collected = False
    if now < start:
        log("캠페인 시작 전입니다. 수집하지 않습니다.")
    else:
        final = now > lock
        if final:
            log("마감 시각이 지났습니다. 전체 게시물 조회수를 마지막으로 한 번 갱신하고 순위를 확정합니다.")

        def matcher_for(kol):
            # 공통 키워드 + 해당 KOL의 초대코드
            extra = [kol["invite_code"]] if kol.get("invite_code") else []
            return make_matcher(camp["keywords"] + extra, camp.get("exclude_phrases", []))

        force_x = os.environ.get("X_FULL_REFRESH", "").lower() == "true"
        if force_x:
            log("X 전체 갱신 요청: 1시간 제한을 무시하고 모든 X 게시물 조회수를 지금 갱신합니다.")
        forced_tg, forced_x = included_targets()
        asyncio.run(collect_telegram(cfg, data, matcher_for, start, end, now, errors, forced_tg))
        collect_x(cfg, data, matcher_for, start, end, now, errors, final=final or force_x)
        collect_x_included(cfg, data, forced_x, start, end, now, errors)
        load_manual_posts(cfg, data, now, errors)
        collected = True
        if final:
            data["final"] = True
            data["final_at"] = iso(now)

    # 운영자가 수동으로 제외한 게시물 (config의 excluded_posts에 URL 입력)
    excluded = {norm_url(u) for u in camp.get("excluded_posts", []) if u and u.strip()}
    # docs/excluded_posts.txt: 한 줄에 링크 하나, 공백이나 # 뒤는 메모
    if EXCLUDED_PATH.exists():
        for line in EXCLUDED_PATH.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            excluded.add(norm_url(line.split()[0]))
    for p in data["posts"].values():
        p["excluded"] = norm_url(p.get("url", "")) in excluded

    kols, squads = aggregate(cfg, data)

    if collected:
        data["history"].append({"t": iso(now), "s": {s["id"]: s["views"] for s in squads}})
        data["history"] = data["history"][-HISTORY_MAX:]
        data["updated_at"] = iso(now)
        data["errors"] = errors

    data["campaign"] = {k: camp.get(k) for k in ("name", "start", "end", "lock", "keywords", "min_posts", "min_views", "min_volume")}
    data["squads"] = squads
    data["kols"] = kols

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    write_exports(cfg, data, kols, squads)

    for e in errors:
        print(f"::warning::{e}", flush=True)  # GitHub Actions 경고로 표시
    log("완료: " + ", ".join(f"{s['name']} {s['views']:,}" for s in squads))
    return 0


if __name__ == "__main__":
    sys.exit(main())
