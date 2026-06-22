"""Built-in check batteries. Importing a module REGISTERS its checks (via the @check decorator).

Lanes (see COORDINATION.md):
- Claude-main: structure, content (per-genre), cost.
- Claude-parallel: audio, visual, video_sync, music.
- Each module is independent — add a module + import it here; no other file changes (INV: add-a-file).
"""
from . import cost, structure  # noqa: F401,I001  (Claude-main lane)
from . import visual  # noqa: F401,I001  (Claude-parallel lane: visual-images)
from . import audio  # noqa: F401,I001  (Claude-parallel lane: audio-mix)
from . import video_sync  # noqa: F401,I001  (Claude-parallel lane: video-sync)
from . import music  # noqa: F401,I001  (Claude-parallel lane: music-sfx)

# Peer lanes import their modules here as they land (one independent line each — I001 suppressed so
# the add-a-file invariant holds and lanes don't fight over a single merged import block):
# from . import content     # noqa: F401,I001  (Claude-main lane, next — from the catalog)
