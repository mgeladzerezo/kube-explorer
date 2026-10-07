from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db import Base
from tests.conftest import BACKEND_DIR


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migrations_create_the_same_schema_as_the_models(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(alembic_config(url), "head")

    inspector = inspect(create_engine(url))
    for table in Base.metadata.sorted_tables:
        migrated = {c["name"]: c["nullable"] for c in inspector.get_columns(table.name)}
        declared = {c.name: c.nullable for c in table.columns}
        assert migrated == declared, table.name


def test_downgrade_removes_everything(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    assert set(inspect(create_engine(url)).get_table_names()) == {"alembic_version"}
