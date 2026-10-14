# -*- coding: utf-8 -*-
"""Batch driver: convert every video in the 技术分享 favorites folder to notes.

Serially submits /api/summarize for each video, waits for terminal status,
logs progress to batch_log.jsonl. Re-running resumes: already-completed
videos are skipped via /api/tasks history + notes archive.
"""
import glob
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOCAL = os.environ.get("VIDEOTONO_SERVICE", "http://127.0.0.1:8000")
FAVS = os.environ.get("FAVS_JSON", "favs.json")
LOG = os.environ.get("BATCH_LOG",
                     os.path.join(os.path.dirname(os.path.abspath(FAVS))
                                  if os.path.dirname(FAVS) else ".",
                                  "batch_log.jsonl"))
NOTES_DIR = os.environ.get("VIDEOTONOTES_NOTES_DIR",
                           r"D:\Develop\videotono\workspace\notes")
UNAVAILABLE_TITLE = "已失效视频"
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
SRC_RE = re.compile(r"^来源：\s*(\S+)", re.M)

SUBMIT = {
    # 风格红线：永远 detailed。「concise/精简摘要」会丢内容，已被用户禁止。
    "output": "note",
    "summary_style": "detailed",
    "processing_mode": "reuse",
    "prefer_subtitles": True,
    "whisper_model": "belle-turbo-zh",
    "use_gpu": True,
    # empty api_key -> service reuses the key saved for this endpoint.
    # 按用户实际通道修改 base_url / model；失败也绝不降级为 concise。
    "llm_config": {
        "model_type": "custom",
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.6-pro",
        "api_key": "",
    },
}


def http_json(url, payload=None, method=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def log(entry):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {entry.get('event')} {entry.get('bvid', '')} "
          f"{str(entry.get('title', ''))[:40]} {entry.get('status', '')}",
          flush=True)


def norm_bvid(s):
    m = BV_RE.search(s or "")
    return m.group(0) if m else ""


def completed_bvids():
    """bvids that already have an archived note, plus completed task history.

    Notes on disk are the authoritative record: /api/tasks only returns a
    bounded window of recent tasks, so restarts must not depend on it alone.
    """
    done = set()
    for p in glob.glob(os.path.join(NOTES_DIR, "*.md")):
        try:
            text = io.open(p, encoding="utf-8").read()
        except Exception:
            continue
        m = SRC_RE.search(text)
        bvid = norm_bvid(m.group(1)) if m else ""
        if bvid:
            done.add(bvid)
    try:
        d = http_json(LOCAL + "/api/tasks")
    except Exception:
        return done
    tasks = d.get("tasks") if isinstance(d, dict) else d
    for t in tasks or []:
        if t.get("status") != "completed":
            continue
        tid = t.get("task_id")
        if not tid:
            continue
        try:
            detail = http_json(LOCAL + f"/api/task/{tid}")
        except Exception:
            continue
        src = detail.get("source_url") or (detail.get("task") or {}).get("source_url")
        bvid = norm_bvid(src) or norm_bvid(t.get("source_url") or "")
        if bvid:
            done.add(bvid)
    return done


TERMINAL = ("completed", "failed", "cancelled", "timeout")


def wait_no_active(deadline_min=30):
    """Wait until the service has no in-flight tasks (avoids twin notes)."""
    end = time.time() + deadline_min * 60
    while time.time() < end:
        try:
            d = http_json(LOCAL + "/api/tasks")
        except Exception:
            time.sleep(15)
            continue
        active = [t for t in (d.get("tasks") or [])
                  if t.get("status") not in TERMINAL]
        if not active:
            return
        log({"event": "waiting_idle", "active": len(active)})
        time.sleep(20)


def wait_terminal(task_id, timeout=3 * 3600):
    t0 = time.time()
    last_pct = -1
    while time.time() - t0 < timeout:
        try:
            d = http_json(LOCAL + f"/api/task/{task_id}")
        except Exception as e:
            time.sleep(15)
            continue
        st = d.get("status") or (d.get("runtime") or {}).get("status")
        pct = (d.get("runtime") or {}).get("progress")
        if pct is not None and pct != last_pct and pct % 20 == 0:
            last_pct = pct
            log({"event": "progress", "task_id": task_id, "pct": pct})
        if st in ("completed", "failed", "cancelled"):
            return d
        time.sleep(12)
    return {"status": "timeout"}


def main():
    only_bvid = sys.argv[1] if len(sys.argv) > 1 else None
    favs = json.load(io.open(FAVS, encoding="utf-8"))
    videos = favs["videos"]
    if only_bvid:
        videos = [v for v in videos if v["bvid"] == only_bvid]
        if not videos:
            print("bvid not found:", only_bvid)
            return

    done = completed_bvids()
    log({"event": "batch_start", "total": len(videos), "already_done": len(done)})
    wait_no_active()

    for i, v in enumerate(videos, 1):
        title = v["title"]
        url = v["url"]
        if title == UNAVAILABLE_TITLE:
            log({"event": "skip_unavailable", "bvid": v["bvid"]})
            continue
        if v["bvid"] in done:
            log({"event": "skip_done", "bvid": v["bvid"], "title": title})
            continue

        payload = dict(SUBMIT, video_url=url)
        submitted = None
        for attempt in range(6):
            try:
                submitted = http_json(LOCAL + "/api/summarize", payload)
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:200]
                log({"event": "submit_error", "bvid": v["bvid"],
                     "code": e.code, "body": body})
                time.sleep(30)
            except Exception as e:
                log({"event": "submit_error", "bvid": v["bvid"],
                     "error": str(e)[:200]})
                time.sleep(30)
        if not submitted:
            log({"event": "give_up", "bvid": v["bvid"], "title": title})
            continue

        task_id = submitted.get("task_id") or submitted.get("id")
        log({"event": "submitted", "bvid": v["bvid"], "title": title,
             "task_id": task_id, "index": i, "of": len(videos)})
        if not task_id:
            log({"event": "no_task_id", "resp": submitted})
            continue

        t0 = time.time()
        result = wait_terminal(task_id)
        status = result.get("status")
        runtime = result.get("runtime") or {}
        note = (runtime.get("result") or {}).get("note_path") \
            or (result.get("result") or {}).get("note_path")
        err = runtime.get("error") or result.get("error")
        log({"event": "finished", "bvid": v["bvid"], "title": title,
             "task_id": task_id, "status": status,
             "elapsed": round(time.time() - t0), "note": note,
             "error": err})
        # failures are handled by the manual fallback (agent writes the
        # note from the transcript) — never downgrade to concise style

    log({"event": "batch_end"})


if __name__ == "__main__":
    main()
