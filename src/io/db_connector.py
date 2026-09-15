import logging
from typing import List, Dict, Any
import geopandas as gpd
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.engine import Engine
from config import settings
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

class DatabaseManager:
    """
    Centralized Database Manager handling connection pooling for both
    asynchronous query execution and synchronous spatial data persistence.
    """
    _instance = None

    def __new__(cls):
        # Implement Singleton pattern to guarantee only one engine pair per worker
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            cls._instance._init_engines()
        return cls._instance

    def _init_engines(self):
        # 1. Asynchronous engine (Loop-safe for Celery via NullPool)
        self.async_engine: AsyncEngine = create_async_engine(
            settings.async_database_url,
            echo=False,
            poolclass=NullPool  # Prevents cross-task event loop collisions
        )
        
        # 2. Synchronous engine specifically for Pandas/GeoPandas .to_postgis()
        sync_url = str(settings.async_database_url).replace("postgresql+asyncpg://", "postgresql://")
        self.sync_engine: Engine = create_engine(
            sync_url,
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True  # Drops stale connections safely during Celery worker forks
        )

    async def execute_spatial_query(self, sql_statement: str, params: dict = None):
        async with self.async_engine.begin() as conn:
            res = await conn.execute(text(sql_statement), params or {})
            if res.returns_rows:
                return [dict(row._mapping) for row in res.fetchall()]
            return []

    def persist_geodataframe(self, gdf: gpd.GeoDataFrame, table_name: str, schema: str = "public", index_geom: bool = True, geom_col: str = "geometry"):
        """
        Synchronously pushes a GeoDataFrame to PostGIS using the connection pool.
        Automatically handles dropping old tables and creating spatial indexes.
        """
        with self.sync_engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{table_name} CASCADE;"))
            
        gdf.to_postgis(
            table_name, 
            self.sync_engine, 
            schema=schema, 
            if_exists="replace", 
            index=False,
            chunksize=1000
        )
        
        if index_geom:
            self.create_spatial_index(schema, table_name, geom_col)

    def create_spatial_index(self, schema: str, table_name: str, geom_col: str = "geometry"):
        """Generates a GiST spatial index for a given table."""
        index_name = f"idx_{table_name}_{geom_col}"
        sql = text(f"""
            CREATE INDEX IF NOT EXISTS {index_name} 
            ON {schema}.{table_name} USING GIST ({geom_col});
        """)
        with self.sync_engine.begin() as conn:
            conn.execute(sql)

# Expose the singleton globally
db_manager = DatabaseManager()

# Legacy wrappers for backward compatibility with existing task files

create_spatial_index = db_manager.create_spatial_index