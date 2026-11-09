"""Each module owns its tables in its own schema.sql, applied in resources.SCHEMA_MODULES order."""

import re

from fuzer.resources import PACKAGE, SCHEMA_MODULES, SCHEMAS

CREATE = re.compile(r"(?im)^create table if not exists (?:\w+\.)?(\w+)")
# Statements that need a table to exist already: a foreign key, a migration, a data fix.
USES = re.compile(r"(?i)\b(?:references|alter table(?: if exists)?|update|insert into|delete from|index if not exists "
                  r"\w+ on)\s+(?:only\s+)?(?:\w+\.)?(\w+)")


def owners() -> dict[str, str]:
    out = {}
    for module, path in zip(SCHEMA_MODULES, SCHEMAS):
        for table in CREATE.findall(path.read_text()):
            assert table not in out, f"{table} is created by both {out[table]} and {module}"
            out[table] = module
    return out


def test_every_module_schema_is_applied():
    on_disk = {p.parent.name for p in PACKAGE.glob("*/schema.sql")}
    assert on_disk == set(SCHEMA_MODULES)
    assert not (PACKAGE / "schema.sql").exists(), "tables belong in the schema.sql of the module that owns them"


def test_a_module_needs_only_its_own_tables_and_ones_applied_before_it():
    owner = owners()
    order = {m: i for i, m in enumerate(SCHEMA_MODULES)}
    late = []
    for module, path in zip(SCHEMA_MODULES, SCHEMAS):
        for table in set(USES.findall(path.read_text())) & owner.keys():
            if order[owner[table]] > order[module]:
                late.append(f"{module} uses {table}, which {owner[table]} creates later")
    assert not late, late
