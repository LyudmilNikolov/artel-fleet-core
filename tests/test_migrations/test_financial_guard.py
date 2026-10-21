"""Unit coverage for fail-before-DDL migration contract; PostgreSQL integration is separate."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


def migration():
    path = Path(__file__).parents[2] / "alembic/versions/46cc0e782a10_eur_only_guard.py"
    spec = importlib.util.spec_from_file_location("eur_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("operation", ["upgrade", "downgrade"])
def test_existing_financial_values_block_before_ddl(monkeypatch, operation):
    module = migration()
    op = Mock()
    op.get_bind.return_value.execute.return_value.scalar_one.return_value = True
    monkeypatch.setattr(module, "op", op)
    with pytest.raises(RuntimeError, match="Financial review required"):
        getattr(module, operation)()
    assert op.execute.call_count == 1
    assert "LOCK TABLE" in op.execute.call_args.args[0]
