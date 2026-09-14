from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import create_engine, text
from typing import List, Dict, Any
from config import settings

async def execute_spatial_query(sql_statement: str, params: dict = None) -> List[Dict[str, Any]]:
    engine = create_async_engine(settings.async_database_url, echo=False)
    async with engine.begin() as conn:
        res = await conn.execute(text(sql_statement), params or {})
        rows = [dict(row._mapping) for row in res.fetchall()] if res.returns_rows else []
    await engine.dispose()
    return rows

async def execute_statements_transactionally(statements: List[str]) -> None:
    engine = create_async_engine(settings.async_database_url, echo=False)
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))
    await engine.dispose()

async def fetch_geojson_results(site_name: str, tier: int = 1, pollutant: str = "no2") -> dict:
    engine = create_async_engine(settings.async_database_url, echo=False)
    pol_clean = pollutant.lower().replace(".", "")
    
    # Target table routing based on assessment tier
    if tier == 1:
        table_name = f"public.tier1_fused_{pol_clean}_{site_name}"
    elif tier == 2:
        table_name = f"rails_north.tier2_risk_model_{site_name}"
    else:
        table_name = f"rails_north.proposed_risk_model_{site_name}"
    
    query = text(f"""
        SELECT json_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(json_agg(ST_AsGeoJSON(t.*)::json), '[]'::json)
        )
        FROM (SELECT * FROM {table_name}) as t;
    """)
    
    async with engine.begin() as conn:
        try:
            result = await conn.execute(query)
            geojson_data = result.scalar()
        except Exception as e:
            geojson_data = {"type": "FeatureCollection", "features": [], "error": str(e)}
            
    await engine.dispose()
    return geojson_data or {"type": "FeatureCollection", "features": []}

def create_spatial_index(schema: str, table_name: str, geom_col: str = "geometry"):
    """Automatically generates a GiST spatial index for a newly created table."""
    
    sync_url = str(settings.async_database_url).replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(sync_url)
    
    index_name = f"idx_{table_name}_{geom_col}"
    
    sql = text(f"""
        CREATE INDEX IF NOT EXISTS {index_name} 
        ON {schema}.{table_name} USING GIST ({geom_col});
    """)
    
    with engine.begin() as conn:
        conn.execute(sql)