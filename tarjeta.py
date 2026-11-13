"""Generación local de tarjetas de noticias para Instagram."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from io import BytesIO
import math
import re
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps


ANCHO = 1080
ALTO = 1350
NARANJA = "#FF7A1A"
BLANCO = "#FFFFFF"
USUARIO = "@ciudadrodrigoenvivo"

CATEGORIAS = {
    "MEDIO AMBIENTE": {"color": "#2E9E5B", "icono": "leaf"},
    "OBRAS": {"color": "#D88927", "icono": "hammer"},
    "CULTURA": {"color": "#9256A8", "icono": "drama"},
    "DEPORTES": {"color": "#347DB8", "icono": "trophy"},
    "AVISO": {"color": "#D84F4B", "icono": "alert-triangle"},
    "AYUNTAMIENTO": {"color": "#526D9A", "icono": "landmark"},
    "SOCIEDAD": {"color": "#B36B45", "icono": "users-round"},
}

_RAIZ = Path(__file__).resolve().parent
_CANDIDATAS_ASSETS = (
    _RAIZ / "assets" / "tarjeta-noticia",
    _RAIZ / "assets" / "tarjeta-noticia" / "tarjeta-noticia",
)
_DIR_FUENTES = next(
    (ruta / "fuentes" for ruta in _CANDIDATAS_ASSETS if (ruta / "fuentes").is_dir()),
    _CANDIDATAS_ASSETS[0] / "fuentes",
)
_ARCHIVOS_FUENTE = {
    500: "montserrat-latin-500-normal.woff2",
    700: "montserrat-latin-700-normal.woff2",
    800: "montserrat-latin-800-normal.woff2",
}


@lru_cache(maxsize=24)
def _fuente(peso: int, tamano: int) -> ImageFont.FreeTypeFont:
    try:
        archivo = _DIR_FUENTES / _ARCHIVOS_FUENTE[peso]
    except KeyError as exc:
        raise ValueError(f"Peso de fuente Montserrat no soportado: {peso}") from exc
    if not archivo.is_file():
        raise FileNotFoundError(f"No se encuentra la fuente Montserrat: {archivo}")
    return ImageFont.truetype(str(archivo), tamano)


def _rgb(color: str) -> tuple[int, int, int]:
    valor = color.removeprefix("#")
    return tuple(int(valor[i : i + 2], 16) for i in (0, 2, 4))


def _interpolar_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _fondo_radial() -> Image.Image:
    escala = 4
    ancho, alto = ANCHO // escala, ALTO // escala
    cx, cy = ancho * 0.8, alto * 0.15
    radio = max(
        math.hypot(x - cx, y - cy)
        for x, y in ((0, 0), (ancho - 1, 0), (0, alto - 1), (ancho - 1, alto - 1))
    )
    centro, medio, borde = _rgb("#3A2A22"), _rgb("#1B1512"), _rgb("#120E0C")
    pixeles = []
    for y in range(alto):
        for x in range(ancho):
            t = math.hypot(x - cx, y - cy) / radio
            if t <= 0.55:
                pixeles.append(_interpolar_color(centro, medio, t / 0.55))
            else:
                pixeles.append(_interpolar_color(medio, borde, (t - 0.55) / 0.45))
    fondo = Image.new("RGB", (ancho, alto))
    fondo.putdata(pixeles)
    return fondo.resize((ANCHO, ALTO), Image.Resampling.BICUBIC)


def _alpha_degradado(y: int) -> int:
    paradas = ((0.0, 0.55), (0.22, 0.0), (0.38, 0.0), (0.72, 0.82), (1.0, 0.92))
    t = y / (ALTO - 1)
    for (y0, a0), (y1, a1) in zip(paradas, paradas[1:]):
        if t <= y1:
            return round(255 * (a0 + (a1 - a0) * (t - y0) / (y1 - y0)))
    return round(255 * paradas[-1][1])


def _aplicar_degradado(fondo: Image.Image) -> Image.Image:
    capa = Image.new("RGBA", fondo.size)
    dibujar = ImageDraw.Draw(capa)
    for y in range(ALTO):
        dibujar.line((0, y, ANCHO, y), fill=(0, 0, 0, _alpha_degradado(y)))
    return Image.alpha_composite(fondo.convert("RGBA"), capa).convert("RGB")


def _curva_bezier(puntos: tuple[tuple[float, float], ...], pasos: int = 20) -> list[tuple[float, float]]:
    resultado = []
    for i in range(pasos + 1):
        t = i / pasos
        u = 1 - t
        x = (
            u**3 * puntos[0][0]
            + 3 * u**2 * t * puntos[1][0]
            + 3 * u * t**2 * puntos[2][0]
            + t**3 * puntos[3][0]
        )
        y = (
            u**3 * puntos[0][1]
            + 3 * u**2 * t * puntos[1][1]
            + 3 * u * t**2 * puntos[2][1]
            + t**3 * puntos[3][1]
        )
        resultado.append((x, y))
    return resultado


def _dibujar_icono(
    dibujo: ImageDraw.ImageDraw,
    nombre: str,
    x: float,
    y: float,
    tamano: float,
    color: str | tuple[int, int, int, int],
) -> None:
    escala = tamano / 24
    trazo = max(1, round(2 * escala))

    def p(punto: tuple[float, float]) -> tuple[float, float]:
        return x + punto[0] * escala, y + punto[1] * escala

    def linea(puntos: list[tuple[float, float]], ancho: int = trazo) -> None:
        dibujo.line([p(punto) for punto in puntos], fill=color, width=ancho, joint="curve")

    if nombre == "leaf":
        contorno = (
            _curva_bezier(((20, 3), (10, 3), (4, 9), (4, 17)), 24)
            + [(4.3, 20)]
            + _curva_bezier(((4.3, 20), (4.8, 19.8), (5.2, 19.7), (5.7, 19.6)), 4)[1:]
            + _curva_bezier(((5.7, 19.6), (7, 14), (10, 10), (16, 8)), 16)[1:]
            + _curva_bezier(((16, 8), (11, 11), (8, 15), (7, 20)), 12)[1:]
            + _curva_bezier(((7, 20), (10, 20), (13, 19), (15.5, 16.5)), 12)[1:]
            + _curva_bezier(((15.5, 16.5), (19, 13), (20, 8), (20, 3)), 16)[1:]
        )
        dibujo.polygon([p(punto) for punto in contorno], fill=color)
    elif nombre == "hammer":
        linea([(4, 20), (14, 10)])
        linea([(12, 4), (16, 2), (22, 8), (20, 12), (12, 4)])
        linea([(3, 14), (9, 20)])
    elif nombre == "drama":
        linea([(3, 6), (7, 4), (11, 5), (12, 10), (10, 15), (6, 14), (3, 10), (3, 6)])
        linea([(12, 7), (16, 5), (21, 7), (21, 12), (18, 16), (14, 14), (12, 10), (12, 7)])
        linea([(5, 10), (7, 11)])
        linea([(15, 11), (18, 10)])
        linea([(5, 13), (9, 12)])
        linea([(15, 14), (19, 13)])
    elif nombre == "trophy":
        linea([(8, 3), (16, 3), (16, 9), (14, 13), (12, 14), (10, 13), (8, 9), (8, 3)])
        linea([(8, 5), (4, 5), (4, 8), (6, 11), (10, 12)])
        linea([(16, 5), (20, 5), (20, 8), (18, 11), (14, 12)])
        linea([(12, 14), (12, 19), (8, 19), (7, 22), (17, 22), (16, 19), (12, 19)])
    elif nombre == "alert-triangle":
        linea([(10, 4), (12, 2), (14, 4), (22, 19), (21, 21), (3, 21), (2, 19), (10, 4)])
        linea([(12, 8), (12, 14)])
        dibujo.ellipse((p((11, 17)), p((13, 19))), fill=color)
    elif nombre == "landmark":
        linea([(2, 8), (12, 3), (22, 8), (2, 8)])
        linea([(4, 21), (20, 21)])
        linea([(3, 23), (21, 23)])
        for columna in (6, 12, 18):
            linea([(columna, 9), (columna, 20)])
    elif nombre == "users-round":
        dibujo.ellipse((p((9, 3)), p((15, 9))), outline=color, width=trazo)
        dibujo.ellipse((p((2, 6)), p((7, 11))), outline=color, width=trazo)
        dibujo.ellipse((p((17, 6)), p((22, 11))), outline=color, width=trazo)
        linea([(5, 21), (5, 18), (7, 14), (10, 12), (14, 12), (17, 14), (19, 18), (19, 21)])
        linea([(2, 19), (2, 16), (4, 13), (7, 12)])
        linea([(22, 19), (22, 16), (20, 13), (17, 12)])
    else:
        raise ValueError(f"Icono de categoría no soportado: {nombre}")


def _icono_grande(fondo: Image.Image, icono: str, color: str) -> Image.Image:
    capa = Image.new("RGBA", fondo.size)
    icono_capa = Image.new("RGBA", (560, 560))
    _dibujar_icono(ImageDraw.Draw(icono_capa), icono, 0, 0, 560, (*_rgb(color), 255))
    icono_capa.putalpha(icono_capa.getchannel("A").point(lambda valor: round(valor * 0.13)))
    capa.alpha_composite(icono_capa, (560, 190))
    return Image.alpha_composite(fondo.convert("RGBA"), capa).convert("RGB")


def _abrir_foto(foto: str | Path | bytes | bytearray | BinaryIO | Image.Image) -> Image.Image:
    if isinstance(foto, Image.Image):
        return foto.convert("RGB").copy()
    if isinstance(foto, (bytes, bytearray)):
        origen = BytesIO(foto)
    elif isinstance(foto, (str, Path)):
        origen = foto
    elif hasattr(foto, "read"):
        origen = foto
    else:
        raise TypeError("foto debe ser una ruta, bytes, un archivo abierto o una imagen de Pillow")
    with Image.open(origen) as imagen:
        return imagen.convert("RGB").copy()


def _texto_con_espaciado(
    dibujo: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    texto: str,
    fuente: ImageFont.FreeTypeFont,
    color: str | tuple[int, int, int, int],
    espaciado: float,
    ancla: str = "lt",
) -> float:
    x, y = xy
    for caracter in texto:
        dibujo.text((x, y), caracter, font=fuente, fill=color, anchor=ancla)
        x += fuente.getlength(caracter) + espaciado
    return x - xy[0]


def _envolver(texto: str, fuente: ImageFont.FreeTypeFont, ancho: float) -> list[tuple[int, int]]:
    palabras = list(re.finditer(r"\S+", texto))
    if not palabras:
        return [(0, 0)]
    lineas = []
    inicio = palabras[0].start()
    fin = inicio
    for palabra in palabras:
        candidato = texto[inicio : palabra.end()]
        if fin > inicio and fuente.getlength(candidato) > ancho:
            lineas.append((inicio, fin))
            inicio = palabra.start()
        fin = palabra.end()
    lineas.append((inicio, fin))
    return lineas


def _envolver_equilibrado(
    texto: str,
    fuente: ImageFont.FreeTypeFont,
    ancho: float,
    cantidad_lineas: int,
) -> list[tuple[int, int]] | None:
    palabras = list(re.finditer(r"\S+", texto))
    cantidad_palabras = len(palabras)
    if cantidad_palabras < cantidad_lineas:
        return None

    mejor = None
    mejor_puntuacion = None
    if cantidad_lineas == 2:
        cortes = ((corte,) for corte in range(1, cantidad_palabras))
    else:
        cortes = (
            (primero, segundo)
            for primero in range(1, cantidad_palabras - 1)
            for segundo in range(primero + 1, cantidad_palabras)
        )

    for cortes_linea in cortes:
        indices = (0, *cortes_linea, cantidad_palabras)
        lineas = [
            (palabras[indices[i]].start(), palabras[indices[i + 1] - 1].end())
            for i in range(cantidad_lineas)
        ]
        anchos = [fuente.getlength(texto[inicio:fin]) for inicio, fin in lineas]
        if any(anchura > ancho for anchura in anchos):
            continue

        palabras_por_linea = [
            indices[i + 1] - indices[i] for i in range(cantidad_lineas)
        ]
        media_ancho = sum(anchos) / cantidad_lineas
        puntuacion = (
            max(palabras_por_linea) - min(palabras_por_linea),
            sum((anchura - media_ancho) ** 2 for anchura in anchos),
            max(anchos),
            cortes_linea,
        )
        if mejor_puntuacion is None or puntuacion < mejor_puntuacion:
            mejor = lineas
            mejor_puntuacion = puntuacion
    return mejor


def _ajustar_titular(texto: str, ancho: int) -> tuple[ImageFont.FreeTypeFont, list[tuple[int, int]], int]:
    fuente_inicial = _fuente(800, 92)
    lineas_iniciales = _envolver(texto, fuente_inicial, ancho)
    if len(lineas_iniciales) <= 1:
        ascenso, descenso = fuente_inicial.getmetrics()
        return fuente_inicial, lineas_iniciales, round((ascenso + descenso) * 1.02)

    for tamano in range(92, 71, -2):
        fuente = _fuente(800, tamano)
        lineas = _envolver_equilibrado(texto, fuente, ancho, 2)
        if lineas:
            ascenso, descenso = fuente.getmetrics()
            return fuente, lineas, round((ascenso + descenso) * 1.02)

    for tamano in range(92, 45, -2):
        fuente = _fuente(800, tamano)
        lineas = _envolver_equilibrado(texto, fuente, ancho, 3)
        if lineas:
            ascenso, descenso = fuente.getmetrics()
            return fuente, lineas, round((ascenso + descenso) * 1.02)

    ascenso, descenso = fuente_inicial.getmetrics()
    return fuente_inicial, lineas_iniciales, round((ascenso + descenso) * 1.02)


def _dibujar_titular(
    imagen: Image.Image,
    texto: str,
    resaltado: str,
    fuente: ImageFont.FreeTypeFont,
    lineas: list[tuple[int, int]],
    x: int,
    y: float,
    interlineado: int,
    reforzar_sombra: bool = False,
) -> None:
    if resaltado:
        coincidencia = re.search(re.escape(resaltado), texto, re.IGNORECASE)
        rango = coincidencia.span() if coincidencia else None
    else:
        rango = None

    sombra = Image.new("RGBA", imagen.size)
    sombra_dibujar = ImageDraw.Draw(sombra)
    desplazamiento_sombra = 4
    opacidad_sombra = 200 if reforzar_sombra else 175
    ascenso, _ = fuente.getmetrics()
    for indice, (inicio, fin) in enumerate(lineas):
        linea = texto[inicio:fin]
        linea_base = y + 6 + ascenso + indice * interlineado
        sombra_dibujar.text(
            (x, linea_base + desplazamiento_sombra),
            linea,
            font=fuente,
            fill=(0, 0, 0, opacidad_sombra),
            anchor="ls",
        )
    sombra = sombra.filter(ImageFilter.GaussianBlur(10 if reforzar_sombra else 9))
    imagen.alpha_composite(sombra)
    dibujo = ImageDraw.Draw(imagen)
    for indice, (inicio, fin) in enumerate(lineas):
        cortes = [inicio]
        if rango and inicio < rango[0] < fin:
            cortes.append(rango[0])
        if rango and inicio < rango[1] < fin:
            cortes.append(rango[1])
        cortes.append(fin)
        cursor_x = x
        linea_base = y + 6 + ascenso + indice * interlineado
        for desde, hasta in zip(cortes, cortes[1:]):
            color = NARANJA if rango and desde < rango[1] and hasta > rango[0] else BLANCO
            fragmento = texto[desde:hasta]
            dibujo.text(
                (cursor_x, linea_base),
                fragmento,
                font=fuente,
                fill=color,
                anchor="ls",
            )
            cursor_x += fuente.getlength(fragmento)


def _envolver_subtitulo(texto: str, fuente: ImageFont.FreeTypeFont, ancho: int) -> list[str]:
    palabras = texto.split()
    if not palabras:
        return [""]
    lineas = []
    actual = palabras[0]
    for palabra in palabras[1:]:
        candidato = f"{actual} {palabra}"
        if fuente.getlength(candidato) <= ancho:
            actual = candidato
        else:
            lineas.append(actual)
            actual = palabra
    lineas.append(actual)
    return lineas


def _fecha_es(fecha: date) -> str:
    meses = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
    return f"{fecha.day} {meses[fecha.month - 1]} {fecha.year}"


def generar_tarjeta(
    titular: str,
    resaltado: str,
    subtitulo: str,
    categoria: str,
    foto: str | Path | bytes | bytearray | BinaryIO | Image.Image | None = None,
    pie: str | None = None,
) -> bytes:
    """Genera una tarjeta PNG de 1080 × 1350 px."""
    if not all(isinstance(valor, str) for valor in (titular, resaltado, subtitulo, categoria)):
        raise TypeError("titular, resaltado, subtitulo y categoria deben ser cadenas")
    clave_categoria = categoria.strip().upper()
    try:
        datos_categoria = CATEGORIAS[clave_categoria]
    except KeyError as exc:
        raise ValueError(
            f"Categoría desconocida: {categoria!r}. Opciones: {', '.join(CATEGORIAS)}"
        ) from exc

    if foto is None:
        fondo = _fondo_radial()
    else:
        fondo = ImageOps.fit(
            _abrir_foto(foto),
            (ANCHO, ALTO),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    imagen = _aplicar_degradado(fondo).convert("RGBA")

    if foto is None:
        imagen = _icono_grande(imagen, datos_categoria["icono"], datos_categoria["color"]).convert("RGBA")

    dibujo = ImageDraw.Draw(imagen)
    halo = Image.new("RGBA", imagen.size)
    ImageDraw.Draw(halo).ellipse((58, 59, 88, 89), fill=(255, 122, 26, 64))
    imagen.alpha_composite(halo)
    dibujo = ImageDraw.Draw(imagen)
    dibujo.ellipse((64, 65, 82, 83), fill=NARANJA)
    _texto_con_espaciado(
        dibujo,
        (96, 62),
        "CIUDAD RODRIGO EN VIVO",
        _fuente(800, 30),
        BLANCO,
        1,
    )

    fuente_chip = _fuente(800, 28)
    etiqueta = clave_categoria
    ancho_etiqueta = sum(fuente_chip.getlength(letra) for letra in etiqueta) + 2 * len(etiqueta)
    ancho_chip = round(24 + 30 + 12 + ancho_etiqueta + 24)

    pie_texto = pie if pie is not None else ("Imagen de archivo" if foto is not None else _fecha_es(date.today()))
    fuente_pie = _fuente(500, 22)
    alto_pie = 27
    y_linea_pie = ALTO - 80 - (2 + 22 + alto_pie)
    y_pie = y_linea_pie + 24

    fuente_subtitulo = _fuente(500, 34)
    lineas_subtitulo = _envolver_subtitulo(subtitulo, fuente_subtitulo, ANCHO - 128)
    interlineado_subtitulo = 44
    y_subtitulo = y_linea_pie - 34 - len(lineas_subtitulo) * interlineado_subtitulo

    fuente_titular, lineas_titular, interlineado_titular = _ajustar_titular(titular, ANCHO - 128)
    y_titular = y_subtitulo - 34 - len(lineas_titular) * interlineado_titular
    y_chip = y_titular - 28 - 58

    dibujo.rounded_rectangle(
        (64, y_chip, 64 + ancho_chip, y_chip + 58),
        radius=29,
        fill=datos_categoria["color"],
    )
    icono_chip = Image.new("RGBA", (30, 30))
    _dibujar_icono(ImageDraw.Draw(icono_chip), datos_categoria["icono"], 0, 0, 30, BLANCO)
    imagen.alpha_composite(icono_chip, (88, round(y_chip + 14)))
    _texto_con_espaciado(
        ImageDraw.Draw(imagen),
        (130, y_chip + 18),
        etiqueta,
        fuente_chip,
        BLANCO,
        2,
    )

    _dibujar_titular(
        imagen,
        titular,
        resaltado,
        fuente_titular,
        lineas_titular,
        64,
        y_titular,
        interlineado_titular,
        reforzar_sombra=foto is not None,
    )
    dibujo = ImageDraw.Draw(imagen)
    for indice, linea in enumerate(lineas_subtitulo):
        dibujo.text(
            (64, y_subtitulo + 6 + indice * interlineado_subtitulo),
            linea,
            font=fuente_subtitulo,
            fill="#EBEBEB",
            anchor="lt",
        )

    dibujo.line((64, y_linea_pie, ANCHO - 64, y_linea_pie), fill="#404040", width=2)
    dibujo.text((64, y_pie + 6), pie_texto, font=fuente_pie, fill="#BFBFBF", anchor="lt")
    ancho_usuario = fuente_pie.getlength(USUARIO)
    dibujo.text(
        (ANCHO - 64 - ancho_usuario, y_pie + 6),
        USUARIO,
        font=fuente_pie,
        fill="#BFBFBF",
        anchor="lt",
    )

    salida = BytesIO()
    imagen.convert("RGB").save(salida, format="PNG", optimize=True)
    return salida.getvalue()


__all__ = ["CATEGORIAS", "generar_tarjeta"]
