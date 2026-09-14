from config import settings

COMPASS_SECTORS_16 = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 
                      'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']

def get_table_name(template: str, site_name: str) -> str:
    return template.format(schema=site_name)

def build_drop_table_sql(table_name: str) -> str:
    return f"DROP TABLE IF EXISTS {table_name};"

def build_tier1_pixel_sql(target_table: str, timeseries_col: str, site_name: str) -> str:
    schema = target_table.split('.')[0] if '.' in target_table else 'public'
    physics_table = f"{schema}.planetary_boundary_layer_chicago_macro"
    
    return f"""
    SELECT 
        ROW_NUMBER() OVER () AS id,
        ST_X(ST_Centroid(s.geometry)) AS longitude,
        ST_Y(ST_Centroid(s.geometry)) AS latitude,
        ST_AsText(s.geometry) AS wkt_geometry,
        s.geometry,
        s.{timeseries_col} AS raw_timeseries,
        COALESCE(c.pbl_height_m, 1000.0) AS pblh_meters,
        298.15 AS temperature_k,
        101325.0 AS pressure_pa
    FROM {target_table} s
    LEFT JOIN {physics_table} c
        ON ST_Intersects(ST_Centroid(s.geometry), c.geometry);
    """

def build_point_exposure_wedges_sql(target_table: str, bldg_table: str) -> str:
    sector_step = 360.0 / settings.TIER2_SECTOR_COUNT
    half_step = sector_step / 2.0
    values_tuples = ", ".join([f"({i}, '{label}')" for i, label in enumerate(COMPASS_SECTORS_16[:settings.TIER2_SECTOR_COUNT])])

    return f"""
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

def build_tier2_exposure_sql(site_name: str) -> str:
    """Extracts all neighborhood buildings for ambient baseline evaluation."""
    return f"""
    SELECT 
        b.building_id,
        'Ambient' AS sector,
        COALESCE(b.population, 0) AS total_population,
        COALESCE(b.exposure_category, 'residential') AS exposure_category,
        COALESCE(b.exposure_type, 'chronic_24h') AS exposure_type,
        1.0 AS decay_factor,  -- No single-source distance decay for ambient baseline
        ST_AsText(b.geometry) AS wkt_geometry
    FROM rails_north.vulnerability_profile_{site_name} b;
    """

def build_tier2_empirical_lur_sql(site_name: str) -> str:
    return f"""
    WITH building_buffers AS (
        SELECT 
            b.building_id,
            COALESCE(b.population, 0) AS total_population,
            b.geometry
        FROM rails_north.vulnerability_profile_{site_name} b
    ),
    industrial_load AS (
        SELECT 
            bb.building_id,
            -- Apply 1/d^2 decay in SQL over a 3km radius (distance normalized in hundreds of meters)
            COALESCE(SUM(
                (p.nox_g_per_sec * 31.536) / 
                GREATEST(POWER(ST_Distance(bb.geometry::geography, p.geometry::geography) / 100.0, 2), 1.0)
            ), 0) AS nox_load_1km,
            
            COALESCE(SUM(
                (p.pm25_g_per_sec * 31.536) / 
                GREATEST(POWER(ST_Distance(bb.geometry::geography, p.geometry::geography) / 100.0, 2), 1.0)
            ), 0) AS pm25_load_1km,
            
            COALESCE(MIN(ST_Distance(bb.geometry::geography, p.geometry::geography)), 5000) AS dist_to_nearest_source_m
        FROM building_buffers bb
        LEFT JOIN rails_north.epa_point_sources_{site_name} p
            ON ST_DWithin(bb.geometry::geography, p.geometry::geography, 3000)
        GROUP BY bb.building_id
    )
    SELECT 
        b.building_id,
        b.total_population,
        i.nox_load_1km,
        i.pm25_load_1km,
        i.dist_to_nearest_source_m,
        ST_AsText(b.geometry) AS wkt_geometry
    FROM building_buffers b
    JOIN industrial_load i ON b.building_id = i.building_id;
    """

def build_prep_vulnerability_profile_sql(site_name: str) -> list[str]:
    """
    Returns a list of sequential SQL statements for spatial deduplication.
    Enforces priority hierarchy: Hospital > School > Residential > Open Space.
    """
    drop_sql = f"DROP TABLE IF EXISTS rails_north.vulnerability_profile_{site_name} CASCADE;"

    create_table_sql = f"""
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

    index_sql = f"""
    CREATE INDEX IF NOT EXISTS idx_vuln_profile_{site_name}_geom 
    ON rails_north.vulnerability_profile_{site_name} USING GIST (geometry);
    """

    return [drop_sql, create_table_sql, index_sql]