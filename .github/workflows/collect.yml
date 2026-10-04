#!/usr/bin/env python3
"""
在庫の収集と整理。毎日1回まわす想定。

ファイルを4つに分けてある。アプリが書き換えるのは stock.json だけで、
残りはこのスクリプトが毎晩まとめて面倒を見る。

  stock.json    いま裁く対象(寝かせ中・ふるい待ち・採用済み)。増えない
  watched.json  視聴済みと評価。信頼度・相関・再放送の 材料
  seen.json     却下した動画のIDだけを11文字ずつ連結した文字列
  stats.json    月×チャンネルの集計。通過率などはここから出す

やること:
  1. アプリが付けた判定(却下・既視聴・視聴済み)を stock から退避
  2. 各チャンネルの新着を拾う
  3. 今日の1本(過去動画からランダム)と再放送(昔の満点)を足す
  4. 再生時間をまとめて取得
  5. 寝かせ明けの昇格と、期限切れの差し戻し
"""

import json
import os
import random
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "docs", "data")
CHANNELS_PATH = os.path.join(D, "channels.json")
STOCK_PATH = os.path.join(D, "stock.json")
WATCHED_PATH = os.path.join(D, "watched.json")
SEEN_PATH = os.path.join(D, "seen.json")
STATS_PATH = os.path.join(D, "stats.json")

API = "https://www.googleapis.com/youtube/v3"
RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {"atom": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}

NOW = datetime.now(timezone.utc)
MONTH = NOW.strftime("%Y-%m")
units = 0


# ── 入出力 ──────────────────────────────────────────────

def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "yt-sieve/0.3"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load(path, fallback):
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ! {os.path.basename(path)} を読めず既定値で続行: {e}", file=sys.stderr)
        return fallback


def save(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


# ── 却下IDの圧縮(11文字固定を連結) ──────────────────────

def seen_set(seen):
    s = seen.get("ids", "")
    return {s[i:i + 11] for i in range(0, len(s), 11)} - {""}


def seen_dump(ids):
    return {"ids": "".join(sorted(ids)), "count": len(ids)}


# ── 取得 ────────────────────────────────────────────────

def uploads_id(channel_id):
    return "UU" + channel_id[2:]


def api_get(url):
    global units
    try:
        data = json.loads(get(url))
        units += 1
    except urllib.error.HTTPError as e:
        print(f"  ! {e.code}: {e.read().decode('utf-8', 'replace')[:160]}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  ! 取得失敗: {e}", file=sys.stderr)
        return None
    if "error" in data:
        print(f"  ! API: {data['error'].get('message')}", file=sys.stderr)
        return None
    return data


def entry_of(it, channel_id):
    sn, cd = it.get("snippet", {}), it.get("contentDetails", {})
    vid = cd.get("videoId") or sn.get("resourceId", {}).get("videoId")
    if not vid or sn.get("title") in ("Private video", "Deleted video"):
        return None
    return {"id": vid, "title": sn.get("title", ""), "channelId": channel_id,
            "channelName": sn.get("videoOwnerChannelTitle", ""),
            "published": cd.get("videoPublishedAt") or sn.get("publishedAt"),
            "desc": (sn.get("description") or "")[:200]}


def fetch_uploads(channel_id, api_key, limit, known):
    out, token, hits = [], None, 0
    while len(out) < limit:
        url = (f"{API}/playlistItems?part=snippet,contentDetails"
               f"&playlistId={uploads_id(channel_id)}&maxResults=50&key={api_key}")
        if token:
            url += f"&pageToken={token}"
        data = api_get(url)
        if not data:
            return out
        for it in data.get("items", []):
            e = entry_of(it, channel_id)
            if not e:
                continue
            if e["id"] in known:
                hits += 1
                continue
            out.append(e)
        if hits >= 5:
            break
        token = data.get("nextPageToken")
        if not token:
            break
    return out[:limit]


def random_old_video(channel_id, api_key, known, max_pages=20):
    """過去動画からランダムに1本。ランダムな深さまでページを送って拾う。"""
    data = api_get(f"{API}/playlists?part=contentDetails&id={uploads_id(channel_id)}&key={api_key}")
    if not data or not data.get("items"):
        return None
    total = data["items"][0].get("contentDetails", {}).get("itemCount", 0)
    if total < 30:
        return None

    pages = min(max_pages, max(1, -(-total // 50)))
    target = random.randrange(pages)
    token = None
    for i in range(target + 1):
        url = (f"{API}/playlistItems?part=snippet,contentDetails"
               f"&playlistId={uploads_id(channel_id)}&maxResults=50&key={api_key}")
        if token:
            url += f"&pageToken={token}"
        data = api_get(url)
        if not data:
            return None
        if i == target:
            cands = [e for e in (entry_of(x, channel_id) for x in data.get("items", []))
                     if e and e["id"] not in known]
            return random.choice(cands) if cands else None
        token = data.get("nextPageToken")
        if not token:
            return None
    return None


def parse_feed(channel_id):
    try:
        root = ET.fromstring(get(RSS.format(channel_id)))
    except Exception as e:  # noqa: BLE001
        print(f"  ! RSS失敗: {e}", file=sys.stderr)
        return []
    out = []
    for en in root.findall("atom:entry", NS):
        vid = en.findtext("yt:videoId", namespaces=NS)
        if vid:
            out.append({"id": vid, "title": en.findtext("atom:title", default="", namespaces=NS),
                        "channelId": channel_id,
                        "channelName": en.findtext("atom:author/atom:name", default="", namespaces=NS),
                        "published": en.findtext("atom:published", namespaces=NS), "desc": ""})
    return out


ISO_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def iso_to_seconds(s):
    m = ISO_DUR.fullmatch(s or "")
    if not m:
        return None
    d, h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec


def fetch_durations(ids, api_key):
    res = {}
    for i in range(0, len(ids), 50):
        data = api_get(f"{API}/videos?part=contentDetails,statistics"
                       f"&id={','.join(ids[i:i + 50])}&key={api_key}")
        if not data:
            continue
        for it in data.get("items", []):
            res[it["id"]] = {
                "duration": iso_to_seconds(it.get("contentDetails", {}).get("duration")),
                "views": int(it.get("statistics", {}).get("viewCount", 0) or 0)}
    return res


def days_since(iso):
    if not iso:
        return 1e9
    try:
        return (NOW - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 86400
    except ValueError:
        return 1e9


def bump(stats, cid, key):
    m = stats.setdefault(MONTH, {}).setdefault(cid or "-", {})
    m[key] = m.get(key, 0) + 1


def blank(e, ch, **extra):
    return {**e, "added": NOW.isoformat(timespec="seconds"), "state": "fresh",
            "type": ch.get("defaultType"), "duration": None, "pre": None,
            "score": None, "post": None, "watchedAt": None, "explore": False, **extra}


# ── 本体 ────────────────────────────────────────────────

def main():
    channels = load(CHANNELS_PATH, {"channels": [], "defaults": {}})
    stock = load(STOCK_PATH, {"videos": {}})
    watched = load(WATCHED_PATH, {"videos": {}})
    seen = load(SEEN_PATH, {"ids": ""})
    stats = load(STATS_PATH, {})

    videos = stock.setdefault("videos", {})
    wvid = watched.setdefault("videos", {})
    seen_ids = seen_set(seen)

    d = channels.get("defaults", {})
    cool_days = d.get("coolDays", 3)
    expire_days = d.get("expireDays", 14)
    default_backfill = d.get("backfill", 10)

    # ── 1. 判定済みを退避(切り詰めの代わり) ─────────────────
    KEEP = ("channelId", "channelName", "title", "duration", "type",
            "pre", "score", "post", "watchedAt", "rerun", "daily", "manual")
    moved_w = moved_s = 0
    for vid in list(videos):
        v = videos[vid]
        st = v.get("state")
        if st == "watched":
            wvid[vid] = {k: v[k] for k in KEEP if v.get(k) is not None}
            bump(stats, v.get("channelId"), "watched")
            del videos[vid]
            moved_w += 1
        elif st in ("dropped", "seen"):
            seen_ids.add(vid)
            bump(stats, v.get("channelId"),
                 "seen" if st == "seen"
                 else {"expired": "expired", "pushed": "pushed", "full": "full"}
                 .get(v.get("dropReason"), "dropped"))
            del videos[vid]
            moved_s += 1
    if moved_w or moved_s:
        print(f"退避: 視聴済み {moved_w} / 却下・既視聴 {moved_s}\n")

    known = set(videos) | set(wvid) | seen_ids

    # ── 2. 新着 ─────────────────────────────────────────
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        print("警告: YOUTUBE_API_KEY が未設定。RSSで試すが失敗しやすい", file=sys.stderr)

    added = renamed = 0
    active = [c for c in channels.get("channels", []) if not c.get("paused")]
    for ch in active:
        cid = ch["id"]
        first = not ch.get("seeded")
        limit = ch.get("backfill", default_backfill) if first else 100
        print(f"- {ch.get('name') or cid}" + (f" (初回: 最大{limit}本)" if first else ""))

        entries = fetch_uploads(cid, api_key, limit, known) if api_key else parse_feed(cid)

        if entries and ch.get("name", cid) in (cid, "", None) and entries[0].get("channelName"):
            ch["name"] = entries[0]["channelName"]
            renamed += 1
            print(f"  -> 名前: {ch['name']}")

        n = 0
        for e in entries:
            if e["id"] in known:
                continue
            videos[e["id"]] = blank(e, ch)
            known.add(e["id"])
            n += 1
        ch["seeded"] = True
        added += n
        if n:
            print(f"  -> 新着 {n}本")

    # ── 3. 今日の1本と再放送 ─────────────────────────────
    if api_key and d.get("dailyPick", True) and active:
        ch = random.choice(active)
        e = random_old_video(ch["id"], api_key, known)
        if e:
            # 過去動画なので寝かせる意味がない。すぐふるいに出す
            videos[e["id"]] = blank(e, ch, state="pool", daily=True)
            known.add(e["id"])
            print(f"\n今日の1本: {e['title'][:40]} ({ch.get('name')})")
        else:
            print(f"\n今日の1本: {ch.get('name')} からは拾えず")

    every = d.get("rerunEveryDays", 7)
    if every and days_since(stock.get("lastRerun")) >= every:
        cands = [k for k, w in wvid.items()
                 if (w.get("post") or {}).get("score") == 5
                 and days_since(w.get("watchedAt")) >= d.get("rerunAfterDays", 180)
                 and k not in videos]
        if cands:
            k = random.choice(cands)
            w = wvid[k]
            videos[k] = {"id": k, "title": w.get("title", ""),
                         "channelId": w.get("channelId", ""),
                         "channelName": w.get("channelName", ""),
                         "published": w.get("watchedAt"), "desc": "",
                         "added": NOW.isoformat(timespec="seconds"), "state": "pool",
                         "type": w.get("type"), "duration": w.get("duration"),
                         "pre": None, "score": None, "post": None,
                         "watchedAt": None, "explore": False, "rerun": True}
            stock["lastRerun"] = NOW.isoformat(timespec="seconds")
            print(f"再放送: {w.get('title', '')[:40]}")
        elif not stock.get("lastRerun"):
            stock["lastRerun"] = NOW.isoformat(timespec="seconds")

    # ── 4. 再生時間 ─────────────────────────────────────
    missing = [k for k, v in videos.items() if v.get("duration") is None]
    if api_key and missing:
        print(f"\n再生時間: {len(missing)}本")
        for vid, meta in fetch_durations(missing[:1000], api_key).items():
            videos[vid].update(meta)

    # ── 5. 昇格と期限切れ ────────────────────────────────
    promoted = returned = expired = 0
    for v in videos.values():
        c = next((x for x in active if x["id"] == v.get("channelId")), {})
        if v["state"] == "fresh" and days_since(v.get("added")) >= c.get("coolDays", cool_days):
            v["state"] = "pool"
            promoted += 1
        elif v["state"] == "picked" and v.get("pickedAt") \
                and days_since(v["pickedAt"]) >= c.get("expireDays", expire_days):
            # 黙って消さず、ふるいに差し戻して裁き直させる。2度目で落とす
            v["expiredCount"] = v.get("expiredCount", 0) + 1
            if v["expiredCount"] >= 2:
                v["state"] = "dropped"
                v["dropReason"] = "expired"
                expired += 1
            else:
                v["state"] = "pool"
                v["pickedAt"] = None
                returned += 1

    stock["updated"] = NOW.isoformat(timespec="seconds")
    save(STOCK_PATH, stock)
    save(WATCHED_PATH, watched)
    save(SEEN_PATH, seen_dump(seen_ids), compact=True)
    save(STATS_PATH, stats)
    save(CHANNELS_PATH, channels)

    def kb(p):
        return os.path.getsize(p) // 1024 if os.path.exists(p) else 0

    print(f"\n新着 {added} / 昇格 {promoted} / 差し戻し {returned} / 落選 {expired}")
    print(f"在庫 {len(videos)} / 視聴済み {len(wvid)} / 却下ID {len(seen_ids)}")
    print(f"サイズ stock {kb(STOCK_PATH)}KB  watched {kb(WATCHED_PATH)}KB  "
          f"seen {kb(SEEN_PATH)}KB  stats {kb(STATS_PATH)}KB")
    print(f"クォータ 約{units}ユニット / 10,000")


if __name__ == "__main__":
    main()
