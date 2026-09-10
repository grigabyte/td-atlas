# The skill

`plugin/skills/touchdesigner/` is an agent-facing guide to working through the
connector, and it names every trap that produced no error while costing real
time:

- dormant branches;
- CPU operators hiding in a menu;
- feedback loops that converge to grey;
- an audio codec that reports success and writes no file;
- a licence that halves your resolution without saying so.

Install it as a plugin; updating it is then one command. This repository is its
own marketplace, so in Claude Code:

```
/plugin marketplace add grigabyte/td-atlas
/plugin install touchdesigner@td-atlas
```

Later, `/plugin marketplace update` pulls in whatever the skill has learned.

An agent whose client has no plugins reads the same guide straight from the
checkout. Point it at
[`plugin/skills/touchdesigner/SKILL.md`](../plugin/skills/touchdesigner/SKILL.md),
and the only thing missing is the one-command update.
