"""M20-M21: images and footnotes."""

from tests.builders import body, convert, doc, p
from tests.mdparse import parse
from tomd.ir import Asset, FootnoteDef, FootnoteRef, Image, Text
from tomd.ir import diagnostics as codes
from tomd.writers.markdown import asset_filename


def test_M20_image_path_and_caption_after() -> None:
    asset = Asset.from_bytes(b"\x89PNG fake", "image/png")
    d = doc(Image(asset.sha256, "Un [gato]", (Text("Figura 1"),)), assets=(asset,))
    out = body(convert(d, slug="mi-libro").text)
    assert out == f"![Un \\[gato\\]](mi-libro.assets/{asset.sha256[:12]}.png)\n\n*Figura 1*\n"
    image = next(c for t in parse(out) if t.type == "inline" for c in t.children or [])
    assert image.type == "image"
    assert "".join(c.content for c in image.children or []) == "Un [gato]"


def test_M20_asset_filename_uses_hash_prefix_and_extension() -> None:
    asset = Asset.from_bytes(b"x", "image/jpeg")
    assert asset_filename(asset) == f"{asset.sha256[:12]}.jpg"


def test_M20_missing_asset_drops_image_and_keeps_caption() -> None:
    result = convert(doc(Image("0" * 64, "alt", (Text("Pie"),))))
    assert body(result.text) == "*Pie*\n"
    assert [d.code for d in result.diagnostics] == [codes.IMAGE_DROPPED]


def test_M21_footnotes_numbered_and_grouped_at_end() -> None:
    d = doc(
        FootnoteDef("b", (p("Nota B"),)),
        p("Uno", FootnoteRef("b"), " dos", FootnoteRef("a"), " otra", FootnoteRef("b")),
        FootnoteDef("a", (p("Nota A"), p("Segundo párrafo"))),
        p("fin"),
    )
    out = body(convert(d).text)
    assert out == (
        "Uno[^1] dos[^2] otra[^1]\n\nfin\n\n[^1]: Nota B\n\n[^2]: Nota A\n\n    Segundo párrafo\n"
    )
    types = [t.type for t in parse(out)]
    assert types.count("footnote_ref") == 0  # refs live inside inline tokens
    assert "footnote_block_open" in types
