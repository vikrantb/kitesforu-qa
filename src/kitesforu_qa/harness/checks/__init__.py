"""Built-in check batteries. Importing a module REGISTERS its checks (via the @check decorator).

Lanes (see COORDINATION.md):
- Claude-main: structure, content (per-genre), cost.
- Claude-parallel: audio, visual, video_sync, music.
- Each module is independent — add a module + import it here; no other file changes (INV: add-a-file).
"""
from . import cost, structure  # noqa: F401  (Claude-main lane)

# Peer lanes import their modules here as they land:
# from . import audio, visual, video_sync, music   # Claude-parallel
# from . import content                             # Claude-main (next, from the catalog)
