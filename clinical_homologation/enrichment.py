from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Mapping

import pandas as pd

from .normalization import normalize_text


VADEMECUM_COLUMNS = {
    "marca": ["marca", "nombre comercial", "producto", "nombre_producto", "descripcion"],
    "principio_activo": ["principio activo", "componente", "droga", "activo"],
    "concentracion": ["concentracion", "dosis"],
    "forma": ["forma", "forma farmaceutica"],
    "laboratorio": ["laboratorio", "titular", "empresa"],
    "registro_sanitario": ["registro sanitario", "registro", "isp"],
}


def find_column(columns: list[str], aliases: list[str]) -> str | None:
    normalized = {normalize_text(column).replace("_", " "): column for column in columns}
    for alias in aliases:
        key = normalize_text(alias).replace("_", " ")
        if key in normalized:
            return normalized[key]
    return None


def read_table(path_or_file) -> pd.DataFrame:
    name = str(getattr(path_or_file, "name", path_or_file)).lower()
    if name.endswith(".csv"):
        return pd.read_csv(path_or_file, dtype=str, keep_default_na=False)
    return pd.read_excel(path_or_file, dtype=str, keep_default_na=False)


def load_vademecum(path_or_file, mapping: Mapping[str, str] | None = None) -> pd.DataFrame:
    raw = read_table(path_or_file)
    mapping = dict(mapping or {})
    output = pd.DataFrame()
    for target, aliases in VADEMECUM_COLUMNS.items():
        source = mapping.get(target) or find_column(list(raw.columns), aliases)
        output[target] = raw[source].fillna("").astype(str) if source in raw.columns else ""
    output["marca_normalizada"] = output["marca"].map(normalize_text)
    output["principio_activo_normalizado"] = output["principio_activo"].map(normalize_text)
    output["concentracion_normalizada"] = output["concentracion"].map(normalize_text)
    output["forma_normalizada"] = output["forma"].map(normalize_text)
    output["fuente"] = "VADEMECUM_USUARIO"
    output["fecha_consulta"] = date.today().isoformat()
    return output.drop_duplicates()


def build_brand_dictionary(vademecum: pd.DataFrame | None) -> dict[str, list[dict[str, str]]]:
    if vademecum is None or vademecum.empty:
        return {}
    dictionary: dict[str, list[dict[str, str]]] = {}
    for _, row in vademecum.iterrows():
        brand = normalize_text(row.get("marca", ""))
        if not brand:
            continue
        dictionary.setdefault(brand, []).append({key: str(value) for key, value in row.items()})
    return dictionary


def dictionary_sheet(vademecum: pd.DataFrame | None) -> pd.DataFrame:
    columns = ["marca", "principio_activo", "concentracion", "forma", "laboratorio", "registro_sanitario", "fuente", "fecha_consulta"]
    if vademecum is None or vademecum.empty:
        return pd.DataFrame(columns=columns)
    return vademecum.reindex(columns=columns).drop_duplicates()


def save_uploaded(fileobj, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / Path(fileobj.filename).name
    with target.open("wb") as fh:
        fh.write(fileobj.file.read())
    return target
