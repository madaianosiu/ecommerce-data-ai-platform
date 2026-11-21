"""Validarea SQL-ului generat de LLM înainte de execuție (sqlglot, dialect postgres).

Straturi: (1) fără comentarii și o singură instrucțiune; (2) doar SELECT (inclusiv UNION și CTE
read-only); (3) fără noduri care scriu sau blochează (INSERT/UPDATE/DELETE/MERGE, DDL, COPY,
SELECT INTO, FOR UPDATE, comenzi necunoscute); (4) tabele doar din allowlist-ul din core; nimic
din raw, staging, pg_catalog sau information_schema; (5) funcții periculoase blocate după nume;
(6) plafon de rânduri: interogarea e împachetată în SELECT * FROM (...) LIMIT 200.

Se execută SQL-ul REGENERAT de sqlglot din arborele validat, nu textul brut de la LLM, ca ce
s-a verificat să fie exact ce rulează. Ce nu se poate parsa e respins (fail closed).
"""
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ROW_CAP = 200
DIALECT = "postgres"

FORBIDDEN_SCHEMAS = {"raw", "staging", "pg_catalog", "information_schema", "pg_toast", "public"}
ALLOWED_TABLE_FUNCTIONS = {"ExplodingGenerateSeries", "GenerateSeries", "Unnest"}

# Noduri care modifică date, schimbă schema, exportă/importă sau iau lock-uri.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name) for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterColumn", "TruncateTable",
        "Copy", "Into", "Lock", "Command", "Set", "SetItem", "Transaction", "Commit", "Rollback",
        "Grant", "Revoke", "Use", "Pragma", "LoadData", "Kill", "Analyze", "Comment", "Refresh", "Cache",
        "Uncache", "Describe", "Show",
    ) if hasattr(exp, name)
)

# Funcții blocate (după nume normalizat): somn, citire de fișiere/setări, large objects,
# conexiuni externe, execuție de SQL din string, administrare server, secvențe.
BLOCKED_FUNCTIONS = {
    "set_config", "current_setting", "query_to_xml", "query_to_xml_and_xmlschema",
    "query_to_xmlschema", "cursor_to_xml", "cursor_to_xmlschema", "table_to_xml",
    "table_to_xml_and_xmlschema", "schema_to_xml", "database_to_xml", "nextval", "setval",
    "currval", "lastval", "version", "txid_current", "inet_server_addr", "inet_server_port",
    "inet_client_addr", "inet_client_port", "current_database", "current_user", "session_user",
    "current_role", "user", "has_table_privilege", "has_schema_privilege", "to_regclass",
}
BLOCKED_PREFIXES = ("pg_", "lo_", "dblink", "xml_", "file_", "copy_")


class UnsafeSQL(ValueError):
    """SQL respins de validator; mesajul e sigur de arătat LLM-ului pentru o reparare."""


@dataclass(frozen=True)
class ValidatedSQL:
    inner: str      # SELECT-ul validat, regenerat de sqlglot
    executed: str   # ce se execută efectiv: inner împachetat cu LIMIT ROW_CAP
    tables: tuple[str, ...]


# ---------------------------------------------------------------- extragere din răspunsul LLM
_FENCE = re.compile(r"^\s*```(?:sql|postgresql|postgres)?\s*\n?(.*?)\n?```\s*$", re.S | re.I)
CANNOT_ANSWER = "CANNOT_ANSWER"


def extract_sql(text: str) -> str:
    """Scoate eventualul bloc ```sql ... ``` și spațiile; nu alterează altceva."""
    text = (text or "").strip()
    match = _FENCE.match(text)
    return (match.group(1) if match else text).strip()


def cannot_answer_reason(text: str) -> str | None:
    """LLM-ul răspunde 'CANNOT_ANSWER: motiv' când întrebarea nu se poate rezolva din schemă."""
    text = extract_sql(text)
    if text.upper().startswith(CANNOT_ANSWER):
        return text[len(CANNOT_ANSWER):].lstrip(" :-").strip() or "not answerable from the schema"
    return None


# ---------------------------------------------------------------- validare
def _function_names(node: exp.Func) -> set[str]:
    """Toate numele sub care poate apărea funcția: sqlglot mapează unele funcții la clase cu alt
    nume intern (ex. version() -> CurrentVersion / CURRENT_VERSION), deci verificăm și forma
    randată în SQL-ul care chiar se execută."""
    names = {node.sql_name().lower(), type(node).__name__.lower()}
    if isinstance(node, exp.Anonymous):
        names.add(str(node.name or "").lower())
    rendered = node.sql(dialect=DIALECT)
    head = rendered.split("(", 1)[0].strip().lower()
    if head and re.fullmatch(r"[a-z_][a-z0-9_]*", head):
        names.add(head)
    return names


def validate_sql(sql: str, allowed_tables: set[str]) -> ValidatedSQL:
    sql = (sql or "").strip()
    if not sql:
        raise UnsafeSQL("empty query")
    if "\x00" in sql:
        raise UnsafeSQL("NUL byte in query")

    try:
        tokens = sqlglot.tokenize(sql, read=DIALECT)
        if any(t.comments for t in tokens):
            raise UnsafeSQL("comments are not allowed in the query")
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except SqlglotError as exc:
        raise UnsafeSQL(f"could not parse the query: {str(exc).splitlines()[0][:200]}") from None

    if len(statements) != 1:
        raise UnsafeSQL(f"exactly one statement is allowed (got {len(statements)})")
    stmt = statements[0]

    if not isinstance(stmt, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise UnsafeSQL(f"only SELECT queries are allowed (got {stmt.key.upper()})")

    for node in stmt.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQL(f"forbidden operation: {node.key.upper()}")

    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    tables: set[str] = set()
    for table in stmt.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            func = type(table.this).__name__
            if func not in ALLOWED_TABLE_FUNCTIONS:
                raise UnsafeSQL(f"table function not allowed: {table.this.sql(dialect=DIALECT)[:60]}")
            continue
        name = table.name.lower()
        schema = (table.db or "").lower()
        catalog = (table.catalog or "").lower()
        if catalog:
            raise UnsafeSQL(f"cross-database reference not allowed: {table.sql(dialect=DIALECT)}")
        if schema in FORBIDDEN_SCHEMAS or (schema and schema != "core"):
            raise UnsafeSQL(f"schema '{schema}' is not allowed; only core tables can be queried")
        if not schema and name in cte_names:
            continue
        if name not in allowed_tables:
            raise UnsafeSQL(f"table '{name}' is not in the allowed list")
        tables.add(name)

    blocked_classes = {n.replace("_", "") for n in BLOCKED_FUNCTIONS} | {"currentversion"}
    for func in stmt.find_all(exp.Func):
        for name in _function_names(func):
            if (name in BLOCKED_FUNCTIONS or name.startswith(BLOCKED_PREFIXES)
                    or name.replace("_", "") in blocked_classes):
                raise UnsafeSQL(f"function '{name}' is not allowed")

    # Coloane de sistem / variabile de sesiune (ex. "current_user" scris fără paranteze).
    for col in stmt.find_all(exp.Column):
        if col.name.lower() in BLOCKED_FUNCTIONS and not col.table:
            raise UnsafeSQL(f"'{col.name}' is not allowed")

    inner = stmt.sql(dialect=DIALECT)
    executed = f"SELECT * FROM ({inner}) AS capped_result LIMIT {ROW_CAP}"
    return ValidatedSQL(inner=inner, executed=executed, tables=tuple(sorted(tables)))
