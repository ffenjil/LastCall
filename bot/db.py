import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorCollection
from bson import ObjectId
from bson.errors import InvalidId

from bot.utils.time import aware, compute_duration

log = logging.getLogger(__name__)


class Database:
    client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
    db: Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None

    # Collections
    guilds: Optional[AsyncIOMotorCollection] = None
    timers: Optional[AsyncIOMotorCollection] = None
    sessions: Optional[AsyncIOMotorCollection] = None
    active: Optional[AsyncIOMotorCollection] = None
    state: Optional[AsyncIOMotorCollection] = None

    @classmethod
    async def connect(cls, uri: str, db_name: str):
        """Connect to MongoDB."""
        cls.client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        cls.db = cls.client[db_name]

        # Set collections
        cls.guilds = cls.db["guilds"]
        cls.timers = cls.db["timers"]
        cls.sessions = cls.db["sessions"]
        cls.active = cls.db["active"]
        cls.state = cls.db["state"]

        # Verify connection
        await cls.client.admin.command("ping")
        log.info(f"Connected to MongoDB: {db_name}")

        # Create indexes
        await cls._create_indexes()

    @classmethod
    async def _create_indexes(cls):
        """Create database indexes."""
        cls._check_connection()
        await cls.guilds.create_index("guild_id", unique=True)  # type: ignore
        await cls.timers.create_index("guild_id")  # type: ignore
        await cls.timers.create_index("status")  # type: ignore
        await cls.timers.create_index([("guild_id", 1), ("user_id", 1)])  # type: ignore
        await cls.sessions.create_index("guild_id")  # type: ignore
        await cls.sessions.create_index([("guild_id", 1), ("user_id", 1)])  # type: ignore
        await cls.active.create_index([("guild_id", 1), ("user_id", 1)], unique=True)  # type: ignore
        log.info("Database indexes created")

    @classmethod
    async def close(cls):
        """Close database connection."""
        if cls.client:
            cls.client.close()
            log.info("Database connection closed")

    @classmethod
    def _check_connection(cls):
        """Ensure database is connected."""
        if (
            cls.guilds is None or cls.timers is None or cls.sessions is None
            or cls.active is None or cls.state is None
        ):
            raise RuntimeError("Database not connected")

    @staticmethod
    def _parse_object_id(value: str) -> Optional[ObjectId]:
        """Parse ObjectId safely and return None for invalid values."""
        try:
            return ObjectId(value)
        except (InvalidId, TypeError):
            return None

    # ============ Bot State ============

    @classmethod
    async def set_heartbeat(cls):
        """Record that the bot is alive right now.

        Session reconciliation settles stale sessions against this timestamp so
        that time the bot spent offline is not credited to users.
        """
        cls._check_connection()
        await cls.state.update_one(  # type: ignore
            {"_id": "bot"},
            {"$set": {"last_heartbeat": datetime.now(timezone.utc)}},
            upsert=True
        )

    @classmethod
    async def get_last_heartbeat(cls) -> Optional[datetime]:
        """Last time the bot was known to be running, or None on first boot."""
        cls._check_connection()
        doc = await cls.state.find_one({"_id": "bot"})  # type: ignore
        if not doc or not doc.get("last_heartbeat"):
            return None
        return aware(doc["last_heartbeat"])

    @classmethod
    async def get_state(cls, key: str) -> Any:
        """Read a single value from the bot state document."""
        cls._check_connection()
        doc = await cls.state.find_one({"_id": "bot"})  # type: ignore
        return doc.get(key) if doc else None

    @classmethod
    async def set_state(cls, key: str, value: Any):
        """Write a single value into the bot state document."""
        cls._check_connection()
        await cls.state.update_one(  # type: ignore
            {"_id": "bot"},
            {"$set": {key: value}},
            upsert=True
        )

    # ============ Guild Settings ============

    @classmethod
    async def get_prefix(cls, guild_id: int) -> str:
        """Get guild prefix."""
        cls._check_connection()
        doc = await cls.guilds.find_one({"guild_id": guild_id})  # type: ignore
        return doc["prefix"] if doc else os.getenv("DEFAULT_PREFIX", "!")

    @classmethod
    async def set_prefix(cls, guild_id: int, prefix: str, user_id: int):
        """Set guild prefix."""
        cls._check_connection()
        await cls.guilds.update_one(  # type: ignore
            {"guild_id": guild_id},
            {
                "$set": {
                    "prefix": prefix,
                    "updated_by": user_id,
                    "updated_at": datetime.now(timezone.utc)
                },
                "$setOnInsert": {
                    "guild_id": guild_id,
                    "created_at": datetime.now(timezone.utc)
                }
            },
            upsert=True
        )

    # ============ Timers ============

    @classmethod
    async def add_timer(
        cls,
        guild_id: int,
        channel_id: int,
        user_id: int,
        set_by: int,
        expires_at: datetime,
        duration: int,
        text_channel_id: Optional[int] = None,
        warn_seconds: int = 0
    ) -> str:
        """Add a disconnect timer.

        `channel_id` is the voice channel; `text_channel_id` is where the
        confirmation was posted. `message_id` is filled in once that message
        exists, so the whole timer can live in one message that gets edited
        rather than posting a new one at every stage.
        """
        cls._check_connection()
        result = await cls.timers.insert_one({  # type: ignore
            "guild_id": guild_id,
            "channel_id": channel_id,
            "text_channel_id": text_channel_id,
            "message_id": None,
            "user_id": user_id,
            "set_by": set_by,
            "expires_at": expires_at,
            "duration": duration,
            "warn_seconds": warn_seconds,
            "warned": False,
            "created_at": datetime.now(timezone.utc),
            "status": "active"
        })
        return str(result.inserted_id)

    @classmethod
    async def set_timer_message(cls, timer_id: str, message_id: int):
        """Remember which message to edit as the timer progresses."""
        cls._check_connection()
        object_id = cls._parse_object_id(timer_id)
        if object_id is None:
            return
        await cls.timers.update_one(  # type: ignore
            {"_id": object_id},
            {"$set": {"message_id": message_id}}
        )

    @classmethod
    async def get_timer(cls, timer_id: str) -> Optional[dict]:
        """Get timer by ID."""
        cls._check_connection()
        object_id = cls._parse_object_id(timer_id)
        if object_id is None:
            return None
        return await cls.timers.find_one({"_id": object_id})  # type: ignore

    @classmethod
    async def get_user_timer(cls, guild_id: int, user_id: int) -> Optional[dict]:
        """Get active timer for a user."""
        cls._check_connection()
        return await cls.timers.find_one({  # type: ignore
            "guild_id": guild_id,
            "user_id": user_id,
            "status": "active"
        })

    @classmethod
    async def get_guild_timers(cls, guild_id: int) -> list[dict]:
        """Get all active timers in a guild."""
        cls._check_connection()
        cursor = cls.timers.find({  # type: ignore
            "guild_id": guild_id,
            "status": "active"
        })
        return await cursor.to_list(length=100)

    @classmethod
    async def get_all_active_timers(cls) -> list[dict]:
        """Get all active timers (for bot restart recovery)."""
        cls._check_connection()
        cursor = cls.timers.find({"status": "active"})  # type: ignore
        return await cursor.to_list(length=1000)

    @classmethod
    async def cancel_timer(cls, timer_id: str) -> bool:
        """Cancel a timer."""
        cls._check_connection()
        object_id = cls._parse_object_id(timer_id)
        if object_id is None:
            return False
        result = await cls.timers.update_one(  # type: ignore
            {"_id": object_id, "status": "active"},
            {"$set": {"status": "cancelled"}}
        )
        return result.modified_count > 0

    @classmethod
    async def extend_timer(cls, timer_id: str, new_expires_at: datetime) -> bool:
        """Push an active timer expiry back.

        Clears the warned flag so the user gets a fresh warning before the new
        expiry rather than being disconnected without one.
        """
        cls._check_connection()
        object_id = cls._parse_object_id(timer_id)
        if object_id is None:
            return False
        result = await cls.timers.update_one(  # type: ignore
            {"_id": object_id, "status": "active"},
            {"$set": {"expires_at": new_expires_at, "warned": False}}
        )
        return result.modified_count > 0

    @classmethod
    async def mark_warned(cls, timer_id: str):
        """Record that the pre-disconnect warning has been sent."""
        cls._check_connection()
        object_id = cls._parse_object_id(timer_id)
        if object_id is None:
            return
        await cls.timers.update_one(  # type: ignore
            {"_id": object_id},
            {"$set": {"warned": True}}
        )

    @classmethod
    async def complete_timer(cls, timer_id: str, outcome: str):
        """Mark timer as completed.

        Guarded on status so a late voice event cannot overwrite an outcome that
        has already been recorded.
        """
        cls._check_connection()
        object_id = cls._parse_object_id(timer_id)
        if object_id is None:
            return
        await cls.timers.update_one(  # type: ignore
            {"_id": object_id, "status": "active"},
            {
                "$set": {
                    "status": "completed",
                    "outcome": outcome,
                    "completed_at": datetime.now(timezone.utc)
                }
            }
        )

    # ============ Voice Sessions ============

    @classmethod
    async def start_session(cls, guild_id: int, user_id: int, channel_id: int, channel_name: str):
        """Start tracking a voice session."""
        cls._check_connection()
        await cls.active.update_one(  # type: ignore
            {"guild_id": guild_id, "user_id": user_id},
            {
                "$set": {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "joined_at": datetime.now(timezone.utc)
                }
            },
            upsert=True
        )

    @classmethod
    async def end_session(
        cls,
        guild_id: int,
        user_id: int,
        disconnect_type: str = "manual",
        end_time: Optional[datetime] = None
    ) -> Optional[dict]:
        """End a voice session and save to history.

        `end_time` defaults to now. Reconciliation passes the last heartbeat so
        that downtime is not credited as voice time.
        """
        cls._check_connection()
        # Get active session
        session = await cls.active.find_one_and_delete({  # type: ignore
            "guild_id": guild_id,
            "user_id": user_id
        })

        if not session:
            return None

        left_at = aware(end_time) if end_time is not None else datetime.now(timezone.utc)
        duration = compute_duration(session["joined_at"], left_at)

        # Save to sessions history
        await cls.sessions.insert_one({  # type: ignore
            "guild_id": guild_id,
            "user_id": user_id,
            "channel_id": session["channel_id"],
            "channel_name": session["channel_name"],
            "joined_at": session["joined_at"],
            "left_at": left_at,
            "duration": duration,
            "disconnect_type": disconnect_type
        })

        return session

    @classmethod
    async def get_active_session(cls, guild_id: int, user_id: int) -> Optional[dict]:
        """Get user's active voice session."""
        cls._check_connection()
        return await cls.active.find_one({  # type: ignore
            "guild_id": guild_id,
            "user_id": user_id
        })

    @classmethod
    async def get_all_active_sessions(cls) -> list[dict]:
        """Get all active voice sessions."""
        cls._check_connection()
        cursor = cls.active.find({})  # type: ignore
        return await cursor.to_list(length=10000)

    @classmethod
    async def get_live_durations(cls, guild_id: int) -> dict[int, int]:
        """Seconds accrued so far by everyone currently in voice in a guild.

        Shared by stats and the leaderboard so the two cannot disagree about how
        much an in-progress session is worth.
        """
        cls._check_connection()
        cursor = cls.active.find({"guild_id": guild_id})  # type: ignore
        sessions = await cursor.to_list(length=10000)
        return {s["user_id"]: compute_duration(s["joined_at"]) for s in sessions}

    @classmethod
    async def get_user_stats(cls, guild_id: int, user_id: int) -> dict:
        """Get user's all-time voice stats, including any in-progress session."""
        cls._check_connection()
        pipeline = [
            {"$match": {"guild_id": guild_id, "user_id": user_id}},
            {
                "$group": {
                    "_id": None,
                    "total_time": {"$sum": "$duration"},
                    "session_count": {"$sum": 1},
                    "channels": {"$addToSet": "$channel_name"}
                }
            }
        ]

        result = await cls.sessions.aggregate(pipeline).to_list(length=1)  # type: ignore

        if result:
            stats = {
                "total_time": result[0].get("total_time", 0),
                "session_count": result[0].get("session_count", 0),
                "channels": list(result[0].get("channels") or [])
            }
        else:
            stats = {"total_time": 0, "session_count": 0, "channels": []}

        # Fold in the session currently in progress, if any.
        session = await cls.get_active_session(guild_id, user_id)
        if session:
            stats["total_time"] += compute_duration(session["joined_at"])
            stats["session_count"] += 1
            if session["channel_name"] not in stats["channels"]:
                stats["channels"].append(session["channel_name"])

        return stats

    @classmethod
    async def get_guild_leaderboard(cls, guild_id: int, limit: int = 10) -> list[dict]:
        """Get guild voice time leaderboard, including in-progress sessions."""
        cls._check_connection()
        pipeline = [
            {"$match": {"guild_id": guild_id}},
            {
                "$group": {
                    "_id": "$user_id",
                    "total_time": {"$sum": "$duration"},
                    "session_count": {"$sum": 1}
                }
            }
        ]

        # Deliberately no $limit in the pipeline: a long in-progress session can
        # promote someone into the top N, and a user whose only session is still
        # running has no history rows at all.
        rows = await cls.sessions.aggregate(pipeline).to_list(length=10000)  # type: ignore
        totals: dict[int, dict[str, int]] = {
            row["_id"]: {
                "total_time": row["total_time"],
                "session_count": row["session_count"]
            }
            for row in rows
        }

        for user_id, live_seconds in (await cls.get_live_durations(guild_id)).items():
            entry = totals.setdefault(user_id, {"total_time": 0, "session_count": 0})
            entry["total_time"] += live_seconds
            entry["session_count"] += 1

        leaderboard = [{"_id": user_id, **values} for user_id, values in totals.items()]
        leaderboard.sort(key=lambda entry: entry["total_time"], reverse=True)

        return leaderboard[:limit]
