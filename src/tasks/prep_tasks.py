import asyncio
from celery_app import celery_app
from sqlalchemy import text

from src.io.db_connector import db_manager
from src.io.orchestrator import orchestrator

@celery_app.task(name="run_prep_vulnerability_profile")
def run_prep_vulnerability_profile(site_name: str):
    return asyncio.run(async_prep_vulnerability(site_name))

async def async_prep_vulnerability(site_name: str):
    # 1. Format the main CREATE TABLE query from the JSON knowledge base
    create_sql = orchestrator.format_query("prep_vulnerability", {"site_name": site_name})
    
    # 2. Prepare the setup and teardown commands
    drop_sql = f"DROP TABLE IF EXISTS {site_name}.vulnerability_profile_{site_name} CASCADE;"
    index_sql = f"CREATE INDEX IF NOT EXISTS idx_vuln_profile_{site_name}_geom ON {site_name}.vulnerability_profile_{site_name} USING GIST (geometry);"
        
    # 3. Execute transactionally via the DatabaseManager pool
    async with db_manager.async_engine.begin() as conn:
        await conn.execute(text(drop_sql))
        await conn.execute(text(create_sql))
        await conn.execute(text(index_sql))
            
    return {"status": "success", "operation": "Vulnerability_Profile_Prep", "site": site_name}