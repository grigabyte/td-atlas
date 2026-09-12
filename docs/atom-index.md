# The atom index

Two passes produce one SQLite index, exact for the build it was made from.

**Static pass.** Offline, no TouchDesigner process, 13–16 seconds over three
runs, measured 2026-09-12 on an M-series Mac and build 2025.32460:

| Source in the app bundle | What it yields |
| --- | --- |
| `Config/TDParameterHelp.json` | every operator and parameter: labels, prose, types |
| `Samples/Learn/OfflineHelp/` | the offline wiki — Python classes, operator pages, glossary entries, concept and technique articles |
| `Samples/Palette/` | **ready-made components** — projection mappers, corner-pinners, audio analysers |
| `Samples/Learn/OPSnippets/` | working example networks, one per operator |
| `Config/Help/{command.help,exprhelp}` | command and expression entries |

**Runtime pass.** Instantiates every operator type inside a non-cooking sandbox
and reads what documentation does not record. Defaults, numeric ranges and
clamps, **menu options**, parameter pages and ordering, connector counts, and
the contracted type names TouchDesigner writes when it saves. Cooking is
disabled on the sandbox. A Video Device In TOP that is allowed to cook opens a
camera.

The two passes do not add up, and the runtime pass is the reason. It finds
operator types the help JSON does not describe and thousands of parameters it
does not list, mostly the ones whose defaults and menu options only a live
instance knows.

**The counts are a property of your build.** Run `td-atlas status` and it prints
the operators, parameters and wiki articles your index actually holds. That line
is what the project treats as authoritative. For scale, on build 2025.32460
under macOS it reads `667 ops, 24251 params, 2060 articles`.
