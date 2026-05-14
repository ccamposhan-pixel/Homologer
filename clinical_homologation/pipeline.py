from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from .enrichment import build_brand_dictionary, dictionary_sheet, load_vademecum, read_table
from .external_sources import isp_consultation_note, secondary_vademecum_note
from .historical import load_historical_learning
from .insumos import (
    as_row as insumo_attr_row,
    extract_insumo_attributes,
    insumo_description,
    insumo_key,
    is_insumo_family,
)
from .matching import propose_homologations, top_counts
from .models import HomologationResult, ProductAttributes
from .normalization import (
    compact_amount,
    detect_alerts,
    detect_concentration,
    detect_dose_components,
    detect_family,
    detect_form,
    detect_presentation,
    detect_route,
    detect_volume_size,
    first_token_brand,
    normalize_text,
    parenthetical_terms,
)
from .rules import ALERT_TERMS, RULE_DESCRIPTIONS


DEFAULT_MAPPING = {
    "clinica": ["clinica", "clínica", "sucursal", "centro", "establecimiento"],
    "codigo_origen": ["codigo producto", "codigo", "cod", "sku", "codigo interno", "codigo_origen"],
    "descripcion_origen": ["descripcion producto", "descripcion", "producto", "glosa", "nombre producto", "nombre_producto", "nombre"],
    "proveedor": ["proveedor"],
    "marca": ["marca"],
    "laboratorio_titular": ["laboratorio", "titular"],
    "unidad_compra": ["unidad de compra", "unidad_compra", "u compra"],
    "unidad_consumo": ["unidad de consumo", "unidad_consumo", "u consumo"],
    "categoria": ["categoria", "rubro"],
    "familia": ["familia"],
    "registro_sanitario": ["registro sanitario", "registro", "isp"],
    "factor_conversion": ["factor", "factor conversion", "factor_conversion"],
    "precio": ["precio", "punit", "precio unitario", "valor"],
}


def _find_column(columns: list[str], aliases: list[str]) -> str | None:
    normalized = {normalize_text(column).replace("_", " "): column for column in columns}
    for alias in aliases:
        key = normalize_text(alias).replace("_", " ")
        if key in normalized:
            return normalized[key]
    # Fallback: partial match (e.g. "NOMBRE PRODUCTO" should match alias "NOMBRE")
    for alias in aliases:
        key = normalize_text(alias).replace("_", " ")
        if not key:
            continue
        candidates = [
            original
            for norm, original in normalized.items()
            if re.search(rf"\\b{re.escape(key)}\\b", norm)
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            # Prefer the shortest column name to reduce accidental over-matches.
            return sorted(candidates, key=lambda value: len(value))[0]
    return None


def _value(row: pd.Series, source: str | None) -> str:
    if not source or source not in row:
        return ""
    value = row.get(source, "")
    return "" if pd.isna(value) else str(value).strip()


def resolve_mapping(columns: list[str], user_mapping: Mapping[str, str] | None = None) -> dict[str, str | None]:
    user_mapping = dict(user_mapping or {})
    mapping: dict[str, str | None] = {}
    for target, aliases in DEFAULT_MAPPING.items():
        mapping[target] = user_mapping.get(target) or _find_column(columns, aliases)
    return mapping


def process_products(
    product_path_or_file,
    vademecum_path_or_file=None,
    historical_paths: list[str | Path] | None = None,
    column_mapping: Mapping[str, str] | None = None,
    vademecum_mapping: Mapping[str, str] | None = None,
    progress: Callable[[int, str, dict[str, Any] | None], None] | None = None,
) -> dict[str, pd.DataFrame]:
    def report(percent: int, message: str, extra: dict[str, Any] | None = None) -> None:
        if progress:
            progress(percent, message, extra)

    report(5, "Leyendo base de productos")
    product_df = read_table(product_path_or_file)
    report(12, "Resolviendo mapeo de columnas")
    mapping = resolve_mapping(list(product_df.columns), column_mapping)
    report(18, "Cargando vademecum local")
    vademecum = load_vademecum(vademecum_path_or_file, vademecum_mapping) if vademecum_path_or_file else pd.DataFrame()
    report(21, "Leyendo homologaciones historicas auxiliares")
    historical_frames = load_historical_learning(historical_paths)
    report(25, "Construyendo diccionario de marcas")
    brand_dictionary = build_brand_dictionary(vademecum)
    brand_dictionary = enrich_brand_dictionary_from_products(product_df, mapping, brand_dictionary)
    known_actives = build_known_actives(brand_dictionary)
    records = []
    total_rows = max(len(product_df), 1)
    for index, (_, row) in enumerate(product_df.iterrows(), start=1):
        records.append(_build_record(row, mapping, brand_dictionary, known_actives))
        if index == 1 or index % 25 == 0 or index == total_rows:
            report(
                25 + int(35 * index / total_rows),
                f"Normalizando y enriqueciendo productos ({index}/{total_rows})",
                {"normalized": normalized_preview(records[-1])},
            )
    infer_missing_presentations_from_peers(records)
    report(68, "Construyendo codigos madre multiclinica")
    frames = build_workbook_frames(records, [], vademecum, brand_dictionary, historical_frames)
    homologation_rows = frames["HOMOLOGACION_MULTICLINICA"].to_dict("records") + frames["HOMOLOGACION_INSUMOS"].to_dict("records")
    total_homologation = max(len(homologation_rows), 1)
    for index, row in enumerate(homologation_rows, start=1):
        report(
            68 + int(14 * index / total_homologation),
            f"Agrupando variantes locales bajo codigos madre ({index}/{total_homologation})",
            {"proposal": mother_preview(row)},
        )
    report(82, "Aplicando reglas de bloqueo y preparando hojas")
    report(88, "Hojas multiclinica listas")
    return frames


def normalized_preview(record: ProductAttributes) -> dict[str, str]:
    return {
        "codigo": record.codigo_origen,
        "descripcion": record.descripcion_origen,
        "familia": record.familia_detectada,
        "activo": record.principio_activo_detectado,
        "concentracion": record.cantidad_total or record.concentracion,
        "forma": record.forma,
        "via": record.via,
        "observaciones": record.observaciones,
    }


def infer_missing_presentations_from_peers(records: list[ProductAttributes]) -> None:
    peer_presentations: dict[tuple[str, str, str], set[str]] = {}
    for record in records:
        key = (record.principio_activo_detectado, compact_amount(record.cantidad_total), record.forma)
        if all(key) and record.presentacion:
            peer_presentations.setdefault(key, set()).add(record.presentacion)
    for record in records:
        key = (record.principio_activo_detectado, compact_amount(record.cantidad_total), record.forma)
        presentations = peer_presentations.get(key, set())
        if not record.presentacion and len(presentations) == 1:
            record.presentacion = next(iter(presentations))
            note = "Presentacion base inferida desde otros registros multiclinica equivalentes"
            record.observaciones = "; ".join(part for part in [record.observaciones, note] if part)


def mother_preview(row: dict[str, object]) -> dict[str, object]:
    return {
        "codigo": row.get("codigo_local", ""),
        "descripcion": row.get("descripcion_local", ""),
        "sugerido": row.get("codigo_madre", ""),
        "estado": row.get("estado_homologacion", ""),
        "score": "",
        "motivo": row.get("motivo_homologacion", ""),
    }


def _build_record(
    row: pd.Series,
    mapping: Mapping[str, str | None],
    brand_dictionary: dict[str, list[dict[str, str]]],
    known_actives: set[str] | None = None,
) -> ProductAttributes:
    description = _value(row, mapping.get("descripcion_origen"))
    normalized = normalize_text(description)
    form = detect_form(normalized)
    brand = normalize_text(_value(row, mapping.get("marca"))) or first_token_brand(normalized)
    amount, ratio_concentration, ratio_volume = detect_dose_components(normalized)
    family = detect_family(normalized, _value(row, mapping.get("familia")), _value(row, mapping.get("categoria")))
    record = ProductAttributes(
        clinica=normalize_text(_value(row, mapping.get("clinica"))),
        codigo_origen=_value(row, mapping.get("codigo_origen")) or str(row.name + 1),
        descripcion_origen=description,
        descripcion_normalizada=normalized,
        familia_detectada=family,
        marca_detectada=brand,
        cantidad_total=amount,
        concentracion=ratio_concentration or detect_concentration(normalized),
        forma=form,
        via=detect_route(normalized, form),
        volumen_tamano=ratio_volume or detect_volume_size(normalized),
        presentacion=detect_presentation(normalized),
        unidad_compra=normalize_text(_value(row, mapping.get("unidad_compra"))),
        unidad_consumo=normalize_text(_value(row, mapping.get("unidad_consumo"))),
        factor_conversion=normalize_text(_value(row, mapping.get("factor_conversion"))),
        precio=normalize_text(_value(row, mapping.get("precio"))),
        laboratorio_titular=normalize_text(_value(row, mapping.get("laboratorio_titular"))),
        registro_sanitario=normalize_text(_value(row, mapping.get("registro_sanitario"))),
        alertas=detect_alerts(normalized),
    )
    if record.familia_detectada == "MEDICAMENTO":
        _enrich_from_local_vademecum(record, brand_dictionary)
        _enrich_from_parenthetical_or_known_active(record, known_actives or set())
    record.fuente_primaria_requerida = isp_consultation_note(record)
    record.fuente_secundaria_sugerida = secondary_vademecum_note(record)
    observations = [record.observaciones] if record.observaciones else []
    if record.alertas:
        observations.append("Alertas: " + ", ".join(record.alertas))
    if record.familia_detectada == "MEDICAMENTO" and not record.principio_activo_detectado:
        observations.append("Medicamento sin principio activo confirmado")
    if record.fuente_primaria_requerida:
        observations.append("Validacion primaria ISP/ANAMED requerida para cierre trazable")
    record.observaciones = "; ".join(observations)
    return record


def _enrich_from_local_vademecum(record: ProductAttributes, brand_dictionary: dict[str, list[dict[str, str]]]) -> None:
    if record.familia_detectada != "MEDICAMENTO":
        return
    if not record.marca_detectada:
        return
    matches = brand_dictionary.get(record.marca_detectada, [])
    if not matches:
        return
    distinct = {
        (
            normalize_text(item.get("principio_activo", "")),
            normalize_text(item.get("concentracion", "")),
            normalize_text(item.get("forma", "")),
        )
        for item in matches
    }
    if len(distinct) > 1:
        record.fuente_enriquecimiento = "VADEMECUM_USUARIO_AMBIGUO"
        record.observaciones = "Marca con mas de una composicion/presentacion en vademecum local; requiere revision QF"
        return
    item = matches[0]
    if not record.principio_activo_detectado:
        record.principio_activo_detectado = normalize_text(item.get("principio_activo", ""))
    if not record.concentracion:
        concentration_text = normalize_text(item.get("concentracion", ""))
        amount, concentration, volume = detect_dose_components(concentration_text)
        record.cantidad_total = record.cantidad_total or amount
        record.concentracion = concentration or concentration_text
        record.volumen_tamano = record.volumen_tamano or volume
    if not record.forma:
        record.forma = normalize_text(item.get("forma", ""))
    if not record.laboratorio_titular:
        record.laboratorio_titular = normalize_text(item.get("laboratorio", ""))
    if not record.registro_sanitario:
        record.registro_sanitario = normalize_text(item.get("registro_sanitario", ""))
    record.fuente_enriquecimiento = "VADEMECUM_USUARIO"


def _enrich_from_parenthetical_or_known_active(record: ProductAttributes, known_actives: set[str]) -> None:
    if record.familia_detectada != "MEDICAMENTO":
        return
    for active in sorted(known_actives, key=len, reverse=True):
        if active and re_contains_term(record.descripcion_normalizada, active):
            if not record.principio_activo_detectado:
                record.principio_activo_detectado = active
                record.fuente_enriquecimiento = record.fuente_enriquecimiento or "INFERIDO_POR_TEXTO_CON_DICCIONARIO"
            return
    if not record.principio_activo_detectado and record.cantidad_total:
        first = first_token_brand(record.descripcion_normalizada)
        blocked = {"SOLUCION", "FRASCO", "AMPOLLA", "COMPRIMIDO", "CAPSULA", "CAJA", "BLISTER"}
        if first and first not in blocked:
            record.principio_activo_detectado = first
            record.fuente_enriquecimiento = "INFERIDO_DESDE_DESCRIPCION_LOCAL"
            note = "Principio activo inferido desde descripcion local; validar si corresponde a marca comercial"
            record.observaciones = "; ".join(part for part in [record.observaciones, note] if part)


def re_contains_term(text: str, term: str) -> bool:
    normalized = normalize_text(term)
    return bool(normalized and re.search(rf"\b{re.escape(normalized)}\b", text))


def build_known_actives(brand_dictionary: dict[str, list[dict[str, str]]]) -> set[str]:
    return {
        active
        for values in brand_dictionary.values()
        for item in values
        for active in [normalize_text(item.get("principio_activo", ""))]
        if looks_like_active(active)
    }


def looks_like_active(value: str) -> bool:
    normalized = normalize_text(value)
    if len(normalized) < 4:
        return False
    if re.search(r"\d", normalized):
        return False
    blocked = {
        "PAR",
        "UNIDAD",
        "CAJA",
        "COMPRA ESPORADICA",
        "DESECHABLE",
        "ADULTO",
        "PEDIATRICO",
        "FRASCO",
        "AMPOLLA",
        "SOLUCION",
        "KIT",
        "PACK",
    }
    return normalized not in blocked


def enrich_brand_dictionary_from_products(
    product_df: pd.DataFrame,
    mapping: Mapping[str, str | None],
    brand_dictionary: dict[str, list[dict[str, str]]],
) -> dict[str, list[dict[str, str]]]:
    dictionary = {brand: list(values) for brand, values in brand_dictionary.items()}
    row_facts = []
    first_tokens = set()
    parent_terms = set()
    for _, row in product_df.iterrows():
        description = _value(row, mapping.get("descripcion_origen"))
        normalized = normalize_text(description)
        if detect_family(normalized) != "MEDICAMENTO":
            continue
        brand = normalize_text(_value(row, mapping.get("marca"))) or first_token_brand(normalized)
        terms = parenthetical_terms(description)
        first = first_token_brand(normalized)
        if first:
            first_tokens.add(first)
        parent_terms.update(terms)
        row_facts.append((brand, first, terms))
    known_active_candidates = {
        normalize_text(item.get("principio_activo", ""))
        for values in dictionary.values()
        for item in values
        if normalize_text(item.get("principio_activo", ""))
    }
    known_active_candidates.update(term for term in parent_terms if term in first_tokens and looks_like_active(term))
    for brand, first, terms in row_facts:
        if not terms:
            continue
        term = terms[-1]
        if not looks_like_active(term):
            continue
        if first in known_active_candidates and term not in known_active_candidates:
            add_brand_dictionary_entry(dictionary, term, first, "DESCRIPCION_LOCAL_PARENTESIS_INVERTIDO")
            add_brand_dictionary_entry(dictionary, first, first, "DESCRIPCION_LOCAL_TEXTO")
        elif term in known_active_candidates or first not in known_active_candidates:
            if brand and brand != term:
                add_brand_dictionary_entry(dictionary, brand, term, "DESCRIPCION_LOCAL_PARENTESIS")
            add_brand_dictionary_entry(dictionary, term, term, "DESCRIPCION_LOCAL_PARENTESIS")
    return dictionary


def add_brand_dictionary_entry(dictionary: dict[str, list[dict[str, str]]], brand: str, active: str, source: str) -> None:
    brand = normalize_text(brand)
    active = normalize_text(active)
    if not brand or not active:
        return
    entry = {
        "marca": brand,
        "principio_activo": active,
        "concentracion": "",
        "forma": "",
        "laboratorio": "",
        "registro_sanitario": "",
        "fuente": source,
        "fecha_consulta": datetime.now().date().isoformat(),
    }
    if entry not in dictionary.setdefault(brand, []):
        dictionary[brand].append(entry)


def build_workbook_frames(records, proposals=None, vademecum=None, brand_dictionary=None, historical_frames=None) -> dict[str, pd.DataFrame]:
    brand_dictionary = brand_dictionary or {}
    historical_frames = historical_frames or load_historical_learning(None)
    medication_records = [record for record in records if not is_insumo_family(record.familia_detectada, record.descripcion_normalizada)]
    insumo_records = [record for record in records if is_insumo_family(record.familia_detectada, record.descripcion_normalizada)]
    mother_rows: list[dict[str, object]] = []
    homologation_rows: list[dict[str, object]] = []
    revision_qf_rows: list[dict[str, object]] = []
    revision_logistica_rows: list[dict[str, object]] = []
    no_homologar_rows: list[dict[str, object]] = []
    alert_rows: list[dict[str, object]] = []

    groups: dict[tuple[str, ...], list[ProductAttributes]] = {}
    for record in medication_records:
        insufficient = missing_mother_fields(record)
        if insufficient:
            row = local_revision_row(record, "REQUIERE REVISION QF", "Faltan datos para construir codigo madre: " + ", ".join(insufficient))
            revision_qf_rows.append(row)
            alert_rows.append(alert_row(record, "descripcion insuficiente", row["motivo_homologacion"]))
            continue
        groups.setdefault(mother_key(record), []).append(record)

    for key, group in groups.items():
        volumes = sorted({record.volumen_tamano for record in group if record.volumen_tamano})
        if len(volumes) > 1:
            motivo = "Volumen informado distinto entre registros equivalentes candidatos: " + ", ".join(volumes)
            for record in group:
                row = local_revision_row(record, "REQUIERE REVISION QF", motivo)
                revision_qf_rows.append(row)
                no_homologar_rows.append(row)
                alert_rows.append(alert_row(record, "volumen distinto", motivo))
            continue

        mother = build_mother(group)
        mother_rows.append(mother)
        for record in group:
            homologation_rows.append(local_homologation_row(record, mother, group))
            if has_logistic_differences(record, group):
                revision_logistica_rows.append(local_homologation_row(record, mother, group, estado="REQUIERE VALIDACION LOGISTICA"))
                alert_rows.append(alert_row(record, "posible variante logistica", "Mismo producto tecnico con diferencias de unidad, factor o presentacion comercial."))
            for term in record.alertas:
                alert_rows.append(alert_row(record, term, "Termino de alerta detectado; revisar antes de aprobacion final."))

    codigos_madre = pd.DataFrame(mother_rows, columns=[
        "codigo_madre",
        "descripcion_madre",
        "principio_activo",
        "cantidad_total",
        "concentracion",
        "volumen",
        "forma_base",
        "presentacion_base",
        "via",
        "familia",
        "nivel_confianza",
        "requiere_revision_qf",
        "motivo",
    ])
    homologacion = pd.DataFrame(homologation_rows, columns=[
        "codigo_madre",
        "descripcion_madre",
        "clinica",
        "codigo_local",
        "descripcion_local",
        "descripcion_normalizada",
        "marca_detectada",
        "principio_activo_detectado",
        "cantidad_total_detectada",
        "concentracion_detectada",
        "volumen_detectado",
        "forma_detectada",
        "presentacion_detectada",
        "unidad_compra",
        "factor_conversion",
        "estado_homologacion",
        "motivo_homologacion",
        "observaciones",
    ])
    revision_qf = pd.DataFrame(revision_qf_rows)
    revision_logistica = pd.DataFrame(revision_logistica_rows)
    no_homologar = pd.DataFrame(no_homologar_rows)
    diccionario = brand_dictionary_frame(brand_dictionary, vademecum)
    alertas = pd.DataFrame(alert_rows, columns=["clinica", "codigo_local", "descripcion_local", "tipo_alerta", "motivo"])
    insumo_frames = build_insumo_frames(insumo_records)
    resumen = build_multiclinic_summary(records, codigos_madre, homologacion, revision_qf, revision_logistica, no_homologar, insumo_frames)
    matriz_clinicas = build_homologation_matrix(homologacion, insumo_frames.get("HOMOLOGACION_INSUMOS", pd.DataFrame()))
    frames = {
        "CODIGOS_MADRE": codigos_madre,
        "HOMOLOGACION_MULTICLINICA": homologacion,
        "REVISION_QF": revision_qf,
        "REVISION_LOGISTICA": revision_logistica,
        "NO_HOMOLOGAR": no_homologar,
        "DICCIONARIO_MARCAS": diccionario,
        "ALERTAS": alertas,
        "MATRIZ_HOMOLOGADOS_CLINICAS": matriz_clinicas,
        "RESUMEN": resumen,
    }
    frames.update(historical_frames)
    frames.update(insumo_frames)
    return frames


def build_homologation_matrix(homologacion: pd.DataFrame, homologacion_insumos: pd.DataFrame) -> pd.DataFrame:
    """
    Matriz multiclinica para revision: una fila por codigo madre, con columnas COD/DESC por clinica.

    Nota: si una clinica tiene mas de un codigo local dentro del mismo codigo madre, se concatenan.
    """

    def _select(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=["codigo_madre", "descripcion_madre", "clinica", "codigo_local", "descripcion_local"])
        cols = [c for c in ["codigo_madre", "descripcion_madre", "clinica", "codigo_local", "descripcion_local"] if c in df.columns]
        out = df.loc[:, cols].copy()
        for needed in ["codigo_madre", "descripcion_madre", "clinica", "codigo_local", "descripcion_local"]:
            if needed not in out.columns:
                out[needed] = ""
        return out

    combined = pd.concat([_select(homologacion), _select(homologacion_insumos)], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=["codigo_madre", "descripcion_madre"])

    combined["clinica"] = combined["clinica"].fillna("").map(lambda x: normalize_text(str(x)) if x else "")
    combined.loc[combined["clinica"] == "", "clinica"] = "SIN_CLINICA"
    combined["codigo_local"] = combined["codigo_local"].fillna("").map(lambda x: str(x).strip())
    combined["descripcion_local"] = combined["descripcion_local"].fillna("").map(lambda x: str(x).strip())

    def _clinic_col(clinica: str) -> str:
        return normalize_text(clinica).replace(" ", "_") or "SIN_CLINICA"

    clinics = sorted({_clinic_col(c) for c in combined["clinica"].tolist()})

    def _join_unique(values: pd.Series) -> str:
        items = [str(v).strip() for v in values.tolist() if v is not None and str(v).strip() != ""]
        if not items:
            return ""
        unique = list(dict.fromkeys(items))
        return "; ".join(unique)

    agg = (
        combined.groupby(["codigo_madre", "descripcion_madre", "clinica"], dropna=False, as_index=False)
        .agg({"codigo_local": _join_unique, "descripcion_local": _join_unique})
    )

    rows: list[dict[str, str]] = []
    for (codigo_madre, descripcion_madre), group in agg.groupby(["codigo_madre", "descripcion_madre"], dropna=False):
        row: dict[str, str] = {
            "codigo_madre": str(codigo_madre or "").strip(),
            "descripcion_madre": str(descripcion_madre or "").strip(),
        }
        mapping = { _clinic_col(str(r["clinica"])): r for r in group.to_dict("records") }
        for clinic in clinics:
            info = mapping.get(clinic, {})
            row[f"COD_{clinic}"] = str(info.get("codigo_local", "") or "")
            row[f"DESC_{clinic}"] = str(info.get("descripcion_local", "") or "")
        rows.append(row)

    matrix = pd.DataFrame(rows)
    matrix = matrix.sort_values(["codigo_madre", "descripcion_madre"], kind="stable").reset_index(drop=True)
    return matrix


def build_insumo_frames(records: list[ProductAttributes]) -> dict[str, pd.DataFrame]:
    groups: dict[tuple[str, ...], list[tuple[ProductAttributes, Any]]] = {}
    revision_rows = []
    for record in records:
        attr = extract_insumo_attributes(record.descripcion_normalizada, record.marca_detectada, record.laboratorio_titular)
        if attr.subfamilia == "OTROS / REQUIERE REVISION" or attr.observaciones:
            revision_rows.append(insumo_revision_row(record, attr, "REQUIERE VALIDACION CLINICA" if attr.requiere_validacion_clinica else "REQUIERE REVISION TECNICA"))
        groups.setdefault(insumo_key(attr), []).append((record, attr))

    homologation_rows = []
    variant_rows = []
    for _, items in groups.items():
        attr = items[0][1]
        mother_desc = insumo_description(attr)
        if not mother_desc or attr.subfamilia == "OTROS / REQUIERE REVISION":
            continue
        codigo_madre = mother_desc
        units = {record.unidad_compra for record, _ in items if record.unidad_compra}
        factors = {record.factor_conversion for record, _ in items if record.factor_conversion}
        logistic = len(units) > 1 or len(factors) > 1 or any(item_attr.cantidad_por_envase for _, item_attr in items)
        for record, item_attr in items:
            status = "REQUIERE VALIDACION CLINICA" if item_attr.requiere_validacion_clinica else "HOMOLOGADO EXACTO"
            if logistic and status == "HOMOLOGADO EXACTO":
                status = "REQUIERE VALIDACION LOGISTICA"
            row = {
                "codigo_madre": codigo_madre,
                "descripcion_madre": mother_desc,
                "clinica": record.clinica,
                "codigo_local": record.codigo_origen,
                "descripcion_local": record.descripcion_origen,
                "descripcion_normalizada": record.descripcion_normalizada,
                **insumo_attr_row(item_attr),
                "modelo": item_attr.modelo,
                "proveedor": record.laboratorio_titular,
                "unidad_compra": record.unidad_compra,
                "factor_conversion": record.factor_conversion,
                "estado_homologacion": status,
                "motivo_homologacion": insumo_motive(items, item_attr),
                "requiere_validacion_logistica": logistic,
                "requiere_validacion_clinica": item_attr.requiere_validacion_clinica,
                "nivel_confianza": insumo_confidence(item_attr, logistic),
            }
            homologation_rows.append(row)
            if logistic:
                variant_rows.append({
                    "codigo_madre": codigo_madre,
                    "descripcion_madre": mother_desc,
                    "codigo_local": record.codigo_origen,
                    "clinica": record.clinica,
                    "unidad_compra": record.unidad_compra,
                    "cantidad_por_envase": item_attr.cantidad_por_envase,
                    "factor_conversion": record.factor_conversion,
                    "precio": record.precio,
                    "observacion": "Variante logistica; no cambia producto tecnico madre.",
                })

    columns = [
        "codigo_madre",
        "descripcion_madre",
        "clinica",
        "codigo_local",
        "descripcion_local",
        "descripcion_normalizada",
        "subfamilia",
        "medida",
        "capacidad",
        "calibre",
        "largo",
        "ancho",
        "material",
        "esterilidad",
        "conexion",
        "tipo_punta",
        "vias",
        "con_sin_aguja",
        "con_sin_filtro",
        "marca",
        "modelo",
        "proveedor",
        "unidad_compra",
        "factor_conversion",
        "estado_homologacion",
        "motivo_homologacion",
        "requiere_validacion_logistica",
        "requiere_validacion_clinica",
        "nivel_confianza",
    ]
    return {
        "HOMOLOGACION_INSUMOS": pd.DataFrame(homologation_rows, columns=columns),
        "VARIANTES_LOGISTICAS": pd.DataFrame(variant_rows, columns=[
            "codigo_madre",
            "descripcion_madre",
            "codigo_local",
            "clinica",
            "unidad_compra",
            "cantidad_por_envase",
            "factor_conversion",
            "precio",
            "observacion",
        ]),
    }


def insumo_revision_row(record: ProductAttributes, attr, status: str) -> dict[str, object]:
    return {
        "clinica": record.clinica,
        "codigo_local": record.codigo_origen,
        "descripcion_local": record.descripcion_origen,
        "descripcion_normalizada": record.descripcion_normalizada,
        "subfamilia": attr.subfamilia,
        "estado_homologacion": status,
        "motivo_homologacion": attr.observaciones or "Insumo requiere validacion tecnica/clinica.",
    }


def insumo_motive(items: list[tuple[ProductAttributes, Any]], attr) -> str:
    base = f"Se agrupa por subfamilia {attr.subfamilia} y atributos dimensionales/funcionales criticos coincidentes."
    if len({item_attr.marca for _, item_attr in items if item_attr.marca}) > 1:
        base += " La marca se conserva como variante, no como agrupador primario."
    return base


def insumo_confidence(attr, logistic: bool) -> str:
    if attr.observaciones or attr.requiere_validacion_clinica:
        return "REVISIÓN"
    if logistic:
        return "MEDIA"
    return "ALTA"


def missing_mother_fields(record: ProductAttributes) -> list[str]:
    missing = []
    if not record.principio_activo_detectado:
        missing.append("principio_activo")
    if not record.cantidad_total:
        missing.append("cantidad_total")
    if not (record.forma or record.presentacion):
        missing.append("forma_presentacion")
    return missing


def mother_key(record: ProductAttributes) -> tuple[str, ...]:
    return (
        record.familia_detectada,
        record.principio_activo_detectado,
        compact_amount(record.cantidad_total),
        base_form(record),
        record.presentacion or record.forma,
        record.via,
    )


def base_form(record: ProductAttributes) -> str:
    if record.presentacion == "FRASCO AMPOLLA":
        return "SOLUCION INYECTABLE"
    return record.forma


def build_mother(group: list[ProductAttributes]) -> dict[str, object]:
    first = group[0]
    volumes = [record.volumen_tamano for record in group if record.volumen_tamano]
    volume = volumes[0] if volumes and len(volumes) == len(group) else ""
    concentrations = [record.concentracion for record in group if record.concentracion and "/" in record.concentracion]
    presentation = first.presentacion or first.forma
    amount = compact_amount(first.cantidad_total)
    amount_text = f"{amount}/{compact_amount(volume)}" if volume and len(volumes) == len(group) else amount
    description = " ".join(part for part in [first.principio_activo_detectado, amount_text, presentation] if part)
    volume_observation = " Volumen informado en todos los registros." if volume else (
        " Volumen informado en algunos registros, no en todos." if volumes else ""
    )
    motivo = (
        f"Se agrupa porque todos los registros corresponden a {first.principio_activo_detectado}, "
        f"cantidad total {amount}, forma {base_form(first)} y presentacion {presentation}."
        " Las diferencias de marca comercial no cambian el producto tecnico."
        + volume_observation
    )
    return {
        "codigo_madre": description,
        "descripcion_madre": description,
        "principio_activo": first.principio_activo_detectado,
        "cantidad_total": amount,
        "concentracion": compact_amount(concentrations[0]) if concentrations else "",
        "volumen": ", ".join(compact_amount(value) for value in sorted(set(volumes))) if volumes else "",
        "forma_base": base_form(first),
        "presentacion_base": presentation,
        "via": first.via,
        "familia": first.familia_detectada,
        "nivel_confianza": "ALTO" if len(group) > 1 else "MEDIO",
        "requiere_revision_qf": False,
        "motivo": motivo,
    }


def local_homologation_row(record: ProductAttributes, mother: dict[str, object], group: list[ProductAttributes], estado: str | None = None) -> dict[str, object]:
    volumes = [item.volumen_tamano for item in group if item.volumen_tamano]
    observations = [record.observaciones] if record.observaciones else []
    if volumes and not record.volumen_tamano:
        observations.append("HOMOLOGADO CON VOLUMEN NO INFORMADO EN ALGUNAS DESCRIPCIONES")
    if len({item.marca_detectada for item in group if item.marca_detectada}) > 1:
        observations.append("Diferencia de marca comercial tratada como variante local")
    status = estado or ("HOMOLOGADO CON DATOS INCOMPLETOS" if volumes and not record.volumen_tamano else "HOMOLOGADO EXACTO")
    return {
        "codigo_madre": mother["codigo_madre"],
        "descripcion_madre": mother["descripcion_madre"],
        "clinica": record.clinica,
        "codigo_local": record.codigo_origen,
        "descripcion_local": record.descripcion_origen,
        "descripcion_normalizada": record.descripcion_normalizada,
        "marca_detectada": record.marca_detectada,
        "principio_activo_detectado": record.principio_activo_detectado,
        "cantidad_total_detectada": compact_amount(record.cantidad_total),
        "concentracion_detectada": record.concentracion,
        "volumen_detectado": compact_amount(record.volumen_tamano),
        "forma_detectada": record.forma,
        "presentacion_detectada": record.presentacion,
        "unidad_compra": record.unidad_compra,
        "factor_conversion": record.factor_conversion,
        "estado_homologacion": status,
        "motivo_homologacion": mother["motivo"],
        "observaciones": "; ".join(dict.fromkeys(observations)),
    }


def local_revision_row(record: ProductAttributes, estado: str, motivo: str) -> dict[str, object]:
    return {
        "clinica": record.clinica,
        "codigo_local": record.codigo_origen,
        "descripcion_local": record.descripcion_origen,
        "descripcion_normalizada": record.descripcion_normalizada,
        "marca_detectada": record.marca_detectada,
        "principio_activo_detectado": record.principio_activo_detectado,
        "cantidad_total_detectada": compact_amount(record.cantidad_total),
        "concentracion_detectada": record.concentracion,
        "volumen_detectado": compact_amount(record.volumen_tamano),
        "forma_detectada": record.forma,
        "presentacion_detectada": record.presentacion,
        "estado_homologacion": estado,
        "motivo_homologacion": motivo,
        "observaciones": record.observaciones,
    }


def has_logistic_differences(record: ProductAttributes, group: list[ProductAttributes]) -> bool:
    units = {item.unidad_compra for item in group if item.unidad_compra}
    factors = {item.factor_conversion for item in group if item.factor_conversion}
    return len(units) > 1 or len(factors) > 1


def alert_row(record: ProductAttributes, tipo: str, motivo: str) -> dict[str, object]:
    return {
        "clinica": record.clinica,
        "codigo_local": record.codigo_origen,
        "descripcion_local": record.descripcion_origen,
        "tipo_alerta": tipo,
        "motivo": motivo,
    }


def brand_dictionary_frame(brand_dictionary: dict[str, list[dict[str, str]]], vademecum) -> pd.DataFrame:
    rows = []
    for brand, values in brand_dictionary.items():
        for item in values:
            rows.append({
                "marca": normalize_text(item.get("marca", brand)) or brand,
                "principio_activo": normalize_text(item.get("principio_activo", "")),
                "fuente": item.get("fuente", "VADEMECUM_USUARIO"),
                "fecha_consulta": item.get("fecha_consulta", datetime.now().date().isoformat()),
            })
    return pd.DataFrame(rows, columns=["marca", "principio_activo", "fuente", "fecha_consulta"]).drop_duplicates()


def build_multiclinic_summary(records, codigos_madre, homologacion, revision_qf, revision_logistica, no_homologar, insumo_frames=None) -> pd.DataFrame:
    insumo_frames = insumo_frames or {}
    homologacion_insumos = insumo_frames.get("HOMOLOGACION_INSUMOS", pd.DataFrame())
    variantes_logisticas = insumo_frames.get("VARIANTES_LOGISTICAS", pd.DataFrame())
    rows = [
        ("total_registros", len(records)),
        ("total_codigos_madre_creados", len(codigos_madre)),
        ("total_codigos_madre_insumos_creados", homologacion_insumos["codigo_madre"].nunique() if not homologacion_insumos.empty else 0),
        ("total_codigos_locales_agrupados", len(homologacion) + len(homologacion_insumos)),
        ("clinicas_cubiertas", len({record.clinica for record in records if record.clinica})),
        ("registros_homologados_exactos", int((homologacion["estado_homologacion"] == "HOMOLOGADO EXACTO").sum()) if not homologacion.empty else 0),
        ("registros_homologados_con_datos_incompletos", int((homologacion["estado_homologacion"] == "HOMOLOGADO CON DATOS INCOMPLETOS").sum()) if not homologacion.empty else 0),
        ("revision_qf", len(revision_qf)),
        ("revision_logistica", len(revision_logistica) + len(variantes_logisticas)),
        ("no_homologar", len(no_homologar)),
    ]
    return pd.DataFrame(rows, columns=["indicador", "valor"])


def build_summary(normalized: pd.DataFrame, proposals: pd.DataFrame) -> pd.DataFrame:
    total = len(proposals)
    confidence = {"ALTO": 1.0, "MEDIO": 0.65, "BAJO": 0.25}
    rows = [
        ("fecha_procesamiento", datetime.now().isoformat(timespec="seconds")),
        ("total_productos_procesados", total),
        ("homologados_exactos", int((proposals["estado_homologacion"] == "HOMOLOGADO EXACTO").sum()) if total else 0),
        ("homologados_equivalentes", int((proposals["estado_homologacion"] == "HOMOLOGADO EQUIVALENTE").sum()) if total else 0),
        ("no_homologar", int((proposals["estado_homologacion"] == "NO HOMOLOGAR").sum()) if total else 0),
        ("revision_qf", int((proposals["requiere_qf"]).sum()) if total else 0),
        ("revision_clinica", int((proposals["requiere_validacion_clinica"]).sum()) if total else 0),
        ("revision_logistica", int((proposals["requiere_validacion_logistica"]).sum()) if total else 0),
        ("descripcion_insuficiente", int((proposals["estado_homologacion"] == "DESCRIPCION INSUFICIENTE").sum()) if total else 0),
        ("porcentaje_confianza_promedio", round(100 * proposals["nivel_confianza"].map(confidence).fillna(0).mean(), 2) if total else 0),
    ]
    for label, counts in [
        ("top_20_causas_no_homologacion", top_counts(proposals.loc[proposals["estado_homologacion"].isin(["NO HOMOLOGAR", "DESCRIPCION INSUFICIENTE"]), "motivo_decision"].tolist())),
        ("top_20_marcas_ambiguas", top_counts(normalized.loc[normalized["fuente_enriquecimiento"] == "VADEMECUM_USUARIO_AMBIGUO", "marca_detectada"].tolist())),
        ("top_20_productos_sin_principio_activo", top_counts(normalized.loc[normalized["principio_activo_detectado"] == "", "descripcion_origen"].tolist())),
    ]:
        rows.extend((f"{label}_{index + 1}", f"{name} ({count})") for index, (name, count) in enumerate(counts))
    return pd.DataFrame(rows, columns=["indicador", "valor"])


def export_workbook(frames: dict[str, pd.DataFrame], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, frame in frames.items():
            frame.to_excel(writer, index=False, sheet_name=sheet_name[:31])
            worksheet = writer.sheets[sheet_name[:31]]
            for column_cells in worksheet.columns:
                max_length = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 60)
            worksheet.freeze_panes = "A2"
    return output_path
