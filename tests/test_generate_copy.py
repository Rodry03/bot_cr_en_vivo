from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot


class GenerateCopyTests(unittest.IsolatedAsyncioTestCase):
    async def test_copy_request_keeps_the_pre_card_call_contract(self) -> None:
        note = "Una asociación local organiza una recogida solidaria."
        output = {
            "tono": "festivo",
            "copy": "Recogida solidaria 🍎",
        }
        create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(output, ensure_ascii=False))
                    )
                ]
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch.object(bot, "AsyncGroq", return_value=client):
                copy_text, tone = await bot.generate_copy(note)

        self.assertEqual((copy_text, tone), (output["copy"], output["tono"]))
        request = create.await_args.kwargs
        self.assertEqual(request["model"], bot.GROQ_MODEL)
        self.assertEqual(request["temperature"], 0.7)
        self.assertEqual(request["max_tokens"], 1024)
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["messages"][0]["content"], bot.COPY_SYSTEM_PROMPT)
        self.assertNotIn("reasoning_effort", request)

    async def test_card_metadata_is_a_separate_low_reasoning_request(self) -> None:
        note = (
            "Limpieza del canal del río Águeda. "
            "Los trabajos retiran maleza y sedimentos junto al puente romano."
        )
        output = {
            "titular": "Limpieza del canal del río Águeda",
            "resaltado": "río Águeda",
            "subtitulo": "Los trabajos retiran maleza y sedimentos junto al puente romano.",
            "categoria": "MEDIO AMBIENTE",
            "lugar": "rio-agueda",
        }
        create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(output, ensure_ascii=False))
                    )
                ]
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch.object(bot, "AsyncGroq", return_value=client):
                metadata = await bot.generate_card_metadata(note)

        self.assertEqual(metadata, output)
        request = create.await_args.kwargs
        self.assertEqual(request["model"], bot.GROQ_MODEL)
        self.assertEqual(request["temperature"], 0.3)
        self.assertEqual(request["reasoning_effort"], "low")
        self.assertEqual(request["max_tokens"], 1024)
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["messages"][0]["content"], bot.CARD_METADATA_INSTRUCTIONS)
        self.assertNotIn('"copy":', request["messages"][0]["content"])
        self.assertNotIn('"tono":', request["messages"][0]["content"])

    def test_blank_line_before_hashtags_is_added_once(self) -> None:
        expected = "Gancho 🎬\n\nLínea uno.\nLínea dos.\n\n#ciudadrodrigoenvivo #cine"
        missing = "Gancho 🎬\n\nLínea uno.\nLínea dos.\n#ciudadrodrigoenvivo #cine"
        doubled = "Gancho 🎬\n\nLínea uno.\nLínea dos.\n\n\n\n#ciudadrodrigoenvivo #cine\n"
        no_hashtags = "Gancho 🎬\nLínea uno con #hashtag en medio."

        self.assertEqual(bot._ensure_blank_line_before_hashtags(missing), expected)
        self.assertEqual(bot._ensure_blank_line_before_hashtags(expected), expected)
        self.assertEqual(bot._ensure_blank_line_before_hashtags(doubled), expected)
        self.assertEqual(bot._ensure_blank_line_before_hashtags(no_hashtags), no_hashtags)

    async def test_generate_copy_adds_blank_line_before_hashtags(self) -> None:
        output = {"tono": "festivo", "copy": "Gancho 🎬\nLínea.\n#ciudadrodrigoenvivo #cine"}
        create, client = self._card_client(output)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch.object(bot, "AsyncGroq", return_value=client):
                copy_text, tone = await bot.generate_copy("nota")

        self.assertEqual(copy_text, "Gancho 🎬\nLínea.\n\n#ciudadrodrigoenvivo #cine")
        self.assertEqual(tone, "festivo")

    @staticmethod
    def _card_client(*outputs: dict) -> tuple[AsyncMock, SimpleNamespace]:
        create = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=json.dumps(o, ensure_ascii=False))
                        )
                    ]
                )
                for o in outputs
            ]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return create, client

    async def test_long_card_title_is_retried_once_and_never_trimmed(self) -> None:
        note = "Premios, cine y música en la clausura del 15º FICCI-ON en el Teatro Nuevo."
        base = {
            "subtitulo": "Premios, cine y música en la clausura del 15º FICCI-ON",
            "categoria": "CULTURA",
            "lugar": None,
        }
        long_title = {
            **base,
            "titular": "Premios, cine y música en la gran clausura del 15º FICCI-ON de Ciudad Rodrigo",
            "resaltado": "FICCI-ON",
        }
        short_title = {**base, "titular": "Premios y música cierran el 15º FICCI-ON", "resaltado": "FICCI-ON"}

        create, client = self._card_client(long_title, short_title)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch.object(bot, "AsyncGroq", return_value=client):
                metadata = await bot.generate_card_metadata(note)

        self.assertEqual(create.await_count, 2)
        self.assertEqual(metadata["titular"], short_title["titular"])

        # Si el reintento sigue largo pero cabe en 3 líneas, se usa entero.
        still_long = {**long_title, "titular": "Premios, cine y música para la clausura del 15º FICCI-ON en Ciudad Rodrigo"}
        create, client = self._card_client(long_title, still_long)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch.object(bot, "AsyncGroq", return_value=client):
                metadata = await bot.generate_card_metadata(note)

        self.assertEqual(create.await_count, 2)
        self.assertEqual(metadata["titular"], still_long["titular"])
        self.assertTrue(metadata["titular"].startswith("Premios"))
