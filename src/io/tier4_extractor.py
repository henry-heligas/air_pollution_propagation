import os
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from sqlalchemy.orm import Session
from src.io.models import Tier4Scenario, Tier4SurfaceImpact, Tier4Receptor, Tier4Asset
from geopy.distance import distance

def latlon_from_local_xy(center_lat: float, center_lon: float, x_offset_m: float, y_offset_m: float):
    """Translates OpenFOAM local meters back to EPSG:4326 GPS coordinates."""
    # Move North/South by Y meters
    point = distance(meters=y_offset_m).destination((center_lat, center_lon), bearing=0 if y_offset_m > 0 else 180)
    # Move East/West by X meters
    point = distance(meters=x_offset_m).destination((point.latitude, point.longitude), bearing=90 if x_offset_m > 0 else 270)
    return point.longitude, point.latitude

def extract_tier4_results(session: Session, case_dir: str, payload: dict, max_time_sec: int = 3600):
    """
    Parses OpenFOAM postProcessing outputs and ingests them into the Master Tables.
    """
    site_name = payload["site_name"]
    scenario_type = payload.get("scenario_type", "acute_fire")
    scenario_id = f"{site_name}_{scenario_type}_{payload['wind']['wind_direction_deg']}deg"
    
    center_lat = payload["sources"][0]["latitude"]
    center_lon = payload["sources"][0]["longitude"]

    # 1. Populate the Scenario Registry
    scenario_record = Tier4Scenario(
        scenario_id=scenario_id,
        site_name=site_name,
        scenario_type=scenario_type,
        wind_direction_deg=payload["wind"]["wind_direction_deg"],
        total_sources=len(payload["sources"]),
        max_hazard_index=0.0 # Will be updated dynamically below
    )
    session.add(scenario_record)
    session.flush() # Locks in the PK so children can reference it

    # 2. Parse Virtual Probes (The Timeseries Chart Data)
    # OpenFOAM puts this in postProcessing/probes/0/T (or similar)
    probes_file = os.path.join(case_dir, "postProcessing", "probes", "0", "C")
    if os.path.exists(probes_file):
        # Read the OpenFOAM probe text file (Space delimited, skipping header comments)
        df_probes = pd.read_csv(probes_file, sep='\s+', comment='#', header=None)
        
        # Assume Col 0 is Time, Cols 1..N are concentration at buildings 1..N
        for index, row in df_probes.iterrows():
            time_sec = float(row[0])
            for building_idx, concentration in enumerate(row[1:]):
                sensor_record = Tier4Receptor(
                    scenario_id=scenario_id,
                    building_id=f"building_uuid_{building_idx}", # Mapped from your Tier 2 config
                    timestep_sec=time_sec,
                    pollutant="PM2.5",
                    concentration=float(concentration)
                )
                session.add(sensor_record)

    # 3. Parse the Evacuation Map (Z=1.5m Surface Slice)
    # OpenFOAM generates surface slices in postProcessing/surfaces/
    surface_file = os.path.join(case_dir, "postProcessing", "surfaces", str(max_time_sec), "zHeight_C.csv")
    max_hi_found = 0.0
    
    if os.path.exists(surface_file):
        df_surf = pd.read_csv(surface_file) # Columns: x, y, z, C
        
        # Convert local X,Y back to GPS points
        geometries = []
        for _, row in df_surf.iterrows():
            lon, lat = latlon_from_local_xy(center_lat, center_lon, row['x'], row['y'])
            geometries.append(Point(lon, lat))
            
            if row['C'] > max_hi_found:
                max_hi_found = row['C']
                
        # In production, we contour these points into polygons using scipy or skimage.
        # For ingestion, we buffer the points slightly to create the geometry blocks.
        gdf = gpd.GeoDataFrame(df_surf, geometry=geometries, crs="EPSG:4326")
        gdf['geometry'] = gdf['geometry'].buffer(0.0001) # Approx 10m blocks
        
        for _, row in gdf.iterrows():
            # Determine Hazard Zone Thresholds
            zone = "Low"
            if row['C'] > 10.0: zone = "Evacuation_Required"
            elif row['C'] > 5.0: zone = "Moderate"
            
            if zone != "Low":
                impact_record = Tier4SurfaceImpact(
                    scenario_id=scenario_id,
                    pollutant="PM2.5",
                    hazard_zone=zone,
                    concentration=row['C'],
                    geometry=row['geometry'].wkt
                )
                session.add(impact_record)
                
    # Update max toxicity found during the run
    scenario_record.max_hazard_index = max_hi_found

    # 4. Link 3D Render Assets
    # Assuming a separate script converted the plume VTK to a GLTF file in an S3 bucket
    asset_record = Tier4Asset(
        scenario_id=scenario_id,
        asset_type="plume_volume_gltf",
        file_path=f"s3://air-pollution-assets/{site_name}/tier4/{scenario_id}_plume.gltf"
    )
    session.add(asset_record)

    # 5. Lock it all into the Database
    session.commit()
    return scenario_id