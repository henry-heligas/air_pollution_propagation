import os
import math

def get_local_xy(target_lat: float, target_lon: float, center_lat: float, center_lon: float):
    """
    Converts GPS Lat/Lon to local Cartesian (X,Y) in meters using Equirectangular approximation.
    Earth radius R is roughly 6,371,000 meters.
    """
    R = 6371000.0
    lat_rad = math.radians(center_lat)
    
    delta_lat = math.radians(target_lat - center_lat)
    delta_lon = math.radians(target_lon - center_lon)
    
    x_m = R * delta_lon * math.cos(lat_rad)
    y_m = R * delta_lat
    return x_m, y_m

def build_openfoam_case(payload: dict, case_dir: str):
    """Master function that writes all required OpenFOAM dictionaries."""
    sources = payload.get("sources", [])
    wind_config = payload.get("wind", {})
    mesh_config = payload.get("mesh", {}) # Grab the mesh settings
    
    if not sources:
        return
        
    anchor_lat = sources[0]["latitude"]
    anchor_lon = sources[0]["longitude"]
    
    # 1. Build the dynamic bounding box
    _write_block_mesh_dict(mesh_config, case_dir)
    
    # 2. Inject the dynamic physics boundaries
    _write_topo_set_dict(sources, anchor_lat, anchor_lon, case_dir)
    _write_temperature_boundary(sources, case_dir)
    _write_velocity_boundary(wind_config, case_dir)

def _write_topo_set_dict(sources: list, anchor_lat: float, anchor_lon: float, case_dir: str):
    """Generates system/topoSetDict."""
    actions = []
    for idx, source in enumerate(sources):
        local_x, local_y = get_local_xy(
            target_lat=source["latitude"], 
            target_lon=source["longitude"], 
            center_lat=anchor_lat, 
            center_lon=anchor_lon
        )
        radius = 10.0
        height = source.get("source_height_m", 0.0) 
        z_bottom, z_top = height, height + 10.0
        
        actions.append(f"""
    {{
        name    sourceZone_{idx};
        type    cellSet;
        action  new;
        source  cylinderToCell;
        sourceInfo
        {{
            p1      ({local_x:.2f} {local_y:.2f} {z_bottom:.2f});
            p2      ({local_x:.2f} {local_y:.2f} {z_top:.2f});
            radius  {radius};
        }}
    }}""")

    # Using the bulletproof one-liner OpenFOAM header
    content = f"""FoamFile {{ version 2.0; format ascii; class dictionary; object topoSetDict; }}

actions
(
    {''.join(actions)}
);
"""
    with open(os.path.join(case_dir, "system", "topoSetDict"), "w") as f:
        f.write(content)

def _write_temperature_boundary(sources: list, case_dir: str):
    content = f"""FoamFile {{ version 2.0; format ascii; class volScalarField; object T; }}
dimensions      [0 0 0 1 0 0 0];
internalField   uniform 293.15;
boundaryField
{{
    inlet           {{ type inletOutlet; inletValue uniform 293.15; value $internalField; }}
    outlet          {{ type inletOutlet; inletValue uniform 293.15; value $internalField; }}
    ground          {{ type zeroGradient; }}
    sky             {{ type inletOutlet; inletValue uniform 293.15; value $internalField; }}
    sides           {{ type zeroGradient; }}
}}
"""
    with open(os.path.join(case_dir, "0", "T"), "w") as f:
        f.write(content)

def _write_velocity_boundary(wind_config: dict, case_dir: str):
    import math
    wind_dir = wind_config.get("wind_direction_deg", 270.0)
    speed = wind_config.get("reference_velocity", 5.0)
    angle_rad = math.radians(270.0 - wind_dir)
    u_x, u_y = speed * math.cos(angle_rad), speed * math.sin(angle_rad)
    
    content = f"""FoamFile {{ version 2.0; format ascii; class volVectorField; object U; }}
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform ({u_x:.3f} {u_y:.3f} 0.0);
boundaryField
{{
    inlet           {{ type fixedValue; value uniform ({u_x:.3f} {u_y:.3f} 0.0); }}
    outlet          {{ type zeroGradient; }}
    ground          {{ type noSlip; }}
    sky             {{ type slip; }}
    sides           {{ type slip; }}
}}
"""
    with open(os.path.join(case_dir, "0", "U"), "w") as f:
        f.write(content)

def _write_fv_options(sources: list, case_dir: str):
    """Uses constant/fvModels (v2406+) to inject continuous heat into the T equation."""
    actions = []
    for idx, _ in enumerate(sources):
        actions.append(f"""
    heatSource_{idx}
    {{
        type            scalarSemiImplicitSource;
        active          true;
        selectionMode   cellSet;
        cellSet         sourceZone_{idx};
        volumeMode      specific;
        // Injecting 500 Kelvin per second into this volume
        injectionRateSuSp {{ T (500 0); }} 
    }}""")
        
    content = f"""FoamFile {{ version 2.0; format ascii; class dictionary; object fvModels; }}
{''.join(actions)}
"""
    # Note the change: v2406 expects this in the 'constant' directory, named 'fvModels'
    with open(os.path.join(case_dir, "constant", "fvModels"), "w") as f:
        f.write(content)

def _write_block_mesh_dict(mesh_config: dict, case_dir: str):
    """Generates a perfectly formatted system/blockMeshDict."""
    radius = mesh_config.get("domain_radius_m", 500.0)
    z_max = mesh_config.get("domain_z_max", 200.0)
    cell_size = mesh_config.get("base_cell_size", 5.0)
    
    x_min, x_max = -radius, radius
    y_min, y_max = -radius, radius
    z_min = 0.0
    
    x_cells = max(1, int((x_max - x_min) / cell_size))
    y_cells = max(1, int((y_max - y_min) / cell_size))
    z_cells = max(1, int((z_max - z_min) / cell_size))

    # Using the bulletproof one-liner OpenFOAM header
    content = f"""FoamFile {{ version 2.0; format ascii; class dictionary; object blockMeshDict; }}

scale   1;

vertices
(
    ({x_min} {y_min} {z_min})
    ({x_max} {y_min} {z_min})
    ({x_max} {y_max} {z_min})
    ({x_min} {y_max} {z_min})
    ({x_min} {y_min} {z_max})
    ({x_max} {y_min} {z_max})
    ({x_max} {y_max} {z_max})
    ({x_min} {y_max} {z_max})
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({x_cells} {y_cells} {z_cells}) simpleGrading (1 1 1)
);

edges
(
);

boundary
(
    inlet
    {{
        type patch;
        faces ( (0 4 7 3) );
    }}
    outlet
    {{
        type patch;
        faces ( (1 2 6 5) );
    }}
    ground
    {{
        type wall;
        faces ( (0 1 2 3) );
    }}
    sky
    {{
        type patch;
        faces ( (4 5 6 7) );
    }}
    sides
    {{
        type patch;
        faces ( (0 1 5 4) (3 2 6 7) );
    }}
);

mergePatchPairs
(
);
"""
    with open(os.path.join(case_dir, "system", "blockMeshDict"), "w") as f:
        f.write(content)


