from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class SpatialBoundingBox(BaseModel):
    min_lon: float = Field(..., ge=-180.0, le=180.0)
    min_lat: float = Field(..., ge=-90.0, le=90.0)
    max_lon: float = Field(..., ge=-180.0, le=180.0)
    max_lat: float = Field(..., ge=-90.0, le=90.0)

class TimeWindow(BaseModel):
    start_time: datetime
    end_time: datetime

class AirQualityQueryPayload(BaseModel):
    site_name: str = Field(..., pattern=r"^[a-z0-9_]+$")
    bounds: SpatialBoundingBox
    time_window: TimeWindow
    target_pollutant: str = "PM2.5"