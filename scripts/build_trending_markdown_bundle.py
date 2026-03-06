#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def fmt_num(value) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return "0"


def sanitize_filename_part(text: str, fallback: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_\-]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or fallback


def strip_source_header(md_text: str) -> str:
    """
    Drop the exporter header:
    - # title
    - top bullet metadata lines
    """
    lines = md_text.splitlines()
    i = 0
    if i < len(lines) and lines[i].startswith("# "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and lines[i].lstrip().startswith("- "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    body = "\n".join(lines[i:]).strip()
    return body + "\n" if body else ""


def parse_hot_date(run_dir: Path, explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    m = re.search(r"(\d{8})", run_dir.name)
    if m:
        raw = m.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return datetime.now().strftime("%Y-%m-%d")


def rewrite_and_copy_images(
    markdown_body: str,
    source_post_dir: Path,
    output_dir: Path,
    tweet_id: str,
) -> str:
    """
    Copy local images into final package and rewrite markdown links so the
    package is self-contained.
    """
    image_out_dir = output_dir / "images" / tweet_id
    image_out_dir.mkdir(parents=True, exist_ok=True)

    def repl(m: re.Match[str]) -> str:
        alt = m.group(1)
        src = m.group(2).strip()

        if re.match(r"^(https?:)?//", src):
            return m.group(0)

        src_path = (source_post_dir / src).resolve()
        if not src_path.exists():
            src_path = (source_post_dir / src.lstrip("./")).resolve()
        if not src_path.exists():
            return m.group(0)

        dst = image_out_dir / src_path.name
        if not dst.exists():
            shutil.copy2(src_path, dst)

        rel = f"images/{tweet_id}/{dst.name}"
        return f"![{alt}]({rel})"

    return IMG_RE.sub(repl, markdown_body)


def find_post_markdown(markdown_root: Path, tweet_id: str) -> Path | None:
    candidates = [
        markdown_root / tweet_id / f"{tweet_id}.zh.md",
        markdown_root / tweet_id / f"{tweet_id}.md",
        markdown_root / "posts" / tweet_id / f"{tweet_id}.zh.md",
        markdown_root / "posts" / tweet_id / f"{tweet_id}.md",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def build_post_markdown(
    entry: dict,
    body: str,
    trending_name: str,
    rank: int,
) -> str:
    title = (entry.get("title") or "").strip() or f"文章 {rank}"
    author = ((entry.get("author") or {}).get("handle") or "").strip().lstrip("@")
    tweet_id = str(entry.get("tweetId") or "").strip()
    source_url = ""
    if author and tweet_id:
        source_url = f"https://x.com/{author}/status/{tweet_id}"
    elif tweet_id:
        source_url = f"https://x.com/i/web/status/{tweet_id}"

    lines = [
        f"# {title}（全文精译）",
        "",
        f"- **原文链接**：{source_url}" if source_url else "- **原文链接**：未知",
        f"- **作者**：`@{author}`" if author else "- **作者**：未知",
        f"- **{trending_name} 排名**：#{rank}",
        (
            f"- **{trending_name} 热度**："
            f"浏览 {fmt_num(entry.get('viewCount'))} | "
            f"点赞 {fmt_num(entry.get('likeCount'))} | "
            f"转推 {fmt_num(entry.get('retweetCount'))} | "
            f"评论 {fmt_num(entry.get('replyCount'))} | "
            f"收藏 {fmt_num(entry.get('bookmarkCount'))}"
        ),
        f"- **原文字数（英文词）**：{int(entry.get('wordCount') or 0)}",
        f"- **阅读时长**：{int(entry.get('readingTimeMinutes') or 0)} 分钟",
        "",
        "## 全文精译",
        "",
        body.strip(),
        "",
    ]
    return "\n".join(lines).strip() + "\n"


def clean_output_dir(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    for p in output_dir.glob("POST_*.md"):
        if p.is_file():
            p.unlink()
    for name in ("FULL_COMBINED.md", "GUIDE_INTRO.md", "bundle_manifest.json"):
        f = output_dir / name
        if f.exists():
            f.unlink()
    images = output_dir / "images"
    if images.exists() and images.is_dir():
        shutil.rmtree(images)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build final Chinese markdown bundle (guide + 3 posts + combined)."
    )
    parser.add_argument("--run-dir", required=True, help="Run directory")
    parser.add_argument("--selected-entries", default="", help="Default: <run-dir>/selected_entries.json")
    parser.add_argument("--markdown-root", default="", help="Default: <run-dir>/markdown_top3")
    parser.add_argument("--output-dir", default="", help="Default: <run-dir>/final_full_zh")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--period-label", default="本周")
    parser.add_argument("--hot-date", default="")
    parser.add_argument("--hot-type", default="X（Twitter）长文热门")
    parser.add_argument("--trending-name", default="X Articles Trending")
    parser.add_argument(
        "--reading-advice",
        default="先看第 2 篇（实操），再看第 1 篇（方法论），最后第 3 篇（量化进阶）。",
    )
    parser.add_argument("--cta-line-1", default="公众号会持续更新有深度的推特长文")
    parser.add_argument(
        "--cta-line-2",
        default="如果你是非 AI 技术专业人士，想要把自己的技能移植到自己的工作流中，请加微信",
    )
    parser.add_argument("--wechat-id", default="")
    parser.add_argument("--clean-output", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    selected_entries_path = (
        Path(args.selected_entries).resolve()
        if args.selected_entries
        else (run_dir / "selected_entries.json")
    )
    markdown_root = (
        Path(args.markdown_root).resolve()
        if args.markdown_root
        else (run_dir / "markdown_top3")
    )
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else (run_dir / "final_full_zh")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.clean_output:
        clean_output_dir(output_dir)

    if not selected_entries_path.exists():
        raise SystemExit(f"selected entries not found: {selected_entries_path}")

    entries = json.loads(read_text(selected_entries_path))
    if not isinstance(entries, list) or not entries:
        raise SystemExit("selected entries is empty")

    entries_sorted = sorted(
        entries,
        key=lambda x: int(x.get("rank") or 10**9),
    )
    selected = entries_sorted[: max(args.top_k, 1)]
    hot_date = parse_hot_date(run_dir, args.hot_date)

    total_words = sum(int(x.get("wordCount") or 0) for x in selected)
    total_minutes = sum(int(x.get("readingTimeMinutes") or 0) for x in selected)

    post_rows: list[dict] = []
    for idx, entry in enumerate(selected, start=1):
        tweet_id = str(entry.get("tweetId") or "").strip()
        if not tweet_id:
            continue

        src_md = find_post_markdown(markdown_root, tweet_id)
        if not src_md:
            raise SystemExit(f"missing markdown for tweet {tweet_id} under {markdown_root}")

        body = strip_source_header(read_text(src_md))
        body = rewrite_and_copy_images(body, src_md.parent, output_dir, tweet_id)

        title = (entry.get("title") or "").strip() or tweet_id
        safe_title = sanitize_filename_part(title, fallback=tweet_id)
        post_name = f"POST_{idx:02d}_{safe_title}.md"
        post_path = output_dir / post_name

        post_md = build_post_markdown(
            entry=entry,
            body=body,
            trending_name=args.trending_name,
            rank=idx,
        )
        write_text(post_path, post_md)

        author = ((entry.get("author") or {}).get("handle") or "").strip().lstrip("@")
        source_url = (
            f"https://x.com/{author}/status/{tweet_id}"
            if author
            else f"https://x.com/i/web/status/{tweet_id}"
        )
        post_rows.append(
            {
                "index": idx,
                "rank": int(entry.get("rank") or idx),
                "tweet_id": tweet_id,
                "title": title,
                "source_url": source_url,
                "post_file": post_name,
                "word_count": int(entry.get("wordCount") or 0),
                "reading_minutes": int(entry.get("readingTimeMinutes") or 0),
            }
        )

    if not post_rows:
        raise SystemExit("no posts were built")

    wechat_tail = f"：`{args.wechat_id}`" if args.wechat_id.strip() else ""
    guide_lines = [
        f"# {args.trending_name} {args.period_label} Top {len(post_rows)} 全文精译合集",
        "",
        f"> 日期：{hot_date}",
        "",
        "## 导读",
        "",
        f"- 热门日期（抓取）：{hot_date}",
        f"- 热门类型：{args.hot_type}",
        f"- 总原文字数（英文词）：{total_words}",
        f"- 总阅读时长：{total_minutes} 分钟",
        f"- 阅读建议：{args.reading_advice}",
        "",
        "## 关注与交流",
        "",
        f"- {args.cta_line_1}",
        f"- {args.cta_line_2}{wechat_tail}",
        "",
        "---",
        "",
        "## 快速导航",
        "",
    ]
    for row in post_rows:
        guide_lines.append(f"- [{row['index']}. {row['title']}]({row['post_file']})")

    guide_text = "\n".join(guide_lines).strip() + "\n"
    guide_path = output_dir / "GUIDE_INTRO.md"
    write_text(guide_path, guide_text)

    combined_lines = [guide_text.strip(), "", "---", ""]
    for row in post_rows:
        post_text = read_text(output_dir / row["post_file"]).strip()
        combined_lines.extend(
            [
                f"## {row['index']}. {row['title']}",
                "",
                f"- **原文**：{row['source_url']}",
                f"- **{args.trending_name} 排名**：#{row['rank']}",
                f"- **单篇全文**：[点击查看]({row['post_file']})",
                "",
                "> 以下为该文全文精译：",
                "",
                post_text,
                "",
                "---",
                "",
            ]
        )

    full_path = output_dir / "FULL_COMBINED.md"
    write_text(full_path, "\n".join(combined_lines).strip() + "\n")

    manifest = {
        "run_dir": str(run_dir),
        "selected_entries": str(selected_entries_path),
        "markdown_root": str(markdown_root),
        "output_dir": str(output_dir),
        "files": {
            "combined": str(full_path),
            "guide": str(guide_path),
            "posts": [str(output_dir / row["post_file"]) for row in post_rows],
        },
        "stats": {
            "post_count": len(post_rows),
            "total_word_count_en": total_words,
            "total_reading_minutes": total_minutes,
            "hot_date": hot_date,
            "hot_type": args.hot_type,
        },
    }
    manifest_path = output_dir / "bundle_manifest.json"
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
