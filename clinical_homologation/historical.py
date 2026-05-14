from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .insumos import extract_insumo_attributes, insumo_dict_rows, insumo_key
from .normalization import normalize_text


DESCRIPTION_ALIASES = ["DESCRIPCION_HIS", "DESCRIPCION PRODUCTO", "DESCRIPCION PRODUCTO MEDIC", "DESCRIPCION MEDIC", "NOMBRE CONSIGNACION"]
LOCAL_CODE_ALIASES = ["CODIGO_HIS", "CODIGO MEDIC", "CODIGO MEDIC", "CODIGO TEMUCO", "CODIGO MEDISYN", "CODIGO MEDIC"]
GROUP_CODE_ALIASES = ["CODIGO_SAP", "CODIGO SAP", "LLAVE_SAP"]
GROUP_DESC_ALIASES = ["DESCRIPCION_MATERIAL_SAP", "DESCRIPCION MATERIAL SAP"]


def load_historical_learning(paths: Iterable[str | Path] | None, max_rows_per_sheet: int = 500) -> dict[str, pd.DataFrame]:
    pattern_rows: list[dict[str, object]] = []
    dictionary_rows = insumo_dict_rows()
    conflict_rows: list[dict[str, object]] = []
    if not paths:
        return {
            "PATRONES_HISTORICOS_APRENDIDOS": pd.DataFrame(columns=historical_pattern_columns()),
            "DICCIONARIO_INSUMOS": pd.DataFrame(dictionary_rows),
            "CONFLICTOS_HISTORICOS": pd.DataFrame(columns=historical_conflict_columns()),
        }
    for path_value in paths:
        path = Path(path_value)
        if not path.exists():
            continue
        try:
            xls = pd.ExcelFile(path)
        except Exception:
            continue
        for sheet_name in xls.sheet_names:
            try:
                frame = pd.read_excel(path, sheet_name=sheet_name, dtype=str, keep_default_na=False, nrows=max_rows_per_sheet)
            except Exception:
                continue
            if frame.empty:
                continue
            columns = list(frame.columns)
            local_desc_col = find_column(columns, DESCRIPTION_ALIASES)
            group_desc_col = find_column(columns, GROUP_DESC_ALIASES)
            group_code_col = find_column(columns, GROUP_CODE_ALIASES)
            local_code_col = find_column(columns, LOCAL_CODE_ALIASES)
            if not local_desc_col and not group_desc_col:
                continue
            group_map: dict[str, list[dict[str, object]]] = {}
            for _, row in frame.iterrows():
                local_desc = str(row.get(local_desc_col, "") if local_desc_col else "")
                group_desc = str(row.get(group_desc_col, "") if group_desc_col else "")
                desc = local_desc or group_desc
                if not normalize_text(desc):
                    continue
                group = str(row.get(group_code_col, "") if group_code_col else group_desc or normalize_text(desc))
                attr = extract_insumo_attributes(desc)
                pattern = summarize_pattern(desc, attr)
                pattern_row = {
                    "archivo_origen": path.name,
                    "codigo_historico_original": str(row.get(local_code_col, "") if local_code_col else ""),
                    "descripcion_historica_original": desc,
                    "descripcion_historica_normalizada": normalize_text(desc),
                    "grupo_historico_detectado": group,
                    "descripcion_madre_historica": group_desc,
                    "familia_detectada": attr.subfamilia,
                    "atributos_extraidos": str({k: v for k, v in attr.__dict__.items() if v}),
                    "patron_aprendido": pattern,
                    "usar_como_regla": "AUXILIAR",
                    "nivel_confianza": "MEDIA" if attr.subfamilia != "OTROS / REQUIERE REVISION" else "BAJA",
                    "observacion": "Referencia historica auxiliar; no se hereda codigo ni vigencia.",
                }
                pattern_rows.append(pattern_row)
                group_map.setdefault(group, []).append({"desc": desc, "attr": attr, "row": pattern_row})
                dictionary_rows.extend(dictionary_from_description(desc, path.name))
            conflict_rows.extend(detect_historical_conflicts(path.name, group_map))
    return {
        "PATRONES_HISTORICOS_APRENDIDOS": pd.DataFrame(pattern_rows, columns=historical_pattern_columns()).drop_duplicates(),
        "DICCIONARIO_INSUMOS": pd.DataFrame(dictionary_rows, columns=dictionary_columns()).drop_duplicates(),
        "CONFLICTOS_HISTORICOS": pd.DataFrame(conflict_rows, columns=historical_conflict_columns()).drop_duplicates(),
    }


def find_column(columns: list[str], aliases: list[str]) -> str | None:
    normalized = {normalize_text(column).replace("_", " "): column for column in columns}
    for alias in aliases:
        key = normalize_text(alias).replace("_", " ")
        if key in normalized:
            return normalized[key]
    return None


def summarize_pattern(desc: str, attr) -> str:
    parts = [attr.subfamilia, attr.capacidad, attr.calibre, attr.medida, attr.material, attr.conexion, attr.esterilidad]
    summary = " ".join(part for part in parts if part)
    return summary or normalize_text(desc)


def dictionary_from_description(desc: str, source: str) -> list[dict[str, str]]:
    text = normalize_text(desc)
    rows = []
    learned = {
        "10CC": "10ML",
        "20CC": "20ML",
        "50CC": "50ML",
        "LL": "LUER LOCK",
        "LS": "LUER SLIP",
        "FCO AMP": "FRASCO AMPOLLA",
        "SOL INY": "SOLUCION INYECTABLE",
        "COMP": "COMPRIMIDO",
    }
    for original, target in learned.items():
        if original in text:
            rows.append({
                "termino_original": original,
                "termino_normalizado": target,
                "tipo_diccionario": "equivalencia_textual_historica",
                "fuente": source,
                "nivel_confianza": "MEDIA",
                "observacion": "Detectado en descripcion historica; usar como auxiliar.",
            })
    return rows


def detect_historical_conflicts(source: str, group_map: dict[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    conflicts = []
    critical_fields = ["conexion", "capacidad", "calibre", "medida", "material", "esterilidad", "vias", "con_sin_aguja", "con_sin_filtro"]
    for group, items in group_map.items():
        if len(items) < 2:
            continue
        for field in critical_fields:
            values = {getattr(item["attr"], field) for item in items if getattr(item["attr"], field)}
            if len(values) <= 1:
                continue
            conflicts.append({
                "archivo_origen": source,
                "grupo_historico": group,
                "descripcion_1": str(items[0]["desc"]),
                "descripcion_2": str(items[1]["desc"]),
                "regla_conflictiva": field,
                "motivo_conflicto": f"Historico agrupa registros con {field} distinto: {', '.join(sorted(values))}.",
                "recomendacion": "CONFLICTO_HISTORICO_REGLA_TECNICA; enviar a revision tecnica.",
            })
    return conflicts


def historical_pattern_columns() -> list[str]:
    return [
        "archivo_origen",
        "codigo_historico_original",
        "descripcion_historica_original",
        "descripcion_historica_normalizada",
        "grupo_historico_detectado",
        "descripcion_madre_historica",
        "familia_detectada",
        "atributos_extraidos",
        "patron_aprendido",
        "usar_como_regla",
        "nivel_confianza",
        "observacion",
    ]


def dictionary_columns() -> list[str]:
    return ["termino_original", "termino_normalizado", "tipo_diccionario", "fuente", "nivel_confianza", "observacion"]


def historical_conflict_columns() -> list[str]:
    return ["archivo_origen", "grupo_historico", "descripcion_1", "descripcion_2", "regla_conflictiva", "motivo_conflicto", "recomendacion"]
