"""SQLite storage for the atom index.

The index is rebuilt wholesale from a TouchDesigner install, so the schema
favours straightforward full-table writes and standalone FTS5 tables over
incremental-update machinery.

Two passes populate it. The static pass (offline, from files in the app bundle)
fills prose: labels, summaries, wiki articles, Python class reference. The
runtime pass (from a probe running inside TouchDesigner) fills the mechanical
facts prose cannot give: defaults, ranges, menu options, parameter pages and
connector counts. Columns owned by the runtime pass stay NULL until it runs.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Article prose is not copied here; doc_page and class_page join to
-- `articles`, which keeps one copy of each of the 2000-odd wiki pages.
CREATE TABLE ops (
    type         TEXT PRIMARY KEY,   -- 'noiseTOP'
    family       TEXT NOT NULL,      -- 'TOP'
    label        TEXT,               -- 'Noise'
    summary      TEXT,
    doc_page     TEXT,               -- wiki page id, e.g. 'Noise_TOP'
    class_page   TEXT,               -- own class page, or the family's
    has_own_class INTEGER NOT NULL DEFAULT 0,
    snippet_path TEXT,               -- example .tox shipped with TD
    -- runtime pass
    min_inputs   INTEGER,
    max_inputs   INTEGER,
    num_outputs  INTEGER,
    is_comp      INTEGER,
    probed       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ops_family ON ops(family);

CREATE TABLE params (
    op_type    TEXT NOT NULL,
    name       TEXT NOT NULL,
    label      TEXT,
    summary    TEXT,
    par_type   TEXT,      -- as written in TDParameterHelp.json
    -- runtime pass
    style      TEXT,      -- authoritative: 'Float', 'Menu', 'XYZ', ...
    default_value TEXT,   -- JSON-encoded
    min_value  REAL,
    max_value  REAL,
    clamp_min  INTEGER,
    clamp_max  INTEGER,
    norm_min   REAL,
    norm_max   REAL,
    menu_names  TEXT,     -- JSON array
    menu_labels TEXT,     -- JSON array
    page       TEXT,
    page_ord   INTEGER,   -- page order as shown in the parameter dialog
    ord        REAL,
    vec_index  INTEGER,
    group_name TEXT,      -- ParGroup this member belongs to, e.g. 'tx' -> 't'
    is_menu    INTEGER,
    is_pulse   INTEGER,
    is_toggle  INTEGER,
    is_number  INTEGER,
    is_string  INTEGER,
    is_op      INTEGER,
    is_sequence INTEGER,
    read_only  INTEGER,
    hidden     INTEGER,
    enable_expr TEXT,
    PRIMARY KEY (op_type, name)
);
CREATE INDEX params_op ON params(op_type);

CREATE TABLE py_classes (
    name     TEXT PRIMARY KEY,  -- 'noiseTOP'
    op_type  TEXT,              -- linked operator type, when there is one
    inherits TEXT,              -- JSON array of base class names
    doc      TEXT
);

CREATE TABLE py_members (
    class_name TEXT NOT NULL,
    kind       TEXT NOT NULL,   -- 'member' | 'method'
    name       TEXT NOT NULL,
    signature  TEXT,
    returns    TEXT,
    read_only  INTEGER,
    doc        TEXT,
    PRIMARY KEY (class_name, kind, name)
);

CREATE TABLE articles (
    page     TEXT PRIMARY KEY,  -- 'Write_a_GLSL_Material'
    title    TEXT,
    category TEXT,              -- 'operator' | 'class' | 'article'
    text     TEXT
);

CREATE TABLE snippets (
    op_type TEXT NOT NULL,
    family  TEXT NOT NULL,
    path    TEXT NOT NULL,
    PRIMARY KEY (op_type, path)
);

CREATE TABLE expressions (
    kind  TEXT NOT NULL,        -- 'command' | 'expression'
    name  TEXT NOT NULL,
    signature TEXT,             -- e.g. 'abs number'
    text  TEXT,
    PRIMARY KEY (kind, name)
);

CREATE VIRTUAL TABLE ops_fts USING fts5(
    type, family, label, summary, doc_text, tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE params_fts USING fts5(
    op_type, name, label, summary, tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE articles_fts USING fts5(
    page, title, text, tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE py_fts USING fts5(
    class_name, name, signature, doc, tokenize='porter unicode61'
);
"""


_STOPWORDS = frozenset(
    """a an and are as at be by for from how in into is it of on or that the
    to use using used with what when which will you your""".split()
)

# Characters that mean the caller is writing FTS5 syntax themselves.
_FTS_SYNTAX = ('"', "*", "(", ")", " OR ", " AND ", " NOT ", "NEAR(")


def fts_query(text: str, allow_syntax: bool = True) -> str:
    """Turn a plain-language question into an FTS5 expression.

    FTS5 ANDs bare terms together, so "displace an image using noise" would
    require every word including the filler ones and match nothing. Terms are
    ORed instead and ranking decides the order. A caller who writes FTS5
    syntax gets it passed through untouched, unless `allow_syntax` is off —
    see AtomStore.match, which retries with it off when the pass-through turns
    out not to parse.
    """
    stripped = text.strip()
    if not stripped:
        return '""'
    if allow_syntax and any(token in stripped for token in _FTS_SYNTAX):
        return stripped

    words = re.findall(r"[A-Za-z0-9_]+", stripped)
    terms = [w for w in words if len(w) > 2 and w.lower() not in _STOPWORDS]
    if not terms:
        terms = words or [stripped]
    # Quoting makes each term a literal, so punctuation cannot become syntax.
    return " OR ".join(f'"{t}"' for t in terms)


def default_db_path() -> Path:
    """Where the index lives unless the caller says otherwise."""
    return Path.home() / ".td-atlas" / "atlas.db"


class AtomStore:
    """Read/write access to the atom index."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> AtomStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def create(self) -> None:
        """Drop any existing content and lay down a fresh schema."""
        self.close()
        if self.path.exists():
            self.path.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        self.conn.executescript(_SCHEMA)
        self.set_meta("schema_version", str(SCHEMA_VERSION))
        self.conn.commit()

    def exists(self) -> bool:
        return self.path.exists()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
        except Exception:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

    # -- metadata ----------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    # -- writes ------------------------------------------------------------

    def insert_ops(self, rows: Iterable[dict[str, Any]]) -> int:
        cols = (
            "type", "family", "label", "summary", "doc_page",
            "class_page", "has_own_class", "snippet_path",
        )
        payload = [tuple(r.get(c) for c in cols) for r in rows]
        placeholders = ", ".join("?" * len(cols))
        self.conn.executemany(
            f"INSERT OR REPLACE INTO ops({', '.join(cols)}) "
            f"VALUES({placeholders})",
            payload,
        )
        return len(payload)

    def insert_params(self, rows: Iterable[dict[str, Any]]) -> int:
        cols = ("op_type", "name", "label", "summary", "par_type")
        payload = [tuple(r.get(c) for c in cols) for r in rows]
        self.conn.executemany(
            f"INSERT OR REPLACE INTO params({', '.join(cols)}) "
            f"VALUES({', '.join('?' * len(cols))})",
            payload,
        )
        return len(payload)

    def insert_articles(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = [
            (r["page"], r.get("title"), r.get("category"), r.get("text"))
            for r in rows
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO articles(page, title, category, text) "
            "VALUES(?, ?, ?, ?)",
            payload,
        )
        return len(payload)

    def insert_snippets(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = [(r["op_type"], r["family"], r["path"]) for r in rows]
        self.conn.executemany(
            "INSERT OR REPLACE INTO snippets(op_type, family, path) "
            "VALUES(?, ?, ?)",
            payload,
        )
        return len(payload)

    def insert_expressions(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = [
            (r["kind"], r["name"], r.get("signature"), r.get("text"))
            for r in rows
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO expressions(kind, name, signature, text) "
            "VALUES(?, ?, ?, ?)",
            payload,
        )
        return len(payload)

    def insert_py_classes(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = [
            (
                r["name"],
                r.get("op_type"),
                json.dumps(r.get("inherits") or []),
                r.get("doc"),
            )
            for r in rows
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO py_classes(name, op_type, inherits, doc) "
            "VALUES(?, ?, ?, ?)",
            payload,
        )
        return len(payload)

    def insert_py_members(self, rows: Iterable[dict[str, Any]]) -> int:
        cols = (
            "class_name", "kind", "name", "signature", "returns",
            "read_only", "doc",
        )
        payload = [tuple(r.get(c) for c in cols) for r in rows]
        self.conn.executemany(
            f"INSERT OR REPLACE INTO py_members({', '.join(cols)}) "
            f"VALUES({', '.join('?' * len(cols))})",
            payload,
        )
        return len(payload)

    # -- runtime merge -----------------------------------------------------

    def merge_runtime_op(self, op: dict[str, Any]) -> None:
        """Apply one operator's runtime facts over its static row."""
        self.conn.execute(
            "UPDATE ops SET min_inputs=?, max_inputs=?, num_outputs=?, "
            "is_comp=?, probed=1 WHERE type=?",
            (
                op.get("min_inputs"),
                op.get("max_inputs"),
                op.get("num_outputs"),
                1 if op.get("is_comp") else 0,
                op["type"],
            ),
        )

    def merge_runtime_params(
        self, op_type: str, params: Iterable[dict[str, Any]]
    ) -> int:
        """Apply runtime parameter facts, inserting rows the static pass missed.

        The static help omits parameters TouchDesigner considers obsolete, so
        the probe legitimately reports names the first pass never saw.
        """
        rows = list(params)
        payload = [
            (
                op_type,
                p["name"],
                p.get("label"),
                p.get("style"),
                json.dumps(p.get("default")),
                p.get("min"),
                p.get("max"),
                _as_int(p.get("clamp_min")),
                _as_int(p.get("clamp_max")),
                p.get("norm_min"),
                p.get("norm_max"),
                json.dumps(p["menu_names"]) if p.get("menu_names") else None,
                json.dumps(p["menu_labels"]) if p.get("menu_labels") else None,
                p.get("page"),
                p.get("page_ord"),
                p.get("order"),
                p.get("vec_index"),
                p.get("group"),
                _as_int(p.get("is_menu")),
                _as_int(p.get("is_pulse")),
                _as_int(p.get("is_toggle")),
                _as_int(p.get("is_number")),
                _as_int(p.get("is_string")),
                _as_int(p.get("is_op")),
                _as_int(p.get("is_sequence")),
                _as_int(p.get("read_only")),
                _as_int(p.get("hidden")),
                p.get("enable_expr"),
            )
            for p in rows
        ]
        self.conn.executemany(
            """
            INSERT INTO params(
                op_type, name, label, style, default_value, min_value,
                max_value, clamp_min, clamp_max, norm_min, norm_max,
                menu_names, menu_labels, page, page_ord, ord, vec_index, group_name,
                is_menu, is_pulse, is_toggle, is_number, is_string, is_op,
                is_sequence, read_only, hidden, enable_expr)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(op_type, name) DO UPDATE SET
                label=COALESCE(params.label, excluded.label),
                style=excluded.style,
                default_value=excluded.default_value,
                min_value=excluded.min_value,
                max_value=excluded.max_value,
                clamp_min=excluded.clamp_min,
                clamp_max=excluded.clamp_max,
                norm_min=excluded.norm_min,
                norm_max=excluded.norm_max,
                menu_names=excluded.menu_names,
                menu_labels=excluded.menu_labels,
                page=excluded.page,
                page_ord=excluded.page_ord,
                ord=excluded.ord,
                vec_index=excluded.vec_index,
                group_name=excluded.group_name,
                is_menu=excluded.is_menu,
                is_pulse=excluded.is_pulse,
                is_toggle=excluded.is_toggle,
                is_number=excluded.is_number,
                is_string=excluded.is_string,
                is_op=excluded.is_op,
                is_sequence=excluded.is_sequence,
                read_only=excluded.read_only,
                hidden=excluded.hidden,
                enable_expr=excluded.enable_expr
            """,
            payload,
        )
        return len(payload)

    # -- search indexes ----------------------------------------------------

    def rebuild_fts(self) -> None:
        """Repopulate every FTS table from the base tables."""
        c = self.conn
        c.execute("DELETE FROM ops_fts")
        c.execute(
            "INSERT INTO ops_fts(type, family, label, summary, doc_text) "
            "SELECT o.type, o.family, COALESCE(o.label,''), "
            "COALESCE(o.summary,''), COALESCE(a.text,'') "
            "FROM ops o LEFT JOIN articles a ON a.page = o.doc_page"
        )
        c.execute("DELETE FROM params_fts")
        c.execute(
            "INSERT INTO params_fts(op_type, name, label, summary) "
            "SELECT op_type, name, COALESCE(label,''), COALESCE(summary,'') "
            "FROM params"
        )
        c.execute("DELETE FROM articles_fts")
        c.execute(
            "INSERT INTO articles_fts(page, title, text) "
            "SELECT page, COALESCE(title,''), COALESCE(text,'') FROM articles"
        )
        c.execute("DELETE FROM py_fts")
        c.execute(
            "INSERT INTO py_fts(class_name, name, signature, doc) "
            "SELECT class_name, name, COALESCE(signature,''), "
            "COALESCE(doc,'') FROM py_members"
        )
        c.execute("INSERT INTO ops_fts(ops_fts) VALUES('optimize')")
        c.execute("INSERT INTO params_fts(params_fts) VALUES('optimize')")
        c.execute("INSERT INTO articles_fts(articles_fts) VALUES('optimize')")
        c.execute("INSERT INTO py_fts(py_fts) VALUES('optimize')")

    def match(self, text: str) -> str:
        """An FTS5 MATCH expression for `text` that is guaranteed to parse.

        A query containing quotes, parentheses or NEAR() is passed through as
        the caller's own FTS5 syntax — but an agent writing plain prose can
        produce those by accident ("it's a NEAR() thing", 'say "blur"'), and
        malformed syntax makes SQLite raise rather than return no results.
        Asking SQLite to parse it is the only reliable check.
        """
        for candidate in (
            fts_query(text),
            fts_query(text, allow_syntax=False),
            '""',
        ):
            try:
                self.conn.execute(
                    "SELECT 1 FROM ops_fts WHERE ops_fts MATCH ? LIMIT 1",
                    (candidate,),
                ).fetchone()
                return candidate
            except sqlite3.OperationalError:
                continue
        return '""'

    def parameters(self, op_type: str) -> list[dict[str, Any]]:
        """An operator's parameters, with group prose resolved onto members.

        Rows the runtime pass never saw are names that appear only in the
        shipped help — parameter-group headings like 't' for Translate, whose
        real settable members are 'tx'/'ty'/'tz'. They are returned with
        settable=False so a caller never offers them as something to write.
        """
        rows = self.conn.execute(
            """
            SELECT p.*,
                   g.summary AS group_summary,
                   g.label   AS group_label
            FROM params p
            LEFT JOIN params g
                   ON g.op_type = p.op_type AND g.name = p.group_name
                  AND g.name != p.name
            WHERE p.op_type = ?
            ORDER BY p.page IS NULL, p.page_ord, p.page, p.ord, p.name
            """,
            (op_type,),
        ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["settable"] = row["style"] is not None
            item["summary"] = row["summary"] or row["group_summary"]
            item["label"] = row["label"] or row["group_label"]
            for key in ("menu_names", "menu_labels"):
                if item.get(key):
                    item[key] = json.loads(item[key])
            if item.get("default_value") is not None:
                try:
                    item["default"] = json.loads(item["default_value"])
                except ValueError:
                    item["default"] = item["default_value"]
            else:
                item["default"] = None
            out.append(item)
        return out

    def stats(self) -> dict[str, int]:
        tables = (
            "ops", "params", "articles", "snippets", "expressions",
            "py_classes", "py_members",
        )
        out = {
            t: self.conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            for t in tables
        }
        out["ops_probed"] = self.conn.execute(
            "SELECT COUNT(*) c FROM ops WHERE probed = 1"
        ).fetchone()["c"]
        return out


def _as_int(value: object) -> int | None:
    """Normalise probe booleans, which arrive as JSON true/false or None."""
    if value is None:
        return None
    return 1 if value else 0
