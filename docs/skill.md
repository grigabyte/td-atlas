# The skill

`plugin/skills/touchdesigner/` is an agent-facing guide: how to work through the
connector, and a reference of every trap that produced no error while costing
real time — dormant branches, CPU operators hiding in a menu, feedback loops
that converge to grey, an audio codec that reports success and writes no file,
a licence that halves your resolution without saying so.

It installs as a plugin rather than by copying the directory, so that updating
it is one command instead of a second `cp` nobody remembers to run. This
repository is its own marketplace, so in Claude Code:

```
/plugin marketplace add grigabyte/td-atlas
/plugin install touchdesigner@td-atlas
```

Later, `/plugin marketplace update` pulls in whatever the skill has learned. An
agent whose client has no plugins reads the same thing from the checkout —
point it at
[`plugin/skills/touchdesigner/SKILL.md`](../plugin/skills/touchdesigner/SKILL.md)
and nothing else is missing but the one-command update.
