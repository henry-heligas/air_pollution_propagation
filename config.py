from typing import ClassVar
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # PostGIS Database Credentials
    POSTGIS_HOST: str = "localhost"
    POSTGIS_PORT: int = 5432
    POSTGIS_DB: str = "morpho"
    POSTGIS_USER: str = "postgres"
    POSTGIS_PASSWORD: str = "postgres"

    # Redis Broker Credentials
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # Tier 1 Table Templates
    TABLE_TIER1_TARGET: str = "{schema}.air_quality_satellite_{schema}"
    TABLE_TIER1_MACRO: str = "{schema}.air_quality_satellite_chicago_macro"
    TABLE_TIER1_CONTROL: str = "{schema}.air_quality_satellite_portage_park_control"
    TABLE_TIER1_SATELLITE_POLLUTANT: str = "{schema}.air_quality_satellite_{pollutant}_{schema}"

    # EPA Regulatory Baseline Limits
    EPA_PM25_STANDARD_UG_M3: float = 9.0
    
    # Compass Configuration
    COMPASS_SECTORS_16: ClassVar[list] = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 
                                          'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']

    # Tier 2 Exposure Thresholds
    TIER2_INNER_BAND_M: float = 183.0
    TIER2_OUTER_RING_M: float = 305.0
    TIER2_SECTOR_COUNT: int = 16

    # Schema Naming Conventions
    TABLE_SITE_BOUNDARY: str = "{schema}.site_boundary_{schema}"
    TABLE_BUILDINGS: str = "{schema}.buildings_demographic_profile_{schema}"
    TABLE_WIND_GRID: str = "{schema}.climate_wind_grid_{schema}"
    TABLE_TIER2_WEDGES: str = "{schema}.tier2_exposure_wedges_{schema}"
    TABLE_TIER1_SATELLITE: str = "{schema}.air_quality_satellite_{schema}"

    DEFAULT_PBLH_METERS: float = 1000.0  # Default planetary boundary layer height
    
    EPA_NAAQS_STANDARDS: ClassVar[dict] = {
        "PM2.5": {"unit": "ug/m3", "limit": 9.0,   "window": "Annual", "type": "mass"},
        "PM10":  {"unit": "ug/m3", "limit": 150.0, "window": "24-hour", "type": "mass"},
        "NO2":   {"unit": "ppb",   "limit": 100.0, "window": "1-hour",  "type": "column", "gamma": 0.75},
        "SO2":   {"unit": "ppb",   "limit": 75.0,  "window": "1-hour",  "type": "column", "gamma": 0.70},
        "CO":    {"unit": "ppm",   "limit": 9.0,   "window": "8-hour",  "type": "column", "gamma": 0.85},
        "O3":    {"unit": "ppb",   "limit": 70.0,  "window": "8-hour",  "type": "stratospheric", "gamma": 1.0},  
        "CH4":   {"unit": "ppm",   "limit": 2.0,   "window": "Annual",  "type": "mixing_ratio", "gamma": 1.0},
        "HCHO":  {"unit": "ppb",   "limit": 10.0,  "window": "1-hour",  "type": "column", "gamma": 0.60}
    }
    # Toxicity Weights as a Class Variable (ignored by env parsers)
    POLLUTANT_TOXICITY_WEIGHTS: ClassVar[dict] = {
        "NO2": 1.0,
        "PM2.5": 3.0,
        "SO2": 1.5,
        "BENZENE": 50.0,
        "CO": 0.05
    }

    # Toxicological Risk Parameters for Tier 2
    # RfC units match baseline (ppb or ug/m3). IUR for Benzene is risk per ug/m3.
    TOXICOLOGICAL_STANDARDS: ClassVar[dict] = {
        "NO2":     {"rfc": 100.0, "type": "non-cancer"}, # 100 ppb acute
        "PM2.5":   {"rfc": 9.0,   "type": "non-cancer"}, # 9.0 ug/m3 annual
        "SO2":     {"rfc": 75.0,  "type": "non-cancer"}, # 75 ppb acute
        "BENZENE": {"iur": 7.8e-6, "type": "cancer"}     # Lifetime risk per ug/m3
    }

    @property
    def async_database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGIS_USER}:{self.POSTGIS_PASSWORD}"
            f"@{self.POSTGIS_HOST}:{self.POSTGIS_PORT}/{self.POSTGIS_DB}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()