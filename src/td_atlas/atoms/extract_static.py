"""The offline extraction pass.

Reads everything td-atlas can learn about TouchDesigner without launching it:
the parameter help database, the mirrored wiki, the Python class reference, the
bundled example networks, and the expression/command help.

What this pass cannot know is anything TouchDesigner computes rather than
documents — defaults, ranges, menu options, parameter pages, connector counts.
Those come from `probe.py` running inside the application.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from ..install import TDInstall
from .htmltext import html_to_text, page_categories, page_title
from .store import AtomStore

Progress = Callable[[str], None]

# TDParameterHelp.json groups operators by pluralised family.
_FAMILY_SINGULAR = {
    "COMPs": "COMP",
    "TOPs": "TOP",
    "CHOPs": "CHOP",
    "SOPs": "SOP",
    "MATs": "MAT",
    "DATs": "DAT",
    "POPs": "POP",
}


@dataclass
class StaticStats:
    ops: int = 0
    params: int = 0
    articles: int = 0
    py_classes: int = 0
    py_members: int = 0
    snippets: int = 0
    expressions: int = 0
    palette: int = 0
    ops_without_doc: list[str] = field(default_factory=list)
    ops_without_class: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.ops} operators, {self.params} parameters, "
            f"{self.articles} articles, {self.py_classes} Python classes "
            f"({self.py_members} members), {self.snippets} snippets, "
            f"{self.palette} palette components, "
            f"{self.expressions} expression/command entries"
        )


# -- operator page naming ---------------------------------------------------

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _doc_page_candidates(op_type: str, label: str, family: str) -> list[str]:
    """Plausible wiki page names for an operator, best guess first.

    'noiseTOP' + label 'Noise' -> 'Noise_TOP'. Labels carry spaces and the
    occasional punctuation, and a handful of operators have a label that does
    not match their page, so a type-derived fallback follows.
    """
    out: list[str] = []
    if label:
        cleaned = re.sub(r"[^\w\s-]", "", label).strip()
        out.append(f"{cleaned.replace(' ', '_')}_{family}")
    stem = op_type[: -len(family)] if op_type.endswith(family) else op_type
    words = _CAMEL_BOUNDARY.sub(" ", stem).split()
    if words:
        titled = "_".join(w[:1].upper() + w[1:] for w in words)
        out.append(f"{titled}_{family}")
    # Seen in practice for multiword operators whose label joins the words.
    if label:
        out.append(f"{label.replace(' ', '')}_{family}")
    seen: set[str] = set()
    return [c for c in out if c and not (c in seen or seen.add(c))]


def _first_sentences(text: str, limit: int = 320) -> str | None:
    """A short summary lifted from the start of an article body."""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and len(line) > 30:
            return line[:limit]
    return None


def _page_id(relative: PurePath) -> str:
    """The stored name of a mirrored wiki page, always '/'-separated."""
    return relative.with_suffix("").as_posix()


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _resolve_doc_page(
    op_type: str,
    label: str,
    family: str,
    pages: dict[str, str],
    claimed: frozenset[str],
) -> str | None:
    """Find an operator's wiki page, falling back through looser matches.

    `pages` maps normalised page name to real page name. Vendor-prefixed
    articles are the reason for the suffix pass: the Flex TOP is documented at
    'NVIDIA_Flex_TOP', which no name-derived candidate would produce.

    `claimed` holds the normalised names of every operator type, so a suffix
    match cannot steal another operator's page — 'flowTOP' matches both
    'NVIDIA_Flow_TOP' and 'Optical_Flow_TOP', but the latter belongs to
    opticalflowTOP.
    """
    known = set(pages.values())
    for candidate in _doc_page_candidates(op_type, label, family):
        if candidate in known:
            return candidate

    target = _normalize(op_type)
    if target in pages:
        return pages[target]

    family_suffix = _normalize(family)
    suffixed = [
        real
        for norm, real in pages.items()
        if norm.endswith(target)
        and norm.endswith(family_suffix)
        and norm not in claimed
    ]
    if len(suffixed) == 1:
        return suffixed[0]
    return None


def _class_page(op_type: str) -> str:
    return f"{op_type[:1].upper()}{op_type[1:]}_Class"


# -- Python class reference -------------------------------------------------

# 'webSocketConnections → (Read Only):' / 'val → Any :'
_MEMBER_RE = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)\s*→\s*(?P<returns>[^:]*?)\s*:\s*$"
)
# 'authenticateBasic(token, userPasswords)→ bool:'
_METHOD_RE = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)\s*\((?P<args>[^)]*)\)\s*→\s*(?P<returns>[^:]*?)\s*:\s*$"
)
# Each class page restates the full inherited reference under H1 headings
# ('# TOP Class', '# OP Class'). Attributing members to the class that actually
# declares them turns 654 pages x ~150 repeated members into one row per real
# member, and recovers the inheritance chain more reliably than prose does.
_OWNER_RE = re.compile(r"^#\s+(?P<owner>\w+)\s+Class\s*$")


def _parse_class_page(name: str, text: str) -> tuple[dict, list[dict]]:
    """Split a *_Class article into a class row and its member rows."""
    lines = text.splitlines()

    doc_lines: list[str] = []
    for line in lines[:12]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            doc_lines.append(stripped)
        if len(doc_lines) >= 3:
            break

    members: list[dict] = []
    inherits: list[str] = []
    owner = name
    current: dict | None = None
    section = ""

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            owner_match = _OWNER_RE.match(stripped)
            if owner_match:
                owner = owner_match.group("owner")
                current = None
                if owner != name and owner not in inherits:
                    inherits.append(owner)
            else:
                section = stripped.lstrip("# ").lower()
            continue

        method = _METHOD_RE.match(stripped)
        member = None if method else _MEMBER_RE.match(stripped)
        if method:
            current = {
                "class_name": owner,
                "kind": "method",
                "name": method.group("name"),
                "signature": f"{method.group('name')}({method.group('args')})",
                "returns": method.group("returns").strip() or None,
                "read_only": 0,
                "doc": "",
            }
            members.append(current)
        elif member and "callback" not in section:
            returns = member.group("returns")
            current = {
                "class_name": owner,
                "kind": "member",
                "name": member.group("name"),
                "signature": member.group("name"),
                "returns": re.sub(
                    r"\(read only\)", "", returns, flags=re.I
                ).strip() or None,
                "read_only": 1 if "read only" in returns.lower() else 0,
                "doc": "",
            }
            members.append(current)
        elif current is not None and stripped:
            # Description lines follow their declaration until the next one.
            if len(current["doc"]) < 1200:
                current["doc"] = (current["doc"] + "\n" + stripped).strip()

    cls = {
        "name": name,
        "inherits": inherits,
        "doc": " ".join(doc_lines)[:2000],
    }
    return cls, members


# -- expression / command help ---------------------------------------------

def _parse_command_help(path: Path) -> list[dict]:
    """command.help: entry names at column zero, tab-indented bodies."""
    if not path.exists():
        return []
    entries: list[dict] = []
    name: str | None = None
    buffer: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line and not line[0].isspace():
            if name:
                entries.append(
                    {
                        "kind": "command",
                        "name": name,
                        "signature": name,
                        "text": "\n".join(buffer).strip(),
                    }
                )
            name = line.strip()
            buffer = []
        elif name:
            buffer.append(line)
    if name:
        entries.append(
            {
                "kind": "command",
                "name": name,
                "signature": name,
                "text": "\n".join(buffer).strip(),
            }
        )
    return [e for e in entries if e["name"]]


def _parse_expr_help(path: Path) -> list[dict]:
    """exprhelp: brace-delimited blocks whose first line is the signature.

        {
        abs number
        Description:
            ...
        }

    The signature's leading token is the function name; the rest are argument
    names, so the whole line is kept as the signature.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    entries: list[dict] = []
    for block in re.findall(r"^\{\s*$(.*?)^\}\s*$", text, re.M | re.S):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        signature = lines[0].strip()
        name = signature.split()[0]
        entries.append(
            {
                "kind": "expression",
                "name": name,
                "signature": signature,
                "text": "\n".join(lines[1:]).strip(),
            }
        )
    return entries


# -- main pass --------------------------------------------------------------

def extract(
    install: TDInstall, store: AtomStore, progress: Progress | None = None
) -> StaticStats:
    """Populate `store` from the files inside `install`."""
    say = progress or (lambda _m: None)
    stats = StaticStats()

    missing = install.missing_sources()
    if "TDParameterHelp.json" in missing:
        raise FileNotFoundError(
            f"TDParameterHelp.json not found under {install.tfs}. "
            "This does not look like a complete TouchDesigner install."
        )
    if missing:
        say(f"note: install is missing optional sources: {', '.join(missing)}")

    # 1. Articles. Indexed first so operators can be linked to them below.
    articles: dict[str, str] = {}
    help_root = install.offline_help
    if help_root.exists():
        # Wiki titles containing a slash were mirrored as subdirectories, so
        # 'TCP/IP DAT' lives at TCP/IP_DAT.htm rather than in the root.
        asset_dirs = {"images", "skins", "resources"}
        pages = sorted(
            p
            for p in help_root.rglob("*.htm")
            if not (set(p.relative_to(help_root).parts[:-1]) & asset_dirs)
        )
        say(f"parsing {len(pages)} wiki pages...")
        batch: list[dict] = []
        for i, page in enumerate(pages):
            # as_posix, not str: a wiki title containing a slash was
            # mirrored as a subdirectory, and the page id has to read back as
            # the title did ('TCP/IP_DAT'). On Windows str() would store a
            # backslash there and no name-derived candidate would match it.
            page_id = _page_id(page.relative_to(help_root))
            html = page.read_text(encoding="utf-8", errors="replace")
            text = html_to_text(html)
            articles[page_id] = text
            category = (
                "class" if page_id.endswith("_Class")
                else "operator" if re.search(
                    r"_(TOP|CHOP|SOP|DAT|MAT|COMP|POP)$", page_id
                ) else "article"
            )
            batch.append(
                {
                    "page": page_id,
                    "title": page_title(html) or page_id.replace("_", " "),
                    "category": category,
                    "categories": page_categories(html),
                    "text": text,
                }
            )
            if len(batch) >= 200:
                stats.articles += store.insert_articles(batch)
                batch.clear()
                say(f"  ...{i + 1}/{len(pages)}")
        if batch:
            stats.articles += store.insert_articles(batch)

    # 2. Operators and parameters.
    say("reading TDParameterHelp.json...")
    payload = json.loads(
        install.param_help.read_text(encoding="utf-8", errors="replace")
    )
    families = payload.get("help", {})

    # Normalised page lookup, used only after the name-derived guesses miss.
    page_index = {_normalize(p): p for p in articles}
    claimed = frozenset(
        _normalize(t) for ops in families.values() for t in ops if t
    )

    op_rows: list[dict] = []
    param_rows: list[dict] = []
    for family_plural, ops in families.items():
        family = _FAMILY_SINGULAR.get(
            family_plural, family_plural.rstrip("s")
        )
        for op_type, info in ops.items():
            if not op_type:
                continue  # the help file carries one placeholder entry
            label = info.get("label") or ""

            doc_page = _resolve_doc_page(
                op_type, label, family, page_index, claimed
            )
            if doc_page is None:
                stats.ops_without_doc.append(op_type)

            # Most operators have no type-specific Python class; they are
            # driven entirely through the family class, so fall back to it
            # rather than leaving the agent with no Python reference at all.
            own_class = _class_page(op_type)
            has_own = own_class in articles
            if not has_own:
                stats.ops_without_class.append(op_type)
            family_class = f"{family}_Class"
            class_page = (
                own_class if has_own
                else family_class if family_class in articles
                else None
            )

            op_rows.append(
                {
                    "type": op_type,
                    "family": family,
                    "label": label,
                    "summary": info.get("summary"),
                    "doc_page": doc_page,
                    "class_page": class_page,
                    "has_own_class": 1 if has_own else 0,
                }
            )
            for par_name, par in (info.get("parameters") or {}).items():
                param_rows.append(
                    {
                        "op_type": op_type,
                        "name": par_name,
                        "label": par.get("label"),
                        "summary": par.get("summary"),
                        "par_type": par.get("parType") or None,
                    }
                )

    stats.ops = store.insert_ops(op_rows)
    stats.params = store.insert_params(param_rows)
    say(f"indexed {stats.ops} operators, {stats.params} parameters")

    # 3. Python class reference.
    say("parsing Python class reference...")
    op_types = {r["type"] for r in op_rows}
    class_rows: list[dict] = []
    member_rows: list[dict] = []
    for page, text in articles.items():
        if not page.endswith("_Class"):
            continue
        class_name = page[: -len("_Class")]
        cls, members = _parse_class_page(class_name, text)
        # Link the class back to its operator, e.g. NoiseTOP -> noiseTOP.
        lowered = class_name[:1].lower() + class_name[1:]
        cls["op_type"] = lowered if lowered in op_types else None
        class_rows.append(cls)
        member_rows.extend(members)
    stats.py_classes = store.insert_py_classes(class_rows)
    stats.py_members = store.insert_py_members(member_rows)

    # 4. Bundled example networks.
    snippet_rows: list[dict] = []
    if install.snippets.exists():
        for family_dir in sorted(install.snippets.iterdir()):
            if not family_dir.is_dir():
                continue
            for tox in sorted(family_dir.glob("*.tox")):
                snippet_rows.append(
                    {
                        "op_type": tox.stem,
                        "family": family_dir.name,
                        "path": str(tox),
                    }
                )
    stats.snippets = store.insert_snippets(snippet_rows)
    if snippet_rows:
        store.conn.executemany(
            "UPDATE ops SET snippet_path = ? WHERE type = ?",
            [(r["path"], r["op_type"]) for r in snippet_rows],
        )

    # 5. Palette components. These are finished tools — a projection mapper,
    # a corner-pinner, a colour picker — that an agent unaware of them would
    # rebuild by hand.
    palette_rows: list[dict] = []
    if install.palette.exists():
        for tox in sorted(install.palette.rglob("*.tox")):
            doc_page = f"Palette-{tox.stem}"
            article = articles.get(doc_page)
            palette_rows.append(
                {
                    "name": tox.stem,
                    "category": tox.parent.name,
                    "path": str(tox),
                    "doc_page": doc_page if article is not None else None,
                    "summary": _first_sentences(article) if article else None,
                }
            )
    stats.palette = store.insert_palette(palette_rows)

    # 6. Expression and command help.
    expr_rows = _parse_command_help(install.command_help)
    expr_rows += _parse_expr_help(install.expr_help)
    stats.expressions = store.insert_expressions(expr_rows)

    store.set_meta("td_version", install.version)
    store.set_meta("td_install", str(install.root))
    store.set_meta("static_pass", "complete")
    return stats
