import os
from datetime import datetime, timezone
from typing import Optional, Any

import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorCollection
from bson import ObjectId


class Database:
    client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
    db: Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None
    
    # Collections
    guilds: Optional[AsyncIOMotorCollection] = None
    timers: Optional[AsyncIOMotorCollection] = None
    sessions: Optional[AsyncIOMotorCollection] = None
    active: Optional[AsyncIOMotorCollection] = None
    
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
        
        # Verify connection
        await cls.client.admin.command("ping")
        print(f"Connected to MongoDB: {db_name}")
        
        # Create indexes
        await cls._create_indexes()
    
    @classmethod
    async def _create_indexes(cls):
        """Create database indexes."""
        if cls.guilds is None or cls.timers is None or cls.sessions is None or cls.active is None:
            raise RuntimeError("Database not connected")
        await cls.guilds.create_index("guild_id", unique=True)
        await cls.timers.create_index("guild_id")
        await cls.timers.create_index([("guild_id", 1), ("user_id", 1)])
        await cls.sessions.create_index("guild_id")
        await cls.sessions.create_index([("guild_id", 1), ("user_id", 1)])
        await cls.active.create_index([("guild_id", 1), ("user_id", 1)], unique=True)
        print("Database indexes created")
    
    @classmethod
    async def close(cls):
        """Close database connection."""
        if cls.client:
            cls.client.close()
            print("Database connection closed")
    
    @classmethod
    def _check_connection(cls):
        """Ensure database is connected."""
        if cls.guilds is None or cls.timers is None or cls.sessions is None or cls.active is None:
            raise RuntimeError("Database not connected")
    
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
        duration: int
    ) -> str:
        """Add a disconnect timer."""
        cls._check_connection()
        result = await cls.timers.insert_one({  # type: ignore
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "set_by": set_by,
            "expires_at": expires_at,
            "duration": duration,
            "created_at": datetime.now(timezone.utc),
            "status": "active"
        })
        return str(result.inserted_id)
    
    @classmethod
    async def get_timer(cls, timer_id: str) -> Optional[dict]:
        """Get timer by ID."""
        cls._check_connection()
        return await cls.timers.find_one({"_id": ObjectId(timer_id)})  # type: ignore
    
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
        result = await cls.timers.update_one(  # type: ignore
            {"_id": ObjectId(timer_id), "status": "active"},
            {"$set": {"status": "cancelled"}}
        )
        return result.modified_count > 0
    
    @classmethod
    async def complete_timer(cls, timer_id: str, outcome: str):
        """Mark timer as completed."""
        cls._check_connection()
        await cls.timers.update_one(  # type: ignore
            {"_id": ObjectId(timer_id)},
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
    async def end_session(cls, guild_id: int, user_id: int, disconnect_type: str = "manual") -> Optional[dict]:
        """End a voice session and save to history."""
        cls._check_connection()
        # Get active session
        session = await cls.active.find_one_and_delete({  # type: ignore
            "guild_id": guild_id,
            "user_id": user_id
        })
        
        if not session:
            return None
        
        # Calculate duration - handle timezone-naive datetimes from MongoDB
        now = datetime.now(timezone.utc)
        joined_at = session["joined_at"]
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)
        duration = int((now - joined_at).total_seconds())
        
        # Save to sessions history
        await cls.sessions.insert_one({  # type: ignore
            "guild_id": guild_id,
            "user_id": user_id,
            "channel_id": session["channel_id"],
            "channel_name": session["channel_name"],
            "joined_at": session["joined_at"],
            "left_at": now,
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
    async def get_user_stats(cls, guild_id: int, user_id: int) -> dict:
        """Get user's all-time voice stats."""
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
        
        if not result:
            return {"total_time": 0, "session_count": 0, "channels": []}
        
        return result[0]
    
    @classmethod
    async def get_guild_leaderboard(cls, guild_id: int, limit: int = 10) -> list[dict]:
        """Get guild voice time leaderboard."""
        cls._check_connection()
        pipeline = [
            {"$match": {"guild_id": guild_id}},
            {
                "$group": {
                    "_id": "$user_id",
                    "total_time": {"$sum": "$duration"},
                    "session_count": {"$sum": 1}
                }
            },
            {"$sort": {"total_time": -1}},
            {"$limit": limit}
        ]
        
        return await cls.sessions.aggregate(pipeline).to_list(length=limit)  # type: ignore
