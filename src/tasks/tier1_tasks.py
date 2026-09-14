import asyncio
import pandas as pd
from celery_app import celery_app
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

from config import settings
from src.physics.atmospheric import format_pixel_compliance
from src.io.db_connector import execute_spatial_query
from src.io.queries import build_tier1_pixel_sql

@celery_app.task(name="run_tier1_baseline")
def run_tier1_baseline(site_name: str, pollutant: str = "NO2"):
    return asyncio.run(async_baseline_execution(site_name, pollutant))

async def async_baseline_execution(site_name: str, pollutant: str = "NO2"):
    pollutant_clean = pollutant.lower().replace(".", "")
    target_table = f"{site_name}.air_quality_satellite_chicago_macro"
    timeseries_col = f"{pollutant_clean}_timeseries"

    engine = create_async_engine(settings.async_database_url, echo=False)
    rows = await execute_spatial_query(build_tier1_pixel_sql(target_table, timeseries_col, site_name))
    
    if not rows:
        await engine.dispose()
        return {"status": "failed", "reason": f"No data found for '{timeseries_col}' in '{target_table}'."}
        
    df_pixels = pd.DataFrame(rows)
    df_compliant = format_pixel_compliance(df_pixels, pollutant)
    
    csv_filename = f"tier1_compliant_export_{pollutant_clean}_{site_name}.csv"
    df_compliant.to_csv(csv_filename, index=False)
    
    fused_table = f"public.tier1_fused_{pollutant_clean}_{site_name}"
    
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {fused_table};"))
        await conn.execute(text(f"""
            CREATE TABLE {fused_table} AS 
            SELECT id, 
                   ST_SetSRID(ST_GeomFromText(wkt_geometry), 4326) AS geometry,
                   estimate AS {pollutant_clean}_estimate, 
                   margin_of_error_95, 
                   lower_bound, 
                   upper_bound,
                   pollutant, 
                   physical_unit, 
                   averaging_window, 
                   naaqs_limit, 
                   naaqs_exceedance
            FROM (
                SELECT * FROM jsonb_populate_recordset(NULL::record, CAST(:json_data AS jsonb)) 
                AS (id int, wkt_geometry text, estimate float, margin_of_error_95 float, lower_bound float, upper_bound float, pollutant text, physical_unit text, averaging_window text, naaqs_limit float, naaqs_exceedance boolean)
            ) as data;
        """), {"json_data": df_compliant.to_json(orient="records")})
        
        # Build the dynamic spatial index asynchronously
        await conn.execute(text(f"CREATE INDEX ON {fused_table} USING GIST (geometry);"))
        
    await engine.dispose()
    return {"status": "success", "operation": "Tier1_Baseline", "fused_table": fused_table}