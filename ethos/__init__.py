__version__ = "3 Beta 1"

# The product's name is the version, not a second string that has to be kept in
# step with it: everything user-facing — the CLI banner, the FastAPI titles, the
# lineage every prompt opens with, the default name the agent answers to — is
# spelled by deriving it from the one version designation. A future release edits
# this line and nothing else, which is the whole reason the version lives alone.
PRODUCT_NAME = f"Clio Agent {__version__}"

# The date this build was cut, Japan Standard Time. Not derived from anything and
# not read from the clock: a release date is a fact about the artefact, and a
# machine whose timezone is not JST still describes the same release.
RELEASE_DATE = "2026-10-03"
RELEASE_TIMEZONE = "JST"

# What the agent calls itself until somebody names it in the interface.
DEFAULT_SELF_NAME = PRODUCT_NAME
