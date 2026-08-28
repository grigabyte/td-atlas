"""An expanded project, loaded into memory as a tree of operators.

This is what lets an agent read a .toe without TouchDesigner running: the
structure, the non-default parameters, the wiring, and — the part that usually
matters most — the Python and GLSL held inside DATs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePath

from .expand import Expansion
from .formats import (
    FormatError,
    NodeFile,
    ParmValue,
    read_build,
    read_node,
    read_parms,
    read_payload,
    read_table,
)

# Files toeexpand writes that describe the project rather than an operator.
_SPECIAL = {".build", ".start", ".grps", ".root", ".parm", ".toc"}


@dataclass
class Node:
    """One operator inside an expanded project."""

    path: str                 # '/project1/noise1'
    name: str
    node: NodeFile
    parms: dict[str, ParmValue] = field(default_factory=dict)
    custom_parms: dict[str, ParmValue] = field(default_factory=dict)
    text: str | None = None            # DAT contents, when present
    table: list[list[str]] | None = None
    children: list[Node] = field(default_factory=list)

    resolved_type: str | None = None

    @property
    def op_type(self) -> str:
        """Canonical type, with the saved abbreviation expanded when known."""
        return self.resolved_type or self.node.op_type

    @property
    def family(self) -> str:
        return self.node.family

    @property
    def inputs(self) -> list[tuple[int, str]]:
        return self.node.inputs

    @property
    def parent_path(self) -> str:
        head = self.path.rsplit("/", 1)[0]
        return head or "/"

    def input_paths(self) -> list[str]:
        """Wiring resolved to absolute paths.

        `.n` files name inputs by sibling name, so they are resolved against
        the node's own parent.
        """
        base = self.parent_path
        out = []
        for _index, source in sorted(self.inputs):
            out.append(source if source.startswith("/") else f"{base.rstrip('/')}/{source}")
        return out

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class Project:
    """A whole .toe or .tox, read from disk."""

    source: Path
    build: dict[str, str]
    roots: list[Node] = field(default_factory=list)

    def walk(self):
        for root in self.roots:
            yield from root.walk()

    def nodes(self) -> dict[str, Node]:
        return {node.path: node for node in self.walk()}

    def find(self, path: str) -> Node | None:
        return self.nodes().get(path.rstrip("/") or "/")

    def of_type(self, op_type: str) -> list[Node]:
        return [n for n in self.walk() if n.op_type == op_type]

    def scripts(self) -> list[Node]:
        """Every node carrying text — Python, GLSL, notes."""
        return [n for n in self.walk() if n.text]

    @property
    def build_version(self) -> str:
        return self.build.get("build", "unknown")


def _node_path(relative: PurePath) -> str:
    """Turn a path relative to the expanded root into an operator path.

    Operator paths are always '/'-separated — that is TouchDesigner's own
    notation, not the filesystem's — so the separator cannot come from
    str(), which on Windows would yield '/project1\\noise1' and break every
    lookup, parent walk and grep that follows.
    """
    return "/" + relative.as_posix()


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


class TypeResolver:
    """Map the abbreviated type names in .n files onto canonical ones.

    A saved node records a contraction rather than the operator's real type:
    'geoCOMP' for geometryCOMP, 'evalDAT' for evaluateDAT, 'audiodevoutCHOP'
    for audiodeviceoutCHOP. Roughly one node in six is affected, and every one
    of those cannot be joined to the index without this.

    The map is measured, not guessed. Guessing looks tempting — a contraction
    is always a subsequence of the full name — but 'parexecDAT' is a
    subsequence of both parameterexecuteDAT and pargroupexecuteDAT, and
    picking by length silently chooses the wrong one. An unresolved type is
    better than a confidently wrong one, so anything absent from the map is
    left as it was found.
    """

    def __init__(self, aliases: dict[str, str], known: set[str] | None = None):
        self.aliases = aliases
        self.known = known or set()

    def resolve(self, op_type: str) -> str:
        return self.aliases.get(op_type, op_type)

    @property
    def size(self) -> int:
        return len(self.aliases)


def load(expansion: Expansion, resolver: TypeResolver | None = None) -> Project:
    """Build a Project from an expanded directory."""
    root = expansion.root
    build_file = root / ".build"
    build = read_build(_read(build_file) or "") if build_file.exists() else {}

    def build_node(n_file: Path) -> Node:
        stem = n_file.with_suffix("")
        path = _node_path(stem.relative_to(root))

        node_data = read_node(_read(n_file) or "")
        entry = Node(path=path, name=stem.name, node=node_data)
        if resolver is not None:
            entry.resolved_type = resolver.resolve(node_data.op_type)

        parm_file = stem.with_suffix(".parm")
        if parm_file.exists():
            entry.parms = read_parms(_read(parm_file) or "")
        cparm_file = stem.with_suffix(".cparm")
        if cparm_file.exists():
            entry.custom_parms = read_parms(_read(cparm_file) or "")

        text_file = stem.with_suffix(".text")
        if text_file.exists():
            try:
                entry.text = read_payload(text_file.read_bytes())
            except (FormatError, OSError):
                entry.text = None

        table_file = stem.with_suffix(".table")
        if table_file.exists():
            try:
                entry.table = read_table(table_file.read_bytes())
            except (FormatError, OSError):
                entry.table = None

        # A component's children live in a directory beside its .n file.
        child_dir = stem
        if child_dir.is_dir():
            entry.children = [
                build_node(child)
                for child in sorted(child_dir.glob("*.n"))
            ]
        return entry

    roots = [
        build_node(n_file)
        for n_file in sorted(root.glob("*.n"))
        if n_file.name not in _SPECIAL
    ]
    return Project(source=expansion.source, build=build, roots=roots)


def load_file(
    source: str | Path,
    refresh: bool = False,
    resolver: TypeResolver | None = None,
) -> Project:
    """Expand and load a .toe/.tox in one step."""
    from .expand import expand

    return load(expand(source, refresh=refresh), resolver=resolver)


def index_resolver(db_path=None) -> TypeResolver | None:
    """A resolver backed by the atom index, when one has been built."""
    from ..atoms.store import AtomStore
    from ..config import db_path as default_db

    store = AtomStore(db_path or default_db())
    if not store.exists():
        return None
    try:
        aliases = store.type_aliases()
        known = {row[0] for row in store.conn.execute("SELECT type FROM ops")}
    except Exception:
        return None
    finally:
        store.close()
    return TypeResolver(aliases, known)
