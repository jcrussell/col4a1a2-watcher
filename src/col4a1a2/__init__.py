"""COL4A1/COL4A2 literature watcher with Claude-based evidence extraction."""

from importlib.metadata import metadata, version

# The one place the version and the repository URL are written is pyproject.toml.
__version__ = version("col4a1a2-watcher")
__repository__ = next((u.split(",", 1)[1].strip() for u in metadata("col4a1a2-watcher").get_all("Project-URL") or []
                       if u.split(",", 1)[0].strip() == "Repository"), "")
