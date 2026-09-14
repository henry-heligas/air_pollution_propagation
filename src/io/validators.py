import pandas as pd
import geopandas as gpd
import numpy as np
from shapely.geometry import box

class SensorDataValidator:
    def __init__(self, target_pollutants: list[str], buffer_degrees: float = 0.05):
        self.pollutants = target_pollutants
        self.buffer_degrees = buffer_degrees # Roughly 5km buffer

    def validate_with_buffer(self, gdf: gpd.GeoDataFrame, core_bounds: tuple) -> gpd.GeoDataFrame:
        # Create core and buffered bounding boxes
        core_box = box(*core_bounds)
        buffer_box = core_box.buffer(self.buffer_degrees)

        # 1. Flag Buffer vs Core Data (Do not delete)
        gdf["is_buffer"] = ~gdf.intersects(core_box)
        
        # Drop only data that falls completely outside the extended buffer zone
        gdf = gdf[gdf.intersects(buffer_box)]

        # 2. Flag and Nullify Physical Impossibilities
        gdf["QA_Flag"] = "PASS"
        for pol in self.pollutants:
            if pol in gdf.columns:
                # Flag extreme outliers or negative values
                invalid_mask = (gdf[pol] < 0) | (gdf[pol] > 5000)
                gdf.loc[invalid_mask, pol] = np.nan
                gdf.loc[invalid_mask, "QA_Flag"] = "IMPUTE_REQUIRED"

        # 3. Temporal Sorting
        gdf = gdf.sort_values(by="timestamp").drop_duplicates(subset=["timestamp", "station_id"])
        
        return gdf