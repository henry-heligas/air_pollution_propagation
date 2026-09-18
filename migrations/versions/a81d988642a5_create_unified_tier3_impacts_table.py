"""create_unified_tier3_impacts_table

Revision ID: a1b2c3d4e5f6
Revises: 
Create Date: 2026-09-18 10:23:00.000000

"""
from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry # Required for PostGIS geometry columns

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Create the unified table
    op.create_table(
        'tier3_environmental_impacts',
        sa.Column('building_id', sa.String(length=255), nullable=False),
        sa.Column('site_name', sa.String(length=255), nullable=False), # Scenario Tag
        sa.Column('exposure_category', sa.String(length=50), nullable=True),
        sa.Column('nearest_source_m', sa.Float(), nullable=True),
        sa.Column('baseline_hazard_index', sa.Float(), nullable=True),
        sa.Column('delta_hazard_index', sa.Float(), nullable=True),
        sa.Column('proposed_hazard_index', sa.Float(), nullable=True),
        sa.Column('delta_cancer_risk', sa.Float(), nullable=True),
        
        # PostGIS Geometry Column
        sa.Column('geom', Geometry(geometry_type='POLYGON', srid=4326), nullable=True),
        
        # Set the Composite Primary Key
        sa.PrimaryKeyConstraint('building_id', 'site_name'),
        
        schema='rails_north'
    )
    
    # 2. Create the index for lightning-fast scenario filtering
    op.create_index(
        'idx_tier3_site_name',
        'tier3_environmental_impacts',
        ['site_name'],
        unique=False,
        schema='rails_north'
    )

def downgrade() -> None:
    # Drop the table if we ever need to roll back this migration
    op.drop_index('idx_tier3_site_name', table_name='tier3_environmental_impacts', schema='rails_north')
    op.drop_table('tier3_environmental_impacts', schema='rails_north')