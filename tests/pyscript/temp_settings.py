"""Verify temporary setting overrides passed to run_pyscript and created via the bridge."""

# The script is launched with `run_pyscript --set quiet=true`, so quiet is True in here.
outer = app("set quiet")
print(outer.data["quiet"])

# A Python setting scope applies inside the block and restores on exit. Nested scopes
# restore the value set by run_pyscript rather than the application default.
with app.temporary_settings(echo=True):
    inner = app("set echo")
    print(inner.data["echo"])

after_inner = app("set echo")
print(after_inner.data["echo"])
