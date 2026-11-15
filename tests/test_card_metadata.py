from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from io import BytesIO

from PIL import Image

import bot


class CardMetadataTests(unittest.TestCase):
    def test_place_is_restricted_to_the_closed_list(self) -> None:
        note = "Los trabajos retiran maleza y sedimentos junto al río Águeda."
        data = {
            "titular": "Limpieza del canal del río Águeda",
            "resaltado": "río Águeda",
            "subtitulo": "Los trabajos retiran maleza y sedimentos",
            "categoria": "MEDIO AMBIENTE",
            "lugar": "RIO-AGUEDA",
        }

        valid = bot._parse_card_metadata(data, note)
        invalid = bot._parse_card_metadata({**data, "lugar": "centro"}, note)

        self.assertEqual(valid["lugar"], "rio-agueda")
        self.assertIsNone(invalid["lugar"])

    def test_invalid_subtitle_uses_only_a_literal_note_fragment(self) -> None:
        note = "Las obras del canal del río Águeda ya han empezado. Se retiran sedimentos."
        data = {
            "titular": "Limpieza del canal del río Águeda",
            "resaltado": "río Águeda",
            "subtitulo": "El canal ya está limpio",
            "categoria": "MEDIO AMBIENTE",
            "lugar": "rio-agueda",
        }

        metadata = bot._parse_card_metadata(data, note)

        self.assertEqual(metadata["subtitulo"], "Las obras del canal del río Águeda ya han empezado.")
        self.assertLessEqual(len(metadata["subtitulo"].split()), 15)

    def test_long_title_is_kept_whole_and_subtitle_quotes_are_tolerated(self) -> None:
        note = (
            "Entre los galardones entregados, «La sospecha de Sofía», de Imanol Uribe, "
            "ha conseguido el premio a mejor largometraje, mejor montaje y otros."
        )
        data = {
            "titular": "Premios, cine y música en la clausura del 15º FICCI-ON de Ciudad Rodrigo",
            "resaltado": "FICCI-ON",
            "subtitulo": '"La sospecha de Sofía", de Imanol Uribe, ha conseguido el premio a mejor largometraje',
            "categoria": "CULTURA",
            "lugar": None,
        }

        metadata = bot._parse_card_metadata(data, note)

        self.assertEqual(metadata["titular"], data["titular"])
        self.assertEqual(metadata["subtitulo"], data["subtitulo"])

    def test_condensed_subtitle_is_accepted_only_if_its_facts_are_in_the_note(self) -> None:
        note = (
            "Durante la gala se han entregado un total de 15 premios, en una edición en la que "
            "han destacado especialmente el cortometraje «Sí se enteran», de Beltrán Peralba, "
            "y el largometraje «La sospecha de Sofía», de Imanol Uribe."
        )
        base = {
            "titular": "Premios en la clausura del 15º FICCI-ON",
            "resaltado": "FICCI-ON",
            "categoria": "CULTURA",
            "lugar": None,
        }
        good = "Se entregaron 15 premios, destacando el cortometraje «Sí se enteran» y el largometraje «La sospecha de Sofía»"
        invented_name = "Se entregaron 15 premios, destacando el largometraje «El hoyo» de Imanol Uribe"
        invented_number = "Se entregaron 20 premios, destacando el largometraje «La sospecha de Sofía»"

        self.assertEqual(bot._parse_card_metadata({**base, "subtitulo": good}, note)["subtitulo"], good)
        for bad in (invented_name, invented_number):
            self.assertNotEqual(bot._parse_card_metadata({**base, "subtitulo": bad}, note)["subtitulo"], bad)

    def test_title_fit_check_allows_three_lines_only(self) -> None:
        from tarjeta import titular_cabe

        self.assertTrue(titular_cabe("Premios, cine y música en la clausura del 15º FICCI-ON"))
        self.assertFalse(titular_cabe(" ".join(["Supercalifragilístico"] * 12)))

    def test_card_image_is_encoded_as_jpeg(self) -> None:
        metadata = {
            "titular": "Limpieza del canal del río Águeda",
            "resaltado": "río Águeda",
            "subtitulo": "Los trabajos retiran maleza junto al puente romano.",
            "categoria": "MEDIO AMBIENTE",
        }

        jpeg = bot._render_card_jpeg(metadata)

        with Image.open(BytesIO(jpeg)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (1080, 1350))

    def test_evicted_news_releases_pdf_assets(self) -> None:
        context = SimpleNamespace(user_data={})
        note = "Nota original por noticia."
        metadata = {
            "titular": "Limpieza del canal del río Águeda",
            "resaltado": "río Águeda",
            "subtitulo": "Los trabajos retiran maleza junto al puente romano.",
            "categoria": "MEDIO AMBIENTE",
            "lugar": "rio-agueda",
        }
        with tempfile.TemporaryDirectory() as temporary:
            paths = []
            for index in range(bot.MAX_CARD_METADATA + 1):
                pdf_path = Path(temporary) / f"{index}.pdf"
                pdf_path.write_bytes(b"pdf")
                paths.append(pdf_path)
                token = bot._store_card_metadata(
                    context,
                    metadata,
                    str(pdf_path),
                    press_release=f"{note} {index}",
                )

            records = context.user_data["card_metadata"]
            self.assertEqual(len(records), bot.MAX_CARD_METADATA)
            self.assertFalse(paths[0].exists())
            self.assertTrue(paths[-1].exists())
            self.assertEqual(records[token]["_press_release"], f"{note} {bot.MAX_CARD_METADATA}")

    def test_draft_keyboard_always_shows_image_button(self) -> None:
        keyboard = bot._draft_keyboard("news-token")

        self.assertEqual(
            keyboard.inline_keyboard[-1][0].callback_data,
            "need_image:news-token",
        )


if __name__ == "__main__":
    unittest.main()
