# -*- coding: utf-8 -*-
"""Save the service's bilibili login session as persistent credentials.

Pulls cookies from /api/bili-login/start (in memory) and POSTs them to
/api/bili-credentials so later summarize/transcribe tasks auto-attach them.
No secret values are printed.
"""
import json
import os
import urllib.request

LOCAL = os.environ.get("VIDEOTONO_SERVICE", "http://127.0.0.1:8000")


def post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    login = post(LOCAL + "/api/bili-login/start", {})
    if not login.get("ok") or not login.get("cookies"):
        print("login not ready:", login.get("state"), login.get("message"))
        return
    ck = login["cookies"]
    payload = {
        "sessdata": ck.get("sessdata", ""),
        "bili_jct": ck.get("bili_jct", ""),
        "buvid3": ck.get("buvid3", ""),
    }
    r = post(LOCAL + "/api/bili-credentials", payload)
    print("save result:", json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
