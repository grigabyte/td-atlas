"""The runtime introspection pass.

TouchDesigner's shipped help documents what a parameter *means*; it does not
record defaults, numeric ranges, menu options, parameter pages or connector
counts. Those exist only in the running application, so this pass instantiates
every operator type inside a non-cooking sandbox component and reads them off
the live objects.

Cooking is disabled on the sandbox for a reason: creating a Video Device In TOP
or an Audio Device In CHOP that is allowed to cook would open real hardware.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from ..bridge.client import BridgeClient, BridgeError
from .store import AtomStore

Progress = Callable[[str], None]

# Enumerate types from TouchDesigner's own `families` dict rather than from the
# help file, so anything the documentation missed still gets probed. The dict
# maps a family name to a list of *classes* (td.noiseTOP, ...), not strings.
_LIST_TYPES = """
result = {
    family: sorted(cls.__name__ for cls in types)
    for family, types in families.items()
}
"""

# Runs inside TouchDesigner. Kept in one string so the whole chunk is a single
# main-thread interruption.
_PROBE_CHUNK = """
_types = %(types)s

_container = op('/tdatlas')
_sandbox = _container.op('probe_sandbox')
if _sandbox is None:
    _sandbox = _container.create(baseCOMP, 'probe_sandbox')
_sandbox.allowCooking = False

def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default

def _describe(par, pageorder):
    _page = _safe(lambda: par.page.name)
    info = {
        'name': par.name,
        'label': _safe(lambda: par.label),
        'style': _safe(lambda: par.style),
        'default': _safe(lambda: par.default),
        'page': _page,
        'page_ord': pageorder.get(_page),
        'order': _safe(lambda: par.order),
        'vec_index': _safe(lambda: par.vecIndex),
        # The shipped help documents parameter *groups* ('t' for Translate),
        # while the real operator exposes members ('tx', 'ty', 'tz'). Recording
        # the group name lets the index attach the group's prose to the
        # settable member names.
        'group': _safe(lambda: par.parGroup.name),
        'read_only': _safe(lambda: bool(par.readOnly), False),
        'hidden': _safe(lambda: bool(par.hidden), False),
        'enable_expr': _safe(lambda: par.enableExpr or None),
        'is_menu': _safe(lambda: bool(par.isMenu), False),
        'is_pulse': _safe(lambda: bool(par.isPulse), False),
        'is_toggle': _safe(lambda: bool(par.isToggle), False),
        'is_number': _safe(lambda: bool(par.isNumber), False),
        'is_string': _safe(lambda: bool(par.isString), False),
        'is_op': _safe(lambda: bool(par.isOP), False),
        'is_sequence': _safe(lambda: bool(par.isSequence), False),
    }
    if info['is_menu']:
        info['menu_names'] = _safe(lambda: list(par.menuNames or []), [])
        info['menu_labels'] = _safe(lambda: list(par.menuLabels or []), [])
    if info['is_number']:
        info['min'] = _safe(lambda: par.min)
        info['max'] = _safe(lambda: par.max)
        info['clamp_min'] = _safe(lambda: bool(par.clampMin), False)
        info['clamp_max'] = _safe(lambda: bool(par.clampMax), False)
        info['norm_min'] = _safe(lambda: par.normMin)
        info['norm_max'] = _safe(lambda: par.normMax)
    # Defaults must survive json.dumps; anything exotic becomes its repr.
    try:
        json.dumps(info['default'])
    except Exception:
        info['default'] = _safe(lambda: str(par.default))
    return info

_out = {}
for _t in _types:
    _node = None
    try:
        _node = _sandbox.create(_t, 'probe', initialize=False)
        _entry = {
            'type': _t,
            'family': _safe(lambda: _node.family),
            'min_inputs': _safe(lambda: _node.minInputs),
            'max_inputs': _safe(lambda: _node.maxInputs),
            'num_outputs': _safe(lambda: len(_node.outputConnectors)),
            'is_comp': _safe(lambda: bool(_node.isCOMP), False),
            'is_filter': _safe(lambda: bool(_node.isFilter), False),
            'params': [],
        }
        # Pages in the order the parameter dialog shows them, so a rebuilt
        # schema reads the way the operator actually looks.
        _pageorder = {}
        try:
            for _i, _pg in enumerate(_node.pages):
                _pageorder[_pg.name] = _i
        except Exception:
            pass
        for _p in _node.pars():
            try:
                _entry['params'].append(_describe(_p, _pageorder))
            except Exception as _pe:
                _entry['params'].append({'name': _p.name, 'error': str(_pe)})
        _out[_t] = _entry
    except Exception as _e:
        _out[_t] = {'type': _t, 'error': '%%s: %%s' %% (type(_e).__name__, _e)}
    finally:
        if _node is not None:
            try:
                _node.destroy()
            except Exception:
                pass

result = _out
"""

_CLEANUP = """
_s = op('/tdatlas/probe_sandbox')
result = 'removed' if _s is not None and not _s.destroy() else 'absent'
"""


class ProbeStats:
    def __init__(self) -> None:
        self.types_seen = 0
        self.types_probed = 0
        self.params_merged = 0
        self.failures: dict[str, str] = {}
        self.new_types: list[str] = []

    def summary(self) -> str:
        text = (
            f"probed {self.types_probed}/{self.types_seen} operator types, "
            f"merged {self.params_merged} parameter records"
        )
        if self.new_types:
            text += f", {len(self.new_types)} type(s) absent from the help file"
        if self.failures:
            text += f", {len(self.failures)} type(s) could not be instantiated"
        return text


_ALIAS_PROBE = """
box = op('/tdatlas/alias_probe')
if box: box.destroy()
box = op('/tdatlas').create(baseCOMP, 'alias_probe')
box.allowCooking = False
made = 0
for fam, types in families.items():
    for cls in types:
        try:
            box.create(cls, 'n%%d' %% made, initialize=False)
            made += 1
        except Exception:
            pass
box.save(%(dest)r)
result = {ch.name: ch.OPType for ch in box.children}
"""


def derive_type_aliases(
    client: BridgeClient, progress: Progress | None = None
) -> dict[str, str]:
    """Measure how TouchDesigner spells each operator type when it saves.

    A saved .toe records a contraction — 'geoCOMP' for geometryCOMP — so a
    project read from disk cannot be joined to the index without this map.
    Deriving it takes one save and one expansion: every type is instantiated
    in a sandbox, the sandbox is written out, and each node's saved spelling
    is compared with what TouchDesigner says its type really is.
    """
    say = progress or (lambda _m: None)
    import tempfile

    from ..project.expand import ExpandError, expand
    from ..project.formats import read_node

    dest = str(Path(tempfile.gettempdir()) / "td_atlas_alias_probe.tox")
    try:
        real_types = client.exec(
            _ALIAS_PROBE % {"dest": dest}, timeout=180.0
        )["result"]
    except BridgeError as exc:
        say(f"could not derive saved-name aliases: {exc.message}")
        return {}

    try:
        expansion = expand(dest, refresh=True)
    except ExpandError as exc:
        say(f"could not expand the alias probe: {exc}")
        return {}

    aliases: dict[str, str] = {}
    for n_file in expansion.root.rglob("*.n"):
        node = read_node(n_file.read_text(errors="replace"))
        canonical = real_types.get(n_file.stem)
        if canonical and node.op_type and node.op_type != canonical:
            aliases[node.op_type] = canonical

    try:
        client.exec("op('/tdatlas/alias_probe').destroy()\nresult=1")
    except BridgeError:
        pass
    return aliases


def list_types(client: BridgeClient) -> dict[str, list[str]]:
    """Every operator type TouchDesigner knows, grouped by family."""
    return client.exec(_LIST_TYPES)["result"]


def run(
    client: BridgeClient,
    store: AtomStore,
    chunk_size: int = 40,
    progress: Progress | None = None,
) -> ProbeStats:
    """Probe every operator type and merge the results into `store`."""
    say = progress or (lambda _m: None)
    stats = ProbeStats()

    families = list_types(client)
    all_types: list[str] = []
    for family, types in sorted(families.items()):
        all_types.extend(types)
    stats.types_seen = len(all_types)
    say(
        f"TouchDesigner reports {len(all_types)} operator types across "
        f"{len(families)} families"
    )

    known = {
        row["type"]
        for row in store.conn.execute("SELECT type FROM ops")
    }

    for start in range(0, len(all_types), chunk_size):
        chunk = all_types[start : start + chunk_size]
        payload = _PROBE_CHUNK % {"types": json.dumps(chunk)}
        try:
            probed = client.exec(payload, timeout=120.0)["result"]
        except BridgeError as exc:
            # A whole chunk failing is worth reporting but must not abort the
            # remaining 600-odd types.
            for op_type in chunk:
                stats.failures[op_type] = f"chunk failed: {exc.message}"
            say(f"  chunk {start}-{start + len(chunk)} failed: {exc.message}")
            continue

        rows_to_add = []
        for op_type, entry in probed.items():
            if entry.get("error"):
                stats.failures[op_type] = entry["error"]
                continue
            if op_type not in known:
                stats.new_types.append(op_type)
                rows_to_add.append(
                    {
                        "type": op_type,
                        "family": entry.get("family") or "?",
                        "label": None,
                        "summary": None,
                        "doc_page": None,
                        "class_page": None,
                        "has_own_class": 0,
                    }
                )

        if rows_to_add:
            store.insert_ops(rows_to_add)
            known.update(r["type"] for r in rows_to_add)

        for op_type, entry in probed.items():
            if entry.get("error"):
                continue
            store.merge_runtime_op(
                {
                    "type": op_type,
                    "min_inputs": entry.get("min_inputs"),
                    "max_inputs": entry.get("max_inputs"),
                    "num_outputs": entry.get("num_outputs"),
                    "is_comp": entry.get("is_comp"),
                }
            )
            usable = [p for p in entry.get("params", []) if not p.get("error")]
            stats.params_merged += store.merge_runtime_params(op_type, usable)
            stats.types_probed += 1

        store.conn.commit()
        say(f"  {min(start + chunk_size, len(all_types))}/{len(all_types)}")

    try:
        client.exec(_CLEANUP)
    except BridgeError:
        pass

    aliases = derive_type_aliases(client, say)
    if aliases:
        store.insert_type_aliases(aliases)
        store.conn.commit()
        say(f"recorded {len(aliases)} saved-name alias(es)")

    store.set_meta("runtime_pass", "complete")
    store.set_meta("runtime_types_probed", str(stats.types_probed))
    return stats
