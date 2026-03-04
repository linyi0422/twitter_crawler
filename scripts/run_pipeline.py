import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> None:
    print(">>", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    scripts = root / "scripts"
    output_root = root / "output"

    parser = argparse.ArgumentParser(description="Run full X crawl pipeline: crawl -> download photos -> rename")
    parser.add_argument("--username", required=True, help="Target username without @")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--auth-token", required=True)
    parser.add_argument("--ct0", required=True)
    parser.add_argument("--tag", default=None, help="Output tag (default: <username>_<limit>)")
    parser.add_argument(
        "--author-id",
        default=None,
        help="Blogger ID prefix for photo naming (default: <username>)",
    )
    args = parser.parse_args()

    tag = args.tag or f"{args.username}_{args.limit}"
    user_dir = output_root / args.username
    user_dir.mkdir(parents=True, exist_ok=True)

    jsonl = user_dir / f"{tag}.jsonl"
    photos = user_dir / f"{tag}_photos"

    run(
        [
            sys.executable,
            str(scripts / "crawl_x_sync.py"),
            "--username",
            args.username,
            "--limit",
            str(args.limit),
            "--output",
            str(jsonl),
            "--auth-token",
            args.auth_token,
            "--ct0",
            args.ct0,
        ],
        root,
    )

    run(
        [
            sys.executable,
            str(scripts / "download_photos_from_jsonl.py"),
            "--input",
            str(jsonl),
            "--output-dir",
            str(photos),
        ],
        root,
    )

    run(
        [
            sys.executable,
            str(scripts / "rename_photos_by_handle_date.py"),
            "--jsonl",
            str(jsonl),
            "--dir",
            str(photos),
            "--author-id",
            str(args.author_id or args.username),
        ],
        root,
    )

    print("done:", jsonl)
    print("done:", photos)


if __name__ == "__main__":
    main()
