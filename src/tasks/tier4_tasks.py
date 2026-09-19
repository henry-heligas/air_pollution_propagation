import os
import shutil
import asyncio
import subprocess
from sqlalchemy.orm import Session
from celery_app import celery_app
from config import settings
from src.io.db_connector import db_manager, engine
from src.physics.openfoam import build_openfoam_case
from src.io.tier4_extractor import extract_tier4_results

@celery_app.task(name="run_tier4_cfd_disaster")
def run_tier4_cfd_disaster(payload: dict, schema_name: str):
    return asyncio.run(async_tier4_execution(payload, schema_name))

async def async_tier4_execution(payload: dict, schema_name: str):
    site_name = payload["site_name"]
    scenario_type = payload.get("scenario_type", "acute_fire")
    sources = payload.get("sources", [])
    
    if not sources:
        return {"status": "failed", "reason": "No emission sources provided."}

    # 1. Clone the Base Case
    # Assuming your base_case lives in src/physics/base_case
    base_case_path = os.path.join(os.getcwd(), "src", "physics", "base_case")
    case_dir = f"/opt/openfoam_runs/{site_name}_{scenario_type}"
    
    # Wipe the old run folder if it exists to ensure a clean slate
    if os.path.exists(case_dir):
        shutil.rmtree(case_dir)
        
    try:
        shutil.copytree(base_case_path, case_dir)
    except FileNotFoundError:
        return {"status": "failed", "step": "setup", "error": "base_case directory not found."}

    # 2. Delegate Dictionary Generation to Physics Module
    # This overwrites the dummy 0/T, 0/U, and system/topoSetDict in the cloned folder
    build_openfoam_case(payload, case_dir)

    # 3. Execute OpenFOAM Meshing 
    try:
        subprocess.run(["blockMesh", "-case", case_dir], check=True)
        # Bypassing snappyHexMesh for this empty wind-tunnel trial run
        subprocess.run(["topoSet", "-case", case_dir], check=True)
    except subprocess.CalledProcessError as e:
        return {"status": "failed", "step": "meshing", "error": str(e)}

    # 4. Execute the CFD Solver 
    try:
        subprocess.run(["buoyantBoussinesqPimpleFoam", "-case", case_dir], check=True)
    except subprocess.CalledProcessError as e:
        return {"status": "failed", "step": "solving", "error": str(e)}

    # 5. Post-Processing
    try:
        with Session(engine) as session:
            registered_id = extract_tier4_results(session, case_dir, payload)
    except Exception as e:
        return {"status": "failed", "step": "extraction", "error": str(e)}

    return {
        "status": "success", 
        "scenario_id": registered_id,
        "case_dir": case_dir
    }