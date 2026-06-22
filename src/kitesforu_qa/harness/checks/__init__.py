"""Built-in check batteries. Importing a module REGISTERS its checks (via the @check decorator)."""

from . import audio, content, cost, music, structure, video_sync, visual  # noqa: F401,I001
