from pydantic import BaseModel, Field

class PointSource(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=180.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)

class ProposedFacilityRequest(BaseModel):
    site_name: str = Field(..., example="rails_north")
    sources: list[PointSource] = Field(
        default=[{"latitude": 41.830, "longitude": -87.633}], 
        description="List of smokestack/emission point coordinates"
    )
    emissions_tpy: dict[str, float] = Field(default={"NO2": 10.0, "PM2.5": 2.5, "BENZENE": 0.5})
    tier2_inner_m: float = Field(default=183.0)
    tier2_outer_m: float = Field(default=305.0)