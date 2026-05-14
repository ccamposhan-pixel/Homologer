from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from .normalization import compact_amount, contains_any, normalize_text


SUBFAMILY_KEYWORDS = {
    "JERINGA": ["JERINGA", "JGA", "SYRINGE"],
    "AGUJA": ["AGUJA", "AG", "NEEDLE"],
    "CATETER": ["CATETER", "CATHETER", "CAT"],
    "BAJADA / EQUIPO INFUSION": ["BAJADA", "EQUIPO INFUSION", "FLEBO", "MICROGOTERO", "MACROGOTERO"],
    "LLAVE TRES PASOS": ["LLAVE 3", "LLAVE TRES", "LLAVE DE TRES"],
    "GUANTE": ["GUANTE", "GLOVE"],
    "MASCARILLA": ["MASCARILLA", "RESPIRADOR", "N95", "KN95"],
    "APOSITO": ["APOSITO", "DRESSING", "TRANSPARENTE"],
    "GASA": ["GASA"],
    "VENDA": ["VENDA"],
    "SUTURA": ["SUTURA", "VICRYL", "PROLENE", "SEDA", "MONOCRYL", "NYLON"],
    "SONDA": ["SONDA", "FOLEY"],
    "TUBO": ["TUBO"],
    "CONECTOR": ["CONECTOR", "ADAPTADOR", "LUER"],
    "BOLSA": ["BOLSA"],
    "DRENAJE": ["DRENAJE", "DREN"],
    "ELECTRODO": ["ELECTRODO"],
    "CAMPO / ROPA CLINICA": ["CAMPO", "BATA", "SABANA", "ROPA"],
    "MATERIAL ESTERILIZACION": ["ESTERILIZACION", "AUTOCLAVE", "INDICADOR BIOLOGICO"],
    "MATERIAL LABORATORIO": ["PCR", "TUBO EDTA", "REACTIVO", "TEST", "KIT"],
}

TEXT_EQUIVALENCES = {
    "10CC": "10ML",
    "20CC": "20ML",
    "50CC": "50ML",
    "CC": "ML",
    "LL": "LUER LOCK",
    "L/L": "LUER LOCK",
    "C/LOCK": "LUER LOCK",
    "LOCK": "LUER LOCK",
    "LS": "LUER SLIP",
    "L/S": "LUER SLIP",
    "C/A": "CON AGUJA",
    "S/A": "SIN AGUJA",
    "EST": "ESTERIL",
    "ESTERILIZADO": "ESTERIL",
    "S/P": "SIN POLVO",
    "SIN PVO": "SIN POLVO",
    "C/P": "CON POLVO",
}


@dataclass
class InsumoAttributes:
    subfamilia: str = ""
    medida: str = ""
    capacidad: str = ""
    calibre: str = ""
    largo: str = ""
    ancho: str = ""
    material: str = ""
    esterilidad: str = ""
    conexion: str = ""
    tipo_punta: str = ""
    vias: str = ""
    con_sin_aguja: str = ""
    con_sin_filtro: str = ""
    marca: str = ""
    modelo: str = ""
    proveedor: str = ""
    cantidad_por_envase: str = ""
    presentacion_comercial: str = ""
    requiere_validacion_clinica: bool = False
    observaciones: str = ""


def normalize_insumo_text(text: object) -> str:
    normalized = normalize_text(text)
    normalized = re.sub(r"\b(\d+)\s*CC\b", r"\1 ML", normalized)
    normalized = re.sub(r"\b(\d+)\s*ML\b", r"\1ML", normalized)
    normalized = re.sub(r"\b(\d+)\s*FR\b", r"\1FR", normalized)
    normalized = re.sub(r"\b(\d+)\s*G\b", r"\1G", normalized)
    for source, target in sorted(TEXT_EQUIVALENCES.items(), key=lambda item: len(item[0]), reverse=True):
        normalized = re.sub(rf"\b{re.escape(source)}\b", target, normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def classify_insumo_subfamily(text: str) -> str:
    normalized = normalize_insumo_text(text)
    for subfamily, keywords in SUBFAMILY_KEYWORDS.items():
        if contains_any(normalized, keywords):
            return subfamily
    return "OTROS / REQUIERE REVISION"


def is_insumo_family(family: str, description: str) -> bool:
    if family in {"INSUMO MEDICO", "DISPOSITIVO MEDICO", "MATERIAL QUIRURGICO", "SUTURA", "REACTIVO / LABORATORIO"}:
        return True
    return classify_insumo_subfamily(description) != "OTROS / REQUIERE REVISION"


def extract_insumo_attributes(description: object, marca: str = "", proveedor: str = "") -> InsumoAttributes:
    text = normalize_insumo_text(description)
    attr = InsumoAttributes(subfamilia=classify_insumo_subfamily(text), marca=marca, proveedor=proveedor)
    capacity = re.search(r"\b(0?\.\d+|\d+(?:\.\d+)?)\s*(ML|L)\b", text)
    if capacity:
        attr.capacidad = compact_amount(f"{capacity.group(1)} {capacity.group(2)}")
    gauge = re.search(r"\b(\d{1,2})\s*G\b", text)
    if gauge:
        attr.calibre = f"{gauge.group(1)}G"
    french = re.search(r"\b(\d{1,2})\s*FR\b", text)
    if french and not attr.calibre:
        attr.calibre = f"{french.group(1)}FR"
    size = re.search(r"\b(\d+(?:\.\d+)?)\s*[Xx]\s*(\d+(?:\.\d+)?)\s*(CM|MM|M)\b", text)
    if size:
        attr.medida = f"{size.group(1)}X{size.group(2)}{size.group(3)}"
        attr.largo = f"{size.group(1)}{size.group(3)}"
        attr.ancho = f"{size.group(2)}{size.group(3)}"
    needle_len = re.search(r"\b(\d+(?:/\d+)?(?:\.\d+)?)\s*(?:\"|PULG|PULGADA|IN)\b", text)
    if needle_len:
        attr.largo = needle_len.group(1)
    talla = re.search(r"\bT(?:ALLA)?\s*(XS|S|M|L|XL|XXL|CH|M|G|P)\b", text)
    if talla:
        attr.medida = f"TALLA {talla.group(1)}"
    for material in ["NITRILO", "LATEX", "VINILO", "SILICONA", "PVC", "POLIURETANO", "ALGODON"]:
        if contains_any(text, [material]):
            attr.material = material
            break
    if contains_any(text, ["NO ESTERIL", "NO ESTERIL"]):
        attr.esterilidad = "NO ESTERIL"
    elif contains_any(text, ["ESTERIL"]):
        attr.esterilidad = "ESTERIL"
    if contains_any(text, ["LUER LOCK"]):
        attr.conexion = "LUER LOCK"
    elif contains_any(text, ["LUER SLIP"]):
        attr.conexion = "LUER SLIP"
    if contains_any(text, ["CON AGUJA"]):
        attr.con_sin_aguja = "CON AGUJA"
    elif contains_any(text, ["SIN AGUJA"]):
        attr.con_sin_aguja = "SIN AGUJA"
    if contains_any(text, ["CON FILTRO"]):
        attr.con_sin_filtro = "CON FILTRO"
    elif contains_any(text, ["SIN FILTRO"]):
        attr.con_sin_filtro = "SIN FILTRO"
    vias = re.search(r"\b([1234])\s*VIAS?\b", text)
    if vias:
        attr.vias = f"{vias.group(1)} VIAS"
    box = re.search(r"\b(?:CAJA|CJ|BOLSA|PACK|BLISTER)\s*(?:X)?\s*(\d+)\b", text)
    if box:
        attr.cantidad_por_envase = box.group(1)
        attr.presentacion_comercial = f"CAJA X {box.group(1)}" if "CAJA" in text or "CJ" in text else f"PACK X {box.group(1)}"
    if contains_any(text, ["SENSOR", "COMPATIBLE", "EQUIPO", "SET", "KIT", "IMPLANTE", "PROTESIS"]):
        attr.requiere_validacion_clinica = True
    attr.observaciones = critical_missing_note(attr)
    return attr


def critical_missing_note(attr: InsumoAttributes) -> str:
    missing = []
    if attr.subfamilia == "JERINGA":
        if not attr.capacidad:
            missing.append("capacidad")
        if not attr.conexion:
            missing.append("conexion")
    elif attr.subfamilia == "AGUJA":
        if not attr.calibre:
            missing.append("calibre")
        if not attr.largo:
            missing.append("largo")
    elif attr.subfamilia == "GUANTE":
        if not attr.material:
            missing.append("material")
        if not attr.medida:
            missing.append("talla")
    elif attr.subfamilia in {"APOSITO", "GASA"} and not attr.medida:
        missing.append("medida")
    if missing:
        return "Atributos criticos incompletos: " + ", ".join(missing)
    return ""


def insumo_key(attr: InsumoAttributes) -> tuple[str, ...]:
    return (
        attr.subfamilia,
        attr.medida,
        attr.capacidad,
        attr.calibre,
        attr.largo,
        attr.ancho,
        attr.material,
        attr.esterilidad,
        attr.conexion,
        attr.tipo_punta,
        attr.vias,
        attr.con_sin_aguja,
        attr.con_sin_filtro,
    )


def insumo_description(attr: InsumoAttributes) -> str:
    parts = [
        attr.subfamilia,
        attr.capacidad,
        attr.calibre,
        attr.medida,
        attr.largo if attr.subfamilia == "AGUJA" and attr.largo else "",
        attr.material,
        attr.conexion,
        attr.vias,
        attr.con_sin_aguja,
        attr.con_sin_filtro,
        attr.esterilidad,
    ]
    return " ".join(dict.fromkeys(part for part in parts if part))


def insumo_dict_rows() -> list[dict[str, str]]:
    rows = []
    for source, target in TEXT_EQUIVALENCES.items():
        rows.append({
            "termino_original": source,
            "termino_normalizado": target,
            "tipo_diccionario": "equivalencia_textual",
            "fuente": "REGLA_BASE_INSUMOS",
            "nivel_confianza": "ALTA",
            "observacion": "Equivalencia operacional para normalizacion de insumos.",
        })
    for subfamily, terms in SUBFAMILY_KEYWORDS.items():
        for term in terms:
            rows.append({
                "termino_original": term,
                "termino_normalizado": subfamily,
                "tipo_diccionario": "subfamilia",
                "fuente": "REGLA_BASE_INSUMOS",
                "nivel_confianza": "ALTA",
                "observacion": "Termino usado para clasificacion dimensional/funcional.",
            })
    return rows


def as_row(attr: InsumoAttributes) -> dict[str, object]:
    return asdict(attr)
