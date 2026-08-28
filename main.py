import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

log = logging.getLogger(__name__)

load_dotenv()

from bot.core import LastCall
from bot.db import Database


async def main():
    # Get token
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN not found in .env")
        sys.exit(1)

    # Connect to MongoDB
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_db = os.getenv("MONGO_DB", "lastcall")

    try:
        await Database.connect(mongo_uri, mongo_db)
    except Exception:
        log.exception("Error connecting to MongoDB")
        sys.exit(1)

    # Start bot
    bot = LastCall()

    try:
        log.info("Starting bot...")
        await bot.start(token)
    except KeyboardInterrupt:
        pass
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("Bot stopped with an error")
    finally:
        log.info("Shutting down...")
        if not bot.is_closed():
            await bot.close()
        await Database.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
