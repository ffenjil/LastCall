"""Ad hoc reader for the events collection.

Tracking writes events but nothing in the bot reads them yet, so this is how you
look at what has been collected.

    python scripts/events.py                    summary of everything
    python scripts/events.py --type command.ok  only that event type
    python scripts/events.py --guild 123 -n 50  scoped to one guild
    python scripts/events.py --days 7           only the last week
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from bot.db import Database  # noqa: E402
from bot.utils.time import aware  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description="Inspect collected events")
    p.add_argument("--type", help="only this event type, e.g. command.ok")
    p.add_argument("--guild", type=int, help="only this guild id")
    p.add_argument("--user", type=int, help="only this user id")
    p.add_argument("--days", type=int, help="only the last N days")
    p.add_argument("-n", "--limit", type=int, default=25, help="rows to show")
    return p.parse_args()


async def main():
    args = parse_args()
    await Database.connect(
        os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        os.getenv("MONGO_DB", "lastcall"),
    )

    query: dict = {}
    if args.type:
        query["type"] = args.type
    if args.guild:
        query["guild_id"] = args.guild
    if args.user:
        query["user_id"] = args.user
    if args.days:
        query["at"] = {"$gte": datetime.now(timezone.utc) - timedelta(days=args.days)}

    total = await Database.events.count_documents(query)
    print(f"{total} matching events\n")

    breakdown = await Database.events.aggregate([
        {"$match": query},
        {"$group": {"_id": "$type", "n": {"$sum": 1}, "last": {"$max": "$at"}}},
        {"$sort": {"n": -1}},
    ]).to_list(length=100)

    if breakdown:
        print("by type:")
        for row in breakdown:
            print(f"  {row['_id']:<24} {row['n']:>6}   last {aware(row['last']):%Y-%m-%d %H:%M}")
        print()

    rows = await Database.events.find(query).sort("at", -1).limit(args.limit).to_list(
        length=args.limit
    )
    if rows:
        print(f"most recent {len(rows)}:")
        for r in rows:
            data = {k: v for k, v in (r.get("data") or {}).items() if v is not None}
            print(f"  {aware(r['at']):%Y-%m-%d %H:%M:%S}  {r['type']:<20} "
                  f"g={r.get('guild_id')} u={r.get('user_id')} {data}")

    await Database.close()


if __name__ == "__main__":
    asyncio.run(main())
