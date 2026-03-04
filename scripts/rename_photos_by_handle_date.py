import argparse
import json
import re
from datetime import datetime
from pathlib import Path


# old format 1: <tweetid>_<idx>_<stem>.jpg
P_RAW = re.compile(r"^(?P<tid>\d+)_(?P<idx>\d+)(?:_.*)?(?P<ext>\.[A-Za-z0-9]+)$")
# old format 2: <YYYYMMDD>_<HHMMSS>_<tweetid>_<idx>.jpg
P_DATE_TID = re.compile(
    r"^(?P<d>\d{8})_(?P<t>\d{6})_(?P<tid>\d+)_(?P<idx>\d+)(?:_.*)?(?P<ext>\.[A-Za-z0-9]+)$"
)
# target format: <author_id>_<YYYYMMDD>_<HHMMSS>_<tweetid>_<idx>.jpg
P_TARGET = re.compile(
    r"^(?P<h>[A-Za-z0-9_]+)_(?P<d>\d{8})_(?P<t>\d{6})_(?P<tid>\d+)_(?P<idx>\d+)(?:_.*)?(?P<ext>\.[A-Za-z0-9]+)$"
)


def sanitize_author_id(value: str) -> str:
    v = re.sub(r"\s+", "_", value.strip())
    v = re.sub(r"[^A-Za-z0-9_]", "_", v)
    v = re.sub(r"_+", "_", v).strip("_")
    return v or "unknown_author"


def load_tweet_meta(jsonl_path: Path) -> dict[str, tuple[str, str]]:
    meta: dict[str, tuple[str, str]] = {}
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            tid = str(obj.get("id", ""))
            date_str = obj.get("date")
            handle = str((obj.get("user") or {}).get("username") or "").strip()
            if not tid or not date_str or not handle:
                continue
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            meta[tid] = (handle, dt.strftime("%Y%m%d_%H%M%S"))
    return meta


def parse_file(file_name: str):
    m = P_TARGET.match(file_name)
    if m:
        return m.group("tid"), int(m.group("idx")), m.group("ext").lower(), True, m.group("h")

    m = P_DATE_TID.match(file_name)
    if m:
        return m.group("tid"), int(m.group("idx")), m.group("ext").lower(), False, ""

    m = P_RAW.match(file_name)
    if m:
        return m.group("tid"), int(m.group("idx")), m.group("ext").lower(), False, ""

    return None


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        cand = path.with_name(f"{stem}_dup{i}{suffix}")
        if not cand.exists():
            return cand
        i += 1


def main():
    parser = argparse.ArgumentParser(description="Rename photos to <author_id>_<date>_<tweetid>_<idx>.ext")
    parser.add_argument("--jsonl", required=True, help="Tweet JSONL path")
    parser.add_argument("--dir", required=True, help="Photo directory")
    parser.add_argument("--author-id", default=None, help="Force blogger ID prefix for all files, e.g. KARINE")
    parser.add_argument("--handle-override", default=None, help="Deprecated alias of --author-id")
    args = parser.parse_args()

    jsonl_path = Path(args.jsonl)
    photo_dir = Path(args.dir)
    meta = load_tweet_meta(jsonl_path)

    renamed = 0
    skipped = 0
    missing_meta = 0

    for p in photo_dir.iterdir():
        if not p.is_file():
            continue
        parsed = parse_file(p.name)
        if not parsed:
            skipped += 1
            continue
        tid, idx, ext, already_target, current_handle = parsed
        m = meta.get(str(tid))
        if not m:
            missing_meta += 1
            continue
        handle, dt = m
        forced_author = args.author_id or args.handle_override
        author_id = sanitize_author_id(forced_author if forced_author else handle)
        new_name = f"{author_id}_{dt}_{tid}_{idx:02d}{ext}"
        dst = photo_dir / new_name
        dst = unique_path(dst) if dst.exists() and dst.name != p.name else dst
        if already_target and dst.name == p.name and (not forced_author or current_handle == author_id):
            skipped += 1
            continue
        p.rename(dst)
        renamed += 1

    print(
        f"done: renamed={renamed}, skipped={skipped}, missing_meta={missing_meta}, dir={photo_dir.resolve()}"
    )


if __name__ == "__main__":
    main()
