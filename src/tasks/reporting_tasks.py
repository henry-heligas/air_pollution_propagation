import os
import json
import asyncio
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import seaborn as sns
from math import pi

from celery_app import celery_app
from src.io.db_connector import db_manager

@celery_app.task(name="run_analysis_handoff")
def run_analysis_handoff(site_name: str):
    return asyncio.run(async_run_analysis_handoff(site_name))

def export_dataframe_as_png(df: pd.DataFrame, output_path: str, title: str):
    """Renders a pandas DataFrame as a styled, presentation-ready PNG table."""
    # Dynamically size the figure based on rows and columns
    fig_width = max(8, len(df.columns) * 1.8)
    fig_height = max(3, len(df) * 0.5 + 1.5)
    
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis('off')
    
    # Format column headers for display
    display_cols = [col.replace('_', ' ').title() for col in df.columns]
    
    table = ax.table(
        cellText=df.values,
        colLabels=display_cols,
        loc='center',
        cellLoc='center'
    )
    
    # Styling
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.8)
    
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor('#d3d3d3')
        if row == 0:
            # Header row styling (Glaze04 Teal)
            cell.set_text_props(weight='bold', color='white')
            cell.set_facecolor('#00676e')
        else:
            # Alternating row colors
            if row % 2 == 0:
                cell.set_facecolor('#f5f5f5')
                
    plt.title(title, fontsize=15, fontweight='bold', pad=20, color='#333333')
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close(fig)

async def async_run_analysis_handoff(site_name: str):
    output_dir = f"artifacts/{site_name}_handoff"
    os.makedirs(output_dir, exist_ok=True)
    
    # =========================================================================
    # 1. DATA EXTRACTION
    # =========================================================================
    # Fetch Tier 3 Proposed Impact Data
    sql = f"""
    SELECT * FROM rails_north.tier3_environmental_impacts 
    WHERE site_name = '{site_name}';
"""
    df = pd.DataFrame(await db_manager.execute_query(sql))
    
    if df.empty:
        return {"status": "failed", "reason": "No Tier 3 data found in PostGIS."}

    # =========================================================================
    # 2. JSON CONTEXT EXPORT (For LLM Narrative Assembly)
    # =========================================================================
    # Baseline Context
    baseline_context = {
        "site_name": site_name,
        "average_baseline_hazard_index": float(df["baseline_hazard_index"].mean()),
        "max_baseline_hazard_index": float(df["baseline_hazard_index"].max()),
        "buildings_over_safety_threshold": int(len(df[df["baseline_hazard_index"] > 1.0]))
    }
    with open(os.path.join(output_dir, f"tier1_2_baseline_summary_{site_name}.json"), "w") as f:
        json.dump(baseline_context, f, indent=2)

    # Proposed Impact Context
    impact_context = {
        "site_name": site_name,
        "peak_hazard_index_increase": float(df["delta_hazard_index"].max()),
        "peak_cancer_risk_increase_per_million": float(df["delta_cancer_risk"].max())
    }
    with open(os.path.join(output_dir, f"tier3_proposed_impact_summary_{site_name}.json"), "w") as f:
        json.dump(impact_context, f, indent=2)

    # =========================================================================
    # 3. CSV TABULAR EXPORT (For Reports & Regulatory Review)
    # =========================================================================
    # A. Top 10 Most Impacted Buildings
    top_10 = df.sort_values(by="delta_hazard_index", ascending=False).head(10)
    csv_cols = ["building_id", "exposure_category", "dist_to_nearest_source_m", 
                "baseline_hazard_index", "proposed_hazard_index", "delta_hazard_index"]
    top_10[csv_cols].to_csv(os.path.join(output_dir, f"tier3_top_impacted_{site_name}.csv"), index=False)

    # B. Executive Summary
    exec_summary_data = [
        {'Metric': 'Total Population Assessed', 'Value': f"{df.get('total_population', pd.Series([0]*len(df))).sum():,}"}, 
        {'Metric': 'Peak Localized Hazard Index Increase (ΔHI)', 'Value': f"{df['delta_hazard_index'].max():.3f}"},
        {'Metric': 'Peak Localized Cancer Risk Increase', 'Value': f"{df['delta_cancer_risk'].max():.2f} per million"},
        {'Metric': 'Buildings Exceeding EPA Hazard Threshold (Proposed)', 'Value': f"{len(df[df['proposed_hazard_index'] > 1.0])}"}, 
        {'Metric': 'Sensitive Receptors Newly Endangered (HI > 1.0)', 'Value': f"{len(df[(df['exposure_category'] != 'residential') & (df['baseline_hazard_index'] <= 1.0) & (df['proposed_hazard_index'] > 1.0)])}"}
    ]
    pd.DataFrame(exec_summary_data).to_csv(os.path.join(output_dir, f"tier3_executive_summary_{site_name}.csv"), index=False)

    export_dataframe_as_png(
        df=pd.DataFrame(exec_summary_data),
        output_path=os.path.join(output_dir, f"tier3_executive_summary_{site_name}.png"),
        title="Executive Impact Summary"
    )

    # C. Statistical Summary by Zoning
    stat_summary_df = df.groupby('exposure_category').agg(
        building_count=('building_id', 'count'),
        mean_baseline_hi=('baseline_hazard_index', 'mean'),
        std_baseline_hi=('baseline_hazard_index', 'std'),
        mean_delta_hi=('delta_hazard_index', 'mean'),
        max_delta_hi=('delta_hazard_index', 'max'),
        mean_delta_cancer=('delta_cancer_risk', 'mean'),
        max_delta_cancer=('delta_cancer_risk', 'max')
    ).reset_index().round(3)
    stat_summary_df.to_csv(os.path.join(output_dir, f"tier3_statistical_summary_by_zoning_{site_name}.csv"), index=False)

    export_dataframe_as_png(
            df=stat_summary_df,
            output_path=os.path.join(output_dir, f"tier3_statistical_summary_by_zoning_{site_name}.png"),
            title="Impact Distribution by Zoning Category"
        )

    # =========================================================================
    # 4. STATISTICAL VISUALIZATION (Publication Quality)
    # =========================================================================
    # Global Style Configurations
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = ['Open Sans', 'JetBrains Mono', 'DejaVu Sans', 'Roboto']
    
    sns.set_theme(style="white", rc={
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.color": "#e0e0e0",
        "grid.linestyle": "--",
        "axes.edgecolor": "#d3d3d3",
        "axes.linewidth": 0.8
    })

    # Dynamic Color Palette Generation based on colors.txt mappings
    base_palette = {
        'residential': '#f2ea8c',    # B Resid 1
        'commercial': '#bebebe',     # Gray
        'school': '#ff7f00',         # Orange
        'hospital': '#ff0000',       # Red
        'daycare': '#ffff00',        # Yellow
        'kindergarten': '#ffff00',   # Yellow
        'park': '#00ff00',           # Green
        'playground': '#7fff00',     # Chartreuse 
        'pitch': '#3fbf7f',          # SeaGreen
        'stadium': '#f5a3a3',        # B Retail
        'social_facility': '#98c4e6' # B Office 1
    }
    custom_palette = {cat: base_palette.get(cat, '#d3d3d3') for cat in df['exposure_category'].unique()}

    # ==========================================
    # Tier 1: Ambient Chemical Comparison
    # ==========================================
    # Note: Replace these mocked arrays with the actual Tier 1 chemical fetch from city_df
    chemicals = ['PM2.5', 'NO2', 'SO2', 'Benzene', 'Formaldehyde']
    city_levels = [65, 45, 20, 30, 40] # Expressed as % of EPA Limit
    local_levels = [85, 80, 25, 95, 60] 

    x = np.arange(len(chemicals))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 6))
    
    # Plotting Broader City (Gray) and Local Study Area (Teal)
    rects1 = ax.bar(x - width/2, city_levels, width, label='Broader City Average', color='#bebebe')
    rects2 = ax.bar(x + width/2, local_levels, width, label='Local Study Area', color='#00676e')

    # EPA Action Limit Line
    ax.axhline(100, color='#002123', linestyle='--', linewidth=2, label='EPA Action Limit (100%)')

    ax.set_ylabel('Concentration (% of EPA Allowable Limit)', fontsize=11)
    ax.set_title('Ambient Chemical Burden: Neighborhood vs. Citywide', fontsize=15, fontweight='bold', pad=20)
    ax.set_xticks(x)
    ax.set_xticklabels(chemicals, fontsize=11)
    ax.legend(loc='upper right', frameon=True)

    # Add explicit percentage labels above the bars
    def autolabel(rects):
        for rect in rects:
            height = rect.get_height()
            ax.annotate(f'{height}%',
                        xy=(rect.get_x() + rect.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha='center', va='bottom', fontsize=10)
    autolabel(rects1)
    autolabel(rects2)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"tier1_chemical_comparison_{site_name}.png"), dpi=300)
    plt.close(fig)

    # --- Tier 1: Pollution Rose ---
    directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    N = len(directions)
    angles = [n / float(N) * 2 * pi for n in range(N)]

    # Note: Replace these synthetic arrays with your real Tier 1 wind/chemical PostGIS queries
    wind_freq = np.array([15, 10, 5, 8, 25, 20, 10, 7]) 
    low_pol = wind_freq * 0.5   # Under 12 µg/m³
    med_pol = wind_freq * 0.3   # 12-35 µg/m³
    high_pol = wind_freq * 0.2  # Over 35 µg/m³

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
    ax.set_theta_offset(pi / 2) # Put North at the top
    ax.set_theta_direction(-1)  # Draw clockwise

    plt.xticks(angles, directions, color='#333333', size=11, fontweight='bold')

    width = 2 * pi / N
    c_low, c_med, c_high = '#bebebe', '#2ea4b1', '#002123' 

    ax.bar(angles, low_pol, width=width, color=c_low, edgecolor='white', label='Low PM2.5 (< 12 µg/m³)')
    ax.bar(angles, med_pol, width=width, bottom=low_pol, color=c_med, edgecolor='white', label='Moderate PM2.5 (12-35 µg/m³)')
    ax.bar(angles, high_pol, width=width, bottom=low_pol+med_pol, color=c_high, edgecolor='white', label='High PM2.5 (> 35 µg/m³)')

    ax.set_rlabel_position(90) # Move radial axis labels to the East
    plt.yticks(color="grey", size=9)
    ax.yaxis.grid(True, linestyle='--', color='#e0e0e0')
    ax.xaxis.grid(True, linestyle='-', color='#e0e0e0')

    fig.suptitle("Prevailing Winds & Chemical Transport", fontsize=16, fontweight='bold', y=1.05)
    ax.set_title(f"Tier 1: Wind Frequency and PM2.5 Concentrations - {site_name}", fontsize=12, color='#555555', pad=20)
    plt.legend(loc='lower center', bbox_to_anchor=(0.5, -0.15), ncol=1, frameon=False)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"tier1_pollution_rose_{site_name}.png"), dpi=300, bbox_inches='tight')
    plt.close(fig)

    # --- Chart A: Baseline HI Histogram ---
    fig, ax = plt.subplots(figsize=(10, 6))
    sns.histplot(
        data=df, 
        x='baseline_hazard_index', 
        hue='exposure_category', 
        palette=custom_palette, 
        multiple="stack", 
        bins=40,
        edgecolor='#a0a0a0', # Lighter bar outlines
        linewidth=0.5,
        ax=ax
    )
    ax.axvline(1.0, color='#ff0000', linestyle='--', linewidth=1.5, label='EPA Safety Threshold (HI=1.0)')
    ax.set_title(f"Tier 1 & 2: Baseline Hazard Index - {site_name.replace('_', ' ').title()}", fontsize=14, pad=15)
    ax.set_xlabel("Baseline Hazard Index", fontsize=12)
    ax.set_ylabel("Number of Buildings", fontsize=12)
    ax.legend(title="Receptor Type", frameon=True, edgecolor='#e0e0e0')
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"tier1_baseline_hi_hist_{site_name}.png"), dpi=300)
    plt.close(fig)
    
    # --- Chart B: Bivariate 2D Histogram (Blue Scale) ---
    fig, ax = plt.subplots(figsize=(10, 6))
    sns.histplot(
        data=df, x='dist_to_nearest_source_m', y='delta_cancer_risk',
        bins=40, pmax=0.9, cmap="Blues", cbar=True, # Changed to Blues
        cbar_kws={'label': 'Number of Buildings'}, ax=ax
    )
    ax.axhline(1.0, color='#ff0000', linestyle=':', linewidth=1.5, label='EPA Threshold')
    ax.invert_xaxis() # Keep proximity logic
    
    fig.suptitle("Localized Carcinogenic Impact Density", fontsize=18, fontweight='bold', y=0.98)
    ax.set_title("Tier 3: Bivariate Frequency of Impact vs. Proximity", fontsize=13, color='#555555', pad=10)
    ax.set_xlabel("Proximity to Nearest Stack (meters) ➔ Closer", fontsize=12, fontweight='medium', labelpad=10)
    ax.set_ylabel("Delta Cancer Risk (per million)", fontsize=12, fontweight='medium', labelpad=10)
    plt.legend(loc='upper left', frameon=True, edgecolor='#e0e0e0')
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"tier3_cancer_risk_bivariate_{site_name}.png"), dpi=300)
    plt.close(fig)

    # --- Chart C: Zoning Violin Plot (Reverting from Neighborhoods) ---
    fig, ax = plt.subplots(figsize=(10, 5))
    sns.violinplot(
        data=df, x='exposure_category', y='delta_hazard_index', 
        color='#2ea4b1', inner=None, linewidth=0, 
        cut=0, # Prevents drawing past actual data points
        density_norm='width', # Forces all violins to have the same visual width
        ax=ax
    )

    y_cap = np.percentile(df['delta_hazard_index'], 99)
    ax.set_ylim(-0.001, y_cap)

    fig.suptitle("Acute Toxicity Increase (ΔHI) by Zoning Type", fontsize=16, fontweight='bold', y=0.98)
    ax.set_title(f"Tier 3: Distribution of Impact - {site_name}", fontsize=12, color='#555555', pad=10)
    ax.set_ylabel("Increase in Hazard Index (ΔHI)")
    ax.set_xlabel("")
    
    plt.savefig(os.path.join(output_dir, f"tier3_violin_{site_name}.png"), dpi=300)
    plt.close(fig)
    
    # --- Chart D: Regional KDE Comparison (Broader City vs Study Area) ---
    # Note: Replace `city_hi_data` with your actual Tier 1 citywide PostGIS query.
    # For this example, we mock a citywide distribution that is generally cleaner than the study area.
    city_hi_data = np.random.lognormal(mean=np.log(0.6), sigma=0.2, size=5000) 
    
    df_comp = pd.DataFrame({
        'hazard_index': np.concatenate([city_hi_data, df['baseline_hazard_index'].values]),
        'location': ['Broader City'] * len(city_hi_data) + [f'{site_name.replace("_", " ").title()}'] * len(df)
    })

    fig, ax = plt.subplots(figsize=(10, 6))
    sns.histplot(
        data=df_comp, 
        x='hazard_index', 
        hue='location', 
        element='step', 
        fill=False, 
        cumulative=True, 
        stat='density', 
        common_norm=False,
        ax=ax
)
    ax.axvline(1.0, color='#ff0000', linestyle='--', linewidth=1.5)
    ax.text(1.02, ax.get_ylim()[1]*0.9, 'EPA Threshold', color='#ff0000', fontsize=10, fontweight='bold')
    
    fig.suptitle("Baseline Toxicity Comparison", fontsize=18, fontweight='bold', y=0.98)
    ax.set_title("Local Study Area vs. Broader City Distribution", fontsize=13, color='#555555', pad=10)
    ax.set_xlabel("Baseline Hazard Index (HI)", fontsize=12, fontweight='medium', labelpad=10)
    ax.set_ylabel("Density (Relative Frequency)", fontsize=12, fontweight='medium', labelpad=10)
    
    legend = ax.get_legend()
    if legend:
        legend.set_title(None)
        legend.get_frame().set_edgecolor('#e0e0e0')
        
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"tier1_regional_comparison_{site_name}.png"), dpi=300)
    plt.close(fig)

    # --- Chart E: Pollutant Burden Radar Chart ---
    # Note: Replace these mocked HQ arrays with actual queries from your Tier 1 pollutant tables.
    pollutants = ['PM2.5', 'NO2', 'SO2', 'CO', 'O3', 'Benzene']
    N = len(pollutants)
    city_hq = [0.3, 0.4, 0.1, 0.2, 0.6, 0.1]
    study_hq = [0.7, 0.8, 0.3, 0.4, 0.7, 0.5] 

    city_values = city_hq + [city_hq[0]]
    study_values = study_hq + [study_hq[0]]
    angles = [n / float(N) * 2 * pi for n in range(N)]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
    ax.set_theta_offset(pi / 2)
    ax.set_theta_direction(-1)

    plt.xticks(angles[:-1], pollutants, color='black', size=11, fontweight='bold')
    ax.set_rlabel_position(0)
    plt.yticks([0.2, 0.4, 0.6, 0.8, 1.0], ["0.2", "0.4", "0.6", "0.8", "1.0 (Threshold)"], color="grey", size=9)
    plt.ylim(0, 1.2)

    ax.plot(angles, city_values, linewidth=1.5, linestyle='solid', label='Broader City', color='#bebebe')
    ax.fill(angles, city_values, '#bebebe', alpha=0.1)
    ax.plot(angles, study_values, linewidth=1.5, linestyle='solid', label=f'{site_name.replace("_", " ").title()}', color='#ff7f00')
    ax.fill(angles, study_values, '#ff7f00', alpha=0.2)
    ax.plot(angles, [1.0]*(N+1), color='#ff0000', linestyle='--', linewidth=1.5, label='EPA Threshold')

    fig.suptitle("Pollutant Burden Fingerprint", fontsize=18, fontweight='bold', y=1.05)
    ax.set_title("Study Area vs. Citywide Average (Hazard Quotients)", fontsize=13, color='#555555', pad=20)
    plt.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1), frameon=True, edgecolor='#e0e0e0')

    plt.savefig(os.path.join(output_dir, f"tier1_pollutant_radar_{site_name}.png"), dpi=300, bbox_inches='tight')
    plt.close(fig)

    # --- Chart F: Before & After Stacked Impact ---
    
    # Calculate percentiles based on DELTA (New Impact)
    percentiles = [99, 90, 75, 50, 25]
    labels = ["Top 1% (Peak)", "Top 10%", "Top 25%", "Top 50% (Median)", "Bottom 25%"]
    
    delta_rank_base, delta_rank_delta = [], []
    for p in percentiles:
        val = np.percentile(df['delta_hazard_index'], p)
        idx = df['delta_hazard_index'].sub(val).abs().idxmin()
        delta_rank_base.append(df.loc[idx, 'baseline_hazard_index'])
        delta_rank_delta.append(df.loc[idx, 'delta_hazard_index'])

    # Calculate percentiles based on BASELINE (Historical Burden)
    base_rank_base, base_rank_delta = [], []
    for p in percentiles:
        val = np.percentile(df['baseline_hazard_index'], p)
        idx = df['baseline_hazard_index'].sub(val).abs().idxmin()
        base_rank_base.append(df.loc[idx, 'baseline_hazard_index'])
        base_rank_delta.append(df.loc[idx, 'delta_hazard_index'])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6), sharey=True)
    
    c_base, c_delta = '#bebebe', '#00676e'

    # Left: Ranked by New Impact
    ax1.barh(labels, delta_rank_base, color=c_base, height=0.5, label='Historical Baseline')
    ax1.barh(labels, delta_rank_delta, left=delta_rank_base, color=c_delta, height=0.5, label='New Impact (Δ)')
    ax1.axvline(1.0, color='#002123', linestyle='--', linewidth=2)
    ax1.set_title("Clustered by Highest New Impact (Δ)", fontsize=13)
    ax1.set_xlabel("Total Hazard Index (HI)", fontsize=11)
    ax1.invert_yaxis()

    # Right: Ranked by Historical Burden
    ax2.barh(labels, base_rank_base, color=c_base, height=0.5)
    ax2.barh(labels, base_rank_delta, left=base_rank_base, color=c_delta, height=0.5)
    ax2.axvline(1.0, color='#002123', linestyle='--', linewidth=2, label='EPA Threshold (1.0)')
    ax2.set_title("Clustered by Highest Historical Burden", fontsize=13)
    ax2.set_xlabel("Total Hazard Index (HI)", fontsize=11)

    fig.suptitle("Comparative Impact: Historical Burden vs. New Facility Addition", fontsize=16, fontweight='bold', y=1.02)
    fig.legend(loc='lower center', bbox_to_anchor=(0.5, -0.05), ncol=3, frameon=False)
    plt.savefig(os.path.join(output_dir, f"tier3_dual_impact_{site_name}.png"), dpi=300, bbox_inches='tight')
    plt.close(fig)

    # =========================================================================
    # 3.1 TIER 1 CSV EXPORTS (Chemical Summaries & Baseline Comparisons)
    # =========================================================================
    # A. Chemical Summary Data (Raw Concentrations & EPA Threshold Percentages)
    # Note: Replace this mock query with your actual PostGIS query against `tier1_fused_*` tables
    chemical_summary_data = [
        {"pollutant": "PM2.5", "unit": "ug/m3", "city_avg": 8.1, "local_avg": 10.6, "epa_limit": 12.0, "pct_epa_allowable_limit": 88.3},
        {"pollutant": "NO2", "unit": "ppb", "city_avg": 24.5, "local_avg": 42.4, "epa_limit": 53.0, "pct_epa_allowable_limit": 80.0},
        {"pollutant": "SO2", "unit": "ppb", "city_avg": 15.0, "local_avg": 18.8, "epa_limit": 75.0, "pct_epa_allowable_limit": 25.0},
        {"pollutant": "Benzene", "unit": "ppb", "city_avg": 0.9, "local_avg": 2.85, "epa_limit": 3.0, "pct_epa_allowable_limit": 95.0},
        {"pollutant": "Formaldehyde", "unit": "ppb", "city_avg": 2.4, "local_avg": 3.6, "epa_limit": 6.0, "pct_epa_allowable_limit": 60.0}
    ]
    df_chem_summary = pd.DataFrame(chemical_summary_data)
    df_chem_summary.to_csv(
        os.path.join(output_dir, f"tier1_chemical_summary_{site_name}.csv"), 
        index=False
    )

    export_dataframe_as_png(
        df=df_chem_summary,
        output_path=os.path.join(output_dir, f"tier1_chemical_summary_{site_name}.png"),
        title="Tier 1: Ambient Chemical Baseline Profiling"
    )

    # B. Baseline Comparisons Data (Local Study Area vs. Broader City Distribution)
    # Note: Replace `city_df` reference with your citywide baseline database query
    city_hi_series = pd.Series(city_hi_data) if 'city_hi_data' in locals() else df["baseline_hazard_index"] * 0.8
    
    baseline_comp_data = [
        {
            "metric": "Average (Mean) Hazard Index",
            "broader_city": float(city_hi_series.mean()),
            "local_study_area": float(df["baseline_hazard_index"].mean()),
            "relative_increase_pct": float(((df["baseline_hazard_index"].mean() - city_hi_series.mean()) / city_hi_series.mean()) * 100)
        },
        {
            "metric": "90th Percentile (High Risk)",
            "broader_city": float(city_hi_series.quantile(0.90)),
            "local_study_area": float(df["baseline_hazard_index"].quantile(0.90)),
            "relative_increase_pct": float(((df["baseline_hazard_index"].quantile(0.90) - city_hi_series.quantile(0.90)) / city_hi_series.quantile(0.90)) * 100)
        },
        {
            "metric": "Absolute Peak (Max Hazard Index)",
            "broader_city": float(city_hi_series.max()),
            "local_study_area": float(df["baseline_hazard_index"].max()),
            "relative_increase_pct": float(((df["baseline_hazard_index"].max() - city_hi_series.max()) / city_hi_series.max()) * 100)
        }
    ]
    df_base_comp = pd.DataFrame(baseline_comp_data).round(3)
    df_base_comp.to_csv(
        os.path.join(output_dir, f"tier1_baseline_comparisons_{site_name}.csv"), 
        index=False
    )

    export_dataframe_as_png(
        df=df_base_comp,
        output_path=os.path.join(output_dir, f"tier1_baseline_comparisons_{site_name}.png"),
        title="Baseline Toxicity: Local vs. Citywide"
    )

    return {"status": "success", "handoff_directory": output_dir}