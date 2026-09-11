# Layout

```
td-atlas/
├── README.md               the front page, English
├── README.ru.md            the same page in Russian
├── README.zh-CN.md         the same page in Simplified Chinese
├── AGENTS.md               contributor guide, human or agent
├── CONTRIBUTING.md         the short version: how to run the checks
├── CLAUDE.md               entry points for an agent opening this repository
├── CHANGELOG.md            Keep a Changelog; every protocol change is in it
├── LICENSE                 MIT
├── install.sh              the one-line install, POSIX sh, nothing outside
│                           the checkout and ~/.td-atlas
├── pyproject.toml
├── .gitignore
├── .github/
│   ├── workflows/
│   │   ├── ci.yml          pytest and ruff, macOS and Windows, Python 3.11-3.14
│   │   └── pages.yml       builds the site, and serves install.sh as /i
│   ├── ISSUE_TEMPLATE/     build, OS and `td-atlas doctor` output
│   └── td-atlas.png        the mark the README shows
├── docs/
│   ├── architecture.md     the three layers and the reasoning
│   ├── cli.md              every subcommand and flag, and what it needs
│   ├── formats.md          the undocumented .toe format, measured
│   ├── atom-index.md       the two passes that build the index
│   ├── bridge.md           the component that runs inside TouchDesigner
│   ├── health.md           what TouchDesigner does not report
│   ├── journal.md          the append-only trail of bridge calls
│   ├── offline-projects.md reading a .toe with TouchDesigner closed
│   ├── network-text.md     the diffable text written beside the .toe
│   ├── compatibility.md    platforms, and what this can change in a project
│   ├── skill.md            the agent skill, and installing it as a plugin
│   ├── bundle.md           building and publishing the .mcpb
│   ├── troubleshooting.md  every symptom, what it is, what to run
│   ├── development.md      the test suite and the invariant it holds
│   ├── layout.md           this file
│   └── ru/                 the Russian mirror, file for file, each one
│                           carrying the hash of its English source
├── site/                   the public site: the landing page and /docs/
│   ├── index.html          the landing page, both languages in one document
│   ├── baza.mjs            the base path, one place for both halves of the build
│   ├── vite.config.ts      the vite build, and the base path in the page's links
│   ├── package.json
│   ├── package-lock.json
│   ├── public/             favicons and the apple touch icon
│   ├── src/                the landing page in the browser
│   │   ├── main.ts             motion, buttons, and the page at rest
│   │   ├── dvizhenie.ts        scroll, reveal and parallax, loaded on its own
│   │   ├── teksty.{ts,json}    every string of the page, both languages
│   │   ├── stil.css            the movement laid over the locked markup
│   │   └── objekt/             the first screen's field, canvas and WebGL2
│   └── generator/          the documentation pages, built from this repository
│       ├── sborka.mjs          sections, both languages, and every link
│       ├── shablon.mjs         markup and theme, the d1 layout
│       ├── md.mjs              markdown into blocks
│       ├── fixtura.mjs         three canonical pages, for the layout check
│       └── znak.svg            the mark in the header
├── .claude-plugin/
│   └── marketplace.json    this repository as a marketplace
├── packaging/
│   ├── manifest.json       the MCPB manifest, generated from pyproject.toml
│   └── .mcpbignore         what stays out of the bundle
├── scripts/
│   ├── build_mcpb.py       build the bundle, the registry submission, build.json
│   └── publish.sh          the one step that sends anything outward
├── plugin/                 the plugin the marketplace above offers, and
│   │                       the only part of this tree it installs
│   ├── .claude-plugin/plugin.json
│   └── skills/
│       └── touchdesigner/  the agent skill
│           ├── SKILL.md
│           └── references/{gotchas,tools}.md
├── src/td_atlas/
│   ├── install.py          locate a TouchDesigner installation
│   ├── config.py           the ~/.td-atlas handshake between host and TD
│   ├── cli.py              command line, parity with the MCP tools
│   ├── journal.py          the append-only trail of bridge calls
│   ├── atoms/              the offline index
│   │   ├── extract_static.py   pass over the app bundle
│   │   ├── probe.py            runtime pass, incl. type-alias derivation
│   │   ├── htmltext.py         wiki HTML -> text, with categories
│   │   ├── validate.py         parameter checking before anything is sent
│   │   └── store.py            SQLite schema, FTS5 search and ranking
│   ├── bridge/             talking to a running instance
│   │   ├── client.py           JSON-RPC over HTTP, stdlib only
│   │   ├── health.py           the silent-failure detector
│   │   └── filmstrip.py        contact sheets, with a stdlib PNG encoder
│   ├── component/          code that runs *inside* TouchDesigner
│   │   ├── bootstrap.py        builds the bridge network in place
│   │   ├── handler.py          the RPC handler
│   │   └── ruff.toml           TouchDesigner's injected globals, declared
│   ├── project/            reading .toe/.tox without TouchDesigner
│   │   ├── expand.py           driving toeexpand/toecollapse on a copy
│   │   ├── formats.py          the undocumented file formats
│   │   ├── model.py            the operator tree and type resolution
│   │   ├── diff.py             semantic comparison
│   │   ├── render.py           tree description and code search
│   │   ├── serialize.py        the whole network as text
│   │   ├── rebuild.py          the return leg: text back into a .toe
│   │   ├── variants.py         saved states, text plus a byte copy
│   │   └── release.py          building the bridge as a .tox
│   └── mcp/
│       ├── server.py           the MCP tools over all three layers
│       └── hints.py            the recovery table every refusal is rendered from
└── tests/
    ├── conftest.py         shared fixtures
    ├── live_network.py     the fixture network the live text diff is measured on
    └── test_*.py           a plain run needs no TouchDesigner running
```

Two kinds of path stay out of the tree above.

**Generated, and not in git.** `.venv/`, the index and staged bridge under
`~/.td-atlas/`, and `dist/` with the built bundle, `dist/server.json` and
`dist/build.json`. That last one records the commit the bundle came from, so
`publish.sh` can refuse a stale one. `.mcp.json` is ignored too.
`td-atlas install --write-mcp-json` writes it, and it holds a path specific to
your machine.

**Ignored, and not generated.** `memory-bank/` holds the owner's working notes.
Nothing in the tree above depends on them, and what a reader needs out of them
lives in `docs/`.
