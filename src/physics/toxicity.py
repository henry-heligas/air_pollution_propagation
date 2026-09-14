import pandas as pd
from config import settings

def calculate_toxicological_risk(df_buildings: pd.DataFrame, df_baseline: pd.DataFrame) -> pd.DataFrame:
    tox = settings.TOXICOLOGICAL_STANDARDS
    df = pd.merge(df_buildings, df_baseline, on="sector", how="left")
    
    # Fill missing LUR loads with 0.0 in case a building has no nearby industrial sources
    df["nox_load_1km"] = df.get("nox_load_1km", 0.0).fillna(0.0)
    df["pm25_load_1km"] = df.get("pm25_load_1km", 0.0).fillna(0.0)
    
    # Empirical LUR dispersion scaling factor (converts annual distance-decayed load to ug/m3 ground increment)
    # NO2 conversion assumes a standard ~0.75 NOx-to-NO2 atmospheric conversion ratio
    lur_nox_scaling = 0.005 * 0.75 
    lur_pm25_scaling = 0.005

    # 1. Combine Macro Satellite Baseline + Micro Local LUR Industrial Load
    df["c_no2_local"] = (df["no2_estimate"] * df["decay_factor"]) + (df["nox_load_1km"] * lur_nox_scaling)
    df["c_pm25_local"] = (df["pm25_estimate"] * df["decay_factor"]) + (df["pm25_load_1km"] * lur_pm25_scaling)
    df["c_so2_local"] = df["so2_estimate"] * df["decay_factor"]
    df["c_hcho_local"] = df["hcho_estimate"] * df["decay_factor"]
    
    # 2. Hazard Quotients
    df["hq_no2"] = df["c_no2_local"] / tox["NO2"]["rfc"]
    df["hq_pm25"] = df["c_pm25_local"] / tox["PM2.5"]["rfc"]
    df["hq_so2"] = df["c_so2_local"] / tox["SO2"]["rfc"]
    
    # 3. Total Respiratory Hazard Index
    df["hazard_index"] = df["hq_no2"] + df["hq_pm25"] + df["hq_so2"]
    df["pop_weighted_hi"] = df["hazard_index"] * df["total_population"]
    
    # 4. Cancer Risk Metrics
    df["c_benzene_local"] = df["c_hcho_local"] * 1.25
    df["cancer_risk_per_million"] = (df["c_benzene_local"] * tox["BENZENE"]["iur"]) * 1_000_000
    
    return df

def calculate_proposed_facility_impact(
    df_decay: pd.DataFrame, 
    df_wind: pd.DataFrame, 
    df_baseline: pd.DataFrame, 
    emissions_tpy: dict[str, float]
) -> pd.DataFrame:
    tox = settings.TOXICOLOGICAL_STANDARDS
    
    df_merged = pd.merge(df_decay, df_wind, on="sector", how="left").fillna({"wind_frequency_pct": 1.0})
    df_merged = pd.merge(df_merged, df_baseline, on="sector", how="left")
    
    df_merged["nox_load_1km"] = df_merged.get("nox_load_1km", 0.0).fillna(0.0)
    df_merged["pm25_load_1km"] = df_merged.get("pm25_load_1km", 0.0).fillna(0.0)
    
    lur_nox_scaling = 0.005 * 0.75 
    lur_pm25_scaling = 0.005

    no2_tpy = emissions_tpy.get("NO2", 0.0)
    pm25_tpy = emissions_tpy.get("PM2.5", 0.0)
    benzene_tpy = emissions_tpy.get("BENZENE", 0.0)
    
    # Proposed facility increment from Tier 3 dispersion wedge
    df_merged["c_no2_prop"] = (no2_tpy * 0.5) * (df_merged["wind_frequency_pct"] / 100) * df_merged["decay_factor"]
    df_merged["c_pm25_prop"] = (pm25_tpy * 0.5) * (df_merged["wind_frequency_pct"] / 100) * df_merged["decay_factor"]
    df_merged["c_benzene_prop"] = (benzene_tpy * 0.5) * (df_merged["wind_frequency_pct"] / 100) * df_merged["decay_factor"]
    
    # Combined Status Quo (Satellite + Existing Industrial LUR) + Proposed Facility Plume
    total_no2 = (df_merged["no2_estimate"] * df_merged["decay_factor"]) + (df_merged["nox_load_1km"] * lur_nox_scaling) + df_merged["c_no2_prop"]
    total_pm25 = (df_merged["pm25_estimate"] * df_merged["decay_factor"]) + (df_merged["pm25_load_1km"] * lur_pm25_scaling) + df_merged["c_pm25_prop"]
    total_so2 = df_merged["so2_estimate"] * df_merged["decay_factor"]
    
    df_merged["hazard_index"] = (
        (total_no2 / tox["NO2"]["rfc"]) +
        (total_pm25 / tox["PM2.5"]["rfc"]) +
        (total_so2 / tox["SO2"]["rfc"])
    )
    
    total_benzene = (df_merged["hcho_estimate"] * df_merged["decay_factor"] * 1.25) + df_merged["c_benzene_prop"]
    df_merged["cancer_risk_per_million"] = (total_benzene * tox["BENZENE"]["iur"]) * 1_000_000
    
    return df_merged