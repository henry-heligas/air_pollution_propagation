import os
import pyvista as pv
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, MultiPolygon, Polygon
from shapely.ops import unary_union
from sqlalchemy.orm import Session
from src.io.models import Tier4Scenario, Tier4SurfaceImpact, Tier4Receptor, Tier4Asset
from geopy.distance import distance

def latlon_from_local_xy(center_lat: float, center_lon: float, x_offset_m: float, y_offset_m: float):
    """Translates OpenFOAM local Cartesian meters to EPSG:4326 GPS coordinates."""
    point = distance(meters=abs(y_offset_m)).destination(
        (center_lat, center_lon), bearing=0 if y_offset_m > 0 else 180
    )
    point = distance(meters=abs(x_offset_m)).destination(
        (point.latitude, point.longitude), bearing=90 if x_offset_m > 0 else 270
    )
    return point.longitude, point.latitude

def extract_tier4_results(session: Session, case_dir: str, payload: dict, max_time_sec: int = 10):
    """
    Parses OpenFOAM VTK outputs, georeferences the hazard zones, 
    and ingests them into the PostGIS Master Tables.
    """
    site_name = payload["site_name"]
    scenario_type = payload.get("scenario_type", "acute_fire")
    wind_dir = payload.get("wind", {}).get("wind_direction_deg", 270.0)
    scenario_id = f"{site_name}_{scenario_type}_{wind_dir}deg"
    
    center_lat = payload["sources"][0]["latitude"]
    center_lon = payload["sources"][0]["longitude"]

    # 1. Wipe the old scenario if it already exists (Idempotency)
    existing = session.query(Tier4Scenario).filter_by(scenario_id=scenario_id).first()
    if existing:
        session.delete(existing)
        session.flush()

    # 2. Populate the Scenario Registry
    scenario_record = Tier4Scenario(
        scenario_id=scenario_id,
        site_name=site_name,
        scenario_type=scenario_type,
        wind_direction_deg=wind_dir,
        total_sources=len(payload["sources"]),
        max_hazard_index=0.0
    )
    session.add(scenario_record)
    session.flush() # Locks in the PK for foreign keys

    # 3. Parse the Evacuation Map (Z=1.5m VTK Surface Slice)
    surface_file = os.path.join(case_dir, "postProcessing", "surfaces", str(max_time_sec), "breathing_zone.vtp")
    max_temp_found = 293.15 # Baseline ambient temp
    
    if os.path.exists(surface_file):
        # Read the 3D binary surface using PyVista
        mesh = pv.read(surface_file)
        points = mesh.points        # (X, Y, Z) array
        temperatures = mesh.point_data.get('T', []) # Temperature array
        
        high_heat_polys = []
        critical_heat_polys = []
        cell_size = payload.get("mesh", {}).get("base_cell_size", 25.0)

        for idx, temp in enumerate(temperatures):
            if temp > max_temp_found:
                max_temp_found = temp
                
            # Filter for dangerous thresholds
            if temp > 310.0: # ~37C / 98F (High Heat)
                x, y = points[idx][0], points[idx][1]
                lon, lat = latlon_from_local_xy(center_lat, center_lon, x, y)
                
                # Create a spatial buffer representing the cell size in degrees
                # (~111,111 meters per degree near the equator)
                degree_buffer = (cell_size / 2) / 111111.0 
                poly = Point(lon, lat).buffer(degree_buffer, cap_style=3) # cap_style=3 makes it a square
                
                if temp > 333.0: # ~60C / 140F (Evacuation Required)
                    critical_heat_polys.append(poly)
                else:
                    high_heat_polys.append(poly)

        # 4. Merge scattered cells into clean, continuous MultiPolygons
        def _save_impact_zone(polys, hazard_label):
            if not polys: return
            merged_geom = unary_union(polys)
            
            # Ensure it's explicitly a MultiPolygon for PostGIS
            if isinstance(merged_geom, Polygon):
                merged_geom = MultiPolygon([merged_geom])
                
            impact_record = Tier4SurfaceImpact(
                scenario_id=scenario_id,
                pollutant="Temperature_K",
                hazard_zone=hazard_label,
                concentration=max_temp_found,
                geometry=merged_geom.wkt
            )
            session.add(impact_record)

        _save_impact_zone(high_heat_polys, "High_Heat_Alert")
        _save_impact_zone(critical_heat_polys, "Evacuation_Required")

    # Update scenario metadata with max physics limit reached
    scenario_record.max_hazard_index = float(max_temp_found)

    # 5. Lock it all into the Database
    session.commit()
    return scenario_id

def latlon_from_local_xy(center_lat: float, center_lon: float, x_offset_m: float, y_offset_m: float):
    """Translates OpenFOAM local Cartesian meters to EPSG:4326 GPS coordinates."""
    point = distance(meters=abs(y_offset_m)).destination(
        (center_lat, center_lon), bearing=0 if y_offset_m > 0 else 180
    )
    point = distance(meters=abs(x_offset_m)).destination(
        (point.latitude, point.longitude), bearing=90 if x_offset_m > 0 else 270
    )
    return point.longitude, point.latitude

def assemble_chronic_matrix(session: Session, scenario_id: str, payload: dict, completed_runs: list, baseline_c: float):
    """
    Dynamically locates the latest VTK surface slices across all steady-state wind rose runs,
    registers chronic scenario metadata, calculates the weighted annual concentration array,
    and persists non-attainment polygons into PostGIS.
    """
    if not completed_runs:
        return

    site_name = payload["site_name"]
    scenario_type = payload.get("scenario_type", "chronic_exposure")
    cell_size = payload.get("mesh", {}).get("base_cell_size", 25.0)
    center_lat = payload["sources"][0]["latitude"]
    center_lon = payload["sources"][0]["longitude"]

    # 1. Register Parent Scenario Metadata (Idempotent)
    existing = session.query(Tier4Scenario).filter_by(scenario_id=scenario_id).first()
    if existing:
        session.delete(existing)
        session.flush()

    scenario_record = Tier4Scenario(
        scenario_id=scenario_id,
        site_name=site_name,
        scenario_type=scenario_type,
        wind_direction_deg=0.0,  # Multi-vector annual matrix
        total_sources=len(payload.get("sources", [])),
        max_hazard_index=0.0
    )
    session.add(scenario_record)
    session.flush()

    # Dynamic file resolver for time-step directories
    def _get_latest_vtp_path(case_dir: str) -> str | None:
        surfaces_dir = os.path.join(case_dir, "postProcessing", "surfaces")
        if not os.path.exists(surfaces_dir):
            return None
        time_dirs = []
        for d in os.listdir(surfaces_dir):
            try:
                time_dirs.append((float(d), d))
            except ValueError:
                continue
        if not time_dirs:
            return None
        time_dirs.sort(key=lambda x: x[0])
        latest_dir = time_dirs[-1][1]
        vtp_path = os.path.join(surfaces_dir, latest_dir, "breathing_zone.vtp")
        return vtp_path if os.path.exists(vtp_path) else None

    # 2. Establish Base Spatial Node Canvas
    first_vtp = _get_latest_vtp_path(completed_runs[0]["case_dir"])
    if not first_vtp:
        print(f"Error: Could not locate breathing_zone.vtp in {completed_runs[0]['case_dir']}")
        return

    base_mesh = pv.read(first_vtp)
    points = base_mesh.points
    annual_concentration = np.zeros(base_mesh.n_points)

    # 3. Weighted Matrix Accumulation
    for run in completed_runs:
        vtk_path = _get_latest_vtp_path(run["case_dir"])
        if vtk_path:
            mesh = pv.read(vtk_path)
            # Detect concentration 'C' or fall back to thermodynamic 'T'
            if 'C' in mesh.point_data:
                c_array = mesh.point_data['C']
            elif 'T' in mesh.point_data:
                c_array = mesh.point_data['T']
            else:
                c_array = np.zeros(base_mesh.n_points)

            annual_concentration += (c_array * run["weight"])

    # Add atmospheric baseline (Google Air Quality)
    annual_concentration += baseline_c
    max_val = float(np.max(annual_concentration))
    scenario_record.max_hazard_index = max_val

    # 4. Spatial Contouring & PostGIS Cataloging
    chronic_polys = []
    degree_buffer = (cell_size / 2) / 111111.0
    chronic_threshold = payload.get("chronic_threshold_ug_m3", 12.0)  # EPA PM2.5 threshold

    for idx, conc in enumerate(annual_concentration):
        if conc > chronic_threshold:
            x, y = points[idx][0], points[idx][1]
            lon, lat = latlon_from_local_xy(center_lat, center_lon, x, y)
            poly = Point(lon, lat).buffer(degree_buffer, cap_style=3)
            chronic_polys.append(poly)

    if chronic_polys:
        merged_geom = unary_union(chronic_polys)
        if isinstance(merged_geom, Polygon):
            merged_geom = MultiPolygon([merged_geom])

        impact_record = Tier4SurfaceImpact(
            scenario_id=scenario_id,
            pollutant=payload.get("pollutant", "PM2.5"),
            hazard_zone="Chronic_Non_Attainment",
            concentration=max_val,
            geometry=merged_geom.wkt
        )
        session.add(impact_record)

    session.commit()