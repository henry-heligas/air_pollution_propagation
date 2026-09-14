import asyncio
from celery_app import celery_app
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from config import settings
from src.io.queries import build_prep_vulnerability_profile_sql

@celery_app.task(name="run_prep_vulnerability_profile")
def run_prep_vulnerability_profile(site_name: str):
    return asyncio.run(async_prep_vulnerability(site_name))

async def async_prep_vulnerability(site_name: str):
    engine = create_async_engine(settings.async_database_url, echo=False)
    queries = build_prep_vulnerability_profile_sql(site_name)
    
    async with engine.begin() as conn:
        for query in queries:
            await conn.execute(text(query))
            
    await engine.dispose()
    return {"status": "success", "operation": "Vulnerability_Profile_Prep", "site": site_name}