from __future__ import annotations

import unittest

from PIL import Image, ImageChops

from tarjeta import ALTO, ANCHO, CATEGORIAS, _icono_grande, _normalizar_subtitulo


class TarjetaTests(unittest.TestCase):
    def test_normaliza_puntuacion_final_del_subtitulo(self) -> None:
        casos = (
            ("Retiran maleza y sedimentos", "Retiran maleza y sedimentos."),
            ("Retiran maleza y sedimentos.", "Retiran maleza y sedimentos."),
            ("Retiran maleza y sedimentos...", "Retiran maleza y sedimentos."),
            ("Retiran maleza y sedimentos!", "Retiran maleza y sedimentos."),
        )
        for entrada, esperado in casos:
            with self.subTest(entrada=entrada):
                self.assertEqual(_normalizar_subtitulo(entrada), esperado)

    def test_iconos_grandes_comparten_margen_derecho_y_no_se_recortan(self) -> None:
        fondo = Image.new("RGB", (ANCHO, ALTO), "#000000")
        bordes_derechos = []
        for categoria, datos in CATEGORIAS.items():
            with self.subTest(categoria=categoria):
                icono = _icono_grande(fondo, datos["icono"], datos["color"])
                limites = ImageChops.difference(fondo, icono).getbbox()
                self.assertIsNotNone(limites)
                assert limites is not None
                bordes_derechos.append(limites[2])

        self.assertLessEqual(max(bordes_derechos) - min(bordes_derechos), 2)
        self.assertGreaterEqual(min(bordes_derechos), ANCHO - 42)
        self.assertLessEqual(max(bordes_derechos), ANCHO - 40)


if __name__ == "__main__":
    unittest.main()
