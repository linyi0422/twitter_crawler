# Twitter Crawler Toolkit

Reusable scripts to crawl one X (Twitter) account, download photo media, and rename files after download.

## Features

- Crawl tweets by username (`limit` supported, practical max around 3200).
- Download `media.photos` from JSONL.
- Post-download rename with blogger ID:
  - `<author_id>_<YYYYMMDD>_<HHMMSS>_<tweetId>_<idx>.ext`
- One-command pipeline:
  - `crawl -> download -> rename`

## Project Layout

- `scripts/crawl_x_sync.py`: main crawler.
- `scripts/download_photos_from_jsonl.py`: photo downloader.
- `scripts/rename_photos_by_handle_date.py`: post-download renamer (`--author-id` supported).
- `scripts/run_pipeline.py`: end-to-end pipeline.
- `scripts/fetch_top5_for_handles.py`: multi-handle crawl + top-5 selection helper.
- `output/`: local output (git ignored).
- `state/accounts.db`: local twscrape state (git ignored).

## Install

```powershell
python -m pip install -r requirements.txt
```

## Quick Start

Run in `twitter_crawler`:

```powershell
python scripts/run_pipeline.py `
  --username omokage_AIsOK `
  --author-id KARINE `
  --limit 1000 `
  --auth-token <YOUR_AUTH_TOKEN> `
  --ct0 <YOUR_CT0>
```

Output:

- `output/<username>/<username>_<limit>.jsonl`
- `output/<username>/<username>_<limit>_photos/`

## Step by Step

1. Crawl tweets

```powershell
python scripts/crawl_x_sync.py `
  --username omokage_AIsOK `
  --limit 1000 `
  --output output/omokage_AIsOK/omokage_AIsOK_1000.jsonl `
  --auth-token <YOUR_AUTH_TOKEN> `
  --ct0 <YOUR_CT0>
```

2. Download photos

```powershell
python scripts/download_photos_from_jsonl.py `
  --input output/omokage_AIsOK/omokage_AIsOK_1000.jsonl `
  --output-dir output/omokage_AIsOK/omokage_AIsOK_1000_photos
```

3. Rename photos by blogger ID

```powershell
python scripts/rename_photos_by_handle_date.py `
  --jsonl output/omokage_AIsOK/omokage_AIsOK_1000.jsonl `
  --dir output/omokage_AIsOK/omokage_AIsOK_1000_photos `
  --author-id KARINE
```

## Notes

- If you get `401 Could not authenticate you`, refresh `auth_token` + `ct0`.
- Current downloader only handles `media.photos`, not video/GIF files.
- Do not commit `auth_token`, `ct0`, `accounts.db`, or `output/` to public repos.
