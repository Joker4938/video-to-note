# -*- coding: utf-8 -*-
"""Insert content-aligned screenshots into VideoToNo notes.

For each `## heading [MM:SS-MM:SS]` section in a note, extract a video frame
at the section midpoint (one per ~2 min of span) and embed it right under the
heading.

Image placement follows the service's own screenshot scheme so every viewer
keeps working:
  - frames land in workspace/<task_id>/frames/<bvid>_sNN_MMmSSs.jpg
  - the archived note (workspace/notes/<title>.md) links them as
    ../<task_id>/frames/<file>  (resolves next to notes/, like service shots)
  - the task-dir mirror (workspace/<task_id>/notes.md) links them as
    ./frames/<file>, which the web UI rewrites to /api/image/<task_id>/<file>
Notes without a completed service task (agent-written fallbacks) keep the
portable scheme notes/images/<bvid>/<file>.

Already-filled notes are retro-migrated onto this scheme on re-run, and the
task mirror is refreshed even when the archive is up to date.

Usage:
  python shotfill.py                 # fill every note that lacks screenshots
  python shotfill.py <note_path>     # fill one note
"""
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOCAL = os.environ.get("VIDEOTONO_SERVICE", "http://127.0.0.1:8000")
NOTES_DIR = os.environ.get("VIDEOTONOTES_NOTES_DIR",
                           r"D:\Develop\videotono\workspace\notes")
WORKSPACE_DIR = os.path.dirname(NOTES_DIR)  # task dirs live beside notes/
IMAGES_DIR = os.path.join(NOTES_DIR, "images")
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") \
    or r"C:\ProgramData\chocolatey\bin\ffmpeg.exe"
FFPROBE = os.environ.get("FFPROBE") or shutil.which("ffprobe") \
    or r"C:\ProgramData\chocolatey\bin\ffprobe.exe"

SEC_RE = re.compile(
    r"^(#{2,3})\s+(.+?)\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*"
    r"(\d{1,2}:\d{2}(?::\d{2})?)\]\s*$")
SRC_RE = re.compile(r"^来源：\s*(\S+)", re.M)
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
# any screenshot link we (or the service) may have written already
SHOT_LINK_RE = re.compile(
    r"!\[[^\]]*\]\((?:images/[^)]+|\.\./[^)]*frames/[^)]+|\./frames/[^)]+)\)")
OLD_SHOT_LINK_RE = r"\]\(images/([^/)]+)/([^)]+)\)"
SHOT_SPAN = 120  # one screenshot per 2 minutes of section span
MAX_SHOTS_PER_SECTION = 4


def parse_ts(ts: str) -> int:
    parts = [int(x) for x in ts.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def fmt_ts(sec: int) -> str:
    return f"{sec // 60:02d}m{sec % 60:02d}s"


def get_cookies():
    req = urllib.request.Request(LOCAL + "/api/bili-login/start",
                                 data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        login = json.loads(r.read().decode("utf-8"))
    if not login.get("ok") or not login.get("cookies"):
        return []
    names = {"sessdata": "SESSDATA"}
    cookies = []
    for k, v in login["cookies"].items():
        if v:
            cookies.append((names.get(k, k), v))
    return cookies


def write_cookie_file(path, cookies):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for name, value in cookies:
            f.write(f".bilibili.com\tTRUE\t/\tTRUE\t1893456000\t"
                    f"{name}\t{value}\n")


def download_video(url, dest, cookies):
    cookie_path = dest + ".cookies.txt"
    write_cookie_file(cookie_path, cookies)
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bv*[height<=720]/bv*/b",
        "--no-playlist",
        "-o", dest,
        "--cookies", cookie_path,
        "--no-simulate",
        "--newline",
        url,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    try:
        os.remove(cookie_path)
    except OSError:
        pass
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or "")[-400:]
    return True, ""


def video_duration(path) -> float:
    p = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def extract_frame(video, t, out_path, width=1280):
    subprocess.run(
        [FFMPEG, "-y", "-ss", str(t), "-i", video,
         "-frames:v", "1", "-q:v", "3",
         "-vf", f"scale='min({width},iw)':-2",
         out_path],
        capture_output=True, timeout=120)
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 0


def section_targets(start: int, end: int):
    """Screenshot timestamps inside a section."""
    span = max(end - start, 1)
    n = max(1, min(MAX_SHOTS_PER_SECTION, round(span / SHOT_SPAN)))
    step = span / (n + 1)
    return [int(start + step * (i + 1)) for i in range(n)]


def norm_bvid(s):
    m = BV_RE.search(s or "")
    return m.group(0) if m else ""


def find_task_dir(bvid, archive_name):
    """Locate workspace/<task_id>/ this note was produced from.

    Prefer the task whose manifest runtime.result.archived_path points at the
    note file; otherwise the newest task whose source mentions the BV id.
    """
    best = None
    try:
        entries = os.listdir(WORKSPACE_DIR)
    except OSError:
        return None
    for name in entries:
        tdir = os.path.join(WORKSPACE_DIR, name)
        manifest_path = os.path.join(tdir, "task.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            manifest = json.load(io.open(manifest_path, encoding="utf-8"))
        except Exception:
            continue
        archived = str(
            ((manifest.get("runtime") or {}).get("result") or {})
            .get("archived_path") or "")
        if archived and os.path.basename(archived) == archive_name:
            return tdir
        src = f"{manifest.get('source_url') or ''} " \
              f"{manifest.get('normalized_source_url') or ''}"
        if bvid and bvid in src:
            mt = os.path.getmtime(tdir)
            if best is None or mt > best[0]:
                best = (mt, tdir)
    return best[1] if best else None


def task_mirror_allowed(tdir):
    """Only completed note tasks may get a notes.md mirror.

    The service treats notes.md presence as "completed" when restoring after a
    restart, so writing one into a failed task would flip its status.
    """
    try:
        manifest = json.load(
            io.open(os.path.join(tdir, "task.json"), encoding="utf-8"))
    except Exception:
        return False
    status = str((manifest.get("runtime") or {}).get("status") or "")
    return status == "completed" or os.path.isfile(os.path.join(tdir, "notes.md"))


def write_mirror(text, task_dir, task_id):
    """Task-dir notes.md is what the web UI serves; link frames the way
    replaceImagePaths expects (./frames/<file> -> /api/image/<task_id>/<file>)."""
    mirror = text.replace(f"](../{task_id}/frames/", "](./frames/")
    io.open(os.path.join(task_dir, "notes.md"), "w",
            encoding="utf-8").write(mirror)


def sync_mirror(note_path, text):
    """Retro-fit an already-filled note onto the task-frames scheme and refresh
    the task mirror so the web UI shows the screenshots. Idempotent."""
    m = SRC_RE.search(text)
    if not m:
        return
    bvid = norm_bvid(m.group(1))
    task_dir = find_task_dir(bvid, os.path.basename(note_path))
    if not (task_dir and task_mirror_allowed(task_dir)):
        return
    task_id = os.path.basename(task_dir)
    frames = os.path.join(task_dir, "frames")
    os.makedirs(frames, exist_ok=True)

    def repl(match):
        sub, fname = match.group(1), match.group(2)
        src = os.path.join(IMAGES_DIR, sub, fname)
        new_name = f"{sub}_{fname}"
        dst = os.path.join(frames, new_name)
        if os.path.isfile(src) and not os.path.isfile(dst):
            shutil.copy2(src, dst)
        return f"](../{task_id}/frames/{new_name})"

    new_text = re.sub(OLD_SHOT_LINK_RE, repl, text)
    if new_text != text:
        io.open(note_path, "w", encoding="utf-8").write(new_text)
        print(f"migrated to task frames: {os.path.basename(note_path)}")
    write_mirror(new_text, task_dir, task_id)


def fill_note(note_path):
    text = io.open(note_path, encoding="utf-8").read()
    if SHOT_LINK_RE.search(text):
        sync_mirror(note_path, text)
        print(f"skip (already has screenshots): {os.path.basename(note_path)}")
        return True
    m = SRC_RE.search(text)
    if not m:
        print(f"skip (no source url): {os.path.basename(note_path)}")
        return False
    url = m.group(1)
    bvid = norm_bvid(url) or url.rstrip("/").split("/")[-1].split("?")[0]

    sections = []  # (line_index, heading, start_ts, end_ts)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        sm = SEC_RE.match(line)
        if sm:
            sections.append((i, sm.group(0), parse_ts(sm.group(3)),
                             parse_ts(sm.group(4))))
    if not sections:
        print(f"skip (no timestamped sections): "
              f"{os.path.basename(note_path)}")
        return False

    task_dir = find_task_dir(bvid, os.path.basename(note_path))
    use_task = bool(task_dir and task_mirror_allowed(task_dir))
    task_id = os.path.basename(task_dir) if use_task else ""
    if use_task:
        img_dir = os.path.join(task_dir, "frames")
        def link_of(name): return f"../{task_id}/frames/{name}"
    else:
        img_dir = os.path.join(IMAGES_DIR, bvid)
        def link_of(name): return f"images/{bvid}/{name}"

    # collect every target timestamp first, one download for all frames
    plan = []  # (line_index, [t...])
    for idx, _h, s, e in sections:
        plan.append((idx, section_targets(s, e)))

    tmpdir = tempfile.mkdtemp(prefix="shotfill_")
    video = os.path.join(tmpdir, "video.bin")
    cookies = get_cookies()
    print(f"downloading {bvid} ...")
    ok, err = download_video(url, video, cookies)
    if not ok:
        print(f"download failed for {bvid}: {err}")
        return False
    dur = video_duration(video)
    if dur <= 0:
        print(f"cannot probe duration for {bvid}")
        os.remove(video)
        return False

    os.makedirs(img_dir, exist_ok=True)
    shots = 0
    inject = {}  # line_index -> [link, ...]
    n = 0
    for idx, targets in plan:
        links = []
        for t in targets:
            tc = max(0, min(t, int(dur) - 1))
            n += 1
            # task frames share one dir with the service's own shots: prefix the
            # bvid. the fallback dir is already per-bvid, plain names there.
            stem = f"s{n:02d}_{fmt_ts(tc)}.jpg"
            name = f"{bvid}_{stem}" if use_task else stem
            out = os.path.join(img_dir, name)
            if extract_frame(video, tc, out):
                links.append(link_of(name))
                shots += 1
        if links:
            inject[idx] = links
    try:
        os.remove(video)
    except OSError:
        pass
    os.rmdir(tmpdir) if not os.listdir(tmpdir) else None

    if not inject:
        print(f"no frames extracted for {bvid}")
        return False

    # rebuild note: insert image lines under each heading
    new_lines = []
    for i, line in enumerate(lines):
        new_lines.append(line)
        if i in inject:
            new_lines.append("")
            for link in inject[i]:
                new_lines.append(f"![{link.split('_')[-1][:-4]}]({link})")
            new_lines.append("")
    new_text = "\n".join(new_lines)
    io.open(note_path, "w", encoding="utf-8").write(new_text)
    if use_task:
        write_mirror(new_text, task_dir, task_id)
    print(f"ok: {os.path.basename(note_path)} "
          f"+{shots} screenshots -> {img_dir}")
    return True


def main():
    os.makedirs(IMAGES_DIR, exist_ok=True)
    if len(sys.argv) > 1:
        fill_note(sys.argv[1])
        return
    notes = sorted(glob.glob(os.path.join(NOTES_DIR, "*.md")))
    print(f"scanning {len(notes)} notes")
    for np in notes:
        try:
            fill_note(np)
        except Exception as e:
            print(f"error on {os.path.basename(np)}: {e}")


if __name__ == "__main__":
    main()
