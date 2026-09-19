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
    """
    Generates system/topoSetDict.
    Creates a distinct geometric cylinder patch for every source in the JSON array.
    """
    actions = []
    
    for idx, source in enumerate(sources):
        # Dynamically calculate the local X/Y offset from the anchor point
        local_x, local_y = get_local_xy(
            target_lat=source["latitude"], 
            target_lon=source["longitude"], 
            center_lat=anchor_lat, 
            center_lon=anchor_lon
        )
        
        radius = source.get("source_radius_m", 5.0)
        height = source.get("source_height_m", 0.0) # E.g., 0 for pool fire, 15 for smokestack
        
        # We make the cylinder 2 meters tall starting from the source_height
        z_bottom = height
        z_top = height + 2.0
        
        action = f"""
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
        }}"""
        actions.append(action)

    content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      topoSetDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

actions
(
    {''.join(actions)}
);
"""
    with open(os.path.join(case_dir, "system", "topoSetDict"), "w") as f:
        f.write(content)

def _write_temperature_boundary(sources: list, case_dir: str):
    """Generates 0/T (Temperature)."""
    boundary_patches = ""
    for idx, source in enumerate(sources):
        temp_k = source.get("exit_temperature_k", 293.15)
        boundary_patches += f"""
    sourceZone_{idx}
    {{
        type            fixedValue;
        value           uniform {temp_k};
    }}"""

    content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      T;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 0 0 1 0 0 0];
internalField   uniform 293.15;

boundaryField
{{
    inlet           {{ type inletOutlet; inletValue uniform 293.15; value $internalField; }}
    outlet          {{ type inletOutlet; inletValue uniform 293.15; value $internalField; }}
    ground          {{ type zeroGradient; }}
    buildings       {{ type zeroGradient; }}
    {boundary_patches}
}}
"""
    with open(os.path.join(case_dir, "0", "T"), "w") as f:
        f.write(content)

def _write_velocity_boundary(wind_config: dict, case_dir: str):
    """
    Generates 0/U (Velocity).
    Translates meteorological wind direction and speed into a 3D vector.
    """
    wind_dir = wind_config.get("wind_direction_deg", 270.0)
    speed = wind_config.get("reference_velocity", 5.0)
    
    # Convert meteorological direction (where wind is FROM) to math vector (where wind goes TO)
    math_angle = 270.0 - wind_dir
    angle_rad = math.radians(math_angle)
    
    u_x = speed * math.cos(angle_rad)
    u_y = speed * math.sin(angle_rad)
    u_z = 0.0 # Wind generally flows horizontally

    content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volVectorField;
    object      U;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 1 -1 0 0 0 0]; // Velocity (m/s)
internalField   uniform ({u_x:.3f} {u_y:.3f} {u_z:.3f});

boundaryField
{{
    inlet           {{ type fixedValue; value uniform ({u_x:.3f} {u_y:.3f} {u_z:.3f}); }}
    outlet          {{ type zeroGradient; }}
    ground          {{ type noSlip; }} // Friction at the ground
    buildings       {{ type noSlip; }} // Friction against buildings
    
    // We treat the fire zones themselves as pushing out gas at a mechanical velocity
    """
    
    # Optionally, we could add specific exit velocities for each source here
    content += """
}
"""
    with open(os.path.join(case_dir, "0", "U"), "w") as f:
        f.write(content)

def _write_block_mesh_dict(mesh_config: dict, case_dir: str):
    """
    Generates system/blockMeshDict.
    Creates a dynamic bounding box based on the requested domain radius and cell size.
    """
    radius = mesh_config.get("domain_radius_m", 500.0)
    z_max = mesh_config.get("domain_z_max", 200.0)
    cell_size = mesh_config.get("base_cell_size", 5.0)
    
    # Calculate the bounding box coordinates (centered at 0,0)
    x_min, x_max = -radius, radius
    y_min, y_max = -radius, radius
    z_min = 0.0
    
    # Calculate how many cells are needed to achieve the target resolution
    # (Length / cell_size)
    x_cells = int((x_max - x_min) / cell_size)
    y_cells = int((y_max - y_min) / cell_size)
    z_cells = int((z_max - z_min) / cell_size)
    
    # Ensure at least 1 cell in every direction to prevent engine crash
    x_cells = max(1, x_cells)
    y_cells = max(1, y_cells)
    z_cells = max(1, z_cells)

    content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      blockMeshDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

scale   1;

vertices
(
    ({x_min} {y_min} {z_min}) // 0: Bottom-South-West
    ({x_max} {y_min} {z_min}) // 1: Bottom-South-East
    ({x_max} {y_max} {z_min}) // 2: Bottom-North-East
    ({x_min} {y_max} {z_min}) // 3: Bottom-North-West
    
    ({x_min} {y_min} {z_max}) // 4: Top-South-West
    ({x_max} {y_min} {z_max}) // 5: Top-South-East
    ({x_max} {y_max} {z_max}) // 6: Top-North-East
    ({x_min} {y_max} {z_max}) // 7: Top-North-West
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
        faces
        (
            (0 4 7 3) // West face (Assuming wind blows West to East for this simple test)
        );
    }}
    outlet
    {{
        type patch;
        faces
        (
            (1 2 6 5) // East face
        );
    }}
    ground
    {{
        type wall;
        faces
        (
            (0 1 2 3) // Bottom face
        );
    }}
    sky
    {{
        type patch;
        faces
        (
            (4 5 6 7) // Top face
        );
    }}
    sides
    {{
        type patch;
        faces
        (
            (0 1 5 4) // South face
            (3 2 6 7) // North face
        );
    }}
);

mergePatchPairs
(
);
"""
    with open(os.path.join(case_dir, "system", "blockMeshDict"), "w") as f:
        f.write(content)