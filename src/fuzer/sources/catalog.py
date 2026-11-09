"""Compatibility facade for the source catalog's focused internal modules.

Systems, connections, and datasets live in ``_catalog_entities``; mapping versions
in ``_catalog_versions``; seeding and interchange in ``_catalog_io``; one system as a source pack in
``_catalog_packs``.
"""

from . import _catalog_entities as _entities
from . import _catalog_io as _io
from . import _catalog_packs as _packs
from . import _catalog_versions as _versions
from ._catalog_common import (CATALOG, CONNECTION_FIELDS, DATASET_FIELDS, DRIFT_POLICIES, LOAD_MODES, SEED,
                              STATUSES, SYSTEM_FIELDS, SYSTEM_KINDS, WHERE, CatalogError, _audit, _changes,
                              _check_dataset)

systems = _entities.systems
system = _entities.system
datasets = _entities.datasets
dataset = _entities.dataset
runs = _entities.runs
audit = _entities.audit
add_system = _entities.add_system
update_system = _entities.update_system
connections = _entities.connections
connection = _entities.connection
add_connection = _entities.add_connection
update_connection = _entities.update_connection
_default_connection = _entities._default_connection
add_dataset = _entities.add_dataset
update_dataset = _entities.update_dataset
pause = _entities.pause
resume = _entities.resume
produces = _entities.produces
retire_preview = _entities.retire_preview
retire = _entities.retire
delete_dataset = _entities.delete_dataset
delete_connection = _entities.delete_connection
delete_system = _entities.delete_system

current_mappings = _versions.current_mappings
versions = _versions.versions
_parse = _versions._parse
_dataset_row = _versions._dataset_row
propose = _versions.propose
_reader_dataset = _versions._reader_dataset
approve = _versions.approve
discard = _versions.discard
rollback = _versions.rollback

_WARNED = _io._WARNED
_sha = _io._sha
_seed_dataset = _io._seed_dataset
_adopt_old_state = _io._adopt_old_state
export = _io.export
import_ = _io.import_
export_pack = _packs.export_pack
import_pack = _packs.import_pack


def ensure(conn) -> None:
    return _io.ensure(conn, seed)


def seed_dirs():
    return _io.seed_dirs(_WARNED)


def seed_files():
    return _io.seed_files(seed_dirs)


def seed(conn, by: str = SEED) -> dict:
    return _io.seed(conn, by, seed_files)


def versions_for_dataset(conn, d: dict) -> list[dict]:
    """All mapping versions for the record types a dataset produces."""
    return [v for source, record_type in produces(d) for v in versions(conn, source, record_type)]
