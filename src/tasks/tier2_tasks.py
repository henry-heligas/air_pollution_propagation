import asyncio
import pandas as pd
import geopandas as gpd
from celery_app import celery_app
from sqlalchemy import create_engine, text

from config import settings
from src.physics.toxicity import calculate_toxicological_risk
from src.io.db_connector import execute_spatial_query
from src.io.queries import build_tier2_exposure_sql, build_tier2_empirical_lur_sql

@celery_app.task(name="run_tier2_risk_model")
def run_tier2_risk_model(site_name: str):
    return asyncio.run(async_tier2_execution(site_name))

async def async_tier2_execution(site_name: str):
    # 1. Fetch Demographics and LUR Loads (Standard SQL)
    df_decay = pd.DataFrame(await execute_spatial_query(build_tier2_exposure_sql(site_name)))
    df_lur = pd.DataFrame(await execute_spatial_query(build_tier2_empirical_lur_sql(site_name)))
    
    if df_decay.empty or df_lur.empty:
        return {"status": "failed", "reason": "Missing baseline tables."}
        
    df_merged = pd.merge(df_decay, df_lur.drop(columns=['total_population', 'wkt_geometry']), on="building_id")
    
    for col in ['decay_factor', 'nox_load_1km', 'pm25_load_1km', 'dist_to_nearest_source_m']:
        if col in df_merged.columns:
            df_merged[col] = df_merged[col].astype(float)
            
    # Enforce string type to guarantee clean joins in physics module
    df_merged['sector'] = df_merged['building_id'].astype(str)
    
    # 2. Optimized Spatial Inverse Distance Weighting (IDW)
    baseline_sql = f"""
    WITH bldgs AS (
        SELECT building_id, ST_Centroid(geometry) AS geom 
        FROM rails_north.vulnerability_profile_{site_name}
    )
    SELECT 
        b.building_id AS sector,
        n.no2_estimate::float,
        n.pm25_estimate::float,
        n.so2_estimate::float,
        n.hcho_estimate::float
    FROM bldgs b
    CROSS JOIN LATERAL (
        WITH knn AS (
            SELECT 
                n_grid.no2_estimate,
                p_grid.pm25_estimate,
                s_grid.so2_estimate,
                h_grid.hcho_estimate,
                -- 1. Cast to ::geography to calculate distance in METERS (prevents weight flattening)
                1.0 / GREATEST(POWER(ST_Distance(n_grid.geometry::geography, b.geom::geography), 2), 1.0) AS weight
            FROM public.tier1_fused_no2_{site_name} n_grid
            JOIN public.tier1_fused_pm25_{site_name} p_grid ON n_grid.id = p_grid.id
            JOIN public.tier1_fused_so2_{site_name} s_grid ON n_grid.id = s_grid.id
            JOIN public.tier1_fused_hcho_{site_name} h_grid ON n_grid.id = h_grid.id
            -- 2. Adjust bounding box delta to 0.05 degrees (~5.5 km) for EPSG:4326
            WHERE n_grid.geometry && ST_Expand(b.geom, 0.05)
            ORDER BY n_grid.geometry <-> b.geom 
            LIMIT 9
        )
        SELECT 
            SUM(no2_estimate * weight) / SUM(weight) AS no2_estimate,
            SUM(pm25_estimate * weight) / SUM(weight) AS pm25_estimate,
            SUM(so2_estimate * weight) / SUM(weight) AS so2_estimate,
            SUM(hcho_estimate * weight) / SUM(weight) AS hcho_estimate
        FROM knn
    ) n;
    """
    df_baseline = pd.DataFrame(await execute_spatial_query(baseline_sql))
    df_baseline['sector'] = df_baseline['sector'].astype(str)
    
    for col in ['no2_estimate', 'pm25_estimate', 'so2_estimate', 'hcho_estimate']:
        if col in df_baseline.columns:
            df_baseline[col] = df_baseline[col].astype(float)
            
    # 3. Restore Original Physics Module
    df_risk = calculate_toxicological_risk(df_merged, df_baseline)
    
    # 4. Construct Geometry, Persist Master Table, and Instantiate Split Views
    gdf_risk = gpd.GeoDataFrame(
        df_risk, 
        geometry=gpd.GeoSeries.from_wkt(df_risk['wkt_geometry']), 
        crs="EPSG:4326"
    )
    
    sync_url = str(settings.async_database_url).replace("postgresql+asyncpg://", "postgresql://")
    sync_engine = create_engine(sync_url)
    table_name = f"tier2_risk_model_{site_name}"
    
    # Explicitly drop the table with CASCADE to clear dependent views before GeoPandas re-creates it
    with sync_engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS rails_north.{table_name} CASCADE;"))

    gdf_risk.to_postgis(
        table_name, 
        sync_engine, 
        schema="rails_north", 
        if_exists="replace", 
        index=False,
        chunksize=1000
    )
    
    # Re-build Spatial Index & Re-instantiate Zero-Copy Views
    with sync_engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX ON rails_north.{table_name} USING GIST (geometry);"))
        
        conn.execute(text(f"""
            CREATE OR REPLACE VIEW rails_north.tier2_residential_{site_name} AS 
            SELECT * FROM rails_north.{table_name} 
            WHERE exposure_category = 'residential';

            CREATE OR REPLACE VIEW rails_north.tier2_non_residential_{site_name} AS 
            SELECT * FROM rails_north.{table_name} 
            WHERE exposure_category != 'residential';
        """))
    
    return {"status": "success", "site": site_name}