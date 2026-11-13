from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fitz
from PIL import Image

import bot


class FakeBot:
    def __init__(self) -> None:
        self.photos = []
        self.messages = []

    async def send_photo(self, **kwargs) -> None:
        self.photos.append(kwargs)

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = SimpleNamespace(chat=SimpleNamespace(id=123))
        self.answers = []

    async def answer(self, *args, **kwargs) -> None:
        self.answers.append((args, kwargs))

    async def edit_message_reply_markup(self, **kwargs) -> None:
        pass


class PdfCardFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_need_image_generates_metadata_from_saved_note(self) -> None:
        token = "1234567890abcdef"
        note = "Nota original de esta noticia."
        record = {"_press_release": note}
        context = SimpleNamespace(
            user_data={"card_metadata": {token: record}},
            bot_data={},
            bot=FakeBot(),
        )
        query = FakeQuery(f"need_image:{token}")
        generated = {
            "titular": "La noticia en Ciudad Rodrigo",
            "resaltado": "Ciudad Rodrigo",
            "subtitulo": "Nota original de esta noticia.",
            "categoria": "SOCIEDAD",
            "lugar": None,
        }

        with patch(
            "bot.generate_card_metadata",
            new=AsyncMock(return_value=generated),
        ) as generate_metadata:
            with patch(
                "bot.fotos_para_lugar",
                side_effect=FileNotFoundError("missing index"),
            ):
                await bot.handle_callback(SimpleNamespace(callback_query=query), context)

        generate_metadata.assert_awaited_once_with(note)
        self.assertEqual(record["categoria"], "SOCIEDAD")
        self.assertEqual(len(context.bot.photos), 1)

    async def test_metadata_failure_is_reported_in_callback_alert(self) -> None:
        token = "1234567890abcdef"
        context = SimpleNamespace(
            user_data={
                "card_metadata": {token: {"_press_release": "Nota original."}}
            },
            bot_data={},
            bot=FakeBot(),
        )
        query = FakeQuery(f"need_image:{token}")

        with patch(
            "bot.generate_card_metadata",
            new=AsyncMock(side_effect=ValueError("invalid metadata")),
        ):
            await bot.handle_callback(SimpleNamespace(callback_query=query), context)

        self.assertEqual(len(context.bot.photos), 0)
        self.assertTrue(query.answers[-1][1]["show_alert"])

    async def test_missing_archive_index_falls_back_to_no_photo(self) -> None:
        token = "1234567890abcdef"
        metadata = {
            "titular": "Limpieza del canal del río Águeda",
            "resaltado": "río Águeda",
            "subtitulo": "Retiran maleza y sedimentos junto al puente romano.",
            "categoria": "MEDIO AMBIENTE",
            "lugar": "rio-agueda",
        }
        fake_bot = FakeBot()
        context = SimpleNamespace(
            user_data={"card_metadata": {token: metadata}},
            bot_data={},
            bot=fake_bot,
        )
        query = FakeQuery(f"need_image:{token}")

        with patch("bot.fotos_para_lugar", side_effect=FileNotFoundError("missing index")):
            await bot.handle_callback(SimpleNamespace(callback_query=query), context)

        self.assertEqual(len(fake_bot.photos), 1)
        self.assertEqual(fake_bot.photos[0]["photo"].filename, "tarjeta.jpg")
        self.assertEqual(len(fake_bot.messages), 0)

    async def test_embedded_pdf_image_can_be_selected_as_card_background(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            image_bytes = BytesIO()
            Image.new("RGB", (500, 400), "blue").save(image_bytes, format="PNG")
            pdf_path = directory / "notice.pdf"
            document = fitz.open()
            document.new_page().insert_image(
                fitz.Rect(20, 20, 520, 420), stream=image_bytes.getvalue()
            )
            document.save(pdf_path)
            document.close()

            token = "1234567890abcdef"
            metadata = {
                "titular": "Limpieza del canal del río Águeda",
                "resaltado": "río Águeda",
                "subtitulo": "Retiran maleza y sedimentos junto al puente romano.",
                "categoria": "MEDIO AMBIENTE",
                "lugar": "rio-agueda",
                "_pdf_path": str(pdf_path),
            }
            fake_bot = FakeBot()
            context = SimpleNamespace(
                user_data={"card_metadata": {token: metadata}},
                bot_data={},
                bot=fake_bot,
            )
            first_query = FakeQuery(f"need_image:{token}")

            await bot.handle_callback(
                SimpleNamespace(callback_query=first_query), context
            )

            self.assertEqual(len(fake_bot.photos), 1)
            choice = fake_bot.photos[0]["reply_markup"].inline_keyboard[0][0]
            self.assertEqual(choice.callback_data, f"pdf_foto:{token}:0")
            self.assertFalse(pdf_path.exists())

            select_query = FakeQuery(choice.callback_data)
            await bot.handle_callback(
                SimpleNamespace(callback_query=select_query), context
            )

            self.assertEqual(len(fake_bot.photos), 2)
            self.assertEqual(fake_bot.photos[1]["photo"].filename, "tarjeta.jpg")
            self.assertTrue(metadata["_current_photo"])
            self.assertEqual(metadata["_pdf_images"], [])

            temporary_directory = context.bot_data.pop("pdf_temp_dir")
            temporary_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
