#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path

import httpx


BASE_API = os.getenv("TRENDING_API_BASE_URL", "https://reply-vc-90459984647.us-central1.run.app")
WINDOW_CHOICES = ("24h", "7d", "14d", "all")
SORT_CHOICES = (
    "views",
    "likes",
    "retweets",
    "replies",
    "quotes",
    "bookmarks",
    "postTime",
    "wordCount",
    "readingTime",
)


def parse_csv_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def to_int(value) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def parse_time_key(value: str | None) -> str:
    return (value or "").strip()


def build_x_url(entry: dict) -> str:
    tweet_id = str(entry.get("tweetId") or "").strip()
    author = entry.get("author") or {}
    handle = str(author.get("handle") or "").strip().lstrip("@")
    if handle and tweet_id:
        return f"https://x.com/{handle}/status/{tweet_id}"
    if tweet_id:
        return f"https://x.com/i/web/status/{tweet_id}"
    return ""


def pass_filters(
    entry: dict,
    langs: set[str],
    regions: set[str],
    category: str | None,
    tags: set[str],
    topic: str | None,
) -> bool:
    author = entry.get("author") or {}
    entry_langs = set((entry.get("langsDetected") or []))
    entry_region = str(author.get("accountBasedIn") or "").strip()
    entry_category = str(entry.get("category") or "").strip()
    entry_tags = set(entry.get("tags") or [])
    entry_topics = set(entry.get("trendingTopics") or [])

    if langs and not (entry_langs & langs):
        return False
    if regions and entry_region not in regions:
        return False
    if category and entry_category != category:
        return False
    if tags and not tags.issubset(entry_tags):
        return False
    if topic and topic not in entry_topics:
        return False
    return True


def sort_key(entry: dict, sort_by: str):
    if sort_by == "postTime":
        return (
            parse_time_key(entry.get("tweetCreatedAt")),
            to_int(entry.get("viewCount")),
        )

    field_map = {
        "views": "viewCount",
        "likes": "likeCount",
        "retweets": "retweetCount",
        "replies": "replyCount",
        "quotes": "quoteCount",
        "bookmarks": "bookmarkCount",
        "wordCount": "wordCount",
        "readingTime": "readingTimeMinutes",
    }
    field = field_map.get(sort_by, "viewCount")
    return (
        to_int(entry.get(field)),
        to_int(entry.get("viewCount")),
        parse_time_key(entry.get("tweetCreatedAt")),
    )


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "rank",
        "tweetId",
        "authorHandle",
        "authorName",
        "authorRegion",
        "title",
        "tweetCreatedAt",
        "viewCount",
        "likeCount",
        "retweetCount",
        "replyCount",
        "quoteCount",
        "bookmarkCount",
        "wordCount",
        "readingTimeMinutes",
        "category",
        "langsDetected",
        "tags",
        "trendingTopics",
        "xUrl",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main() -> None:
    default_out = Path(__file__).resolve().parents[1] / "output" / "twitter_trending"
    parser = argparse.ArgumentParser(
        description="Fetch ranking entries and export X URL list for markdown pipeline."
    )
    parser.add_argument("--window", choices=WINDOW_CHOICES, default="7d")
    parser.add_argument("--lang", default="en,zh")
    parser.add_argument("--region", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--sort", choices=SORT_CHOICES, default="views")
    parser.add_argument("--limit", type=int, default=3, help="Top N after filtering.")
    parser.add_argument("--api-limit", type=int, default=10000, help="Server query limit.")
    parser.add_argument("--output-root", default=str(default_out))
    parser.add_argument("--run-name", default=datetime.now().strftime("top3_%Y%m%d"))
    parser.add_argument("--x-url-file", default="", help="Optional path for X URL list txt.")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()

    run_dir = Path(args.output_root) / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    langs = set(parse_csv_list(args.lang))
    regions = set(parse_csv_list(args.region))
    tags = set(parse_csv_list(args.tags))
    category = args.category.strip() or None
    topic = args.topic.strip() or None

    with httpx.Client(timeout=args.timeout_seconds) as client:
        resp = client.get(
            f"{BASE_API}/v1/articles/leaderboard",
            params={
                "window": args.window,
                "sortBy": "views",
                "limit": args.api_limit,
            },
        )
        resp.raise_for_status()
        payload = resp.json()

    all_entries = payload.get("entries") or []
    filtered = [
        e for e in all_entries if pass_filters(e, langs, regions, category, tags, topic)
    ]
    sorted_entries = sorted(filtered, key=lambda e: sort_key(e, args.sort), reverse=True)
    selected = sorted_entries[: max(args.limit, 0)]

    rows = []
    x_urls = []
    for i, e in enumerate(selected, start=1):
        author = e.get("author") or {}
        x_url = build_x_url(e)
        if x_url:
            x_urls.append(x_url)

        rows.append(
            {
                "rank": i,
                "tweetId": e.get("tweetId"),
                "authorHandle": author.get("handle"),
                "authorName": author.get("name"),
                "authorRegion": author.get("accountBasedIn"),
                "title": e.get("title"),
                "tweetCreatedAt": e.get("tweetCreatedAt"),
                "viewCount": e.get("viewCount"),
                "likeCount": e.get("likeCount"),
                "retweetCount": e.get("retweetCount"),
                "replyCount": e.get("replyCount"),
                "quoteCount": e.get("quoteCount"),
                "bookmarkCount": e.get("bookmarkCount"),
                "wordCount": e.get("wordCount"),
                "readingTimeMinutes": e.get("readingTimeMinutes"),
                "category": e.get("category"),
                "langsDetected": ",".join(e.get("langsDetected") or []),
                "tags": ",".join(e.get("tags") or []),
                "trendingTopics": ",".join(e.get("trendingTopics") or []),
                "xUrl": x_url,
            }
        )

    dedup_urls = []
    seen = set()
    for u in x_urls:
        if u in seen:
            continue
        seen.add(u)
        dedup_urls.append(u)

    raw_path = run_dir / "raw_leaderboard.json"
    selected_path = run_dir / "selected_entries.json"
    csv_path = run_dir / "selected_entries.csv"
    x_url_path = Path(args.x_url_file) if args.x_url_file else (run_dir / "x_urls.txt")
    summary_path = run_dir / "summary.md"
    meta_path = run_dir / "meta.json"

    raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    selected_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, rows)
    x_url_path.write_text("\n".join(dedup_urls) + ("\n" if dedup_urls else ""), encoding="utf-8")

    summary_lines = [
        "# X Articles Trending Export",
        "",
        f"- run_name: {args.run_name}",
        f"- window: {args.window}",
        f"- sort: {args.sort}",
        f"- selected_entries: {len(selected)}",
        f"- unique_x_urls: {len(dedup_urls)}",
        "",
        "## Top Preview",
        "",
    ]
    for r in rows[:10]:
        title = (r.get("title") or "").replace("\n", " ").strip()
        if len(title) > 120:
            title = title[:117] + "..."
        summary_lines.append(
            f"- {r['rank']}. {r.get('xUrl') or '(missing url)'} | views={r.get('viewCount')} | {title}"
        )
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    meta = {
        "run_name": args.run_name,
        "window": args.window,
        "sort": args.sort,
        "lang_filter": sorted(langs),
        "region_filter": sorted(regions),
        "category_filter": category,
        "tags_filter": sorted(tags),
        "topic_filter": topic,
        "selected_entries": len(selected),
        "unique_x_urls": len(dedup_urls),
        "paths": {
            "raw_leaderboard_json": str(raw_path.resolve()),
            "selected_entries_json": str(selected_path.resolve()),
            "selected_entries_csv": str(csv_path.resolve()),
            "x_urls_txt": str(x_url_path.resolve()),
            "summary_md": str(summary_path.resolve()),
        },
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"done: {run_dir.resolve()}")
    print(f"selected_entries: {len(selected)}")
    print(f"unique_x_urls: {len(dedup_urls)}")
    print(f"x_urls_txt: {x_url_path.resolve()}")


if __name__ == "__main__":
    main()
