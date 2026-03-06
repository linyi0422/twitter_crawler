# Twitter Crawler Toolkit

一个面向 `X (Twitter)` 长文内容的实用工具集，支持从热门榜抓取到最终中文精译 Markdown 的一键流水线。

## 核心能力

- 抓取 `X` 长文热门 Top N（默认 Top 3）
- 批量导出长文 Markdown 与图片
- 全文翻译为中文（保留 Markdown 结构、链接、术语）
- 自动打包为最终交付格式：
  - `FULL_COMBINED.md`（全文合并版）
  - `GUIDE_INTRO.md`（导读）
  - `POST_01...POST_03...`（分篇版）

## 安装

```powershell
python -m pip install -r requirements.txt
```

## 一键运行（推荐）

在 `twitter_crawler` 目录执行：

```powershell
python scripts/run_trending_tool.py `
  --run-date 2026-03-05 `
  --top-k 3 `
  --auth-token <YOUR_AUTH_TOKEN> `
  --ct0 <YOUR_CT0> `
  --translator auto `
  --openai-api-key <YOUR_OPENAI_API_KEY> `
  --clean-output
```

说明：

- `--translator auto`：有 OpenAI Key 时走 OpenAI，无 Key 时走公开翻译源（更慢）。
- `--clean-output`：每次打包前清理旧的最终文件，避免重复版本。

## 输出结构

默认输出根目录：`output/twitter_trending/`

单次运行目录示例：`output/twitter_trending/top3_20260305/`

- `selected_entries.json`：热门条目
- `x_urls.txt`：待抓取 X 链接列表
- `markdown_top3/`：原文 Markdown + 图片 + 批处理报告
- `zh_draft/`：中文翻译中间产物
- `final_full_zh/`：最终交付目录
  - `FULL_COMBINED.md`
  - `GUIDE_INTRO.md`
  - `POST_01_*.md` `POST_02_*.md` `POST_03_*.md`
  - `images/`（最终包内图片）

## 手动分步运行

### 1) 抓取热门并生成 URL 列表

```powershell
python scripts/fetch_trending_rankings.py `
  --window 7d `
  --lang en,zh `
  --sort views `
  --limit 3 `
  --output-root output/twitter_trending `
  --run-name top3_20260305
```

### 2) 导出 Markdown

```powershell
python scripts/batch_export_x_md.py `
  --url-file output/twitter_trending/top3_20260305/x_urls.txt `
  --output-root output/twitter_trending/top3_20260305/markdown_top3 `
  --auth-token <YOUR_AUTH_TOKEN> `
  --ct0 <YOUR_CT0>
```

### 3) 翻译为中文

```powershell
python scripts/translate_md_to_zh_wechat.py `
  --report-json output/twitter_trending/top3_20260305/markdown_top3/batch_reports/<LATEST>.json `
  --output-root output/twitter_trending/top3_20260305/zh_draft `
  --translator auto `
  --openai-api-key <YOUR_OPENAI_API_KEY>
```

### 4) 打包最终 Markdown

```powershell
python scripts/build_trending_markdown_bundle.py `
  --run-dir output/twitter_trending/top3_20260305 `
  --markdown-root output/twitter_trending/top3_20260305/markdown_top3 `
  --output-dir output/twitter_trending/top3_20260305/final_full_zh `
  --period-label 本周 `
  --hot-date 2026-03-05 `
  --trending-name "X Articles Trending" `
  --wechat-id N742746391 `
  --clean-output
```

## 旧版能力（仍可用）

仍保留基础抓取流水线：

- `scripts/crawl_x_sync.py`
- `scripts/download_photos_from_jsonl.py`
- `scripts/rename_photos_by_handle_date.py`
- `scripts/run_pipeline.py`

## 安全提醒

- 不要提交 `auth_token`、`ct0`、数据库与 `output/` 产物。
- `.gitignore` 已默认忽略本地运行产物与敏感目录。
