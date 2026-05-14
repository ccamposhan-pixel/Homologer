from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from .rules import ALERT_TERMS, FAMILY_KEYWORDS, FORM_SYNONYMS, ROUTE_SYNONYMS, UNIT_SYNONYMS


def strip_accents(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", value) if unicodedata.category(ch) != "Mn")


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = strip_accents(str(value)).upper()
    text = text.replace("×", " X ").replace("*", " X ")
    text = re.sub(r"(\d)([A-Z%])", r"\1 \2", text)
    text = re.sub(r"([A-Z])(\d)", r"\1 \2", text)
    text = re.sub(r"[^A-Z0-9%/.,+\- ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for source, target in sorted(UNIT_SYNONYMS.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(rf"\b{re.escape(source)}\b", target, text)
    text = re.sub(r"(\d+)\s*,\s*(\d+)", r"\1.\2", text)
    text = re.sub(r"\bFCO\.?\s*AMP\.?\b", "FRASCO AMPOLLA", text)
    text = re.sub(r"\bFRASCO\s*AMP\.?\b", "FRASCO AMPOLLA", text)
    text = re.sub(r"\bF\s*A\b", "FRASCO AMPOLLA", text)
    text = re.sub(r"\bSOL\.?\s*INY\.?\b", "SOLUCION INYECTABLE", text)
    text = re.sub(r"\bSOL\.?\b", "SOLUCION", text)
    text = re.sub(r"\bMG\s*/\s*ML\b", "MG/ML", text)
    text = re.sub(r"\bUI\s*/\s*ML\b", "UI/ML", text)
    text = re.sub(r"\s*/\s*", "/", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms if term)


def detect_family(text: str, provided_family: object = None, category: object = None) -> str:
    haystack = " ".join(part for part in [normalize_text(provided_family), normalize_text(category), text] if part)
    for family, keywords in FAMILY_KEYWORDS.items():
        if contains_any(haystack, keywords):
            return family
    if detect_form(text) or detect_concentration(text):
        return "MEDICAMENTO"
    return "NO CLASIFICABLE / REQUIERE REVISION"


def detect_form(text: str) -> str:
    if contains_any(text, ["FRASCO AMPOLLA", "SOLUCION INYECTABLE", "INYECTABLE"]):
        return "SOLUCION INYECTABLE"
    if contains_any(text, ["SOLUCION"]) and not contains_any(text, ["JARABE", "GOTAS", "ORAL"]):
        return "SOLUCION INYECTABLE"
    for canonical, synonyms in FORM_SYNONYMS.items():
        if contains_any(text, synonyms):
            return canonical
    return ""


def detect_route(text: str, form: str = "") -> str:
    for canonical, synonyms in ROUTE_SYNONYMS.items():
        if contains_any(text, synonyms):
            return canonical
    if form in {"COMPRIMIDO", "CAPSULA", "JARABE", "GOTAS", "SUSPENSION"}:
        return "ORAL"
    if form in {"CREMA", "GEL", "UNGUENTO"}:
        return "TOPICA"
    if form == "SOLUCION INYECTABLE":
        return "PARENTERAL"
    return ""


def detect_concentration(text: str) -> str:
    units = r"MG/ML|G/L|MCG/DOSIS|UI/ML|MG|G|MCG|UI|%"
    matches = re.findall(rf"\b(\d+(?:\.\d+)?)\s*({units})\b", text)
    if not matches:
        return ""
    return " + ".join(f"{float(number):g} {unit}" if "." in number else f"{number} {unit}" for number, unit in matches)


def compact_amount(value: str) -> str:
    return value.replace(" ", "")


def detect_dose_components(text: str) -> tuple[str, str, str]:
    units = r"MG|G|MCG|UI|%"
    volume_units = r"ML|L"
    ratio = re.search(rf"\b(\d+(?:\.\d+)?)\s*({units})/(\d+(?:\.\d+)?)\s*({volume_units})\b", text)
    if ratio:
        amount_number = f"{float(ratio.group(1)):g}" if "." in ratio.group(1) else ratio.group(1)
        volume_number = f"{float(ratio.group(3)):g}" if "." in ratio.group(3) else ratio.group(3)
        amount = f"{amount_number} {ratio.group(2)}"
        volume = f"{volume_number} {ratio.group(4)}"
        concentration_value = float(ratio.group(1)) / float(ratio.group(3))
        concentration = f"{concentration_value:g} {ratio.group(2)}/{ratio.group(4)}"
        return amount, concentration, volume

    amount = ""
    amount_match = re.search(rf"\b(\d+(?:\.\d+)?)\s*({units})\b", text)
    if amount_match:
        number = f"{float(amount_match.group(1)):g}" if "." in amount_match.group(1) else amount_match.group(1)
        amount = f"{number} {amount_match.group(2)}"

    concentration = ""
    per_volume = re.search(rf"\b(\d+(?:\.\d+)?)\s*({units})/({volume_units})\b", text)
    if per_volume:
        number = f"{float(per_volume.group(1)):g}" if "." in per_volume.group(1) else per_volume.group(1)
        concentration = f"{number} {per_volume.group(2)}/{per_volume.group(3)}"

    volume = ""
    volume_match = re.search(rf"\b(\d+(?:\.\d+)?)\s*({volume_units})\b", text)
    if volume_match:
        number = f"{float(volume_match.group(1)):g}" if "." in volume_match.group(1) else volume_match.group(1)
        volume = f"{number} {volume_match.group(2)}"
    return amount, concentration, volume


def detect_volume_size(text: str) -> str:
    matches = re.findall(r"\b(\d+(?:\.\d+)?)\s*(ML|L|MM|CM|M|FR|G)\b", text)
    filtered = []
    for number, unit in matches:
        if unit == "G" and re.search(rf"\b{re.escape(number)}\s*(MG|MCG|G/L)\b", text):
            continue
        filtered.append(f"{number} {unit}")
    return " + ".join(dict.fromkeys(filtered))


def detect_presentation(text: str) -> str:
    boxed = re.search(r"\b(?:CJ|CAJA|BLISTER|BLIST)\s*(?:X)?\s*(\d+)\b", text)
    if boxed:
        prefix = "BLISTER X" if re.search(r"\b(?:BLISTER|BLIST)\b", text) else "CAJA X"
        return f"{prefix} {boxed.group(1)}"
    x_only = re.search(r"\bX\s*(\d+)\b", text)
    if x_only:
        return f"CAJA X {x_only.group(1)}"
    if contains_any(text, ["FRASCO AMPOLLA", "FA", "VIAL"]):
        return "FRASCO AMPOLLA"
    if contains_any(text, ["AMPOLLA", "AMP"]):
        return "AMPOLLA"
    if contains_any(text, ["JERINGA PRELLENADA"]):
        return "JERINGA PRELLENADA"
    if contains_any(text, ["FRASCO", "FCO"]):
        return "FRASCO"
    if contains_any(text, ["BOLSA"]):
        return "BOLSA"
    return ""


def detect_alerts(text: str) -> list[str]:
    return [term for term in ALERT_TERMS if re.search(rf"\b{re.escape(term)}\b", text)]


def first_token_brand(text: str) -> str:
    tokens = text.split()
    if not tokens:
        return ""
    if tokens[0].isdigit():
        return ""
    return tokens[0]


def parenthetical_terms(raw_text: object) -> list[str]:
    raw = strip_accents(str(raw_text)).upper()
    return [normalize_text(term) for term in re.findall(r"\(([^)]+)\)", raw) if normalize_text(term)]
