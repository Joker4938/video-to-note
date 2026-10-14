# -*- coding: utf-8 -*-
"""List videos in a bilibili favorites folder (default: 技术分享).

Usage:
  python list_favs.py [输出文件] [收藏夹名]

Uses VideoToNo's own /api/bili-login/start to obtain the session (no browser
credential-store access). Cookies stay in memory; only public video metadata
is printed. Note: bilibili requires the cookie name SESSDATA in uppercase.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

LOCAL = os.environ.get("VIDEOTONO_SERVICE", "http://127.0.0.1:8000")
FOLDER_NAME = os.environ.get("FOLDER_NAME", "技术分享")


def api_get(url, cookies: dict) -> dict:
    # bilibili requires the cookie name SESSDATA in uppercase
    names = {"sessdata": "SESSDATA"}
    hdr = "; ".join(f"{names.get(k, k)}={v}"
                    for k, v in cookies.items() if v)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
        "Referer": "https://www.bilibili.com/",
        "Cookie": hdr,
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    with urllib.request.urlopen(urllib.request.Request(
            LOCAL + "/api/bili-login/start", data=b"", method="POST"),
            timeout=30) as r:
        login = json.loads(r.read().decode("utf-8"))
    if not login.get("ok") or not login.get("cookies"):
        print(json.dumps({"error": "login not ready", "detail": login},
                         ensure_ascii=False))
        sys.exit(1)
    cookies = login["cookies"]

    nav = api_get("https://api.bilibili.com/x/web-interface/nav", cookies)
    if nav.get("code") != 0:
        print(json.dumps({"error": "nav failed", "detail": nav.get("message")},
                         ensure_ascii=False))
        sys.exit(1)
    uid = nav["data"]["mid"]
    uname = nav["data"].get("uname")

    folders = api_get(
        f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={uid}",
        cookies)
    if folders.get("code") != 0:
        print(json.dumps({"error": "folder list failed",
                          "detail": folders.get("message")},
                         ensure_ascii=False))
        sys.exit(1)

    target = None
    all_names = []
    for f in (folders.get("data") or {}).get("list") or []:
        all_names.append(f"{f.get('title')}(id={f.get('id')},count={f.get('media_count')})")
        if f.get("title") == FOLDER_NAME:
            target = f
    if not target:
        print(json.dumps({
            "error": f"folder '{FOLDER_NAME}' not found",
            "uid": uid, "uname": uname, "folders": all_names,
        }, ensure_ascii=False))
        sys.exit(2)

    fid = target["id"]
    videos = []
    pn = 1
    while True:
        page = api_get(
            "https://api.bilibili.com/x/v3/fav/resource/list"
            f"?media_id={fid}&pn={pn}&ps=20&platform=web&order=mtime",
            cookies)
        if page.get("code") != 0:
            print(json.dumps({"error": "resource list failed",
                              "detail": page.get("message"), "pn": pn},
                             ensure_ascii=False))
            sys.exit(1)
        data = page.get("data") or {}
        medias = data.get("medias") or []
        for m in medias:
            videos.append({
                "id": m.get("id"),
                "bvid": m.get("bvid"),
                "title": m.get("title"),
                "duration": m.get("duration"),
                "upper": (m.get("upper") or {}).get("name"),
                "fav_at": m.get("fav_at"),
                "url": f"https://www.bilibili.com/video/{m.get('bvid')}",
            })
        if not data.get("has_more") or not medias:
            break
        pn += 1

    out = {
        "uid": uid, "uname": uname,
        "folder": {"id": fid, "title": target.get("title"),
                   "media_count": target.get("media_count")},
        "video_count": len(videos),
        "videos": videos,
    }
    out_path = sys.argv[1] if len(sys.argv) > 1 else "favs.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote", out_path, "videos:", len(videos))


if __name__ == "__main__":
    if len(sys.argv) > 2:
        FOLDER_NAME = sys.argv[2]
    main()
