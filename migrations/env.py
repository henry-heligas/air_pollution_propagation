import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from config import settings
from alembic import context
from src.io.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

def include_object(object, name, type_, reflected, compare_to):
    """
    Ignore tables that Alembic shouldn't touch.
    """
    if type_ == "table":
        # Ignore standard PostGIS and Tiger geocoder tables
        if name in ("spatial_ref_sys", "geometry_columns", "geography_columns", "topology"):
            return False
        # Ignore your dynamically generated pipeline tables
        if name.startswith(("tier1_", "tier2_", "tier3_", "vulnerability_profile_", "climate_wind_", "epa_point_")):
            return False
            
    return True

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # 1. Get the raw dictionary from the .ini file
    configuration = config.get_section(config.config_ini_section)
    
    # 2. FORCE the URL override right here using your settings
    configuration["sqlalchemy.url"] = settings.DATABASE_URL_SYNC

    # 3. Now pass the corrected dictionary to the engine
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=include_object
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
