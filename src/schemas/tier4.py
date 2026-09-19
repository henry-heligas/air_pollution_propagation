from pydantic import BaseModel, Field
from typing import List, Dict

class CFDWindConfig(BaseModel):
    wind_direction_deg: float = Field(default=270.0, ge=0.0, le=360.0, description="Meteorological wind direction (270 = West)")
    reference_velocity: float = Field(default=5.0, gt=0.0, description="Wind speed (m/s) at the reference height")
    reference_height: float = Field(default=10.0, gt=0.0, description="Height (m) where the reference velocity was measured")
    roughness_length_z0: float = Field(default=1.0, ge=0.0, description="Urban surface friction (1.0 = dense city)")

class CFDMeshConfig(BaseModel):
    domain_radius_m: float = Field(default=500.0, gt=100.0, description="Distance from center to domain edges")
    domain_z_max: float = Field(default=200.0, gt=50.0, description="Height of the sky boundary")
    base_cell_size: float = Field(default=5.0, gt=0.1, description="Background wind tunnel grid resolution (m)")
    building_refinement_level: int = Field(default=3, ge=1, le=5, description="Octree subdivision level for buildings")

class CFDSource(BaseModel):
    source_id: str
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    emissions_tpy: Dict[str, float]
    
    # 3D Physical Parameters
    source_radius_m: float = Field(default=2.0, gt=0.0, description="Physical size of the vent or fire pool.")
    source_height_m: float = Field(default=0.0, ge=0.0, description="Z-elevation of the emission (0m for ground).")
    exit_velocity_m_s: float = Field(default=2.5, ge=0.0, description="Mechanical or buoyant upward velocity.")
    exit_temperature_k: float = Field(default=293.15, gt=0.0, description="Temperature of the plume in Kelvin.")

class Tier4CFDPayload(BaseModel):
    site_name: str = Field(..., description="The schema and run isolation namespace")
    scenario_type: str = Field(default="acute_fire", description="Tag for output tables (e.g., 'acute_fire', 'derailment')")
    mesh: CFDMeshConfig = CFDMeshConfig()
    wind: CFDWindConfig = CFDWindConfig()
    sources: List[CFDSource]