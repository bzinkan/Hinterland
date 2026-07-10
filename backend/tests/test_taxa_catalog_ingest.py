from __future__ import annotations

from pathlib import Path

from admin.taxa_catalog_ingest import _default_pack_path, _parse_args, load_pack


def test_default_pack_resolves_repository_layout() -> None:
    path = _default_pack_path()

    assert path.is_file()
    pack, checksum = load_pack(path)
    assert pack.pack_id == "core"
    assert len(checksum) == 64


def test_default_pack_resolves_container_image_layout(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    module_file = app_root / "admin" / "taxa_catalog_ingest.py"
    pack_path = app_root / "content" / "taxa" / "core.json"
    pack_path.parent.mkdir(parents=True)
    pack_path.write_text("{}", encoding="utf-8")

    resolved = _default_pack_path(
        cwd=tmp_path / "unrelated-working-directory",
        module_file=module_file,
    )

    assert resolved == pack_path


def test_explicit_pack_argument_wins() -> None:
    path = Path("regional.json")

    assert _parse_args([str(path)]).pack == path
