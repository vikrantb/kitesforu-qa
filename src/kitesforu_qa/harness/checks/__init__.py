"""Built-in check batteries. Importing a module REGISTERS its checks (via the @check decorator).

"""

# Peer lanes import their modules here as they land:
# from . import audio, visual, video_sync, music   # Claude-parallel

from . import content, cost, structure  # noqa: F401,I001
