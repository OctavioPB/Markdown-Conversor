"""Serializers. writers/markdown.py is the only module that emits Markdown."""

from tomd.writers.markdown import RenderResult, asset_filename, render_markdown

__all__ = ["RenderResult", "asset_filename", "render_markdown"]
