import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx


def file_name_from_url(url: str, tweet_id: str, idx: int) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix or ".jpg"
    stem = Path(path).stem or f"{tweet_id}_{idx}"
    return f"{tweet_id}_{idx}_{stem}{ext}"


def main():
    parser = argparse.ArgumentParser(description="Download photo media from twscrape JSONL")
    parser.add_argument("--input", required=True, help="Input JSONL path")
    parser.add_argument("--output-dir", required=True, help="Output directory for photos")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    seen = set()
    saved = 0
    skipped = 0

    with httpx.Client(timeout=args.timeout, follow_redirects=True) as client:
        with input_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                tweet_id = str(obj.get("id", "unknown"))
                photos = ((obj.get("media") or {}).get("photos") or [])
                for i, ph in enumerate(photos, start=1):
                    url = ph.get("url")
                    if not url:
                        skipped += 1
                        continue
                    if url in seen:
                        skipped += 1
                        continue
                    seen.add(url)

                    name = file_name_from_url(url, tweet_id, i)
                    dst = out_dir / name

                    if dst.exists():
                        skipped += 1
                        continue

                    rep = client.get(url)
                    if rep.status_code != 200:
                        skipped += 1
                        continue

                    dst.write_bytes(rep.content)
                    saved += 1

    print(f"done: saved={saved}, skipped={skipped}, dir={out_dir.resolve()}")


if __name__ == "__main__":
    main()
