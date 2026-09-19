import os
import math
import shutil
import asyncio
import subprocess
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from celery_app import celery_app
from config import settings
from src.io.db_connector import engine
from src.physics.openfoam import build_openfoam_case
from src.io.tier4_extractor import extract_tier4_results, assemble_chronic_matrix

@celery_app.task(name="run_tier4_cfd_simulation")
def run_tier4_cfd_simulation(payload: dict, schema_name: str):
    return asyncio.run(async_tier4_execution(payload, schema_name))

async def async_tier4_execution(payload: dict, schema_name: str):
    mode = payload.get("scenario_mode", "acute")
    
    if mode == "acute":
        return await execute_acute_pipeline(payload, schema_name)
    elif mode == "chronic":
        return await execute_chronic_matrix_pipeline(payload, schema_name)
    else:
        return {"status": "failed", "error": "Invalid scenario mode."}

async def execute_acute_pipeline(payload: dict, schema_name: str):
    """Your existing, fully-functioning transient disaster simulation."""
    site_name = payload["site_name"]
    scenario_type = payload.get("scenario_type", "acute_fire")
    case_dir = os.path.join(os.getcwd(), "openfoam_runs", f"{site_name}_{scenario_type}")
    base_case_path = os.path.join(os.getcwd(), "src", "physics", "base_case")
    of_bashrc = "/usr/lib/openfoam/openfoam2406/etc/bashrc"
    
    # 1. Setup
    if os.path.exists(case_dir): shutil.rmtree(case_dir)
    shutil.copytree(base_case_path, case_dir)
    build_openfoam_case(payload, case_dir)

    # 2. Meshing & Solving (Transient buoyantBoussinesqPimpleFoam)
    try:
        subprocess.run(f"source {of_bashrc} && blockMesh -case {case_dir}", shell=True, executable='/bin/bash', check=True)
        subprocess.run(f"source {of_bashrc} && topoSet -case {case_dir}", shell=True, executable='/bin/bash', check=True)
        subprocess.run(f"source {of_bashrc} && buoyantBoussinesqPimpleFoam -case {case_dir}", shell=True, executable='/bin/bash', check=True)
    except subprocess.CalledProcessError as e:
        return {"status": "failed", "step": "physics_solver", "error": str(e)}

    # 3. Extraction
    with Session(engine) as session:
        scenario_id = extract_tier4_results(session, case_dir, payload)

    return {"status": "success", "scenario_id": scenario_id, "mode": "acute"}

def _generate_wind_rose_matrix_from_db(site_name: str) -> list:
    """
    Fetches Google climate data from PostGIS: "{site_name}"."climate_plume_physics_{site_name}"
    Converts (wind_u_10m, wind_v_10m) vectors into meteorological speed and direction.
    """
    table_name = f"climate_plume_physics_{site_name}"
    query = f'SELECT * FROM "{site_name}"."{table_name}"'
    
    try:
        df = pd.read_sql_query(query, con=engine)
    except Exception as e:
        print(f"Error querying climate table: {e}")
        return []

    # Check for Google Climate vector format (wind_u_10m, wind_v_10m)
    u_col = next((c for c in df.columns if 'wind_u' in c.lower() or c.lower() == 'u'), None)
    v_col = next((c for c in df.columns if 'wind_v' in c.lower() or c.lower() == 'v'), None)

    if u_col and v_col:
        u = pd.to_numeric(df[u_col], errors='coerce')
        v = pd.to_numeric(df[v_col], errors='coerce')
        
        speed = np.sqrt(u**2 + v**2)
        # Vector conversion to meteorological wind direction (degrees from North)
        direction = (270 - np.degrees(np.arctan2(v, u))) % 360
        clean_df = pd.DataFrame({'dir': direction, 'speed': speed}).dropna()
    else:
        # Fallback for standard direction/speed columns
        dir_col = next((c for c in df.columns if 'dir' in c.lower() or 'deg' in c.lower()), None)
        speed_col = next((c for c in df.columns if 'speed' in c.lower() or 'vel' in c.lower()), None)
        if not dir_col or not speed_col:
            return []
        
        d = pd.to_numeric(df[dir_col], errors='coerce') % 360
        s = pd.to_numeric(df[speed_col], errors='coerce')
        clean_df = pd.DataFrame({'dir': d, 'speed': s}).dropna()

    total_records = len(clean_df)
    if total_records == 0:
        return []

    # Bin directions into 16 sectors of 22.5 degrees each
    bins = np.arange(-11.25, 371.25, 22.5)
    labels = [0, 22.5, 45, 67.5, 90, 112.5, 135, 157.5, 180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5]
    
    clean_df['sector'] = pd.cut(clean_df['dir'], bins=bins, labels=labels, right=False)
    
    matrix = []
    grouped = clean_df.groupby('sector', observed=False)
    for sector_deg, group in grouped:
        frequency = len(group) / total_records
        if frequency > 0.005:  # Filter sectors representing <0.5% of the record
            matrix.append({
                "wind_direction_deg": float(sector_deg),
                "reference_velocity": float(group['speed'].mean()),
                "annual_frequency": float(frequency)
            })
            
    return matrix


def _get_baseline_pollution_from_db(site_name: str, pollutant: str = "PM2.5") -> float:
    """
    Fetches baseline air quality from PostGIS: "{site_name}"."google_air_quality_{site_name}"
    Sanitizes keys (e.g., "PM2.5" matches "pm25_mean").
    """
    table_name = f"google_air_quality_{site_name}"
    query = f'SELECT * FROM "{site_name}"."{table_name}"'
    
    try:
        df = pd.read_sql_query(query, con=engine)
        # Normalize target pollutant string ("PM2.5" -> "pm25")
        norm_target = pollutant.lower().replace('.', '').replace('-', '').replace(' ', '')
        
        for col in df.columns:
            norm_col = col.lower().replace('.', '').replace('-', '').replace('_', '')
            if norm_target in norm_col and 'mean' in norm_col:
                series = pd.to_numeric(df[col], errors='coerce').dropna()
                if not series.empty:
                    return float(series.mean())
    except Exception as e:
        print(f"Warning: Could not fetch baseline pollution table for {site_name}: {e}")
        
    return 0.0

async def execute_chronic_matrix_pipeline(payload: dict, schema_name: str):
    site_name = payload["site_name"]
    scenario_id = f"{site_name}_chronic_annual"
    pollutant = payload.get("pollutant", "PM2.5")
    of_bashrc = "/usr/lib/openfoam/openfoam2406/etc/bashrc"
    base_case_path = os.path.join(os.getcwd(), "src", "physics", "base_case")

    # 1. Fetch wind rose matrix and baseline pollution from PostGIS
    wind_matrix = _generate_wind_rose_matrix_from_db(site_name)
    baseline_concentration = _get_baseline_pollution_from_db(site_name, pollutant)
    
    if not wind_matrix:
        return {"status": "failed", "error": f"No climate data found in table climate_plume_physics_{site_name}"}

    completed_runs = []

    # 2. Execute C++ Solvers Across Wind Rose Sectors
    for run_config in wind_matrix:
        direction_deg = run_config["wind_direction_deg"]
        speed_m_s = run_config["reference_velocity"]
        
        # Calculate wind vector components (wind blowing TOWARD direction)
        rad = math.radians(direction_deg)
        u_x = round(-speed_m_s * math.sin(rad), 4)
        u_y = round(-speed_m_s * math.cos(rad), 4)

        case_dir = os.path.join(os.getcwd(), "openfoam_runs", f"{site_name}_chronic_{direction_deg}deg")

        # Setup directory
        if os.path.exists(case_dir):
            shutil.rmtree(case_dir)
        shutil.copytree(base_case_path, case_dir)

        # Inject current wind vector into payload copy
        sector_payload = payload.copy()
        sector_payload["wind"] = {
            "u_x": u_x,
            "u_y": u_y,
            "u_z": 0.0,
            "wind_direction_deg": direction_deg
        }

        # Build mesh and boundary files
        build_openfoam_case(sector_payload, case_dir)

        # Invoke OpenFOAM C++ Binaries
        try:
            subprocess.run(f"source {of_bashrc} && blockMesh -case {case_dir}", shell=True, executable='/bin/bash', check=True)
            subprocess.run(f"source {of_bashrc} && topoSet -case {case_dir}", shell=True, executable='/bin/bash', check=True)
            
            # Using buoyantBoussinesqPimpleFoam for testing, or simpleFoam for steady-state
            subprocess.run(f"source {of_bashrc} && buoyantBoussinesqPimpleFoam -case {case_dir}", shell=True, executable='/bin/bash', check=True)

            completed_runs.append({
                "direction": direction_deg,
                "case_dir": case_dir,
                "weight": run_config["annual_frequency"]
            })
        except subprocess.CalledProcessError as e:
            print(f"Warning: Sector {direction_deg}deg failed: {e}")

    # 3. Assemble Weighted Footprint in PostGIS via PyVista
    with Session(engine) as session:
        assemble_chronic_matrix(
            session=session,
            scenario_id=scenario_id,
            payload=payload,
            completed_runs=completed_runs,
            baseline_c=baseline_concentration
        )

    return {
        "status": "success",
        "scenario_id": scenario_id,
        "mode": "chronic",
        "matrix_runs_completed": len(completed_runs)
    }