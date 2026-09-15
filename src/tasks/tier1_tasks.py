import asyncio
import pandas as pd
from celery_app import celery_app
from sqlalchemy import text

from config import settings
from src.physics.atmospheric import format_pixel_compliance
from src.io.db_connector import db_manager
from src.io.orchestrator import orchestrator

@celery_app.task(name="run_tier1_baseline")
def run_tier1_baseline(site_name: str, pollutant: str = "NO2"):
    return asyncio.run(async_baseline_execution(site_name, pollutant))

async def async_baseline_execution(site_name: str, pollutant: str = "NO2"):
    pollutant_clean = pollutant.lower().replace(".", "")
    timeseries_col = f"{pollutant_clean}_timeseries"

    # 1. Fetch Pixel Data via Orchestrator
    pixel_sql = orchestrator.format_query("tier1_pixel", {
        "site_name": site_name,
        "timeseries_col": timeseries_col
    })
    rows = await db_manager.execute_spatial_query(pixel_sql)
    
    if not rows:
        return {"status": "failed", "reason": f"No data found for '{timeseries_col}'."}
        
    # 2. Physics & Compliance Formatting
    df_pixels = pd.DataFrame(rows)
    df_compliant = format_pixel_compliance(df_pixels, pollutant)
    
    csv_filename = f"tier1_compliant_export_{pollutant_clean}_{site_name}.csv"
    df_compliant.to_csv(csv_filename, index=False)
    
    # 3. Persist to PostGIS using secure bound parameters
    fused_table = f"public.tier1_fused_{pollutant_clean}_{site_name}"
    insert_sql = orchestrator.format_query("tier1_fused_insert", {
        "site_name": site_name,
        "pollutant_clean": pollutant_clean
    })
    
    async with db_manager.async_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {fused_table};"))
        # Bind the massive JSON payload natively to prevent string-escape crashes
        await conn.execute(text(insert_sql), {"json_data": df_compliant.to_json(orient="records")})
        await conn.execute(text(f"CREATE INDEX ON {fused_table} USING GIST (geometry);"))
        
    return {"status": "success", "operation": "Tier1_Baseline", "fused_table": fused_table}