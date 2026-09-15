import os
import json
from pydantic import BaseModel, Field, ConfigDict

# ---------------------------------------------------------
# 1. Input Schemas (Strict Validation Rules)
# ---------------------------------------------------------
class SiteSchema(BaseModel):
    """Base schema ensuring site names are SQL-safe."""
    model_config = ConfigDict(extra='forbid')
    site_name: str = Field(..., pattern=r"^[a-z0-9_]+$", description="Sanitized site identifier")

class PointSourceSchema(SiteSchema):
    """Schema for individual smokestacks and Tier 3 coordinate anchors."""
    lat: float = Field(..., ge=-90.0, le=90.0, description="Anchor Latitude")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Anchor Longitude")

class Tier1FusedSchema(SiteSchema):
    """Schema for Tier 1 satellite baseline mapping."""
    pollutant_clean: str = Field(..., pattern=r"^[a-z0-9_]+$", description="E.g., pm25, no2")

class Tier1PixelSchema(SiteSchema):
    """Schema for fetching timeseries pixel arrays."""
    timeseries_col: str = Field(..., pattern=r"^[a-z0-9_]+_timeseries$")

# ---------------------------------------------------------
# 2. Raw SQL Templates
# ---------------------------------------------------------
TEMPLATES = {
    # ------------------
    # TIER 1 QUERIES
    # ------------------
    "tier1_pixel": {
        "description": "Extracts raw satellite timeseries and joins to planetary boundary layer.",
        "schema": Tier1PixelSchema,
        "template": """
        SELECT 
            ROW_NUMBER() OVER () AS id,
            ST_X(ST_Centroid(s.geometry)) AS longitude,
            ST_Y(ST_Centroid(s.geometry)) AS latitude,
            ST_AsText(s.geometry) AS wkt_geometry,
            s.{timeseries_col} AS raw_timeseries,
            COALESCE(c.pbl_height_m, 1000.0) AS pblh_meters,
            298.15 AS temperature_k,
            101325.0 AS pressure_pa
        FROM {site_name}.air_quality_satellite_chicago_macro s
        LEFT JOIN public.planetary_boundary_layer_chicago_macro c
            ON ST_Intersects(ST_Centroid(s.geometry), c.geometry);
        """
    },
    
    "tier1_fused_insert": {
        "description": "Constructs the final geometry table for compliant Tier 1 grids.",
        "schema": Tier1FusedSchema,
        "template": """
        CREATE TABLE public.tier1_fused_{pollutant_clean}_{site_name} AS 
        SELECT id, 
               ST_SetSRID(ST_GeomFromText(wkt_geometry), 4326) AS geometry,
               estimate AS {pollutant_clean}_estimate, 
               margin_of_error_95, lower_bound, upper_bound,
               pollutant, physical_unit, averaging_window, naaqs_limit, naaqs_exceedance
        FROM (
            SELECT * FROM jsonb_populate_recordset(NULL::record, CAST(:json_data AS jsonb)) 
            AS (id int, wkt_geometry text, estimate float, margin_of_error_95 float, 
                lower_bound float, upper_bound float, pollutant text, physical_unit text, 
                averaging_window text, naaqs_limit float, naaqs_exceedance boolean)
        ) as data;
        """
    },

    # ------------------
    # TIER 2 QUERIES
    # ------------------

    "prep_vulnerability": {
        "description": "Prepares the residential and non-residential data for impact analysis in tier 2.",
        "schema": SiteSchema,
        "template": """
        CREATE TABLE rails_north.vulnerability_profile_{site_name} AS
            WITH hospital_buildings AS (
                SELECT DISTINCT ON (b.id)
                    'hospital_3d_' || b.id AS building_id,
                    ST_SetSRID(b.geometry, 4326) AS geometry,
                    GREATEST(200, ((ST_Area(b.geometry::geography) * COALESCE(b.num_floors, GREATEST(1, ROUND(b.height / 3.5)), 1)) / 30)::int) AS population,
                    'hospital'::text AS exposure_category,
                    'acute_sensitive'::text AS exposure_type
                FROM rails_north.overture_buildings_3d_{site_name} b
                JOIN rails_north.overture_places_{site_name} p ON ST_Intersects(b.geometry, p.geometry)
                WHERE p.primary_category = 'hospital'
            ),
            school_buildings AS (
                SELECT DISTINCT ON (b.id)
                    'school_' || b.id AS building_id,
                    ST_SetSRID(b.geometry, 4326) AS geometry,
                    COALESCE(GREATEST(150, ((ST_Area(b.geometry::geography) * COALESCE(b.num_floors, GREATEST(1, ROUND(b.height / 3.5)), 1)) / 25)::int), 300) AS population,
                    a.amenity AS exposure_category,
                    'acute_sensitive'::text AS exposure_type
                FROM rails_north.amenities_{site_name} a
                JOIN rails_north.overture_buildings_3d_{site_name} b ON ST_DWithin(a.geometry::geography, b.geometry::geography, 20)
                WHERE a.amenity IN ('school', 'kindergarten', 'social_facility')
                AND NOT EXISTS (
                    SELECT 1 FROM hospital_buildings h WHERE ST_Intersects(b.geometry, h.geometry)
                )
            ),
            residential_buildings AS (
                SELECT 
                    r.building_id::text,
                    ST_SetSRID(r.geometry, 4326) AS geometry,
                    r.est_population AS population,
                    'residential'::text AS exposure_category,
                    'chronic_24h'::text AS exposure_type
                FROM rails_north.buildings_demographic_profile_{site_name} r
                WHERE NOT EXISTS (
                    SELECT 1 FROM hospital_buildings h WHERE ST_Intersects(r.geometry, h.geometry)
                )
                AND NOT EXISTS (
                    SELECT 1 FROM school_buildings s WHERE ST_Intersects(r.geometry, s.geometry)
                )
            ),
            open_spaces AS (
                SELECT DISTINCT ON (l.id)
                    'open_space_' || l.id AS building_id,
                    ST_SetSRID(l.geometry, 4326) AS geometry,
                    GREATEST(50, (ST_Area(l.geometry::geography) / 100)::int) AS population,
                    l.leisure AS exposure_category,
                    'transient_daytime'::text AS exposure_type
                FROM rails_north.landuse_{site_name} l
                WHERE l.leisure IN ('park', 'playground', 'pitch', 'stadium')
            )
            SELECT * FROM hospital_buildings
            UNION ALL
            SELECT * FROM school_buildings
            UNION ALL
            SELECT * FROM residential_buildings
            UNION ALL
            SELECT * FROM open_spaces;
            """
    },
    
    "tier2_exposure": {
        "description": "Extracts ambient baseline data from vulnerability profiles.",
        "schema": SiteSchema,
        "template": """
        SELECT 
            b.building_id,
            b.building_id AS sector,
            COALESCE(b.population, 0) AS total_population,
            COALESCE(b.exposure_category, 'residential') AS exposure_category,
            COALESCE(b.exposure_type, 'chronic_24h') AS exposure_type,
            1.0 AS decay_factor,
            ST_AsText(b.geometry) AS wkt_geometry
        FROM rails_north.vulnerability_profile_{site_name} b;
        """
    },

    "tier2_empirical_lur": {
        "description": "Calculates localized land use regression from major industrial sources.",
        "schema": SiteSchema,
        "template": """
        WITH building_buffers AS (
            SELECT b.building_id, b.geometry
            FROM rails_north.vulnerability_profile_{site_name} b
        ),
        industrial_load AS (
            SELECT 
                bb.building_id,
                COALESCE(SUM((p.nox_g_per_sec * 31.536) / GREATEST(POWER(ST_Distance(bb.geometry::geography, p.geometry::geography) / 100.0, 2), 1.0)), 0) AS nox_load_1km,
                COALESCE(SUM((p.pm25_g_per_sec * 31.536) / GREATEST(POWER(ST_Distance(bb.geometry::geography, p.geometry::geography) / 100.0, 2), 1.0)), 0) AS pm25_load_1km,
                COALESCE(MIN(ST_Distance(bb.geometry::geography, p.geometry::geography)), 5000) AS dist_to_nearest_source_m
            FROM building_buffers bb
            LEFT JOIN rails_north.epa_point_sources_{site_name} p
                ON ST_DWithin(bb.geometry::geography, p.geometry::geography, 3000)
            GROUP BY bb.building_id
        )
        SELECT 
            b.building_id,
            i.nox_load_1km,
            i.pm25_load_1km,
            i.dist_to_nearest_source_m
        FROM building_buffers b
        JOIN industrial_load i ON b.building_id = i.building_id;
        """
    },

    "tier2_knn_idw": {
        "description": "Executes Spatial Inverse Distance Weighting across 9 nearest satellite grid cells.",
        "schema": SiteSchema,
        "template": """
        WITH bldgs AS (
            SELECT building_id, ST_Centroid(geometry) AS geom 
            FROM rails_north.vulnerability_profile_{site_name}
        )
        SELECT 
            b.building_id AS sector,
            n.no2_estimate::float, n.pm25_estimate::float,
            n.so2_estimate::float, n.hcho_estimate::float
        FROM bldgs b
        CROSS JOIN LATERAL (
            WITH knn AS (
                SELECT 
                    n_grid.no2_estimate, p_grid.pm25_estimate,
                    s_grid.so2_estimate, h_grid.hcho_estimate,
                    1.0 / GREATEST(POWER(ST_Distance(n_grid.geometry::geography, b.geom::geography), 2), 1.0) AS weight
                FROM public.tier1_fused_no2_{site_name} n_grid
                JOIN public.tier1_fused_pm25_{site_name} p_grid ON n_grid.id = p_grid.id
                JOIN public.tier1_fused_so2_{site_name} s_grid ON n_grid.id = s_grid.id
                JOIN public.tier1_fused_hcho_{site_name} h_grid ON n_grid.id = h_grid.id
                WHERE n_grid.geometry && ST_Expand(b.geom, 0.05)
                ORDER BY n_grid.geometry <-> b.geom 
                LIMIT 9
            )
            SELECT 
                SUM(no2_estimate * weight) / SUM(weight) AS no2_estimate,
                SUM(pm25_estimate * weight) / SUM(weight) AS pm25_estimate,
                SUM(so2_estimate * weight) / SUM(weight) AS so2_estimate,
                SUM(hcho_estimate * weight) / SUM(weight) AS hcho_estimate
            FROM knn
        ) n;
        """
    },

    # ------------------
    # TIER 3 QUERIES
    # ------------------

    "build_point_exposure_wedges": {
        "description": "Creates standard 12-direction wedges for visualizing impacts.",
        "schema": PointSourceSchema,
        "template": """
        CREATE TABLE {target_table} AS
            WITH sites AS (
                SELECT (ST_Dump(ST_GeomFromText(:multipoint_wkt, 4326))).geom AS center
            ),
            sectors AS (
                SELECT 
                    idx, sector,
                    RADIANS((idx * {sector_step} - {half_step} + 360)::numeric % 360) AS a_start,
                    RADIANS((idx * {sector_step})::numeric % 360) AS a_mid,
                    RADIANS((idx * {sector_step} + {half_step})::numeric % 360) AS a_end
                FROM (VALUES {values_tuples}) AS t(idx, sector)
            ),
            wedge_geoms AS (
                SELECT 
                    s.sector,
                    ST_SetSRID(ST_MakePolygon(ST_MakeLine(ARRAY[
                        sites.center,
                        ST_Project(sites.center::geography, CAST(:outer_m AS DOUBLE PRECISION), s.a_start)::geometry,
                        ST_Project(sites.center::geography, CAST(:outer_m AS DOUBLE PRECISION), s.a_mid)::geometry,
                        ST_Project(sites.center::geography, CAST(:outer_m AS DOUBLE PRECISION), s.a_end)::geometry,
                        sites.center
                    ])), 4326) AS geometry_outer,
                    ST_SetSRID(ST_MakePolygon(ST_MakeLine(ARRAY[
                        sites.center,
                        ST_Project(sites.center::geography, CAST(:inner_m AS DOUBLE PRECISION), s.a_start)::geometry,
                        ST_Project(sites.center::geography, CAST(:inner_m AS DOUBLE PRECISION), s.a_mid)::geometry,
                        ST_Project(sites.center::geography, CAST(:inner_m AS DOUBLE PRECISION), s.a_end)::geometry,
                        sites.center
                    ])), 4326) AS geometry_inner
                FROM sectors s CROSS JOIN sites
            ),
            merged_wedges AS (
                SELECT 
                    sector,
                    ST_Union(geometry_outer) AS geometry_outer,
                    ST_Union(geometry_inner) AS geometry_inner
                FROM wedge_geoms
                GROUP BY sector
            )
            SELECT 
                w.sector,
                COUNT(DISTINCT CASE WHEN ST_Intersects(b.geometry, w.geometry_inner) THEN b.geometry END)::int AS homes_in_band183,
                COUNT(DISTINCT CASE WHEN ST_Intersects(b.geometry, w.geometry_outer) THEN b.geometry END)::int AS homes_in_ring305,
                ROUND(COALESCE(SUM(CASE WHEN ST_Intersects(b.geometry, w.geometry_inner) THEN b.population END), 0)::numeric, 1)::float AS pop_in_band183,
                ROUND(COALESCE(SUM(CASE WHEN ST_Intersects(b.geometry, w.geometry_outer) THEN b.population END), 0)::numeric, 1)::float AS pop_in_ring305,
                w.geometry_outer AS geometry
            FROM merged_wedges w
            LEFT JOIN {bldg_table} b ON ST_Intersects(b.geometry, w.geometry_outer)
            GROUP BY w.sector, w.geometry_outer;
            """
    },

    "tier3_dispersion": {
        "description": "Calculates ST_Distance and 1/d^2 decay for a localized point source.",
        "schema": PointSourceSchema,
        "template": """
        WITH stack AS (SELECT ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326) AS geom)
        SELECT 
            b.building_id AS sector,
            DEGREES(ST_Azimuth(s.geom, ST_Centroid(b.geometry))) AS azimuth_deg,
            1.0 / GREATEST(POWER(ST_Distance(b.geometry::geography, s.geom::geography)/100.0, 2), 1.0) AS decay_factor
        FROM rails_north.vulnerability_profile_{site_name} b CROSS JOIN stack s;
        """
    },
    
    "tier3_wind_anchor": {
        "description": "Fetches the localized 16-point wind array nearest to the primary stack.",
        "schema": PointSourceSchema,
        "template": """
        SELECT wind_direction_timeseries 
        FROM rails_north.climate_wind_grid_{site_name}
        ORDER BY ST_Distance(geometry, ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326))
        LIMIT 1;
        """
    }
}

# ---------------------------------------------------------
# 3. Deterministic Builder
# ---------------------------------------------------------
def generate_knowledge_base(output_dir: str = "knowledge_tasks", filename: str = "queries.json"):
    """
    Folds the schemas and string templates into a unified JSON manifest.
    """
    os.makedirs(output_dir, exist_ok=True)
    manifest = {}
    
    for query_id, data in TEMPLATES.items():
        # Clean up leading/trailing whitespace from multi-line strings
        clean_template = "\n".join([line.strip() for line in data["template"].strip().split("\n")])
        
        manifest[query_id] = {
            "description": data["description"],
            "schema": data["schema"].model_json_schema(),
            "template": clean_template
        }
        
    output_path = os.path.join(output_dir, filename)
    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=4)
        
    print(f"✅ Successfully compiled {len(manifest)} queries to '{output_path}'")

if __name__ == "__main__":
    generate_knowledge_base()