import os

base = "src/physics/base_case"
for folder in ["0", "constant", "system"]:
    os.makedirs(os.path.join(base, folder), exist_ok=True)

files = {
    "system/controlDict": """FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application     buoyantBoussinesqPimpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         10;
deltaT          0.1;
writeControl    timeStep;
writeInterval   10;

// NEW: Auto-Extraction Block
functions
{
    surfaces
    {
        type            surfaces;
        libs            ("libsampling.so");
        writeControl    timeStep;
        writeInterval   10; // Export at exactly 10 seconds
        surfaceFormat   vtk;
        fields          (T U);
        interpolationScheme cellPoint;
        
        surfaces
        (
            breathing_zone
            {
                type            cuttingPlane;
                planeType       pointAndNormal;
                pointAndNormalDict
                {
                    point   (0 0 1.5); // Slice at Z = 1.5 meters
                    normal  (0 0 1);   // Flat horizontal plane
                }
                interpolate     true;
            }
        );
    }
}
""",
    "system/fvSchemes": """FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }
ddtSchemes { default Euler; }
gradSchemes { default Gauss linear; }
divSchemes { default none; div(phi,U) Gauss upwind; div(phi,T) Gauss upwind; div(phi,k) Gauss upwind; div(phi,epsilon) Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
""",
    "system/fvSolution": """FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }
solvers {
    "p_rgh.*" { solver PCG; preconditioner DIC; tolerance 1e-08; relTol 0; }
    "(U|T|k|epsilon).*" { solver PBiCGStab; preconditioner DILU; tolerance 1e-06; relTol 0; }
}
PIMPLE { nOuterCorrectors 1; nCorrectors 2; nNonOrthogonalCorrectors 0; pRefCell 0; pRefValue 0; }
""",
    "constant/g": """FoamFile { version 2.0; format ascii; class dictionary; object g; }
dimensions      [0 1 -2 0 0 0 0];
value           (0 0 -9.81);
""",
    "constant/thermophysicalProperties": """FoamFile { version 2.0; format ascii; class dictionary; object thermophysicalProperties; }
thermoType { type heBoussinesqThermo; mixture pureMixture; transport const; thermo hConst; equationOfState Boussinesq; specie specie; energy sensibleEnthalpy; }
mixture { specie { molWeight 28.9; } equationOfState { rho0 1.2; T0 293.15; beta 3e-03; } thermodynamics { Cp 1000; Hf 0; } transport { mu 1.8e-05; Pr 0.7; } }
""",
    "constant/turbulenceProperties": """FoamFile { version 2.0; format ascii; class dictionary; object turbulenceProperties; }
simulationType  laminar; // Kept simple to bypass k/epsilon dependencies
""",
    "constant/transportProperties": """FoamFile { version 2.0; format ascii; class dictionary; object transportProperties; }
transportModel  Newtonian;
nu              [0 2 -1 0 0 0 0] 1.5e-05; // Kinematic viscosity of air
beta            [0 0 0 -1 0 0 0] 3e-03;   // Thermal expansion coefficient
TRef            [0 0 0 1 0 0 0] 293.15;   // Reference temperature
Pr              [0 0 0 0 0 0 0] 0.7;      // Prandtl number
Prt             [0 0 0 0 0 0 0] 0.85;     // Turbulent Prandtl number
""",
    "0/p_rgh": """FoamFile { version 2.0; format ascii; class volScalarField; object p_rgh; }
dimensions [0 2 -2 0 0 0 0]; internalField uniform 0;
boundaryField {
    inlet { type fixedFluxPressure; value uniform 0; }
    outlet { type fixedValue; value uniform 0; }
    ground { type fixedFluxPressure; value uniform 0; }
    sky { type fixedFluxPressure; value uniform 0; }
    sides { type fixedFluxPressure; value uniform 0; }
}""",
    "0/alphat": """FoamFile { version 2.0; format ascii; class volScalarField; object alphat; }
dimensions [0 2 -1 0 0 0 0]; internalField uniform 0;
boundaryField { ".*" { type calculated; value uniform 0; } }"""
}

for path, content in files.items():
    with open(os.path.join(base, path), "w") as f:
        f.write(content)
print("Base case populated successfully.")