import asyncio
import pandas as pd
import geopandas as gpd
from celery_app import celery_app
from sqlalchemy import text

from config import settings
from src.physics.toxicity import calculate_toxicological_risk
from src.io.db_connector import db_manager
from src.io.orchestrator import orchestrator

@celery_app.task(name="run_tier2_risk_model")
def run_tier2_risk_model(site_name: str):
    return asyncio.run(async_tier2_execution(site_name))

async def async_tier2_execution(site_name: str):
    # 1. Fetch Demographics and LUR Loads via Orchestrator
    exposure_sql = orchestrator.format_query("tier2_exposure", {"site_name": site_name})
    lur_sql = orchestrator.format_query("tier2_empirical_lur", {"site_name": site_name})
    
    df_decay = pd.DataFrame(await db_manager.execute_spatial_query(exposure_sql))
    df_lur = pd.DataFrame(await db_manager.execute_spatial_query(lur_sql))
    
    if df_decay.empty or df_lur.empty:
        return {"status": "failed", "reason": "Missing baseline tables."}
        
    df_merged = pd.merge(df_decay, df_lur, on="building_id")
    
    for col in ['decay_factor', 'nox_load_1km', 'pm25_load_1km', 'dist_to_nearest_source_m']:
        if col in df_merged.columns:
            df_merged[col] = df_merged[col].astype(float)
            
    df_merged['sector'] = df_merged['building_id'].astype(str)
    
    # 2. Optimized Spatial IDW (KNN) via Orchestrator
    baseline_sql = orchestrator.format_query("tier2_knn_idw", {"site_name": site_name})
    df_baseline = pd.DataFrame(await db_manager.execute_spatial_query(baseline_sql))
    df_baseline['sector'] = df_baseline['sector'].astype(str)
    
    for col in ['no2_estimate', 'pm25_estimate', 'so2_estimate', 'hcho_estimate']:
        if col in df_baseline.columns:
            df_baseline[col] = df_baseline[col].astype(float)
            
    # 3. Restore Original Physics Module
    df_risk = calculate_toxicological_risk(df_merged, df_baseline)
    
    # 4. Construct Geometry & Persist using DatabaseManager
    gdf_risk = gpd.GeoDataFrame(
        df_risk, 
        geometry=gpd.GeoSeries.from_wkt(df_risk['wkt_geometry']), 
        crs="EPSG:4326"
    )
    
    table_name = f"tier2_risk_model_{site_name}"
    db_manager.persist_geodataframe(gdf_risk, table_name, schema="rails_north")
    
    # 5. Instantiate Zero-Copy Views
    with db_manager.sync_engine.begin() as conn:
        conn.execute(text(f"""
            CREATE OR REPLACE VIEW rails_north.tier2_residential_{site_name} AS 
            SELECT * FROM rails_north.{table_name} 
            WHERE exposure_category = 'residential';

            CREATE OR REPLACE VIEW rails_north.tier2_non_residential_{site_name} AS 
            SELECT * FROM rails_north.{table_name} 
            WHERE exposure_category != 'residential';
        """))
    
    return {"status": "success", "site": site_name}