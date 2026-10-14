# -*- coding: utf-8 -*-
"""Sweep archived notes: web-verify「模型补充」claims, rewrite the analysis section.

用户红线：质量优先、不省 token。老笔记里「模型补充」内容只挂了
「未联网核验」免责声明，现在服务端出稿已经自动联网核验，本脚本负责
把存量笔记也补上同样的核验改写（新出稿的笔记不用跑）。

复用 video-to-note-src 的 LLMSummarizer._verify_model_additions（与服务端
出稿同一条管线、同一套兜底语义）：证实→正常叙述+（来源：…）；矛盾→改正；
查不到→删除或（未经证实）；删总括免责声明；检索完全不可用→保留原样。

Usage:
  <video-to-note-src>/.venv/Scripts/python.exe verify_claims.py [note.md ...]
  无参数：扫 VIDEOTONOTES_NOTES_DIR 下全部含「模型补充」的笔记
  环境变量：VIDEOTONOTES_NOTES_DIR  VIDEOTONOTONOTE_SRC（backend 导入路径）
            VIDEOTONOTE_LLM_BASE_URL  VIDEOTONOTE_LLM_MODEL

跑完后执行一次 shotfill.py 刷新任务镜像，网页端才能看到改写后的正文。
凭据从本机密钥存档（DPAPI）解析，不落盘、不进日志。
"""
import asyncio
import glob
import io
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NOTES_DIR = os.environ.get("VIDEOTONOTES_NOTES_DIR",
                           r"D:\Develop\videotono\workspace\notes")
SRC_DIR = os.environ.get("VIDEOTONOTONOTE_SRC",
                         r"D:\Develop\videotono\video-to-note-src")
LLM_BASE_URL = os.environ.get("VIDEOTONOTE_LLM_BASE_URL",
                              "https://api.xiaomimimo.com/v1")
LLM_MODEL = os.environ.get("VIDEOTONOTE_LLM_MODEL", "mimo-v2.6-pro")

sys.path.insert(0, SRC_DIR)

from backend.config_store import ConfigStore  # noqa: E402
from backend.llm_summarizer import (  # noqa: E402
    LLMSummarizer,
    extract_model_additions,
    strip_disclaimer,
)

ANALYSIS_HEADING = "## 点评与分析"
FOOTER_RE = re.compile(r"\n---\n来源：", re.M)
TITLE_RE = re.compile(r"^#\s*视频笔记：《(.+?)》", re.M)


def split_analysis(text):
    """→ (before, heading_line, analysis, footer)；没有点评段返回 None。"""
    idx = text.find(ANALYSIS_HEADING)
    if idx < 0:
        return None
    m = FOOTER_RE.search(text, idx)
    end = m.start() if m else len(text)
    return text[:idx], text[idx:end].rstrip(), text[end:] if m else ""


async def verify_note(summarizer, path, text):
    parts = split_analysis(text)
    if not parts:
        return text, "no-analysis-heading"
    before, analysis, footer = parts
    title_m = TITLE_RE.search(text)
    title = title_m.group(1) if title_m else os.path.basename(path)
    rewritten = await summarizer._verify_model_additions(
        title, analysis, "auto", "detailed", None, None
    )
    rewritten = rewritten.strip()
    # 改写结果应自带「## 点评与分析」标题；缺了就补回
    if not rewritten.startswith("#"):
        rewritten = ANALYSIS_HEADING + "\n\n" + rewritten
    elif not rewritten.startswith(ANALYSIS_HEADING):
        rewritten = ANALYSIS_HEADING + "\n\n" + rewritten
    return before + rewritten + ("\n" + footer if footer else ""), "rewritten"


def main():
    targets = [sys.argv[1]] if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]) \
        else sorted(glob.glob(os.path.join(NOTES_DIR, "*.md")))
    queue = []
    for path in targets:
        try:
            text = io.open(path, encoding="utf-8").read()
        except OSError:
            continue
        if "模型补充" in text or "本文中所有标注" in text:
            queue.append((path, text))
    if not queue:
        print("no notes with 模型补充 markers")
        return

    workspace = os.path.join(NOTES_DIR, os.pardir)
    store = ConfigStore(os.path.abspath(workspace))
    resolved = store.resolve_stored_key(model_type="custom", base_url=LLM_BASE_URL)
    api_key = str(resolved.get("api_key") or "")
    if not api_key:
        print(f"no saved key for {LLM_BASE_URL} ({resolved.get('reason')})")
        sys.exit(1)
    summarizer = LLMSummarizer(
        model_type="custom", api_key=api_key,
        base_url=LLM_BASE_URL, model=LLM_MODEL,
    )
    print(f"queued {len(queue)} notes, model={LLM_MODEL}")

    async def run():
        done = 0
        for path, text in queue:
            name = os.path.basename(path)
            try:
                new_text, event = await verify_note(summarizer, path, text)
            except Exception as exc:  # 单篇失败继续扫
                print(f"[FAIL] {name[:60]} {type(exc).__name__}: {str(exc)[:120]}")
                continue
            if new_text != text:
                with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new_text)
            # 改写后不再有断言就清掉免责噪声；有断言但检索失败的保留
            if extract_model_additions(new_text):
                kept = "kept-disclaimer" if "未联网核验" in new_text else "verified"
            else:
                cleaned = strip_disclaimer(new_text)
                if cleaned != new_text:
                    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(cleaned)
                    new_text = cleaned
                kept = "clean"
            done += 1
            print(f"[{done}/{len(queue)}] {kept:16s} {name[:60]}")
            for w in summarizer.warnings[-2:]:
                print(f"    warn: {w[:120]}")
            summarizer.warnings.clear()

    asyncio.run(run())
    print("done — 现在跑一次 shotfill.py 刷新任务镜像")


if __name__ == "__main__":
    main()
