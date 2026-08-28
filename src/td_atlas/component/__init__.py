"""Sources that run inside TouchDesigner.

`handler.py` and `bootstrap.py` are copied into ~/.td-atlas and executed by
TouchDesigner's own interpreter, where the `td` globals exist. `bootstrap.py`
is useless on the host. `handler.py` is the one file the host does import on
purpose, to read `PROTOCOL_VERSION` from its single owner instead of keeping
a copy in sync — which only works because its module-level code stays plain
stdlib, with every touch of TouchDesigner's injected globals pushed inside
function bodies. This package (`__init__.py`) still deliberately imports
nothing.
"""
