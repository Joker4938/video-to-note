---
name: bili-fav-batch-notes
description: 把 bilibili 收藏夹批量转成带内容对齐截图的 Markdown 笔记（依托本机 VideoToNo 服务）。当用户要求"把收藏夹/合集的视频转成笔记""批量出视频笔记""给笔记配截图"，或需要对 VideoToNo 批量产物做续跑、质检、失败兜底时使用。
---

# B 站收藏夹批量转笔记（带内容对齐截图）

端到端流程：登录态校验 → 列收藏夹 → 串行批量出笔记（详细风格）→ 按时间戳回填内容对齐截图 → 质检补漏。单个视频的转写/出笔记用同项目 `video-to-note` 技能即可；本技能解决**整个收藏夹的批量、断点续跑、截图对齐和失败兜底**。

## 三条红线（用户明确要求，任何步骤都不可违反）

1. **笔记风格永远 `detailed`（详细）**。禁止 `concise/精简摘要`——会丢内容。任务失败也**绝不降级重试**，走下面的「失败兜底」。
2. **截图必须与内容对齐**：笔记小节标题带时间戳（`## 标题 [MM:SS-MM:SS]`），截图取小节中点帧，禁止按固定间隔无差别截图。
3. **凭据不落盘、不进日志**：cookie/api_key 只在脚本内存中传递，禁止出现在命令行参数、打印输出、提交到 git 的文件里。

## 第 0 步：确认服务与补丁

```bash
curl -s --max-time 2 http://127.0.0.1:8000/api/health   # 8000-8019 逐个试
```

- 服务未运行：请用户启动 VideoToNo（便携 exe 或源码 `python launcher.py`），不要自建服务。
- **思考型模型必须用补丁版服务**（`fix(llm): 思考链吃满输出额度…`，见 Joker4938/video-to-note）。未打补丁的服务遇到思考模型（如 mimo-v2.6-pro）会报「模型未返回正文…finish_reason=length」——那是思考链吃光 max_tokens 的预算 bug，不是内容问题。补丁版会自动改为不下发额度上限重试。

## 第 1 步：B 站登录态

服务自带独立浏览器会话（不碰 Edge 的凭据库）：

```bash
python "<本技能目录>/scripts/save_bili_creds.py"   # 登录并把凭据持久化到服务
```

登录没就绪时按服务提示走 `/api/bili-login/start` 弹窗登录。之后所有任务自动带凭据，无需再管。

> **坑**：bilibili API 的 cookie 名必须是**大写 `SESSDATA`**。小写 `sessdata` 会报「账号未登录」。脚本已做映射，手写请求时务必注意。

## 第 2 步：列收藏夹

```bash
python "<本技能目录>/scripts/list_favs.py" favs.json "技术分享"
# 输出 favs.json：{folder, videos:[{bvid, title, duration, url, ...}]}
```

标题为「已失效视频」的条目自动跳过（驱动里有判断）。

## 第 3 步：批量出笔记

```bash
export FAVS_JSON=favs.json
export VIDEOTONOTES_NOTES_DIR="D:/Develop/videotono/workspace/notes"
python "<本技能目录>/scripts/batch_notes.py"            # 全量
python "<本技能目录>/scripts/batch_notes.py" BV1xxxxxxx  # 只跑一个
```

- **串行**提交 `/api/summarize`，一个跑完再跑下一个（服务默认单任务队列；并行提交会产生孪生笔记）。
- **多分 P 视频**：条目 URL 用 `?p=N` 出单 P 笔记（归档名自动带【P{n} 分P名】）；条目加 `pages: [14,15,16,17]` 则几 P 合一篇（显式下发 `bilibili_pages`，URL 的 `?p=` 取组内首个页码作身份标记）。**绝不能裸提交多 P 总链接**——全部分 P 会合并成一篇。断点续跑按「BV+分P」去重（分 P 共用 BV，只按 BV 去重会首篇完成就跳过其余分 P）。
- 配置固定：`summary_style: "detailed"` + `processing_mode: "reuse"`（同链接已有转录直接复用，快且省 token）+ `prefer_subtitles: true` + whisper `belle-turbo-zh`。LLM 通道在脚本顶部 `SUBMIT.llm_config` 里改；`api_key: ""` 表示沿用本机已存 Key（HTTP 接口的 llm_config 必填，留空即可复用）。
- **断点续跑**：驱动以「磁盘上已有笔记的来源 BV 号」为权威完成记录（读笔记页脚 `来源：`），叠加任务历史。直接重跑即续。
  - **坑**：`/api/tasks` 只返回最近 20 条左右，绝不能只靠任务历史判断完成——重启后旧完成项会被挤出窗口、误判未做、重复出稿。磁盘笔记才是权威。
- 失败的条目只记日志继续，**不自动降级**。失败视频留给第 6 步兜底。
- 日志追加到 `batch_log.jsonl`（event: batch_start / skip_done / submitted / finished / give_up）。

## 第 4 步：内容对齐截图

```bash
python "<本技能目录>/scripts/shotfill.py"           # 扫全部缺截图的笔记
python "<本技能目录>/scripts/shotfill.py" <笔记路径>  # 单篇
```

- 解析 `## 标题 [MM:SS-MM:SS]`，每约 2 分钟跨度取 1 帧（小节中点，上限 4 帧/节），yt-dlp 下载 ≤720p，ffmpeg 抽帧。**多分 P 合并稿自动逐 P 映射**：合并时间轴按转录【P{n}】标记折算到各分 P 本地时间、逐 P 下载抽帧；只下页脚那一个 P 会把后面所有截图夹到片尾同一帧（文件名时间戳用合并时间轴，与小节区间一致）。
- **落盘走服务原生截图方案**，保证网页端/导出/磁盘三种查看方式都能显示：
  - 帧写入 `workspace/<task_id>/frames/<bvid>_sNN_MMmSSs.jpg`（与服务自带截图同目录）；
  - 归档笔记（`workspace/notes/<title>.md`）里链接为 `../<task_id>/frames/<file>`（从 notes/ 目录就地看图）；
  - 同步回写任务目录镜像 `workspace/<task_id>/notes.md`（链接 `./frames/<file>`），网页端按 `/api/image/<task_id>/<file>` 渲染。**不回写镜像，网页端就看不到图**。
  - 没有已完成服务任务的兜底笔记（agent 手写稿）用便携方案 `notes/images/<bvid>/`。
- 幂等：已有截图链接的笔记跳过补帧，但仍会**回写/刷新任务镜像**（旧版 `images/<bvid>/` 方案的笔记会自动迁移到任务 frames 方案）。批量跑时建议挂循环（每 3 分钟一轮），新笔记落盘后自动补图。
- 依赖：`yt-dlp`（pip）、`ffmpeg/ffprobe`（PATH 或 `FFMPEG`/`FFPROBE` 环境变量）。cookie 从服务在内存里取，不落盘。

## 第 5 步：质检

```bash
python "<本技能目录>/scripts/notes_audit.py" favs.json
```

标记 TWIN（孪生）/ THIN（正文过短）/ NO-SRC / NO-TS（无时间戳小节）/ NO-SHOT / NO-COVER（小节没覆盖到视频末尾）。出现即处理：孪生删掉多余的；THIN、NO-COVER 按第 6 步兜底重写。

## 第 6 步：失败兜底（不降级）

任务失败、正文残缺、空转写时，**自己写笔记**，保持同一版式：

1. **有转录**：`GET /api/task/{id}/transcript?output_format=json` 取分段转录（失败任务也保留转录，不用重跑转写）；长转录读任务产物目录的 `transcript.json` 自行切片，别整篇塞进上下文。按转录写 `## 小节标题 [MM:SS-MM:SS]` 版式笔记 + 「点评与分析」。
2. **无语音视频**（whisper 输出空/只有 `...` 占位，两次转写都空即可确认）：yt-dlp 下载视频，ffmpeg 每 3~9 秒抽帧，**逐帧读画面**（界面演示、字幕卡）复原内容，同样按时间戳分节。这正是「转写为空 ≠ 内容为空」的场景。
3. 写完补页脚（`来源：` URL 必写，续跑去重和截图回填都靠它），再跑一次 `shotfill.py` 补图。

笔记版式（与服务产稿一致）：

```markdown
# 视频笔记：《标题》

> UP主：xxx · 发布时间：xxxx · 时长：xx:xx · 播放：xxx · 点赞：xx

## 小节标题 [00:00-00:30]

正文（忠实内容，可含要点列表/数据）

## 点评与分析

…

---
来源：https://www.bilibili.com/video/BVxxxx
生成模型：（如实标注，如 agent 兜底）
笔记风格：详细笔记 + 点评分析
```

## 已知坑速查

| 症状 | 原因与处理 |
|---|---|
| bilibili 报「账号未登录」 | cookie 名用了小写 `sessdata`，必须大写 `SESSDATA` |
| 重启驱动后重复出稿 | 只靠 `/api/tasks` 判断完成；以磁盘笔记页脚为准 |
| `模型未返回正文…finish_reason=length` | 思考型模型思考链吃光 max_tokens；需补丁版服务，或换非思考模型 |
| `llm_config Field required`（HTTP 422） | HTTP 接口必填 `llm_config`；`api_key: ""` 即可沿用本机已存 Key |
| 转写只有 `...` 占位 | 视频无解说语音；走第 6 步抽帧兜底，别反复重转写 |
| 同视频出现 `-2.md` | 孪生提交；删多余的，检查是否两个驱动在跑 |
| 笔记无 `## xxx [MM:SS-MM:SS]` | 出稿没按时间戳版式（或空转写占位稿）；兜底重写 |
| 归档 .md 有截图，但网页端/任务目录里看不到 | 截图只写进了归档、没回写任务镜像；重跑 `shotfill.py` 自动迁移到 `frames/` + 镜像方案 |
| 笔记带「模型补充，未联网核验」免责声明 | 新出稿已自动联网核验改写（附（来源：…））；存量笔记跑 `verify_claims.py` 补扫，之后 `shotfill.py` 刷镜像 |
| 多分 P 合组笔记只覆盖一个 P，或各篇内容串成同一讲 | 旧版服务转录复用按整 BV 匹配（`?p=14` 吃掉 `?p=33` 的转录）、单页字幕捷径截断合并范围；7cdfec6 起复用对齐 `?p=`+勾选范围、显式 `bilibili_pages` 直走合并通道。**质检必看**：各篇小节主题对得上分 P 标题；小节时间轴要覆盖全片（对照笔记头「时长」，超过 90 秒空洞/尾部缺口即是漏内容，转录里有、稿子没写） |
| 删了笔记想强制重跑，驱动却直接 skip_done | 驱动的完成判定=磁盘笔记∪任务历史；任务目录还在时历史照样算完成。强制重生成要直接 `POST /api/summarize`（processing_mode=reuse 会复用任务目录里的转录，只重跑出稿），或先把任务目录移出 workspace |
| 控制台 UnicodeEncodeError | Windows GBK；脚本已 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` |
| 并行提交任务 | 服务默认单任务队列；串行等待，避免孪生与 GPU 争抢 |

## 脚本清单

| 脚本 | 作用 | 关键环境变量 |
|---|---|---|
| `save_bili_creds.py` | 登录并持久化 B 站凭据到服务 | `VIDEOTONO_SERVICE` |
| `list_favs.py` | 列收藏夹 → `favs.json`（argv: 输出路径、收藏夹名） | `VIDEOTONO_SERVICE` |
| `batch_notes.py` | 串行批量出笔记，磁盘笔记断点续跑 | `FAVS_JSON` `VIDEOTONOTES_NOTES_DIR` `BATCH_LOG` |
| `shotfill.py` | 按时间戳回填内容对齐截图 | `VIDEOTONOTES_NOTES_DIR` `FFMPEG` `FFPROBE` |
| `notes_audit.py` | 质检：孪生/残缺/缺图/覆盖不足 | `VIDEOTONOTES_NOTES_DIR` |
| `verify_claims.py` | 存量笔记「模型补充」联网核验改写（用服务同款 .venv python 跑） | `VIDEOTONOTES_NOTES_DIR` `VIDEOTONOTONOTE_SRC` |

全部脚本：Windows 上用 `python`（不要 `python3`），服务地址默认 `http://127.0.0.1:8000`。
