#!/usr/bin/env python3
"""
チャンネルの動画を集めて在庫(stock.json)を更新する。

取得元は playlistItems.list。チャンネルIDの3文字目を C から U に変えたものが
「全アップロード動画」のプレイリストIDになる。1ページ50件で1ユニット。

RSSは2025年末から404を返す障害が続いているため、APIキーが無いときの
保険としてのみ残してある。

やること:
  1. 各チャンネルの新着を拾って在庫に追加(state: fresh)
  2. 初回だけ backfill 件数ぶん過去に遡る
  3. videos.list で再生時間をまとめて取得(50件/1unit)
  4. 寝かせ期間が明けたものを fresh -> pool に昇格
  5. picked のまま期限を過ぎたものを dropped に落とす(減価)
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNELS_PATH = os.path.join(ROOT, "docs", "data", "channels.json")
STOCK_PATH = os.path.join(ROOT, "docs", "data", "stock.json")

API = "https://www.googleapis.com/youtube/v3"
RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

NOW = datetime.now(timezone.utc)
units = 0  # 使ったクォータの目安


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "yt-sieve/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_json(path, fallback):
    if not os.path.exists(path):
        return fallback
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


# ── 取得: playlistItems.list ────────────────────────────────

def uploads_id(channel_id):
    """UCxxx -> UUxxx。全アップロード動画のプレイリスト。"""
    return "UU" + channel_id[2:]


def fetch_uploads(channel_id, api_key, limit, known):
    """
    新しい順に遡る。既に在庫にあるIDに当たったら、そのページで打ち切る。
    limit 件に達しても打ち切る。
    """
    global units
    out, token, seen_known = [], None, 0

    while len(out) < limit:
        url = (f"{API}/playlistItems?part=snippet,contentDetails"
               f"&playlistId={uploads_id(channel_id)}&maxResults=50&key={api_key}")
        if token:
            url += f"&pageToken={token}"
        try:
            data = json.loads(get(url))
            units += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            print(f"  ! 取得失敗 {e.code}: {body}", file=sys.stderr)
            return out
        except Exception as e:  # noqa: BLE001
            print(f"  ! 取得失敗: {e}", file=sys.stderr)
            return out

        if "error" in data:
            print(f"  ! API: {data['error'].get('message')}", file=sys.stderr)
            return out

        for it in data.get("items", []):
            sn, cd = it.get("snippet", {}), it.get("contentDetails", {})
            vid = cd.get("videoId") or sn.get("resourceId", {}).get("videoId")
            if not vid:
                continue
            if vid in known:
                seen_known += 1
                continue
            if sn.get("title") in ("Private video", "Deleted video"):
                continue
            out.append({
                "id": vid,
                "title": sn.get("title", ""),
                "channelId": channel_id,
                "channelName": sn.get("videoOwnerChannelTitle", ""),
                # publishedAt はプレイリスト追加日時なので、実公開日時を優先する
                "published": cd.get("videoPublishedAt") or sn.get("publishedAt"),
                "desc": (sn.get("description") or "")[:300],
            })

        # 既知のものが並び始めたら、それ以上遡っても新着はない
        if seen_known >= 5:
            break
        token = data.get("nextPageToken")
        if not token:
            break

    return out[:limit]


# ── 取得: RSS(保険) ───────────────────────────────────────

def parse_feed(channel_id):
    try:
        raw = get(RSS.format(channel_id))
    except Exception as e:  # noqa: BLE001
        print(f"  ! RSS取得失敗 {channel_id}: {e}", file=sys.stderr)
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"  ! RSS解析失敗: {e}", file=sys.stderr)
        return []

    out = []
    for entry in root.findall("atom:entry", NS):
        vid = entry.findtext("yt:videoId", namespaces=NS)
        if not vid:
            continue
        group = entry.find("media:group", NS)
        desc = group.findtext("media:description", default="", namespaces=NS) if group is not None else ""
        out.append({
            "id": vid,
            "title": entry.findtext("atom:title", default="", namespaces=NS),
            "channelId": channel_id,
            "channelName": entry.findtext("atom:author/atom:name", default="", namespaces=NS),
            "published": entry.findtext("atom:published", namespaces=NS),
            "desc": (desc or "")[:300],
        })
    return out


# ── 再生時間 ───────────────────────────────────────────────

ISO_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def iso_to_seconds(s):
    m = ISO_DUR.fullmatch(s or "")
    if not m:
        return None
    d, h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec


def fetch_durations(video_ids, api_key):
    global units
    result = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        url = (f"{API}/videos?part=contentDetails,statistics"
               f"&id={','.join(chunk)}&key={api_key}")
        try:
            data = json.loads(get(url))
            units += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ! videos.list 失敗: {e}", file=sys.stderr)
            continue
        for item in data.get("items", []):
            result[item["id"]] = {
                "duration": iso_to_seconds(item.get("contentDetails", {}).get("duration")),
                "views": int(item.get("statistics", {}).get("viewCount", 0) or 0),
            }
    return result


def days_since(iso):
    if not iso:
        return 0.0
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (NOW - t).total_seconds() / 86400.0


# ── 本体 ───────────────────────────────────────────────────

def main():
    channels = load_json(CHANNELS_PATH, {"channels": [], "defaults": {}})
    stock = load_json(STOCK_PATH, {"videos": {}, "updated": None})
    videos = stock.setdefault("videos", {})

    defaults = channels.get("defaults", {})
    cool_days = defaults.get("coolDays", 3)
    expire_days = defaults.get("expireDays", 14)
    default_backfill = defaults.get("backfill", 30)

    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        print("警告: YOUTUBE_API_KEY が未設定。RSSで試すが、"
              "YouTube側の障害で失敗する可能性が高い\n", file=sys.stderr)

    added = renamed = 0
    for ch in channels.get("channels", []):
        if ch.get("paused"):
            continue
        cid = ch["id"]
        known = {k for k, v in videos.items() if v.get("channelId") == cid}
        first_time = not known
        limit = ch.get("backfill", default_backfill) if first_time else 100

        print(f"- {ch.get('name') or cid}" + (f" (初回: 最大{limit}本遡る)" if first_time else ""))

        entries = fetch_uploads(cid, api_key, limit, known) if api_key else parse_feed(cid)

        if entries and ch.get("name", cid) in (cid, "", None):
            real = entries[0].get("channelName")
            if real:
                ch["name"] = real
                renamed += 1
                print(f"  -> 名前を取得: {real}")

        n = 0
        for v in entries:
            if v["id"] in videos:
                continue
            videos[v["id"]] = {
                **v,
                "added": NOW.isoformat(timespec="seconds"),
                "state": "fresh",
                "type": ch.get("defaultType"),
                "duration": None,
                "pre": None, "score": None, "post": None,
                "watchedAt": None, "explore": False,
            }
            n += 1
        added += n
        if n:
            print(f"  -> 新着 {n}本")

    # 再生時間の補完
    missing = [k for k, v in videos.items() if v.get("duration") is None]
    if api_key and missing:
        target = missing[:1000]
        print(f"\n再生時間を取得: {len(target)}本")
        for vid, meta in fetch_durations(target, api_key).items():
            videos[vid].update(meta)

    # 状態遷移
    promoted = expired = 0
    for v in videos.values():
        c = next((x for x in channels.get("channels", []) if x["id"] == v.get("channelId")), {})
        if v["state"] == "fresh" and days_since(v.get("added")) >= c.get("coolDays", cool_days):
            v["state"] = "pool"
            promoted += 1
        elif v["state"] == "picked" and days_since(v.get("pickedAt")) >= c.get("expireDays", expire_days):
            v["state"] = "dropped"
            v["dropReason"] = "expired"
            expired += 1

    stock["updated"] = NOW.isoformat(timespec="seconds")
    save_json(STOCK_PATH, stock)
    if renamed:
        save_json(CHANNELS_PATH, channels)

    print(f"\n新着 {added} / 昇格 {promoted} / 減価で落選 {expired} / 在庫 {len(videos)}")
    print(f"使ったクォータ: 約{units}ユニット (1日の枠は10,000)")


if __name__ == "__main__":
    main()
