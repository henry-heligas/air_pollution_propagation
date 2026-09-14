# src/tasks/proposed_tasks.py

import asyncio
import pandas as pd
import geopandas as gpd
from celery_app import celery_app
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

from config import settings
from src.physics.atmospheric import parse_wind_frequencies
from src.physics.toxicity import calculate_proposed_facility_impact
from src.io.db_connector import execute_spatial_query
from src.io.queries import build_drop_table_sql, build_point_exposure_wedges_sql

@celery_app.task(name="run_proposed_site_assessment")
def run_proposed_site_assessment(payload_dict: dict):
    return asyncio.run(async_proposed_site_execution(payload_dict))

async def async_proposed_site_execution(payload: dict):
    site_name = payload["site_name"]
    sources = payload.get("sources", [])
    emissions_tpy = payload.get("emissions_tpy", {"NO2": 10.0, "PM2.5": 2.5, "BENZENE": 0.5})
    inner_m, outer_m = payload.get("tier2_inner_m", 183.0), payload.get("tier2_outer_m", 1500.0)
    
    # 1. Geometry & Table Targets
    centroid_lat = sum(pt['latitude'] for pt in sources) / len(sources)
    centroid_lon = sum(pt['longitude'] for pt in sources) / len(sources)
    centroid_wkt = f"POINT({centroid_lon} {centroid_lat})"
    multipoint_wkt = f"MULTIPOINT({', '.join([f'{pt['longitude']} {pt['latitude']}' for pt in sources])})"
    
    target_table = f"public.tier2_exposure_wedges_{site_name}"
    bldg_table = f"rails_north.vulnerability_profile_{site_name}"
    wind_table = f"rails_north.climate_wind_grid_{site_name}"
    
    # 2. Build Spatial Wind Wedges
    engine = create_async_engine(settings.async_database_url, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(build_drop_table_sql(target_table)))
        await conn.execute(text(build_point_exposure_wedges_sql(target_table, bldg_table)), 
                           {"multipoint_wkt": multipoint_wkt, "inner_m": inner_m, "outer_m": outer_m})
        
        res_wind = await conn.execute(
            text(f"SELECT wind_direction_timeseries FROM {wind_table} ORDER BY geometry <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) LIMIT 1;"),
            {"lat": centroid_lat, "lon": centroid_lon}
        )
        wind_row = res_wind.fetchone()
        
    df_wind = parse_wind_frequencies(wind_row[0] if wind_row else None)
    
    # 3. Extract Exposure Demographics
    decay_sql = f"""
    SELECT 
        b.building_id,
        w.sector,
        COALESCE(b.population, 0) AS total_population,
        COALESCE(b.exposure_category, 'residential') AS exposure_category,
        COALESCE(b.exposure_type, 'chronic_24h') AS exposure_type,
        ST_Distance(b.geometry::geography, ST_GeomFromText('{centroid_wkt}', 4326)::geography) AS distance_m,
        EXP(-ST_Distance(b.geometry::geography, ST_GeomFromText('{centroid_wkt}', 4326)::geography) / 500.0) AS decay_factor,
        ST_AsText(b.geometry) AS wkt_geometry
    FROM {bldg_table} b
    JOIN {target_table} w ON ST_Intersects(b.geometry, w.geometry)
    """
    df_decay = pd.DataFrame(await execute_spatial_query(decay_sql))
    
    # Guardrail: Handle empty intersections cleanly
    if df_decay.empty:
        await engine.dispose()
        return {
            "status": "failed",
            "reason": "Zero buildings or receptors found within the specified dispersion radius.",
            "site_name": site_name
        }
    
    # 4. Extract Environmental Baseline
    baseline_sql = f"""
    SELECT 
        w.sector,
        (SELECT no2_estimate FROM public.tier1_fused_no2_{site_name} p ORDER BY p.geometry <-> w.geometry LIMIT 1) AS no2_estimate,
        (SELECT pm25_estimate FROM public.tier1_fused_pm25_{site_name} p ORDER BY p.geometry <-> w.geometry LIMIT 1) AS pm25_estimate,
        (SELECT so2_estimate FROM public.tier1_fused_so2_{site_name} p ORDER BY p.geometry <-> w.geometry LIMIT 1) AS so2_estimate,
        (SELECT hcho_estimate FROM public.tier1_fused_hcho_{site_name} p ORDER BY p.geometry <-> w.geometry LIMIT 1) AS hcho_estimate
    FROM {target_table} w;
    """
    df_baseline = pd.DataFrame(await execute_spatial_query(baseline_sql))
    
    # 5. Call Physics Helper Module
    df_risk = calculate_proposed_facility_impact(df_decay, df_wind, df_baseline, emissions_tpy)
    
    # 6. Persist Output Layer
    sync_url = str(settings.async_database_url).replace("postgresql+asyncpg://", "postgresql://")
    sync_engine = create_engine(sync_url)
    
    table_name = f"proposed_risk_model_{site_name}"
    
    gdf_risk = gpd.GeoDataFrame(df_risk, geometry=gpd.GeoSeries.from_wkt(df_risk['wkt_geometry']), crs="EPSG:4326")
    gdf_risk.to_postgis(
        table_name, 
        sync_engine, 
        schema="rails_north", 
        if_exists="replace", 
        index=False,
        chunksize=1000
    )
    
    # 7. Apply Spatial Index immediately after creation
    with sync_engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX ON rails_north.{table_name} USING GIST (geometry);"))
    
    await engine.dispose()
    return {
        "status": "success",
        "operation": "Proposed_Facility_Assessment",
        "site_name": site_name,
        "buildings_processed": len(gdf_risk)
    }