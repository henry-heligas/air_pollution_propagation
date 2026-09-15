from pydantic import BaseModel, Field
from typing import Dict, List, Optional

class PointSource(BaseModel):
    source_id: str = Field(default="unnamed_stack")
    latitude: float = Field(..., ge=-90.0, le=180.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    emissions_tpy: Dict[str, float] = Field(
        default={"NO2": 0.0, "PM2.5": 0.0, "BENZENE": 0.0},
        description="Tons per year of emissions for this specific stack"
    )

class ProposedFacilityRequest(BaseModel):
    site_name: str = Field(..., example="rails_north")
    sources: List[PointSource] = Field(
        description="List of smokestack/emission point coordinates and their distinct loads"
    )
    tier2_inner_m: float = Field(default=183.0)
    tier2_outer_m: float = Field(default=305.0)