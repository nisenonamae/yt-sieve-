#!/usr/bin/env python3
"""
チャンネルのRSSを読んで在庫(stock.json)を更新する。

やること:
  1. 各チャンネルのRSSから新着を拾って在庫に追加(state: fresh)
  2. APIキーがあれば videos.list で再生時間をまとめて取得(50件/1unit)
  3. 寝かせ期間が明けたものを fresh -> pool に昇格
  4. picked のまま期限を過ぎたものを dropped に落とす(減価)
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNELS_PATH = os.path.join(ROOT, "docs", "data", "channels.json")
STOCK_PATH = os.path.join(ROOT, "docs", "data", "stock.json")

RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

NOW = datetime.now(timezone.utc)


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "yt-sieve/0.1"})
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


def parse_feed(channel_id):
    """RSSから最新15件を返す。落ちても全体は止めない。"""
    try:
        raw = get(RSS.format(channel_id))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  ! RSS取得失敗 {channel_id}: {e}", file=sys.stderr)
        return []

    root = ET.fromstring(raw)
    out = []
    for entry in root.findall("atom:entry", NS):
        vid = entry.findtext("yt:videoId", namespaces=NS)
        if not vid:
            continue
        group = entry.find("media:group", NS)
        desc = ""
        if group is not None:
            desc = group.findtext("media:description", default="", namespaces=NS) or ""
        out.append(
            {
                "id": vid,
                "title": entry.findtext("atom:title", default="", namespaces=NS),
                "channelId": channel_id,
                "channelName": entry.findtext(
                    "atom:author/atom:name", default="", namespaces=NS
                ),
                "published": entry.findtext("atom:published", namespaces=NS),
                "desc": desc[:300],
            }
        )
    return out


ISO_DUR = re.compile(
    r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
)


def iso_to_seconds(s):
    m = ISO_DUR.fullmatch(s or "")
    if not m:
        return None
    d, h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec


def fetch_durations(video_ids, api_key):
    """videos.list を50件ずつ。1回1unit。"""
    result = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        url = (
            "https://www.googleapis.com/youtube/v3/videos"
            f"?part=contentDetails,statistics&id={','.join(chunk)}&key={api_key}"
        )
        try:
            data = json.loads(get(url))
        except Exception as e:  # noqa: BLE001
            print(f"  ! videos.list 失敗: {e}", file=sys.stderr)
            continue
        for item in data.get("items", []):
            result[item["id"]] = {
                "duration": iso_to_seconds(
                    item.get("contentDetails", {}).get("duration")
                ),
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


def main():
    channels = load_json(CHANNELS_PATH, {"channels": [], "defaults": {}})
    stock = load_json(STOCK_PATH, {"videos": {}, "updated": None})
    videos = stock.setdefault("videos", {})

    defaults = channels.get("defaults", {})
    cool_days = defaults.get("coolDays", 3)
    expire_days = defaults.get("expireDays", 14)

    added = 0
    renamed = 0
    for ch in channels.get("channels", []):
        if ch.get("paused"):
            continue
        cid = ch["id"]
        print(f"- {ch.get('name') or cid}")
        entries = parse_feed(cid)

        # 名前が未設定、またはIDのままなら、RSSの著者名で埋める
        if entries and ch.get("name", cid) in (cid, "", None):
            real = entries[0].get("channelName")
            if real:
                ch["name"] = real
                renamed += 1
                print(f"  -> 名前を取得: {real}")

        for v in entries:
            if v["id"] in videos:
                continue
            videos[v["id"]] = {
                **v,
                "added": NOW.isoformat(timespec="seconds"),
                "state": "fresh",
                "type": ch.get("defaultType"),
                "duration": None,
                "pre": None,
                "score": None,
                "post": None,
                "watchedAt": None,
                "explore": False,
            }
            added += 1

    # 再生時間がまだ入っていないものをまとめて取得
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    missing = [k for k, v in videos.items() if v.get("duration") is None]
    if api_key and missing:
        print(f"再生時間を取得: {len(missing)}件 ({-(-len(missing)//50)}unit)")
        for vid, meta in fetch_durations(missing[:1000], api_key).items():
            videos[vid].update(meta)
    elif missing:
        print(f"再生時間未取得 {len(missing)}件 (YOUTUBE_API_KEY 未設定)")

    # 状態遷移
    promoted = expired = 0
    for v in videos.values():
        ch = next(
            (c for c in channels.get("channels", []) if c["id"] == v.get("channelId")),
            {},
        )
        cd = ch.get("coolDays", cool_days)
        ed = ch.get("expireDays", expire_days)

        if v["state"] == "fresh" and days_since(v.get("added")) >= cd:
            v["state"] = "pool"
            promoted += 1
        elif v["state"] == "picked" and days_since(v.get("pickedAt")) >= ed:
            v["state"] = "dropped"
            v["dropReason"] = "expired"
            expired += 1

    stock["updated"] = NOW.isoformat(timespec="seconds")
    save_json(STOCK_PATH, stock)
    if renamed:
        save_json(CHANNELS_PATH, channels)
    print(f"\n新着 {added} / 昇格 {promoted} / 減価で落選 {expired} / 在庫 {len(videos)}")


if __name__ == "__main__":
    main()
