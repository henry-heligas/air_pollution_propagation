import asyncio
import math
import json
import numpy as np
import pandas as pd
import geopandas as gpd
from geopy.distance import geodesic
from shapely.geometry import Point, Polygon

from celery_app import celery_app
from config import settings
from src.physics.toxicity import calculate_proposed_facility_impact
from src.physics.atmospheric import parse_wind_frequencies
from src.io.db_connector import db_manager
from src.io.orchestrator import orchestrator

@celery_app.task(name="run_proposed_site_assessment")
def run_proposed_site_assessment(payload: dict):
    return asyncio.run(async_proposed_site_execution(payload))

async def async_run_tier3_multi_source(payload: dict, schema_name: str):
    scenario_id = payload["site_name"]
    sources = payload["sources"]
    
    flush_sql = f"DELETE FROM {schema_name}.tier3_environmental_impacts WHERE site_name = '{scenario_id}';"
    await db_manager.execute(flush_sql)

    # 2. Fetch baseline data
    buildings_df = pd.DataFrame(await db_manager.execute_query(f"SELECT * FROM {schema_name}.tier2_buildings;"))
    
    # Initialize cumulative tracking columns
    buildings_df['delta_hazard_index'] = 0.0
    buildings_df['delta_cancer_risk'] = 0.0
    buildings_df['nearest_source_m'] = 999999.0

    # 3. Cumulative Multi-Source Decay Calculation
    for source in sources:
        src_lat = source["latitude"]
        src_lon = source["longitude"]
        
        # Calculate distance in meters from this specific source to all buildings
        distances = buildings_df.apply(
            lambda row: geodesic((src_lat, src_lon), (row['latitude'], row['longitude'])).meters, 
            axis=1
        )
        
        # Track the absolute nearest source distance for the bivariate histogram
        buildings_df['nearest_source_m'] = np.minimum(buildings_df['nearest_source_m'], distances)
        
        # Calculate standard 2D radial decay factor
        decay_factor = 1 / ((distances / 100) ** 1.5)
        
        # Extract specific pollutants
        pm25 = source["emissions_tpy"].get("PM2.5", 0.0)
        benzene = source["emissions_tpy"].get("BENZENE", 0.0)
        
        # Cumulatively sum the intersecting plumes (Applying theoretical impact constants)
        buildings_df['delta_hazard_index'] += decay_factor * (pm25 * 0.01)
        buildings_df['delta_cancer_risk'] += decay_factor * (benzene * 0.05)

    # 4. Finalize Proposed Values
    buildings_df['proposed_hazard_index'] = buildings_df['baseline_hazard_index'] + buildings_df['delta_hazard_index']
    buildings_df['site_name'] = scenario_id

    # 5. Insert back into the unified table
    await db_manager.insert_dataframe(f"{schema_name}.tier3_environmental_impacts", buildings_df)
    print(f"[{scenario_id}] Successfully simulated {len(sources)} intersecting plumes.")
    
    return scenario_id

@celery_app.task(name="trigger_tier3_scenario")
def trigger_tier3_scenario(payload: dict, schema_name: str):
    """Synchronous Celery wrapper for the async simulation solver."""
    return asyncio.run(async_run_tier3_multi_source(payload, schema_name))

async def async_proposed_site_execution(payload: dict):
    site_name = payload["site_name"]
    sources = payload.get("sources", [])
    
    if not sources:
        return {"status": "failed", "reason": "No emission sources provided."}

    # 1. Fetch wind using Orchestrator and Database Manager
    anchor_lat = sources[0]["latitude"]
    anchor_lon = sources[0]["longitude"]
    
    wind_sql = orchestrator.format_query("tier3_wind_anchor", {
        "site_name": site_name,
        "lat": anchor_lat,
        "lon": anchor_lon
    })
    wind_records = await db_manager.execute_spatial_query(wind_sql)
    wind_raw_json = wind_records[0]["wind_direction_timeseries"] if wind_records else "[]"
    df_wind = parse_wind_frequencies(wind_raw_json)
    
    # 2. Fetch Baseline
    baseline_sql = f"SELECT * FROM {site_name}.tier2_risk_model_{site_name};"
    df_master = pd.DataFrame(await db_manager.execute_spatial_query(baseline_sql))
    df_master['sector'] = df_master['sector'].astype(str)
    
    if "decay_factor" in df_master.columns:
        df_master.drop(columns=["decay_factor"], inplace=True)
    
    # Initialize running totals ONCE before loop
    df_master["c_no2_prop_total"] = 0.0
    df_master["c_pm25_prop_total"] = 0.0
    df_master["c_benzene_prop_total"] = 0.0

    # 3. Loop through every stack and accumulate overlapping plumes
    for source in sources:
        lat = source["latitude"]
        lon = source["longitude"]
        emissions = source.get("emissions_tpy", {"NO2": 0.0, "PM2.5": 0.0, "BENZENE": 0.0})
        
        # Pydantic-validated SQL injection via Orchestrator
        dispersion_sql = orchestrator.format_query("tier3_dispersion", {
            "site_name": site_name,
            "lat": lat,
            "lon": lon
        })
        
        df_decay = pd.DataFrame(await db_manager.execute_spatial_query(dispersion_sql))
        df_decay['sector'] = df_decay['sector'].astype(str)
        
        # Key Alignment: Convert raw UUIDs to match df_master's prefixed keys
        if not df_decay['sector'].isin(df_master['sector']).all():
            uuid_map = {s.split('_')[-1]: s for s in df_master['sector']}
            df_decay['sector'] = df_decay['sector'].map(uuid_map).fillna(df_decay['sector'])
            
        sector_idx = np.floor(((df_decay['azimuth_deg'].values + 11.25) % 360) / 22.5).astype(int)
        df_decay['compass_sector'] = [settings.COMPASS_SECTORS_16[i] for i in sector_idx]
        
        df_stack = pd.merge(df_decay, df_wind, left_on="compass_sector", right_on="sector", how="left")
        df_stack["wind_frequency_pct"] = df_stack["wind_frequency_pct"].fillna(1.0)
        
        # Merge temporary stack factors
        df_master = pd.merge(
            df_master, 
            df_stack[["sector_x", "decay_factor", "wind_frequency_pct"]], 
            left_on="sector", 
            right_on="sector_x", 
            how="left"
        )
        
        df_master["decay_factor"] = df_master["decay_factor"].fillna(0.0)
        df_master["wind_frequency_pct"] = df_master["wind_frequency_pct"].fillna(0.0)
        
        # Accumulate math for this stack
        df_master["c_no2_prop_total"] += (emissions.get("NO2", 0) * 0.5) * (df_master["wind_frequency_pct"] / 100) * df_master["decay_factor"]
        df_master["c_pm25_prop_total"] += (emissions.get("PM2.5", 0) * 0.5) * (df_master["wind_frequency_pct"] / 100) * df_master["decay_factor"]
        df_master["c_benzene_prop_total"] += (emissions.get("BENZENE", 0) * 0.5) * (df_master["wind_frequency_pct"] / 100) * df_master["decay_factor"]
        
        # Drop temporary merged columns
        df_master.drop(columns=["sector_x", "decay_factor", "wind_frequency_pct"], inplace=True)
        
    # 4. FINAL EXPORT MAPPING (OUTSIDE LOOP)
    df_master["c_no2_prop"] = df_master["c_no2_prop_total"].fillna(0.0)
    df_master["c_pm25_prop"] = df_master["c_pm25_prop_total"].fillna(0.0)
    df_master["c_benzene_prop"] = df_master["c_benzene_prop_total"].fillna(0.0)
    df_master["building_id"] = df_master["sector"]
        
    # 5. Finalize Toxicology
    df_impact = calculate_proposed_facility_impact(df_master)
    
    # 6. Persist Tier 3 Plume Results to PostGIS (Using new DatabaseManager method)
    gdf_impact = gpd.GeoDataFrame(
        df_impact, 
        geometry=gpd.GeoSeries.from_wkt(df_impact['wkt_geometry']), 
        crs="EPSG:4326"
    )
    
    table_name = f"tier3_proposed_impact_{site_name}"
    db_manager.persist_geodataframe(gdf_impact, table_name, schema=site_name)
    
    # ---------------------------------------------------------
    # Generate and Persist Wind Rose Geometric Layer for QGIS
    # ---------------------------------------------------------
    radius_m = 2500
    
    # Project the anchor stack to EPSG:3857 (meters) for accurate math
    stack_pt = gpd.GeoSeries(
        [Point(anchor_lon, anchor_lat)], crs="EPSG:4326"
    ).to_crs(epsg=3857).iloc[0]
    
    wedges = []
    for i, sector_label in enumerate(settings.COMPASS_SECTORS_16):
        bearing_center = i * 22.5
        bearing_start = bearing_center - 11.25
        bearing_end = bearing_center + 11.25

        points = [stack_pt]
        # Draw the curved outer edge of the wedge
        for b in np.linspace(bearing_start, bearing_end, 10):
            math_angle = math.radians(90 - b)
            x = stack_pt.x + radius_m * math.cos(math_angle)
            y = stack_pt.y + radius_m * math.sin(math_angle)
            points.append(Point(x, y))
        points.append(stack_pt)
        wedges.append(Polygon([[p.x, p.y] for p in points]))

    # Create the GeoDataFrame and project back to Lat/Lon
    gdf_wedges = gpd.GeoDataFrame({'sector': settings.COMPASS_SECTORS_16}, geometry=wedges, crs="EPSG:3857")
    gdf_wedges = gdf_wedges.to_crs(epsg=4326)
    
    # Attach the wind frequencies so they can be mapped as a heat gradient in QGIS
    gdf_wedges = gdf_wedges.merge(
        df_wind, on='sector', how='left'
    ).fillna({'wind_frequency_pct': 0.0})
    
    # Push the wind rose layer to PostgreSQL using DatabaseManager
    wind_table_name = f"tier3_wind_rose_{site_name}"
    db_manager.persist_geodataframe(gdf_wedges, wind_table_name, schema=site_name)

    return {"status": "success", "site": site_name, "table": table_name}