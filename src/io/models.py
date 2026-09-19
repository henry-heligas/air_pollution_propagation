from sqlalchemy import Column, String, Float, Integer, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from geoalchemy2 import Geometry

Base = declarative_base()

class Tier4Scenario(Base):
    """1. The Scenario Registry: Tracks every disaster simulation run."""
    __tablename__ = 'tier4_scenario_metadata'
    
    # We use a composite of site_name + scenario to keep things organized
    scenario_id = Column(String, primary_key=True, index=True) # e.g., "amtrak_study_diesel_fire_270"
    site_name = Column(String, index=True, nullable=False)     # e.g., "amtrak_study"
    scenario_type = Column(String, nullable=False)             # e.g., "acute_fire"
    
    wind_direction_deg = Column(Float)
    total_sources = Column(Integer)
    max_hazard_index = Column(Float)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships to link this registry to the data below
    impacts = relationship("Tier4SurfaceImpact", back_populates="scenario", cascade="all, delete")
    sensors = relationship("Tier4Receptor", back_populates="scenario", cascade="all, delete")
    assets = relationship("Tier4Asset", back_populates="scenario", cascade="all, delete")


class Tier4SurfaceImpact(Base):
    """2. The Evacuation Map: 2D contoured polygons for mapping (Z=1.5m)."""
    __tablename__ = 'tier4_surface_impacts_2d'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    scenario_id = Column(String, ForeignKey('tier4_scenario_metadata.scenario_id'), index=True)
    
    pollutant = Column(String, nullable=False)      # e.g., "PM2.5"
    hazard_zone = Column(String, nullable=False)    # e.g., "Evacuation_Required"
    concentration = Column(Float)                   # Exact boundary threshold
    
    # The actual geometric boundary on the map
    geometry = Column(Geometry(geometry_type='MULTIPOLYGON', srid=4326))
    
    scenario = relationship("Tier4Scenario", back_populates="impacts")


class Tier4Receptor(Base):
    """3. Virtual Sensor Network: Minute-by-minute exposure for charting."""
    __tablename__ = 'tier4_receptor_timeseries'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    scenario_id = Column(String, ForeignKey('tier4_scenario_metadata.scenario_id'), index=True)
    building_id = Column(String, index=True) # Links to your Tier 2 buildings
    
    timestep_sec = Column(Integer, nullable=False)  # e.g., 60, 120, 180 seconds
    pollutant = Column(String, nullable=False)
    concentration = Column(Float, nullable=False)
    
    scenario = relationship("Tier4Scenario", back_populates="sensors")


class Tier4Asset(Base):
    """4. 3D Render Assets: File paths to videos or 3D GLTF models."""
    __tablename__ = 'tier4_3d_assets'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    scenario_id = Column(String, ForeignKey('tier4_scenario_metadata.scenario_id'), index=True)
    
    asset_type = Column(String, nullable=False) # e.g., "plume_volume_gltf"
    file_path = Column(String, nullable=False)  # e.g., "s3://my-bucket/amtrak/fire.gltf"
    
    scenario = relationship("Tier4Scenario", back_populates="assets")