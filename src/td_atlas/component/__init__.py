"""Sources that run inside TouchDesigner.

`handler.py` and `bootstrap.py` are data as far as the host is concerned: they
are copied into ~/.td-atlas and executed by TouchDesigner's own interpreter,
where the `td` globals exist. Importing them on the host would fail, so this
package deliberately imports nothing.
"""
