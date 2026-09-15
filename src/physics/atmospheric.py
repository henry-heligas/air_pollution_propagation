import re
import json
import numpy as np
import pandas as pd
import scipy.stats as st
from config import settings

def convert_column_to_surface(
    column_umol_m2: float, 
    target_unit: str, 
    gamma: float, 
    pblh_m: float = 1000.0, 
    temp_k: float = 298.15, 
    pressure_pa: float = 101325.0
) -> float:
    column_umol_m2, pblh_m, temp_k, pressure_pa, gamma = (
        float(column_umol_m2), float(pblh_m), float(temp_k), float(pressure_pa), float(gamma)
    )
    if np.isnan(column_umol_m2) or pblh_m <= 0:
        return np.nan
        
    molar_volume_m3_mol = (8.314 * temp_k) / pressure_pa
    surface_ppm = (column_umol_m2 / pblh_m) * molar_volume_m3_mol * gamma
    
    return surface_ppm * 1000.0 if target_unit.lower() == "ppb" else surface_ppm

def parse_wind_frequencies(wind_raw_json: str) -> pd.DataFrame:
    """
    Parses wind direction arrays into 16 compass sectors. 
    Pads unrepresented sectors with 0.0% to prevent missing keys during Tier 3 wind joins.
    """
    # Default zeroed DataFrame for all 16 sectors
    full_compass = pd.DataFrame({
        "sector": settings.COMPASS_SECTORS_16, 
        "wind_frequency_pct": 0.0
    })
    
    if not wind_raw_json:
        return full_compass
    try:
        wind_dirs = json.loads(wind_raw_json)
        if not wind_dirs:
            return full_compass
            
        sector_idx = np.floor(((np.array(wind_dirs) + 11.25) % 360) / 22.5).astype(int)
        counts = pd.Series(sector_idx).value_counts(normalize=True) * 100
        
        parsed_df = pd.DataFrame({
            "sector": [settings.COMPASS_SECTORS_16[i] for i in counts.index],
            "wind_frequency_pct": counts.values.round(2)
        })
        
        # Merge with full compass to ensure all 16 sectors exist
        merged = pd.merge(full_compass[["sector"]], parsed_df, on="sector", how="left").fillna(0.0)
        return merged
    except (json.JSONDecodeError, ValueError, TypeError):
        return full_compass

def format_pixel_compliance(df: pd.DataFrame, pollutant: str) -> pd.DataFrame:
    def parse_timeseries(val):
        if val is None:
            return []
        if isinstance(val, str):
            nums = re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', val)
            return [float(n) for n in nums]
        if hasattr(val, '__iter__'):
            return [float(v) for v in val if v is not None]
        return [float(val)]

    df["clean_ts"] = df["raw_timeseries"].apply(parse_timeseries)
    df["raw_column_estimate"] = df["clean_ts"].apply(lambda x: np.mean(x) if len(x) > 0 else np.nan)
    
    pol_key = pollutant.upper()
    lookup_key = "PM2.5" if pol_key == "PM25" else pol_key
    naaqs = settings.EPA_NAAQS_STANDARDS.get(
        lookup_key, 
        {"unit": "unknown", "limit": 999.9, "window": "unknown", "type": "mass", "gamma": 1.0}
    )
    
    if naaqs["type"] == "column":
        df["estimate"] = df.apply(
            lambda r: convert_column_to_surface(
                r["raw_column_estimate"], naaqs["unit"], naaqs["gamma"], 
                pblh_m=r.get("pblh_meters", 1000.0),
                temp_k=r.get("temperature_k", 298.15),
                pressure_pa=r.get("pressure_pa", 101325.0)
            ), axis=1
        )
    elif naaqs["type"] == "mixing_ratio":
        df["estimate"] = df["raw_column_estimate"] / 1e9
    elif naaqs["type"] == "stratospheric":
        df["estimate"] = df["raw_column_estimate"] * 0.00045
    else:
        df["estimate"] = df["raw_column_estimate"]

    def calc_error_margin(row):
        ts = row["clean_ts"]
        if len(ts) < 2:
            val = row["estimate"] if pd.notnull(row["estimate"]) else 0.0
            return 0.0, val, val
            
        arr = np.array(ts)
        se = st.sem(arr)
        margin = se * st.t.ppf((1 + 0.95) / 2., len(arr)-1)
        mean_val = np.mean(arr)
        
        if naaqs["type"] == "column":
            pblh, tk, pa = row.get("pblh_meters", 1000.0), row.get("temperature_k", 298.15), row.get("pressure_pa", 101325.0)
            margin = convert_column_to_surface(margin, naaqs["unit"], naaqs["gamma"], pblh_m=pblh, temp_k=tk, pressure_pa=pa)
            mean_val = convert_column_to_surface(mean_val, naaqs["unit"], naaqs["gamma"], pblh_m=pblh, temp_k=tk, pressure_pa=pa)
        elif naaqs["type"] == "mixing_ratio":
            margin, mean_val = margin / 1e9, mean_val / 1e9
        elif naaqs["type"] == "stratospheric":
            margin, mean_val = margin * 0.00045, mean_val * 0.00045
            
        # Clamp lower bound to 0.0 to prevent impossible negative atmospheric concentrations
        lower = max(0.0, mean_val - margin)
        upper = mean_val + margin
        return margin, lower, upper

    bounds = df.apply(calc_error_margin, axis=1)
    df["margin_of_error_95"] = [b[0] for b in bounds]
    df["lower_bound"] = [b[1] for b in bounds]
    df["upper_bound"] = [b[2] for b in bounds]
    df["pollutant"] = lookup_key
    df["physical_unit"] = naaqs["unit"]
    df["averaging_window"] = naaqs["window"]
    df["naaqs_limit"] = naaqs["limit"]
    df["naaqs_exceedance"] = df.apply(lambda r: r["estimate"] > r["naaqs_limit"] if pd.notnull(r["estimate"]) else False, axis=1)

    # Use errors="ignore" to handle variations where geometry or raw_timeseries are omitted
    return df.drop(columns=["raw_timeseries", "geometry", "clean_ts", "raw_column_estimate"], errors="ignore")