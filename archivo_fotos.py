"""Utilidades para el archivo persistente de fotos y las imágenes de PDFs."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import fitz
import yaml
from PIL import Image, ImageOps


LUGARES = (
    "rio-agueda",
    "puente-romano",
    "plaza-mayor",
    "ayuntamiento",
    "catedral",
    "castillo",
    "muralla",
    "dehesa",
)
ARCHIVE_DIR = Path(__file__).resolve().parent / "archivo"
MIN_IMAGE_WIDTH = 200
MIN_IMAGE_HEIGHT = 200
MIN_IMAGE_PIXELS = 100_000
PDF_IMAGE_MAX_HEIGHT = 1350


def cargar_indice(archive_dir: Path = ARCHIVE_DIR) -> list[dict[str, object]]:
    """Lee y valida el índice del archivo."""
    index_path = archive_dir / "indice.yaml"
    if not index_path.is_file():
        raise FileNotFoundError(f"No se encuentra el índice de fotos: {index_path}")

    try:
        with index_path.open(encoding="utf-8") as file:
            entries = yaml.safe_load(file)
    except yaml.YAMLError as exc:
        raise ValueError(f"El índice de fotos no es YAML válido: {index_path}") from exc
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError("El índice de fotos debe contener una lista.")

    validated: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Cada entrada del índice debe ser un objeto.")
        filename = entry.get("archivo")
        places = entry.get("lugares")
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
            or "/" in filename
            or "\\" in filename
            or filename in {".", ".."}
            or Path(filename).suffix.lower() not in {".jpg", ".jpeg"}
        ):
            raise ValueError("El índice contiene un nombre de foto no válido.")
        if (
            not isinstance(places, list)
            or not places
            or any(not isinstance(place, str) or place not in LUGARES for place in places)
        ):
            raise ValueError(f"El índice contiene lugares no válidos para {filename}.")
        photo_path = archive_dir / filename
        if not photo_path.is_file():
            raise FileNotFoundError(f"No se encuentra la foto del índice: {photo_path}")
        validated.append({"archivo": filename, "lugares": places})
    return validated


def fotos_para_lugar(lugar: str | None, archive_dir: Path = ARCHIVE_DIR) -> list[Path]:
    """Devuelve, en orden de índice, las fotos asociadas a un lugar válido."""
    if lugar not in LUGARES:
        return []
    return [
        archive_dir / str(entry["archivo"])
        for entry in cargar_indice(archive_dir)
        if lugar in entry["lugares"]
    ]


def optimizar_jpeg(
    origen: str | Path | Image.Image,
    destino: str | Path,
    max_alto: int = 1350,
    calidad: int = 85,
) -> Path:
    """Guarda una imagen como JPEG, reduciendo su alto sin ampliarla ni deformarla."""
    destino_path = Path(destino)
    destino_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(origen, Image.Image):
        image = ImageOps.exif_transpose(origen).convert("RGB")
    else:
        with Image.open(origen) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    if image.height > max_alto:
        ancho = round(image.width * max_alto / image.height)
        image = image.resize((ancho, max_alto), Image.Resampling.LANCZOS)
    image.save(
        destino_path,
        format="JPEG",
        quality=calidad,
        optimize=True,
        progressive=True,
    )
    return destino_path


def extraer_imagenes_pdf(
    pdf_path: str | Path,
    output_dir: str | Path,
    max_paginas: int = 5,
    max_imagenes: int = 8,
) -> list[Path]:
    """Extrae fotos incrustadas no triviales de las primeras páginas de un PDF."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []
    seen_xrefs: set[int] = set()

    with fitz.open(pdf_path) as document:
        if document.needs_pass:
            raise ValueError("El PDF está protegido con contraseña.")
        for page in document[:max_paginas]:
            for image_ref in page.get_images(full=True):
                xref = int(image_ref[0])
                if xref <= 0 or xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                extracted = document.extract_image(xref)
                width = int(extracted["width"])
                height = int(extracted["height"])
                if (
                    width < MIN_IMAGE_WIDTH
                    or height < MIN_IMAGE_HEIGHT
                    or width * height < MIN_IMAGE_PIXELS
                ):
                    continue

                output_file = output_path / f"pdf-image-{uuid4().hex}.jpg"
                with Image.open(BytesIO(extracted["image"])) as image:
                    optimizar_jpeg(
                        image,
                        output_file,
                        max_alto=PDF_IMAGE_MAX_HEIGHT,
                        calidad=85,
                    )
                image_paths.append(output_file)
                if len(image_paths) >= max_imagenes:
                    return image_paths
    return image_paths


__all__ = [
    "ARCHIVE_DIR",
    "LUGARES",
    "cargar_indice",
    "extraer_imagenes_pdf",
    "fotos_para_lugar",
    "optimizar_jpeg",
]
