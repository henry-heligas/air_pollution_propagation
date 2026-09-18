from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query
from typing import List
from pydantic import BaseModel, Field

# Refactored task imports from modular router files
from src.tasks.tier1_tasks import run_tier1_baseline
from src.tasks.prep_tasks import run_prep_vulnerability_profile  # NEW IMPORT
from src.tasks.tier2_tasks import run_tier2_risk_model
from src.tasks.proposed_tasks import run_proposed_site_assessment, trigger_tier3_scenario

from src.schemas.impact import ProposedFacilityRequest
from celery_app import celery_app

app = FastAPI(
    title="Urban Morphology Pollution API",
    description="Multi-tier atmospheric chemistry and physical dispersion engine."
)

class PointSource(BaseModel):
    source_id: str
    latitude: float
    longitude: float
    emissions_tpy: dict[str, float]

class Tier1BaselineRequest(BaseModel):
    site_name: str = Field(..., example="rails_north")
    pollutants: List[str] = Field(
        default=["NO2", "PM2.5", "PM10", "SO2", "CO", "HCHO", "CH4", "O3"],
        example=["NO2", "PM2.5"]
    )

class Tier2RiskRequest(BaseModel):
    site_name: str = Field(..., example="rails_north")

class Tier3ScenarioPayload(BaseModel):
    site_name: str
    schema_name: str = "rails_north"
    sources: List[PointSource]

class SitePayload(BaseModel):
    site_name: str = Field(..., example="rails_north")

@app.post("/run_tier1_baseline")
async def run_tier1_baseline_endpoint(payload: Tier1BaselineRequest):
    task_ids = {}
    for chem in payload.pollutants:
        task = run_tier1_baseline.delay(payload.site_name, chem)
        task_ids[chem] = task.id
        
    return {
        "status": "bulk_queued",
        "site_name": payload.site_name,
        "tasks_queued": len(task_ids),
        "task_ids": task_ids
    }

@app.post("/api/v1/assessments/prep-vulnerability")
async def trigger_vulnerability_prep(payload: SitePayload):
    """
    Deduplicates spatial features and compiles clean vulnerability baseline table.
    """
    task = run_prep_vulnerability_profile.delay(payload.site_name)
    return {
        "status": "Processing", 
        "task_id": task.id, 
        "site": payload.site_name,
        "operation": "Vulnerability_Profile_Prep"
    }

@app.post("/api/v1/assessments/tier2")
async def trigger_tier2_assessment(payload: SitePayload):
    """
    Executes the pure-SQL Tier 2 neighborhood risk assessment.
    """
    task = run_tier2_risk_model.delay(payload.site_name)
    return {"status": "Processing", "task_id": task.id, "site": payload.site_name}

@app.get("/api/v1/pipeline/results/{site_name}")
async def get_pipeline_results(
    site_name: str, 
    tier: int = Query(1, description="1 for Tier 1 Satellite, 2 for Community Risk, 3 for Proposed Site"),
    pollutant: str = Query("no2", description="Pollutant for Tier 1 baseline (e.g. no2, pm25)")
):
    try:
        geojson = await fetch_geojson_results(site_name, tier, pollutant)
        return geojson
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.post("/evaluate_proposed_site")
async def evaluate_proposed_site(payload: ProposedFacilityRequest):
    payload_dict = payload.model_dump()
    task = run_proposed_site_assessment.delay(payload_dict)
    return {
        "status": "processing",
        "task_id": task.id,
        "site_name": payload.site_name,
        "sources_evaluated": len(payload.sources)
    }

@app.post("/api/v1/assessments/{site_name}/analysis-handoff")
async def trigger_analysis_handoff(site_name: str):
    """
    Manually triggers the final data packaging, chart generation, 
    and LLM context creation for downstream reporting.
    """
    task = celery_app.send_task("run_analysis_handoff", args=[site_name])
    return {
        "status": "Analysis handoff initiated",
        "task_id": task.id,
        "site_name": site_name
    }

@app.post("/api/v1/simulate/tier3-scenario")
async def simulate_tier3_scenario(request: Tier3ScenarioPayload):
    """
    Ingests a multi-source emission JSON, validates it, 
    and queues the spatial decay simulation in Celery.
    """
    # 1. Convert the validated Pydantic object back into a dictionary for Celery
    payload_dict = request.model_dump()
    
    # 2. Hand off to the Celery worker
    task = trigger_tier3_scenario.delay(
        payload=payload_dict, 
        schema_name=request.schema_name
    )
    
    # 3. Return immediately so the API stays highly responsive
    return {
        "message": f"Scenario '{request.site_name}' queued successfully.",
        "task_id": task.id,
        "schema_target": request.schema_name,
        "sources_detected": len(request.sources)
    }