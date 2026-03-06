import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

from export_article_md import (
    build_article_markdown,
    build_plain_tweet_markdown,
    fetch_tweet_detail,
    get_latest_tweet_detail_op,
    load_cookies_from_db,
    make_client,
    parse_tweet_id,
    walk_find_tweet,
)


def parse_source_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    # keep order while removing duplicates
    out = []
    seen = set()
    for s in lines:
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def to_tweet_id(item: str) -> str:
    if item.isdigit():
        return item
    return parse_tweet_id(item)


def main():
    default_db = Path(__file__).resolve().parents[1] / "state" / "accounts.db"
    default_out = Path(__file__).resolve().parents[1] / "output" / "single_post"

    parser = argparse.ArgumentParser(description="Batch export X posts/articles to Markdown + images.")
    parser.add_argument("--url-file", required=True, help="Text file with X status URLs, one per line.")
    parser.add_argument("--output-root", default=str(default_out))
    parser.add_argument("--auth-token", default=None)
    parser.add_argument("--ct0", default=None)
    parser.add_argument("--db", default=str(default_db))
    parser.add_argument("--save-raw", action="store_true")
    parser.add_argument("--sleep-seconds", type=float, default=0.5)
    args = parser.parse_args()

    url_file = Path(args.url_file)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    items = parse_source_lines(url_file)
    if not items:
        raise SystemExit(f"No valid lines found in: {url_file}")

    if args.auth_token and args.ct0:
        auth_token, ct0 = args.auth_token, args.ct0
        user_agent = "Mozilla/5.0"
    else:
        auth_token, ct0, user_agent = load_cookies_from_db(Path(args.db))

    reports = []
    ok_count = 0
    fail_count = 0

    with make_client(auth_token, ct0, user_agent) as client:
        op_tweet_detail = get_latest_tweet_detail_op(client)

        for idx, item in enumerate(items, start=1):
            started = time.time()
            try:
                tweet_id = to_tweet_id(item)
                out_dir = output_root / tweet_id
                out_dir.mkdir(parents=True, exist_ok=True)

                payload = fetch_tweet_detail(client, tweet_id, op_tweet_detail)
                if args.save_raw:
                    raw = out_dir / f"{tweet_id}_tweetdetail_raw.json"
                    raw.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

                tweet_obj = walk_find_tweet(payload, tweet_id)
                if not tweet_obj:
                    raise RuntimeError("Cannot find focal tweet in response")

                has_article = bool(((tweet_obj.get("article") or {}).get("article_results") or {}).get("result"))
                if has_article:
                    out_md, image_count, meta = build_article_markdown(client, tweet_obj, tweet_id, out_dir)
                else:
                    out_md, image_count, meta = build_plain_tweet_markdown(client, tweet_obj, tweet_id, out_dir)

                meta_path = out_dir / "meta.json"
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

                ok_count += 1
                reports.append(
                    {
                        "order": idx,
                        "input": item,
                        "tweet_id": tweet_id,
                        "status": "ok",
                        "mode": meta.get("mode", ""),
                        "title": meta.get("title", ""),
                        "images": image_count,
                        "md_path": str(out_md.resolve()),
                        "meta_path": str(meta_path.resolve()),
                        "seconds": round(time.time() - started, 2),
                        "error": "",
                    }
                )
                print(f"[{idx}/{len(items)}] ok: {tweet_id} images={image_count}")
            except Exception as e:
                fail_count += 1
                reports.append(
                    {
                        "order": idx,
                        "input": item,
                        "tweet_id": "",
                        "status": "failed",
                        "mode": "",
                        "title": "",
                        "images": 0,
                        "md_path": "",
                        "meta_path": "",
                        "seconds": round(time.time() - started, 2),
                        "error": str(e),
                    }
                )
                print(f"[{idx}/{len(items)}] failed: {item} -> {e}")
            time.sleep(max(args.sleep_seconds, 0.0))

    report_dir = output_root / "batch_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_csv = report_dir / f"batch_report_{stamp}.csv"
    report_json = report_dir / f"batch_report_{stamp}.json"

    fields = [
        "order",
        "input",
        "tweet_id",
        "status",
        "mode",
        "title",
        "images",
        "md_path",
        "meta_path",
        "seconds",
        "error",
    ]
    with report_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in reports:
            writer.writerow(r)
    report_json.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"done: ok={ok_count}, failed={fail_count}")
    print(f"report_csv: {report_csv.resolve()}")
    print(f"report_json: {report_json.resolve()}")


if __name__ == "__main__":
    main()
