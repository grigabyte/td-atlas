"""Recovery hints: what happened, what to do, what to call next.

An MCP tool that fails hands the agent a string, and a bare exception message
is not enough to act on: "LookupError: no operator at path '/project1/blur2'"
says nothing about whether to re-read the network, rebuild the index, restart
TouchDesigner or give up. Without that, an agent guesses, and its cheapest
guess is to repeat the same call.

So every refusal carries doctor's three-part shape (`cli.py`, `render_checks`)
— observed state, `fix:`, and here also `continue with:`, naming a tool or
command that exists. One table below holds every wording, which is what lets
the tests check all of them at once rather than the handful a test happens to
exercise: that no hint recommends an uninstallable package, and that every
tool and subcommand named in one is real.

Where no honest recovery is known — a TouchDesigner exception type nobody has
mapped, a bug in this package — the hint says so and names nothing. That is
the project invariant (an honest gap over a confident wrong answer) applied to
its own error paths.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Callable

from ..bridge.client import BridgeError, BridgeUnavailable


class IndexMissing(RuntimeError):
    """The atom index has not been built on this host.

    A distinct type rather than a message the hint layer has to recognise by
    its text: `store()` raises it, `classify()` matches on the class, and
    neither can drift from the other by a reworded string.
    """


@dataclass(frozen=True)
class Recovery:
    """One mapped failure: the cause, the repair, and what to call next.

    `resume` names MCP tools (`td_network`) or CLI subcommands
    (`td-atlas build`) and is empty when no honest next call exists — an
    invented one costs more than a gap, because the agent will make it.
    """

    cause: str
    action: str
    resume: tuple[str, ...] = ()

    def fill(self, **values: str) -> Recovery:
        return Recovery(
            self.cause.format(**values),
            self.action.format(**values),
            self.resume,
        )

    def render(self) -> str:
        lines = [f"cause: {self.cause}", f"fix: {self.action}"]
        if self.resume:
            lines.append("continue with: " + ", ".join(self.resume))
        return "\n".join(lines)


# -- the table --------------------------------------------------------------
#
# Keyed by what was observed, not by which tool observed it: the same bridge
# timeout reaches an agent through any of the twelve bridge tools and the
# useful advice does not vary between them.

HINTS: dict[str, Recovery] = {
    # Bridge transport. Keys match `BridgeUnavailable.reason`.
    "bridge_unreachable": Recovery(
        cause=(
            "nothing answered on the bridge port — TouchDesigner is not "
            "running, or is running without the td-atlas bridge"
        ),
        action=(
            "have the artist open the project and paste the bootstrap line "
            "that 'td-atlas install' prints into TouchDesigner's textport; "
            "'td-atlas doctor' says which link of the chain is missing"
        ),
        resume=("td_instances", "td_doctor"),
    ),
    "bridge_timeout": Recovery(
        cause=(
            "the bridge took the call but did not answer in time; every "
            "request runs on TouchDesigner's main thread during a cook, so a "
            "long script blocks it"
        ),
        action=(
            "wait for the running work to finish, then retry in smaller "
            "pieces rather than one long td_exec"
        ),
        resume=("td_status",),
    ),
    "bridge_http": Recovery(
        cause=(
            "something answered on that port but not with a bridge reply — "
            "another program may hold it, or the Web Server DAT is misconfigured"
        ),
        action=(
            "check the port and the token with 'td-atlas doctor', and "
            "re-stage the bridge with 'td-atlas install' if it disagrees"
        ),
        resume=("td_instances", "td_doctor"),
    ),
    "bridge_protocol": Recovery(
        cause="the bridge and this host speak different protocol versions",
        action=(
            "the line above names which side is behind; 'td-atlas doctor' "
            "reports both versions, and 'td-atlas reload' pushes a current "
            "handler into the running instance"
        ),
        resume=("td_doctor",),
    ),
    # Failures the handler reported. Keys match `BridgeError.type`, which is
    # the exception class name from inside TouchDesigner; the ones here were
    # observed live against build 2025.32460, not guessed.
    "Unauthorized": Recovery(
        cause="the bridge rejected the token this host sent",
        action=(
            "the bridge reads its token from ~/.td-atlas/config.json itself; "
            "'td-atlas doctor' compares the two, and 'td-atlas install' "
            "re-stages the bridge against the current one"
        ),
        resume=("td_doctor",),
    ),
    "LookupError": Recovery(
        cause="no operator exists at that path in the running project",
        action=(
            "list what is actually there before retrying — paths are "
            "case-sensitive, and TouchDesigner numbers a new node ('blur2') "
            "when the name you asked for was taken"
        ),
        resume=("td_network", "td_op_info"),
    ),
    "AttributeError": Recovery(
        cause=(
            "the operator has no member by that name — usually a parameter "
            "name that does not exist on this operator type"
        ),
        action=(
            "take the exact names from the index instead of from "
            "TouchDesigner's documentation, which describes parameter groups "
            "('t') where the settable members are 'tx', 'ty', 'tz'"
        ),
        resume=("td_operator_schema",),
    ),
    "TypeError": Recovery(
        cause="the target is the wrong kind of operator for this call",
        action=(
            "check the family and type of the path you are aiming at, then "
            "aim at one the call accepts"
        ),
        resume=("td_op_info",),
    ),
    "SyntaxError": Recovery(
        cause="the Python sent to TouchDesigner does not parse",
        action=(
            "the message above names the line; note that the code runs in "
            "TouchDesigner's embedded Python 3.11, so 3.12+ syntax fails here"
        ),
        resume=("td_python_api",),
    ),
    "ScopeHeld": Recovery(
        cause="another agent's claim covers the subtree this call writes to",
        action=(
            "the refusal above names the owner and when the claim lapses: "
            "send that owner if the claim is yours, work outside the subtree, "
            "or wait it out"
        ),
        resume=("td_scopes",),
    ),
    "ValueError": Recovery(
        cause="the bridge refused one of the arguments",
        action=(
            "the message above names which one; correct that argument rather "
            "than repeating the call unchanged"
        ),
    ),
    # The opposite of a ValueError, so not folded into it: the write landed.
    "ParEvalError": Recovery(
        cause=(
            "the parameter was written, and reading the value back raised — "
            "which is not the same as the write failing"
        ),
        action=(
            "read the stored expression with td_op_info before changing it: an "
            "expression only evaluable during a cook is already correct. From "
            "a step inside td_build the batch was still rolled back, so the "
            "value is gone either way and has to be reapplied"
        ),
    ),
    # No mapping. Deliberately names nothing to call: see the module docstring.
    "unmapped_bridge_error": Recovery(
        cause=(
            "TouchDesigner raised {type}, which this connector has no mapped "
            "recovery for"
        ),
        action=(
            "none known — treat the message above as the whole answer, and "
            "change something before retrying rather than repeating the call"
        ),
    ),
    "unmapped_error": Recovery(
        cause="{type} was raised inside td-atlas itself, not by TouchDesigner",
        action=(
            "none known — this is a fault in this package rather than "
            "something to repair in the project"
        ),
    ),
    # The offline index.
    "index_missing": Recovery(
        cause="this host has no atom index yet, so nothing can be looked up",
        action=(
            "run 'td-atlas build' for the offline pass, then 'td-atlas probe' "
            "with TouchDesigner open to add defaults, ranges and menu options"
        ),
        resume=("td_doctor",),
    ),
    "index_stale": Recovery(
        cause=(
            "the index names a file that is not on disk — it caches one "
            "TouchDesigner installation and that installation has changed"
        ),
        action=(
            "re-run 'td-atlas build' (then 'td-atlas probe') after "
            "TouchDesigner was moved, updated or reinstalled"
        ),
        resume=("td_doctor",),
    ),
    "no_match": Recovery(
        cause="nothing in the index matched that wording",
        action=(
            "widen it before concluding the thing does not exist: fewer "
            "words, plain language describing the effect, and no family filter"
        ),
        resume=("td_search_operators", "td_search_parameters", "td_docs"),
    ),
    "unknown_op_type": Recovery(
        cause="the index holds no operator of that type",
        action=(
            "type names are camelCase with the family suffix ('noiseTOP', "
            "'audiodeviceinCHOP'); search by what the operator does instead "
            "of spelling one out"
        ),
        resume=("td_search_operators",),
    ),
    "no_example": Recovery(
        cause="TouchDesigner ships no example network for this operator type",
        action=(
            "there is nothing to fetch — read the parameter list and the "
            "documentation for it instead"
        ),
        resume=("td_operator_schema", "td_docs"),
    ),
    "params_refused": Recovery(
        cause=(
            "the parameter names were checked against the index first and "
            "would have failed in TouchDesigner, so nothing was sent"
        ),
        action=(
            "use the exact names from the operator's schema; the suggestions "
            "above come from the index and are safe to trust"
        ),
        resume=("td_operator_schema",),
    ),
    "palette_ambiguous": Recovery(
        cause="that palette name exists in more than one palette folder",
        action=(
            "repeat the call with category= set to one of the folders listed "
            "above, so the choice is yours rather than a guess"
        ),
        resume=("td_palette",),
    ),
    "palette_unknown": Recovery(
        cause="no palette component carries that exact name",
        action=(
            "names are camelCase and matched exactly here; search names, "
            "folders and descriptions instead of guessing the spelling"
        ),
        resume=("td_palette",),
    ),
    # Python extensions on a COMP.
    "extension_syntax": Recovery(
        cause=(
            "the extension code does not parse — it was compiled on this host "
            "first, so nothing reached TouchDesigner"
        ),
        action=(
            "fix the line named above; note that this host parses with its own "
            "Python while TouchDesigner runs an embedded 3.11, so 3.12+ syntax "
            "gets past this check and fails on the far side instead"
        ),
        resume=("td_python_api",),
    ),
    "extension_class_missing": Recovery(
        cause=(
            "the code does not define a top-level class under the name the "
            "extension is being built from"
        ),
        action=(
            "TouchDesigner reaches the class as an attribute of the DAT's "
            "module, so it must be at the top level and spelled exactly as "
            "class_name — a nested or renamed class cannot be reached"
        ),
        resume=("td_docs",),
    ),
    "extension_target": Recovery(
        cause=(
            "the call named both an existing COMP and a COMP to create, or "
            "neither"
        ),
        action=(
            "pass path= for a COMP that is already there, or parent= with "
            "name= to have one created — exactly one of the two"
        ),
        resume=("td_network",),
    ),
    "extension_init_failed": Recovery(
        cause=(
            "the Extensions parameters were set, but instantiating the class "
            "raised, so the COMP carries no extension — and TouchDesigner "
            "reports that nowhere except the textport"
        ),
        action=(
            "the message above is the exception, recovered by re-evaluating "
            "the Extension Object expression; repair the class in the DAT "
            "named above and repeat the call, which overwrites both the code "
            "and the parameters"
        ),
        resume=("td_extension_add", "td_op_info"),
    ),
    "flag_unknown": Recovery(
        cause="no node flag goes by that name",
        action=(
            "the flags a node has are display, render, bypass, lock, expose, "
            "viewer, activeViewer, cloneImmune, allowCooking, selected and "
            "pickable; Clone Master is a parameter, not a flag, so it is set "
            "with td_set_params"
        ),
        resume=("td_flags", "td_set_params"),
    ),
    "no_annotations": Recovery(
        cause="no Annotate COMP exists in that subtree",
        action=(
            "nothing was written there to read — widen the path, or leave the "
            "first note yourself so the next agent has one"
        ),
        resume=("td_network", "td_annotate"),
    ),
    # Reading .toe/.tox from disk.
    "project_unreadable": Recovery(
        cause="the file could not be unpacked as a TouchDesigner project",
        action=(
            "check the path, and that TouchDesigner is installed on this host "
            "— reading a .toe shells out to its 'toeexpand'; "
            "'td-atlas doctor' checks that installation"
        ),
        resume=("td_doctor",),
    ),
    "bad_pattern": Recovery(
        cause="the search pattern is not a valid regular expression",
        action=(
            "the message above names the position; escape the character or "
            "search for a plain substring"
        ),
        resume=("td_project_grep",),
    ),
    "project_text_too_large": Recovery(
        cause=(
            "the network serialises to more text than one tool result may "
            "carry, and a cut-off dump is not valid JSON"
        ),
        action=(
            "narrow with 'path' to one component — td_project_read shows the "
            "tree and where the operators are — or write the whole file with "
            "'td-atlas project text FILE -o network.json' and read it from disk"
        ),
        resume=("td_project_read", "td_project_text"),
    ),
    "project_write_output_exists": Recovery(
        cause=(
            "something already sits at that output path, and repacking writes "
            "the file whole rather than merging into it"
        ),
        action=(
            "name a path that does not exist yet, then compare the two with "
            "td_project_diff before replacing anything of the user's"
        ),
        resume=("td_project_diff",),
    ),
    "project_write_not_a_dump": Recovery(
        cause=(
            "the text handed in is not a network dump — it has no 'operators' "
            "key, so there is nothing to write back"
        ),
        action=(
            "take the text from td_project_text on the same file, edit it, "
            "and hand the whole document back; a fragment cannot be applied"
        ),
        resume=("td_project_text",),
    ),
    "project_path_unknown": Recovery(
        cause="the project holds no operator at that path",
        action=(
            "the message above lists the top-level operators; walk down from "
            "one of those rather than guessing a path"
        ),
        resume=("td_project_read",),
    ),
    "variant_label_taken": Recovery(
        cause=(
            "a variant of that project is already saved under that label, and "
            "saving never overwrites one"
        ),
        action=(
            "pick a label that is free — td_variant_list names the ones in "
            "use — or delete that variant's directory, which the message "
            "above gives the path of"
        ),
        resume=("td_variant_list",),
    ),
    "variant_unknown": Recovery(
        cause="no variant of that project is saved under that label",
        action=(
            "the message above lists the labels that do exist; variants are "
            "grouped by the project's path, so a project that has moved since "
            "the save has none under its new path"
        ),
        resume=("td_variant_list", "td_variant_save"),
    ),
    "variant_corrupt": Recovery(
        cause=(
            "the variant's own copy of the project no longer matches what its "
            "manifest records, so restoring it would hand back something "
            "other than what was saved"
        ),
        action=(
            "nothing was written; the variant's text is still readable, so "
            "take what is needed out of it, remove the variant directory and "
            "save the state again from the project"
        ),
        resume=("td_variant_list",),
    ),
    "variant_output_exists": Recovery(
        cause=(
            "something already sits at that output path, and a restore writes "
            "the file whole rather than merging into it"
        ),
        action=(
            "name a path that does not exist yet, then compare the two with "
            "td_project_diff before replacing anything of the user's"
        ),
        resume=("td_project_diff",),
    ),
    "bad_label": Recovery(
        cause="the label would not be a usable filename",
        action="use only letters, digits, dot, dash and underscore",
        resume=("td_snapshot",),
    ),
    "UnknownMethod": Recovery(
        cause=(
            "the running bridge has no such method — it was staged from an "
            "older version of this package than the one calling it"
        ),
        action=(
            "run 'td-atlas reload' to re-stage the bridge from this host's "
            "sources, then repeat the call — a bridge that answers at all is "
            "running, so the reload reaches it"
        ),
        resume=("td_doctor",),
    ),
    "doctor_failed": Recovery(
        cause="the check pass itself raised, so no link was verified",
        action=(
            "run 'td-atlas doctor' on this host: the CLI runs the same checks "
            "and shows the traceback this call cannot"
        ),
        resume=("td_status",),
    ),
}

# BridgeError types that have their own entry above. Anything else is
# unmapped on purpose rather than by omission.
MAPPED_BRIDGE_ERRORS = frozenset(
    {
        "Unauthorized",
        "LookupError",
        "AttributeError",
        "TypeError",
        "SyntaxError",
        "ScopeHeld",
        "ValueError",
        "UnknownMethod",
    }
)


def hint(key: str, **values: str) -> str:
    """The rendered hint block for a table key, without a leading newline."""
    return HINTS[key].fill(**values).render() if values else HINTS[key].render()


def classify(exc: BaseException) -> Recovery:
    """The recovery for an observed exception.

    Never returns None: an unmapped failure gets the honest-gap entry, so no
    caller has to decide what to say when it does not know.
    """
    if isinstance(exc, BridgeUnavailable):
        reason = getattr(exc, "reason", "") or "bridge_unreachable"
        return HINTS.get(reason, HINTS["bridge_unreachable"])
    if isinstance(exc, BridgeError):
        if exc.type in MAPPED_BRIDGE_ERRORS:
            return HINTS[exc.type]
        return HINTS["unmapped_bridge_error"].fill(type=exc.type)
    if isinstance(exc, IndexMissing):
        return HINTS["index_missing"]

    from ..project import ExpandError
    from ..project.variants import VariantError

    if isinstance(exc, VariantError):
        # The key travels on the exception, so the refusal that raised it and
        # the hint that answers it cannot drift apart over a reworded string.
        return HINTS.get(getattr(exc, "key", ""), HINTS["variant_unknown"])
    if isinstance(exc, ExpandError):
        return HINTS["project_unreadable"]
    return HINTS["unmapped_error"].fill(type=type(exc).__name__)


def from_record(error_type: str = "", reason: str = "") -> Recovery:
    """The recovery for a failure read back out of the call journal.

    `classify` needs a live exception. The journal outlives the process that
    saw one, so it keeps the two fields the table is actually keyed on — the
    exception's type, and for a transport failure its `reason` — and this is
    the same lookup performed on those instead. Keeping them apart matters:
    every `BridgeUnavailable` has the same type and four different repairs.
    """
    if reason:
        return HINTS.get(reason, HINTS["bridge_unreachable"])
    if error_type == "BridgeUnavailable":
        return HINTS["bridge_unreachable"]
    if error_type in MAPPED_BRIDGE_ERRORS:
        return HINTS[error_type]
    if error_type:
        return HINTS["unmapped_bridge_error"].fill(type=error_type)
    return HINTS["unmapped_error"].fill(type="Error")


def failure(exc: BaseException, head: str | None = None) -> str:
    """A tool's whole failure text: what it already said, plus the hint.

    `head` keeps a tool's own wording where that wording carries something the
    generic line cannot — td_build's rollback notice, td_exec's traceback.
    """
    if head is None:
        head = f"error: {exc}"
    return f"{head}\n{classify(exc).render()}"


def guarded(func: Callable[..., Any]) -> Callable[..., Any]:
    """Turn any escaping exception into a hinted string, for one MCP tool.

    Two things at once. An exception out of a tool reaches the agent as an
    opaque ToolError with no hint attached (see AGENTS.md), and the branches
    that already return text should not each need their own copy of the
    mapping. `functools.wraps` keeps the signature FastMCP derives its schema
    from, including td_render's deliberately absent return annotation.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            # Deliberately everything, not a tuple of the expected types: an
            # unforeseen one is precisely the case where the agent gets a
            # ToolError with no hint, and `classify` already has an honest
            # answer for it. BaseException stays uncaught — a KeyboardInterrupt
            # is not a failure to recover from.
            return failure(exc)

    # Read by the test that every registered tool is wrapped, so a tool added
    # later cannot quietly go back to raising ToolError.
    wrapper.__td_atlas_guarded__ = True
    return wrapper
