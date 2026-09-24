"""What `td_build` refuses on the host, before anything reaches TouchDesigner.

Three refusals from the agents' reports (`ПРОЧТИ-БОЛИ-АГЕНТА.md`, "Спотыкания
помельче"), each one a wrong answer the batch used to give without a word:

- `renameto` on a CHOP was refused as "not a valid menu entry". It is a
  StrMenu, whose menu is a list of suggestions over free text; the index holds
  `["*"]` for it, and the validator treated those as the only legal values.
- `op_connect` with `input_index` instead of `index` wired input 0, because
  the handler reads `params.get("index", 0)` and nothing looked at the key
  that was not read.
- `geo1.par.material = '../mat1'` read back as None. OP-reference parameters
  resolve from the operator's parent, not from the operator — measured below.
"""

from __future__ import annotations

import pytest

from td_atlas.atoms.store import AtomStore
from td_atlas.atoms.validate import (
    STEP_KEYS,
    op_reference_problem,
    step_key_problems,
    validate_params,
)
from td_atlas.mcp import server


@pytest.fixture
def store(tmp_path):
    db = AtomStore(tmp_path / "atlas.db")
    db.create()
    db.insert_ops([
        {"type": "renameCHOP", "family": "CHOP", "label": "Rename"},
        {"type": "noiseTOP", "family": "TOP", "label": "Noise"},
        {"type": "geometryCOMP", "family": "COMP", "label": "Geometry"},
    ])
    # Rows as the shipped index holds them (2025.32460): a StrMenu's menu is
    # its suggestions, `["*"]` for every CHOP's renamefrom/renameto.
    db.merge_runtime_params("renameCHOP", [
        {"name": "renameto", "style": "StrMenu", "default": "", "is_menu": True,
         "is_string": True, "menu_names": ["*"], "page": "Rename"},
    ])
    db.merge_runtime_params("noiseTOP", [
        {"name": "type", "style": "Menu", "default": "simplex3d",
         "is_menu": True, "menu_names": ["perlin3d", "simplex3d"],
         "page": "Noise"},
    ])
    db.merge_runtime_params("geometryCOMP", [
        {"name": "material", "style": "MAT", "default": "", "is_op": True,
         "is_string": True, "page": "Render"},
    ])
    db.conn.commit()
    yield db
    db.close()


# -- Г4: a StrMenu takes free text ---------------------------------------------

def test_a_strmenu_takes_a_value_outside_its_suggestions(store):
    result = validate_params(store, "renameCHOP", {"renameto": "bass*"})
    assert result.ok, result.render()


def test_a_closed_menu_still_refuses_a_value_it_does_not_list(store):
    result = validate_params(store, "noiseTOP", {"type": "simplex5d"})
    assert not result.ok
    assert "simplex3d" in result.problems[0].suggestions


# -- Г2: a key the bridge does not read is refused, with the one it does ------

def test_an_unknown_op_connect_key_is_refused_with_the_right_one():
    problems = step_key_problems([
        {"method": "op_connect",
         "params": {"from": "/p/a", "to": "/p/b", "input_index": 3}},
    ])
    assert len(problems) == 1
    assert "input_index" in problems[0] and "'index'" in problems[0]


def test_known_keys_pass_and_owner_is_always_accepted():
    assert step_key_problems([
        {"method": "op_connect",
         "params": {"from": "/p/a", "to": "/p/b", "index": 3, "owner": "me"}},
        {"method": "op_create",
         "params": {"parent": "/p", "type": "noiseTOP", "name": "n",
                    "connect": [{"from": "/p/a", "index": 0}]}},
    ]) == []


def test_a_key_inside_op_create_connect_is_checked_too():
    problems = step_key_problems([
        {"method": "op_create",
         "params": {"parent": "/p", "type": "noiseTOP",
                    "connect": [{"from": "/p/a", "input": 1}]}},
    ])
    assert len(problems) == 1 and "'index'" in problems[0]


def test_a_step_key_outside_method_and_params_is_refused():
    problems = step_key_problems([
        {"method": "par_set", "path": "/p/a", "pars": {"tx": 1}},
    ])
    assert problems and "params" in problems[0]


def test_a_method_without_a_table_row_is_not_second_guessed():
    # exec, capture and the rest are not in the table: an unlisted method is
    # left to the bridge rather than refused on a guess.
    assert step_key_problems([{"method": "exec", "params": {"code": "1"}}]) == []


def test_the_key_table_matches_what_the_handler_reads():
    """The table is a copy, so it is held to the source it copies.

    `derive()` is the protocol fingerprint's own reading of the handler: every
    key each method pulls out of `params`. A key the handler starts reading
    that the table lacks would be refused here while TouchDesigner accepts it.
    """
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).with_name("test_protocol_fingerprint.py")
    spec = importlib.util.spec_from_file_location("_fingerprint", path)
    fingerprint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fingerprint)
    derived = {}
    for line in fingerprint.derive().splitlines():
        name, _, keys = line.partition(": ")
        derived[name] = set(keys.split()) - {"-"}
    for method, keys in STEP_KEYS.items():
        assert method in derived, f"{method} is not a bridge method"
        assert set(keys) == derived[method], (
            f"{method}: the table says {sorted(keys)}, the handler reads "
            f"{sorted(derived[method])}"
        )


def test_td_build_refuses_the_batch_before_dialling(store, monkeypatch):
    monkeypatch.setattr(server, "store", lambda: store)

    def no_bridge():
        raise AssertionError("the refusal must come before the bridge")

    monkeypatch.setattr(server, "bridge", no_bridge)
    text = server.td_build([
        {"method": "op_connect",
         "params": {"from": "/p/a", "to": "/p/b", "input_index": 3}},
    ])
    assert text.startswith("Refusing to apply")
    assert "input_index" in text


# -- Г3: OP references resolve from the parent --------------------------------
#
# Measured on 2025.32460 (2026-09-24), in a sandbox holding geo1 (geometryCOMP)
# and mat1 (constantMAT) side by side, geo1 holding innermat:
#   material='mat1'        -> /box/mat1
#   material='../mat1'     -> None
#   material='./innermat'  -> /box/geo1/innermat
#   material='innermat'    -> None
# and a Select TOP beside noise n1: 'n1' -> /box/n1, '../n1' -> None,
# './n1' -> None. A bare name is a sibling; '../' starts from the parent.

PROJECT = {"/project1", "/project1/geo1", "/project1/mat1"}


def _exists(path):
    return path in PROJECT


def test_a_parent_relative_op_reference_is_refused_with_both_fixes():
    message = op_reference_problem(
        "material", "../mat1", "/project1/geo1", _exists
    )
    assert message is not None
    assert "resolves to /mat1" in message        # where TouchDesigner looks
    assert "'mat1'" in message                   # the sibling spelling
    assert "'/project1/mat1' (which exists)" in message


def test_a_parent_relative_reference_that_resolves_is_left_alone():
    # One level up is a legitimate place to point: /project1/geo1/sub's
    # '../mat1' reaches /project1/mat1, which is there.
    assert op_reference_problem(
        "material", "../mat1", "/project1/geo1/sub", _exists
    ) is None


def test_nothing_is_refused_when_the_project_cannot_be_asked():
    assert op_reference_problem("material", "../mat1", "/project1/geo1") is None
    assert op_reference_problem(
        "material", "../mat1", "/project1/geo1", lambda _p: None
    ) is None


@pytest.mark.parametrize(
    "value", ["mat1", "/project1/mat1", "./innermat", "", "../geo*", "../a b"]
)
def test_other_op_references_are_left_to_touchdesigner(value):
    assert op_reference_problem(
        "material", value, "/project1/geo1", lambda _p: False
    ) is None


def test_the_validator_applies_it_to_op_parameters(store):
    refused = validate_params(
        store, "geometryCOMP", {"material": "../mat1"},
        owner="/project1/geo1", exists=_exists,
    )
    assert not refused.ok
    assert validate_params(
        store, "geometryCOMP", {"material": "mat1"},
        owner="/project1/geo1", exists=_exists,
    ).ok


class _Project:
    """Answers `op_types` the way the handler does: a type, or None."""

    def __init__(self, paths):
        self.paths = paths
        self.sent = []

    def call(self, method, **params):
        assert method == "op_types"
        return {p: ("geometryCOMP" if p in self.paths else None)
                for p in params["paths"]}

    def batch(self, ops, undo_name, owner):
        self.sent.append(ops)
        return {"applied": len(ops), "results": []}

    version_warning = None
    selection_warning = None


def test_td_build_refuses_a_reference_that_resolves_to_nothing(store, monkeypatch):
    project = _Project({"/project1", "/project1/mat1"})
    monkeypatch.setattr(server, "store", lambda: store)
    monkeypatch.setattr(server, "bridge", lambda: project)
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    text = server.td_build([
        {"method": "op_create",
         "params": {"parent": "/project1", "type": "geometryCOMP",
                    "name": "geo1", "pars": {"material": "../mat1"}}},
    ])
    assert text.startswith("Refusing to apply"), text
    assert "'/project1/mat1' (which exists)" in text
    assert project.sent == []


def test_td_build_counts_what_the_batch_itself_creates(store, monkeypatch):
    # /project1/geo1/sub is made by step 0 and pointed at from inside geo1's
    # child; nothing in the running project has it yet.
    project = _Project({"/project1", "/project1/geo1"})
    monkeypatch.setattr(server, "store", lambda: store)
    monkeypatch.setattr(server, "bridge", lambda: project)
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    text = server.td_build([
        {"method": "op_create",
         "params": {"parent": "/project1/geo1", "type": "noiseTOP",
                    "name": "sub"}},
        {"method": "op_create",
         "params": {"parent": "/project1/geo1/inner", "type": "geometryCOMP",
                    "name": "g", "pars": {"material": "../sub"}}},
    ])
    assert text.startswith("applied 2"), text


class _Writable(_Project):
    """A project that answers `op_types` and records any `par_set` sent."""

    def __init__(self, paths):
        super().__init__(paths)
        self.asked = []

    def call(self, method, **params):
        if method == "par_set":
            self.sent.append(params)
            return {"path": params["path"], "applied": dict(params["pars"])}
        self.asked.append(sorted(params["paths"]))
        return super().call(method, **params)


def test_td_set_params_checks_a_reference_without_being_told_the_type(
    store, monkeypatch
):
    """'../mat1' on /project1/geo1 read back as None, with no op_type passed.

    The type is asked of the bridge in the same `op_types` call that resolves
    the reference, so the check costs no round trip it did not already make.
    """
    project = _Writable({"/project1", "/project1/geo1", "/project1/mat1"})
    monkeypatch.setattr(server, "store", lambda: store)
    monkeypatch.setattr(server, "bridge", lambda: project)
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    text = server.td_set_params("/project1/geo1", {"material": "../mat1"})
    assert "'/project1/mat1' (which exists)" in text, text
    assert project.sent == []
    assert len(project.asked) == 1 and "/project1/geo1" in project.asked[0]


def test_td_set_params_without_a_type_still_writes_what_checks_out(
    store, monkeypatch
):
    project = _Writable({"/project1", "/project1/geo1", "/project1/mat1"})
    monkeypatch.setattr(server, "store", lambda: store)
    monkeypatch.setattr(server, "bridge", lambda: project)
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    text = server.td_set_params("/project1/geo1", {"material": "mat1"})
    assert text.startswith("/project1/geo1: material="), text
    assert len(project.sent) == 1


def test_a_type_the_bridge_reports_but_the_index_lacks_is_not_refused(
    store, monkeypatch
):
    """The bridge's type is real; an index without it is from another build."""

    class Newer(_Writable):
        def call(self, method, **params):
            if method == "op_types":
                self.asked.append(sorted(params["paths"]))
                return {p: "brandnewTOP" for p in params["paths"]}
            return super().call(method, **params)

    project = Newer(set())
    monkeypatch.setattr(server, "store", lambda: store)
    monkeypatch.setattr(server, "bridge", lambda: project)
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    text = server.td_set_params("/project1/new1", {"amp": 2})
    assert "unknown operator type" not in text, text
    assert len(project.sent) == 1


def test_td_set_params_without_an_index_does_not_ask_for_types(monkeypatch):
    project = _Writable({"/project1/geo1"})

    def no_index():
        raise RuntimeError("no index")

    monkeypatch.setattr(server, "store", no_index)
    monkeypatch.setattr(server, "bridge", lambda: project)
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    server.td_set_params("/project1/geo1", {"material": "../mat1"})
    assert project.asked == []
    assert len(project.sent) == 1


def test_the_schema_prints_a_strmenu_as_suggestions(store):
    rows = {p["name"]: p for p in store.parameters("renameCHOP")}
    assert "suggestions=" in server._fmt_param(rows["renameto"])
    rows = {p["name"]: p for p in store.parameters("noiseTOP")}
    assert "options=" in server._fmt_param(rows["type"])
