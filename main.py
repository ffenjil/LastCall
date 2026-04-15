import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from bot.core import LastCall
from bot.db import Database


async def main():
    # Get token
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not found in .env")
        sys.exit(1)

    # Connect to MongoDB
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_db = os.getenv("MONGO_DB", "lastcall")

    try:
        await Database.connect(mongo_uri, mongo_db)
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        sys.exit(1)

    # Start bot
    bot = LastCall()

    try:
        print("Starting bot...")
        await bot.start(token)
    except KeyboardInterrupt:
        pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Error: {e}")
    finally:
        print("Shutting down...")
        await Database.close()
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
