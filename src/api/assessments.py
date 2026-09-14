from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/assessments", tags=["Assessments"])

class SiteAssessmentRequest(BaseModel):
    site_name: str

@router.post("/prep-vulnerability")
async def trigger_vulnerability_prep(payload: SiteAssessmentRequest):
    """
    Deduplicates spatial features and compiles clean vulnerability baseline table.
    """
    try:
        from src.tasks.prep_tasks import run_prep_vulnerability_profile
        task = run_prep_vulnerability_profile.delay(payload.site_name)
        return {
            "status": "queued",
            "task_id": task.id,
            "message": f"Vulnerability profile prep started for site '{payload.site_name}'."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))