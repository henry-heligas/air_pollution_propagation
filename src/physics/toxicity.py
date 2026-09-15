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

def calculate_proposed_facility_impact(df_merged: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates final toxicological impacts for a proposed facility.
    Preserves baseline metrics and generates explicit delta columns 
    to evaluate the localized increase in community risk.
    """
    tox = settings.TOXICOLOGICAL_STANDARDS
    
    # 1. Fill missing baseline LUR loads
    df_merged["nox_load_1km"] = df_merged.get("nox_load_1km", 0.0).fillna(0.0)
    df_merged["pm25_load_1km"] = df_merged.get("pm25_load_1km", 0.0).fillna(0.0)
    
    lur_nox_scaling = 0.005 * 0.75 
    lur_pm25_scaling = 0.005
    
    # 2. Extract the accumulated proposed plume variables
    c_no2_prop = df_merged.get("c_no2_prop_total", 0.0)
    c_pm25_prop = df_merged.get("c_pm25_prop_total", 0.0)
    c_benzene_prop = df_merged.get("c_benzene_prop_total", 0.0)
    
    # 3. Preserve Baseline State
    df_merged["baseline_hazard_index"] = df_merged["hazard_index"]
    df_merged["baseline_cancer_risk"] = df_merged["cancer_risk_per_million"]
    
    # 4. Calculate Combined State (Status Quo + Cumulative Proposed Facility Plume)
    total_no2 = df_merged["no2_estimate"] + (df_merged["nox_load_1km"] * lur_nox_scaling) + c_no2_prop
    total_pm25 = df_merged["pm25_estimate"] + (df_merged["pm25_load_1km"] * lur_pm25_scaling) + c_pm25_prop
    total_so2 = df_merged["so2_estimate"]
    
    # 5. Calculate Proposed Hazard Quotients
    df_merged["proposed_hq_no2"] = total_no2 / tox["NO2"]["rfc"]
    df_merged["proposed_hq_pm25"] = total_pm25 / tox["PM2.5"]["rfc"]
    df_merged["proposed_hq_so2"] = total_so2 / tox["SO2"]["rfc"]
    
    # 6. Calculate Proposed Cumulative Metrics
    df_merged["proposed_hazard_index"] = (
        df_merged["proposed_hq_no2"] + 
        df_merged["proposed_hq_pm25"] + 
        df_merged["proposed_hq_so2"]
    )
    df_merged["proposed_pop_weighted_hi"] = df_merged["proposed_hazard_index"] * df_merged["total_population"]
    
    total_benzene = (df_merged["hcho_estimate"] * 1.25) + c_benzene_prop
    df_merged["proposed_cancer_risk"] = (total_benzene * tox["BENZENE"]["iur"]) * 1_000_000
    
    # 7. Calculate Deltas (The absolute localized impact of the facility)
    df_merged["delta_hazard_index"] = df_merged["proposed_hazard_index"] - df_merged["baseline_hazard_index"]
    df_merged["delta_cancer_risk"] = df_merged["proposed_cancer_risk"] - df_merged["baseline_cancer_risk"]
    
    # Clean up intermediate plume totals for a cleaner GeoJSON output
    cols_to_drop = ["c_no2_prop_total", "c_pm25_prop_total", "c_benzene_prop_total", "c_no2_prop", "c_pm25_prop", "c_benzene_prop"]
    df_merged.drop(columns=[c for c in cols_to_drop if c in df_merged.columns], inplace=True)
    
    return df_merged