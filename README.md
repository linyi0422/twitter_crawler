# Twitter Crawler Toolkit

用于抓取单个 X(Twitter) 账号推文并下载图片的可复用工具集。

## Features

- 按用户名抓取推文（支持 `limit`，最多可到约 3200）
- 从 JSONL 批量下载图片（`media.photos`）
- 图片按时间重命名：`YYYYMMDD_HHMMSS_tweetId_idx.ext`
- 一键流水线：`抓取 -> 下载图片 -> 重命名`

## Project Layout

- `scripts/crawl_x_sync.py`: 主抓取脚本（推荐）
- `scripts/download_photos_from_jsonl.py`: 下载图片
- `scripts/rename_photos_by_tweet_date.py`: 重命名图片
- `scripts/run_pipeline.py`: 一键流水线
- `scripts/crawl_twitter_user.py`: twscrape 兼容脚本
- `state/accounts.db`: twscrape 本地状态库（默认忽略，不上传）
- `output/`: 抓取结果目录（默认忽略，不上传）

## Install

```powershell
python -m pip install -r requirements.txt
```

## Quick Start

在 `twitter_crawler` 目录执行：

```powershell
python scripts/run_pipeline.py `
  --username omokage_AIsOK `
  --limit 1000 `
  --auth-token <YOUR_AUTH_TOKEN> `
  --ct0 <YOUR_CT0>
```

输出将出现在：

- `output/<username>/<username>_<limit>.jsonl`
- `output/<username>/<username>_<limit>_photos/`

## Step-by-Step

1. 抓推文

```powershell
python scripts/crawl_x_sync.py `
  --username omokage_AIsOK `
  --limit 1000 `
  --output output/omokage_AIsOK/omokage_AIsOK_1000.jsonl `
  --auth-token <YOUR_AUTH_TOKEN> `
  --ct0 <YOUR_CT0>
```

2. 下载图片

```powershell
python scripts/download_photos_from_jsonl.py `
  --input output/omokage_AIsOK/omokage_AIsOK_1000.jsonl `
  --output-dir output/omokage_AIsOK/omokage_AIsOK_1000_photos
```

3. 重命名图片

```powershell
python scripts/rename_photos_by_tweet_date.py `
  --jsonl output/omokage_AIsOK/omokage_AIsOK_1000.jsonl `
  --dir output/omokage_AIsOK/omokage_AIsOK_1000_photos
```

## Notes

- 如果出现 `401 Could not authenticate you`，请刷新并更新 `auth_token + ct0`。
- 当前图片下载只覆盖 `media.photos`，不包含视频/GIF 文件。
- 请勿把 `auth_token`、`ct0`、`accounts.db`、`output/` 上传到公开仓库。
