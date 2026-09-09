# The atom index

Two passes produce one SQLite index, exact for the build it was made from
rather than scraped from a wiki describing some other release.

**Static pass** — offline, no TouchDesigner process, 23–30 seconds measured
on an M-series Mac:

| Source in the app bundle | What it yields |
| --- | --- |
| `Config/TDParameterHelp.json` | every operator and parameter: labels, prose, types |
| `Samples/Learn/OfflineHelp/` | the offline wiki — Python classes, operator pages, glossary entries, concept and technique articles |
| `Samples/Palette/` | **ready-made components** — projection mappers, corner-pinners, audio analysers |
| `Samples/Learn/OPSnippets/` | working example networks, one per operator |
| `Config/Help/{command.help,exprhelp}` | command and expression entries |

**Runtime pass** — instantiates every operator type inside a non-cooking
sandbox and reads what documentation does not record: defaults, numeric ranges
and clamps, **menu options**, parameter pages and ordering, connector counts,
and the contracted type names TouchDesigner writes when it saves. Cooking is
disabled on the sandbox so that creating a Video Device In TOP does not open a
camera.

The two passes do not add up, and the runtime pass is the reason: it finds
operator types the help JSON does not describe and thousands of parameters it
does not list — mostly the ones whose defaults and menu options are only
knowable by asking a live instance.

**The counts are a property of your build, not of this page.** Run
`td-atlas status`: it prints the operators, parameters and wiki articles your
index actually holds, and that line — not this page — is what the project
treats as authoritative. For scale, on the build this page was written
against (2025.32460, macOS) it reads
`667 ops, 24251 params, 2060 articles`.
