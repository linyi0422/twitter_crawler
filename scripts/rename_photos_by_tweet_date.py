import argparse
import json
import re
from datetime import datetime
from pathlib import Path


NAME_RE = re.compile(r"^(?P<tid>\d+)_(?P<idx>\d+)(?:_.*)?(?P<ext>\.[A-Za-z0-9]+)$")


def load_tweet_dates(jsonl_path: Path) -> dict[str, str]:
    res: dict[str, str] = {}
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            tid = str(obj.get("id", ""))
            date_str = obj.get("date")
            if not tid or not date_str:
                continue
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            res[tid] = dt.strftime("%Y%m%d_%H%M%S")
    return res


def unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        cand = path.with_name(f"{stem}_dup{i}{suffix}")
        if not cand.exists():
            return cand
        i += 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Rename downloaded X photos by tweet date + tweet id.")
    parser.add_argument("--jsonl", required=True, help="Tweet JSONL file")
    parser.add_argument("--dir", required=True, help="Photo directory to rename files in")
    args = parser.parse_args()

    jsonl_path = Path(args.jsonl)
    photo_dir = Path(args.dir)
    mapping = load_tweet_dates(jsonl_path)

    renamed = 0
    skipped = 0

    for p in photo_dir.iterdir():
        if not p.is_file():
            continue
        m = NAME_RE.match(p.name)
        if not m:
            skipped += 1
            continue

        tid = m.group("tid")
        idx = int(m.group("idx"))
        ext = m.group("ext").lower()
        dt_part = mapping.get(tid, "unknown_date")
        new_name = f"{dt_part}_{tid}_{idx:02d}{ext}"
        dst = unique_target(photo_dir / new_name)

        if dst.name == p.name:
            skipped += 1
            continue

        p.rename(dst)
        renamed += 1

    print(f"done: renamed={renamed}, skipped={skipped}, dir={photo_dir.resolve()}")


if __name__ == "__main__":
    main()
