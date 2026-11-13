"""Añade una foto optimizada y sus lugares al índice de archivo."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from archivo_fotos import ARCHIVE_DIR, LUGARES, cargar_indice, optimizar_jpeg


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reduce una foto a JPEG y la añade a archivo/indice.yaml."
    )
    parser.add_argument("foto", type=Path, help="Ruta de la foto original")
    parser.add_argument(
        "--lugar",
        action="append",
        choices=LUGARES,
        required=True,
        help="Lugar asociado; repite la opción para asignar varios",
    )
    args = parser.parse_args()
    if not args.foto.is_file():
        parser.error(f"No existe la foto: {args.foto}")

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{args.foto.stem}.jpg"
    destination = ARCHIVE_DIR / filename
    entries = cargar_indice()
    if destination.exists() or any(entry["archivo"] == filename for entry in entries):
        parser.error(f"Ya existe una foto con el nombre {filename}.")

    unique_places = list(dict.fromkeys(args.lugar))
    optimizar_jpeg(args.foto, destination, max_alto=1350, calidad=85)
    entries.append({"archivo": filename, "lugares": unique_places})
    index_path = ARCHIVE_DIR / "indice.yaml"
    temporary_index = index_path.with_suffix(".yaml.tmp")
    try:
        temporary_index.write_text(
            yaml.safe_dump(entries, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        temporary_index.replace(index_path)
    except OSError:
        destination.unlink(missing_ok=True)
        temporary_index.unlink(missing_ok=True)
        raise
    print(f"Foto optimizada: {destination}")
    print(f"Índice actualizado: {index_path}")


if __name__ == "__main__":
    main()
