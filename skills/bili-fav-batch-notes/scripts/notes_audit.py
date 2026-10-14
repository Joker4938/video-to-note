# -*- coding: utf-8 -*-
"""Audit archived notes for quality problems after a batch run.

Flags:
  TWIN      文件名带 -2/-3 后缀（同一视频重复出稿）
  THIN      正文过短（疑似只有点评、正文丢失或空转写占位稿）
  NO-SRC    没有「来源：」页脚（shotfill 无法回填截图，续跑无法去重）
  NO-TS     没有带时间戳的小节（截图方案失效）
  NO-SHOT   没有内容对齐截图（跑过 shotfill 后仍缺）
  NO-COVER  小节时间轴没有覆盖到视频末尾（对照 favs.json 的 duration）

Usage:
  python notes_audit.py [favs.json]
  环境变量 VIDEOTONOTES_NOTES_DIR 指定笔记目录（默认 D:\\Develop\\videotono\\workspace\\notes）
"""
import glob
import io
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NOTES_DIR = os.environ.get("VIDEOTONOTES_NOTES_DIR",
                           r"D:\Develop\videotono\workspace\notes")
SEC_RE = re.compile(
    r"^#{2,3}\s+(.+?)\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*"
    r"(\d{1,2}:\d{2}(?::\d{2})?)\]\s*$", re.M)
SRC_RE = re.compile(r"^来源：\s*(\S+)", re.M)
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
# screenshots may link images/<bvid>/…（兜底）or ../<task_id>/frames/…（服务方案）
SHOT_RE = re.compile(r"!\[[^\]]*\]\([^)]*(?:images|frames)/[^)]*\)")
THIN_CHARS = 1500


def parse_ts(ts):
    parts = [int(x) for x in ts.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def main():
    dur_by_bvid = {}
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        favs = json.load(io.open(sys.argv[1], encoding="utf-8"))
        for v in favs.get("videos") or []:
            dur_by_bvid[v.get("bvid", "")] = v.get("duration") or 0

    flagged = 0
    for p in sorted(glob.glob(os.path.join(NOTES_DIR, "*.md"))):
        name = os.path.basename(p)
        text = io.open(p, encoding="utf-8").read()
        tags = []
        if re.search(r"-\d+\.md$", name):
            tags.append("TWIN")
        if len(text) < THIN_CHARS:
            tags.append(f"THIN({len(text)})")
        m = SRC_RE.search(text)
        if not m:
            tags.append("NO-SRC")
        ends = [parse_ts(sm.group(3)) for sm in SEC_RE.finditer(text)]
        if not ends:
            tags.append("NO-TS")
        if not SHOT_RE.search(text):
            tags.append("NO-SHOT")
        bvid = ""
        if m:
            bm = BV_RE.search(m.group(1))
            bvid = bm.group(0) if bm else ""
        dur = dur_by_bvid.get(bvid, 0)
        if ends and dur and max(ends) < dur - 30:
            tags.append(f"NO-COVER(ends={max(ends)}s,dur={dur}s)")
        if tags:
            flagged += 1
            print(f"{' '.join(tags):40s} {name[:55]}")
    print(f"\nscanned {len(glob.glob(os.path.join(NOTES_DIR, '*.md')))} notes, "
          f"flagged {flagged}")


if __name__ == "__main__":
    main()
