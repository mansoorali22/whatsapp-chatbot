"""
Database connection management with connection pooling
"""
import asyncpg
from contextlib import asynccontextmanager
from typing import Optional
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
    
    async def connect(self):
        """Create database connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                dsn=settings.DATABASE_URL,
                min_size=2,
                max_size=10,
                command_timeout=60,
                timeout=30
            )
            logger.info("Database connection pool created successfully")
            
            # Test connection and verify pgvector
            async with self.pool.acquire() as conn:
                version = await conn.fetchval("SELECT version()")
                logger.info(f"Connected to: {version}")
                
                # Check if pgvector is installed
                has_vector = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')"
                )
                if not has_vector:
                    logger.warning("pgvector extension not found! Run: CREATE EXTENSION vector;")
                else:
                    logger.info("pgvector extension verified")
                    
        except Exception as e:
            logger.error(f"Failed to create database pool: {e}")
            raise
    
    async def disconnect(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection pool closed")
    
    @asynccontextmanager
    async def acquire(self):
        """Context manager for acquiring a connection from the pool"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized. Call connect() first.")
        
        async with self.pool.acquire() as connection:
            yield connection
    
    async def execute(self, query: str, *args):
        """Execute a query without returning results"""
        async with self.acquire() as conn:
            return await conn.execute(query, *args)
    
    async def fetch(self, query: str, *args):
        """Execute a query and return all results"""
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)
    
    async def fetchrow(self, query: str, *args):
        """Execute a query and return one result"""
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)
    
    async def fetchval(self, query: str, *args):
        """Execute a query and return a single value"""
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)


# Global database instance
db = Database()


async def init_db():
    """Initialize database connection"""
    await db.connect()


async def close_db():
    """Close database connection"""
    await db.disconnect()


async def check_db_health() -> bool:
    """Check if database is healthy"""
    try:
        result = await db.fetchval("SELECT 1")
        return result == 1
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False