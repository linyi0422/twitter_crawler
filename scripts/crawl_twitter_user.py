import argparse
import asyncio
import json
from pathlib import Path

from twscrape import API
from twscrape import queue_client


def to_dict(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj


async def _patched_ctx_req(self, method: str, url: str, params=None):
    # Some environments hang while generating x-client-transaction-id.
    # Direct GraphQL requests still work, so we skip that header generation.
    return await self.clt.request(method, url, params=params)


async def main() -> None:
    default_db = Path(__file__).resolve().parents[1] / "state" / "accounts.db"
    parser = argparse.ArgumentParser(description="Crawl one X(Twitter) user timeline with twscrape.")
    parser.add_argument("--username", required=True, help="Target username without @")
    parser.add_argument("--limit", type=int, default=3200, help="Max tweets to fetch (default: 3200)")
    parser.add_argument("--output", default=None, help="Output JSONL path")
    parser.add_argument("--db", default=str(default_db), help="Path to twscrape accounts.db")
    parser.add_argument("--add-account", action="store_true", help="Add + login one scraping account before crawling")
    parser.add_argument("--account-username", default=None)
    parser.add_argument("--account-password", default=None)
    parser.add_argument("--account-email", default=None)
    parser.add_argument("--account-email-password", default=None)
    parser.add_argument("--account-cookies", default=None, help="Optional cookies string")
    parser.add_argument("--account-mfa-code", default=None, help="Optional TOTP secret/code")
    args = parser.parse_args()

    queue_client.Ctx.req = _patched_ctx_req
    api = API(args.db)

    if args.add_account:
        if not args.account_username:
            raise SystemExit("Missing required arg for --add-account: account-username")

        # Cookie-mode works for Apple/Google SSO users: no password login needed.
        cookie_mode = bool(args.account_cookies)
        account_password = args.account_password or "__cookie_mode__"
        account_email = args.account_email or "__cookie_mode__@local"
        account_email_password = args.account_email_password or "__cookie_mode__"

        if not cookie_mode:
            required = {
                "account-password": args.account_password,
                "account-email": args.account_email,
                "account-email-password": args.account_email_password,
            }
            missing = [k for k, v in required.items() if not v]
            if missing:
                raise SystemExit(f"Missing required args for password-login mode: {', '.join(missing)}")

        await api.pool.add_account(
            args.account_username,
            account_password,
            account_email,
            account_email_password,
            cookies=args.account_cookies,
            mfa_code=args.account_mfa_code,
        )
        if not cookie_mode:
            await api.pool.login_all([args.account_username])

    user = await api.user_by_login(args.username)
    if not user:
        raise SystemExit(f"Cannot resolve user by login: {args.username}")

    output_path = Path(args.output or f"{args.username}.jsonl")
    count = 0

    with output_path.open("w", encoding="utf-8") as f:
        async for tw in api.user_tweets_and_replies(user.id, limit=args.limit):
            f.write(json.dumps(to_dict(tw), ensure_ascii=False) + "\n")
            count += 1

    print(f"done: {count} tweets -> {output_path.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
