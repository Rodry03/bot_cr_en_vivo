from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import fitz
import yaml
from PIL import Image

from archivo_fotos import cargar_indice, extraer_imagenes_pdf, fotos_para_lugar, optimizar_jpeg


class ArchivoFotosTests(unittest.TestCase):
    def test_index_filters_photos_by_place_and_rejects_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_dir = Path(temporary)
            (archive_dir / "puente.jpg").write_bytes(b"placeholder")
            (archive_dir / "indice.yaml").write_text(
                yaml.safe_dump(
                    [{"archivo": "puente.jpg", "lugares": ["rio-agueda", "puente-romano"]}]
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                fotos_para_lugar("rio-agueda", archive_dir),
                [archive_dir / "puente.jpg"],
            )
            self.assertEqual(fotos_para_lugar("dehesa", archive_dir), [])

            (archive_dir / "indice.yaml").write_text(
                yaml.safe_dump([{"archivo": "faltante.jpg", "lugares": ["rio-agueda"]}]),
                encoding="utf-8",
            )
            with self.assertRaises(FileNotFoundError):
                cargar_indice(archive_dir)

    def test_optimizer_limits_height_and_writes_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "tall.png"
            destination = Path(temporary) / "optimized.jpg"
            Image.new("RGB", (400, 2000), "green").save(source)

            optimizar_jpeg(source, destination, max_alto=1350)

            with Image.open(destination) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (270, 1350))

    def test_pdf_extraction_skips_small_logos_and_deduplicates_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            pdf_path = directory / "fixture.pdf"
            photo = BytesIO()
            logo = BytesIO()
            Image.new("RGB", (500, 400), "blue").save(photo, format="PNG")
            Image.new("RGB", (64, 64), "red").save(logo, format="PNG")
            document = fitz.open()
            page = document.new_page(width=600, height=800)
            page.insert_image(fitz.Rect(20, 20, 520, 420), stream=photo.getvalue())
            page.insert_image(fitz.Rect(530, 20, 594, 84), stream=logo.getvalue())
            document.new_page().insert_image(
                fitz.Rect(20, 20, 520, 420), stream=photo.getvalue()
            )
            document.save(pdf_path)
            document.close()

            extracted = extraer_imagenes_pdf(pdf_path, directory / "extracted")

            self.assertEqual(len(extracted), 1)
            with Image.open(extracted[0]) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (500, 400))


if __name__ == "__main__":
    unittest.main()
