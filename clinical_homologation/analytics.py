from __future__ import annotations

import json
import re
from collections import Counter
from statistics import median
from typing import Any

import pandas as pd

from .insumos import extract_insumo_attributes, insumo_description, insumo_key, is_insumo_family
from .models import ProductAttributes
from .normalization import compact_amount, normalize_text


MASTER_COLUMNS = [
    "clinica",
    "codigo_producto",
    "descripcion_original",
    "descripcion_limpia",
    "categoria_detectada",
    "familia_detectada",
    "atributos_clinicos_extraidos",
    "unidad_medida_maestro",
    "unidad_compra_detectada",
    "contenido_detectado",
    "unidad_contenido_detectada",
    "unidad_comparable_recomendada",
    "factor_conversion",
    "precio_original",
    "precio_comparable",
    "proveedor",
    "marca_laboratorio",
    "nivel_confianza_normalizacion",
    "observaciones",
]

GAP_COLUMNS = [
    "clinica",
    "codigo_producto",
    "descripcion_original",
    "gap_detectado",
    "severidad",
    "impacto_potencial",
    "explicacion",
    "accion_recomendada",
]

CANDIDATE_COLUMNS = [
    "grupo_homologacion_propuesto",
    "familia",
    "descripcion_estandar_holding",
    "skus_incluidos",
    "clinicas_incluidas",
    "score_homologacion",
    "nivel_confianza",
    "fundamento",
    "riesgos",
    "requiere_validacion_clinica",
]

OPPORTUNITY_COLUMNS = [
    "grupo_homologacion",
    "gasto_total",
    "precio_minimo_comparable",
    "precio_promedio_comparable",
    "precio_maximo_comparable",
    "clinica_mas_cara",
    "ahorro_potencial_estimado",
    "supuestos",
    "prioridad",
]

CLINICAL_COLUMNS = [
    "producto",
    "skus_afectados",
    "motivo_validacion",
    "riesgo_clinico",
    "pregunta_para_el_area_clinica",
    "responsable_sugerido",
]


UNIT_EQUIVALENCES = {
    "COMP": "COMPRIMIDO",
    "COMPR": "COMPRIMIDO",
    "COMPRIMIDOS": "COMPRIMIDO",
    "COMPRIMIDO": "COMPRIMIDO",
    "TAB": "COMPRIMIDO",
    "TABLETA": "COMPRIMIDO",
    "CAP": "CAPSULA",
    "CAPS": "CAPSULA",
    "CAPSULA": "CAPSULA",
    "CAPSULAS": "CAPSULA",
    "AMP": "AMPOLLA",
    "AMPOLLA": "AMPOLLA",
    "AMPOLLAS": "AMPOLLA",
    "FCO": "FRASCO",
    "FRASCO": "FRASCO",
    "FRASCOS": "FRASCO",
    "FA": "FRASCO AMPOLLA",
    "FRASCO AMPOLLA": "FRASCO AMPOLLA",
    "CJ": "CAJA",
    "CAJA": "CAJA",
    "CAJAS": "CAJA",
    "BL": "BLISTER",
    "BLIST": "BLISTER",
    "BLISTER": "BLISTER",
    "UN": "UNIDAD",
    "UND": "UNIDAD",
    "UNIDAD": "UNIDAD",
    "UNIDADES": "UNIDAD",
    "PAR": "PAR",
    "PARES": "PAR",
    "TIR": "TIRA",
    "TIRA": "TIRA",
    "TIRAS": "TIRA",
    "BOLSA": "BOLSA",
    "BOLSAS": "BOLSA",
    "SET": "SET",
    "KIT": "KIT",
    "PACK": "PACK",
    "ROLLO": "ROLLO",
    "ROLLOS": "ROLLO",
    "ML": "ML",
    "MG": "MG",
    "G": "G",
    "CM": "CM",
    "MM": "MM",
}

PACK_UNITS = {"CAJA", "BLISTER", "BOLSA", "PACK", "KIT", "SET", "TIRA", "ROLLO"}
MINIMAL_UNITS = {"COMPRIMIDO", "CAPSULA", "AMPOLLA", "FRASCO", "FRASCO AMPOLLA", "UNIDAD", "PAR", "ML"}


def build_supply_analytics_frames(records: list[ProductAttributes]) -> dict[str, pd.DataFrame]:
    master_full = pd.DataFrame([_master_row(record) for record in records])
    if master_full.empty:
        master_full = pd.DataFrame(columns=MASTER_COLUMNS + ["__grupo_homologacion", "__precio_num", "__cantidad_num", "__monto_num"])

    gaps = _build_gaps(master_full)
    candidates = _build_candidates(master_full)
    opportunities = _build_opportunities(master_full)
    gaps = pd.concat([gaps, _price_outlier_gaps(master_full)], ignore_index=True)
    clinical = _build_clinical_validation(master_full, gaps, candidates)
    summary = _build_analytics_summary(master_full, gaps, candidates, opportunities, clinical)

    return {
        "MAESTRO_NORMALIZADO": master_full.loc[:, MASTER_COLUMNS],
        "GAPS_DETECTADOS": gaps.loc[:, GAP_COLUMNS] if not gaps.empty else pd.DataFrame(columns=GAP_COLUMNS),
        "CANDIDATOS_HOMOLOGACION": candidates,
        "OPORTUNIDADES_ECONOMICAS": opportunities,
        "VALIDACION_CLINICA": clinical,
        "RESUMEN_ANALITICO": summary,
    }


def _master_row(record: ProductAttributes) -> dict[str, Any]:
    is_supply = is_insumo_family(record.familia_detectada, record.descripcion_normalizada)
    presentation = _extract_presentation(record)
    unit_master = normalize_unit(record.unidad_medida_maestro or record.unidad_compra or record.unidad_consumo)
    factor = _select_factor(record, presentation)
    price = parse_number(record.precio)
    quantity = parse_number(record.cantidad_comprada)
    amount = parse_number(record.monto_total)
    comparable_price = price / factor["value"] if price is not None and factor["value"] else None
    calculated_price = amount / quantity if amount is not None and quantity not in (None, 0) else None

    if is_supply:
        insumo = extract_insumo_attributes(record.descripcion_normalizada, record.marca_detectada, record.laboratorio_titular)
        family = insumo.subfamilia
        attrs = {
            "familia": insumo.subfamilia,
            "medida": insumo.medida,
            "capacidad": insumo.capacidad,
            "calibre": insumo.calibre,
            "material": insumo.material,
            "esterilidad": insumo.esterilidad,
            "conexion": insumo.conexion,
            "vias": insumo.vias,
            "compatibilidad": "requiere validacion" if insumo.requiere_validacion_clinica else "",
        }
        group = "INSUMO|" + "|".join(insumo_key(insumo))
        standard = insumo_description(insumo) or record.descripcion_normalizada
    else:
        family = record.forma or record.presentacion or "FARMACO"
        attrs = {
            "principio_activo": record.principio_activo_detectado,
            "concentracion": compact_amount(record.cantidad_total or record.concentracion),
            "forma_farmaceutica": record.forma,
            "via": record.via,
            "presentacion_clinica": _clinical_presentation(record),
        }
        group = _medication_group(record)
        standard = _medication_standard(record)

    observations = [record.observaciones] if record.observaciones else []
    if factor["source"] == "INCIERTO":
        observations.append("Factor de conversion no confiable; precio no comparable.")
    if calculated_price is not None and price is not None and price:
        diff = abs(calculated_price - price) / abs(price)
        if diff > 0.3:
            observations.append(f"Precio calculado por monto/cantidad difiere {diff:.0%} del precio maestro.")
    if unit_master and presentation["purchase_unit"] and unit_master != presentation["purchase_unit"] and factor["value"] and factor["value"] > 1:
        observations.append("Unidad del maestro no calza con presentacion detectada en descripcion.")

    confidence = _normalization_confidence(record, factor, attrs, is_supply)

    row = {
        "clinica": record.clinica,
        "codigo_producto": record.codigo_origen,
        "descripcion_original": record.descripcion_origen,
        "descripcion_limpia": record.descripcion_normalizada,
        "categoria_detectada": "INSUMO CLINICO" if is_supply else "FARMACO",
        "familia_detectada": family,
        "atributos_clinicos_extraidos": json.dumps({k: v for k, v in attrs.items() if v}, ensure_ascii=False),
        "unidad_medida_maestro": unit_master,
        "unidad_compra_detectada": presentation["purchase_unit"] or unit_master,
        "contenido_detectado": presentation["content"],
        "unidad_contenido_detectada": presentation["content_unit"],
        "unidad_comparable_recomendada": presentation["comparable_unit"],
        "factor_conversion": factor["value"],
        "precio_original": price,
        "precio_comparable": comparable_price,
        "proveedor": record.laboratorio_titular,
        "marca_laboratorio": record.marca_detectada or record.laboratorio_titular,
        "nivel_confianza_normalizacion": confidence,
        "observaciones": "; ".join(dict.fromkeys(observations)),
        "__grupo_homologacion": group,
        "__descripcion_estandar": standard,
        "__factor_source": factor["source"],
        "__precio_num": price,
        "__cantidad_num": quantity,
        "__monto_num": amount,
        "__precio_calculado": calculated_price,
        "__requiere_validacion_clinica": _requires_clinical_validation(record, attrs, is_supply),
    }
    return row


def _extract_presentation(record: ProductAttributes) -> dict[str, Any]:
    text = record.descripcion_normalizada
    purchase_unit = ""
    content: float | None = None
    content_unit = ""

    patterns = [
        r"\b(CAJA|CJ|BLISTER|BLIST|BOLSA|PACK|KIT|SET|TIRA|ROLLO)\s*(?:X|POR)?\s*(\d+(?:\.\d+)?)\s*([A-Z]+(?:\s+[A-Z]+)?)?",
        r"\bX\s*(\d+(?:\.\d+)?)\s*([A-Z]+(?:\s+[A-Z]+)?)?",
        r"\b(\d+(?:\.\d+)?)\s*(COMPRIMIDOS?|COMP|COMPR|CAPS?|CAPSULAS?|AMPOLLAS?|AMP|UN|UND|UNIDADES|PARES?|ML)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 3:
            purchase_unit = normalize_unit(groups[0])
            content = parse_number(groups[1])
            content_unit = normalize_unit(groups[2] or "")
        else:
            purchase_unit = purchase_unit or _purchase_unit_from_text(text)
            content = parse_number(groups[0])
            content_unit = normalize_unit(groups[1] or "")
        break

    if not purchase_unit:
        purchase_unit = _purchase_unit_from_text(text)
    if not content_unit:
        content_unit = _infer_content_unit(record, purchase_unit)
    comparable = _comparable_unit(record, content_unit, purchase_unit)
    return {
        "purchase_unit": purchase_unit,
        "content": content,
        "content_unit": content_unit,
        "comparable_unit": comparable,
    }


def _select_factor(record: ProductAttributes, presentation: dict[str, Any]) -> dict[str, Any]:
    explicit = parse_number(record.factor_conversion)
    if explicit and explicit > 0:
        return {"value": explicit, "source": "MAESTRO"}
    if presentation["content"] and presentation["content"] > 0:
        return {"value": presentation["content"], "source": "DESCRIPCION"}
    if presentation["purchase_unit"] in MINIMAL_UNITS or not presentation["purchase_unit"]:
        return {"value": 1.0, "source": "INFERIDO"}
    return {"value": None, "source": "INCIERTO"}


def normalize_unit(value: object) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    if text in UNIT_EQUIVALENCES:
        return UNIT_EQUIVALENCES[text]
    for source, target in sorted(UNIT_EQUIVALENCES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(source)}\b", text):
            return target
    return text


def parse_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)
    raw = str(value).strip()
    if not raw or raw.upper() == "NAN":
        return None
    text = re.sub(r"[^0-9,.\-]", "", raw)
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _purchase_unit_from_text(text: str) -> str:
    for unit in ["CAJA", "CJ", "BLISTER", "BLIST", "BOLSA", "PACK", "KIT", "SET", "TIRA", "ROLLO", "FRASCO AMPOLLA", "AMPOLLA", "FRASCO", "PAR"]:
        if re.search(rf"\b{re.escape(unit)}\b", text):
            return normalize_unit(unit)
    return "UNIDAD"


def _infer_content_unit(record: ProductAttributes, purchase_unit: str) -> str:
    if record.forma == "COMPRIMIDO":
        return "COMPRIMIDO"
    if record.forma == "CAPSULA":
        return "CAPSULA"
    if record.presentacion in {"AMPOLLA", "FRASCO AMPOLLA", "FRASCO"}:
        return record.presentacion
    if purchase_unit == "PAR":
        return "PAR"
    if record.volumen_tamano and "ML" in record.volumen_tamano:
        return "ML"
    return "UNIDAD"


def _comparable_unit(record: ProductAttributes, content_unit: str, purchase_unit: str) -> str:
    if content_unit:
        return content_unit
    if record.forma in {"COMPRIMIDO", "CAPSULA"}:
        return record.forma
    if record.presentacion in {"AMPOLLA", "FRASCO AMPOLLA", "FRASCO"}:
        return record.presentacion
    if purchase_unit == "PAR":
        return "PAR"
    return "UNIDAD"


def _medication_group(record: ProductAttributes) -> str:
    active = record.principio_activo_detectado
    dose = compact_amount(record.cantidad_total or record.concentracion)
    form = record.forma
    if not (active and dose and form):
        return ""
    return "|".join(["FARMACO", active, dose, form, record.via or "", _clinical_presentation(record)])


def _clinical_presentation(record: ProductAttributes) -> str:
    if record.presentacion and not re.match(r"^(CAJA|BLISTER) X", record.presentacion):
        return record.presentacion
    if record.forma == "SOLUCION INYECTABLE":
        return record.presentacion or "PRESENTACION INYECTABLE"
    return ""


def _medication_standard(record: ProductAttributes) -> str:
    parts = [
        record.principio_activo_detectado,
        compact_amount(record.cantidad_total or record.concentracion),
        record.forma or record.presentacion,
        _clinical_presentation(record),
    ]
    return " ".join(dict.fromkeys(part for part in parts if part))


def _normalization_confidence(record: ProductAttributes, factor: dict[str, Any], attrs: dict[str, Any], is_supply: bool) -> str:
    if factor["source"] == "INCIERTO":
        return "BAJA"
    if is_supply and any(not attrs.get(field) for field in _critical_supply_fields(attrs)):
        return "BAJA"
    if not is_supply and not (record.principio_activo_detectado and (record.cantidad_total or record.concentracion) and record.forma):
        return "BAJA"
    if factor["source"] == "INFERIDO":
        return "MEDIA"
    return "ALTA"


def _critical_supply_fields(attrs: dict[str, Any]) -> list[str]:
    family = attrs.get("familia", "")
    if family == "JERINGA":
        return ["capacidad", "conexion"]
    if family == "AGUJA":
        return ["calibre"]
    if family == "GUANTE":
        return ["material"]
    if family in {"APOSITO", "GASA"}:
        return ["medida"]
    return []


def _requires_clinical_validation(record: ProductAttributes, attrs: dict[str, Any], is_supply: bool) -> bool:
    if record.familia_detectada == "NO CLASIFICABLE / REQUIERE REVISION":
        return True
    if is_supply and attrs.get("compatibilidad"):
        return True
    return False


def _build_gaps(master: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in master.iterrows():
        rows.extend(_row_gaps(row))
    return pd.DataFrame(rows, columns=GAP_COLUMNS)


def _row_gaps(row: pd.Series) -> list[dict[str, Any]]:
    rows = []

    def add(gap: str, severity: str, impact: str, explanation: str, action: str) -> None:
        rows.append({
            "clinica": row["clinica"],
            "codigo_producto": row["codigo_producto"],
            "descripcion_original": row["descripcion_original"],
            "gap_detectado": gap,
            "severidad": severity,
            "impacto_potencial": impact,
            "explicacion": explanation,
            "accion_recomendada": action,
        })

    master_unit = row["unidad_medida_maestro"]
    detected_unit = row["unidad_compra_detectada"]
    factor = row["factor_conversion"]
    if master_unit == "UNIDAD" and detected_unit in PACK_UNITS and factor and factor > 1:
        add("Unidad de medida posiblemente erronea", "ALTA", "Precio unitario no comparable y riesgo de compra/stock distorsionado.", "La descripcion contiene presentacion multiple, pero el maestro declara unidad.", "Validar unidad de compra y registrar factor explicito.")
    if pd.isna(factor) or factor == "":
        add("Factor de conversion incierto", "ALTA", "No permite comparar precios entre clinicas.", "No se pudo derivar contenido confiable desde descripcion ni maestro.", "Completar contenido por envase o factor de conversion.")
    if row["contenido_detectado"] == "" or pd.isna(row["contenido_detectado"]):
        if detected_unit in PACK_UNITS or re.search(r"\b(CAJA|BLISTER|PACK|KIT|SET|BOLSA|TIRA|ROLLO)\b", str(row["descripcion_limpia"])):
            add("Contenido no detectado", "MEDIA", "Puede ocultar diferencias de precio por caja/unidad.", "La descripcion sugiere presentacion comercial, pero no trae cantidad clara.", "Solicitar contenido por envase al proveedor o area de abastecimiento.")
    if pd.isna(row["precio_comparable"]) or row["precio_comparable"] == "":
        add("Precio no comparable", "MEDIA", "No permite medir ahorro ni detectar outliers.", "Falta precio original o factor confiable.", "Completar precio y factor antes de comparar economicamente.")
    if row["nivel_confianza_normalizacion"] == "BAJA":
        add("Descripcion insuficiente", "MEDIA", "Riesgo de homologacion incorrecta.", "Faltan atributos clinicos o logisticos criticos.", "Enviar a revision manual antes de homologar.")
    calculated = row.get("__precio_calculado")
    price = row.get("__precio_num")
    if calculated is not None and price not in (None, 0) and not pd.isna(calculated) and not pd.isna(price):
        diff = abs(calculated - price) / abs(price)
        if diff > 0.3:
            add("Posible error de carga de precio", "ALTA", "Impacta comparaciones economicas y valorizacion.", f"Precio maestro difiere {diff:.0%} del calculado por monto/cantidad.", "Reconciliar precio_unitario, cantidad_comprada y monto_total.")
    if bool(row.get("__requiere_validacion_clinica")):
        add("Producto requiere validacion clinica", "ALTA", "Puede afectar compatibilidad o uso clinico.", "El producto tiene atributos clinicos insuficientes o menciona compatibilidad/equipo.", "Validar con QF, enfermeria, pabellon o area tecnica segun familia.")
    return rows


def _price_outlier_gaps(master: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    comparable = master.dropna(subset=["precio_comparable"])
    for _, group in comparable.groupby("__grupo_homologacion", dropna=False):
        prices = [float(v) for v in group["precio_comparable"].tolist() if v and not pd.isna(v)]
        if len(prices) < 3:
            continue
        med = median(prices)
        if med <= 0:
            continue
        for _, row in group.iterrows():
            price = row["precio_comparable"]
            if price > 2 * med:
                gap = "Precio outlier alto"
                severity = "ALTA"
                impact = "Posible oportunidad de ahorro o error de unidad/precio."
            elif price < 0.5 * med:
                gap = "Precio outlier bajo"
                severity = "MEDIA"
                impact = "Posible error de unidad, bonificacion o precio no comparable."
            else:
                continue
            rows.append({
                "clinica": row["clinica"],
                "codigo_producto": row["codigo_producto"],
                "descripcion_original": row["descripcion_original"],
                "gap_detectado": gap,
                "severidad": severity,
                "impacto_potencial": impact,
                "explicacion": f"Precio comparable {price:g} versus mediana de grupo {med:g}.",
                "accion_recomendada": "Validar factor/unidad y negociar si el precio es real.",
            })
    return pd.DataFrame(rows, columns=GAP_COLUMNS)


def _build_candidates(master: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    grouped = master[master["__grupo_homologacion"] != ""].groupby("__grupo_homologacion", dropna=False)
    for group_key, group in grouped:
        clinics = sorted(set(str(v) for v in group["clinica"].tolist() if str(v)))
        if len(group) < 2 or len(clinics) < 2:
            continue
        score = _candidate_score(group)
        validation = score < 90 or bool(group["__requiere_validacion_clinica"].any())
        standard = str(group["__descripcion_estandar"].dropna().iloc[0] or group_key)
        risks = _candidate_risks(group)
        rows.append({
            "grupo_homologacion_propuesto": group_key,
            "familia": str(group["familia_detectada"].mode().iloc[0]) if not group["familia_detectada"].mode().empty else "",
            "descripcion_estandar_holding": standard,
            "skus_incluidos": "; ".join(f"{r.clinica}|{r.codigo_producto}" for r in group.itertuples()),
            "clinicas_incluidas": "; ".join(clinics),
            "score_homologacion": score,
            "nivel_confianza": _score_confidence(score),
            "fundamento": _candidate_foundation(group, score),
            "riesgos": risks,
            "requiere_validacion_clinica": validation,
        })
    return pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)


def _candidate_score(group: pd.DataFrame) -> int:
    score = 30
    score += 25 if _has_comparable_critical_attribute(group) else 0
    score += 20 if _has_clinical_type(group) else 0
    score += 10 if group["factor_conversion"].notna().all() else 0
    score += 5
    prices = [float(v) for v in group["precio_comparable"].tolist() if v and not pd.isna(v)]
    if len(prices) >= 2 and min(prices) > 0 and max(prices) <= 2 * median(prices):
        score += 10
    if group["nivel_confianza_normalizacion"].eq("BAJA").any():
        score = min(score, 74)
    if bool(group["__requiere_validacion_clinica"].any()):
        score = min(score, 74)
    return min(score, 100)


def _has_comparable_critical_attribute(group: pd.DataFrame) -> bool:
    text = " ".join(group["atributos_clinicos_extraidos"].fillna("").astype(str).tolist())
    return any(term in text for term in ["concentracion", "capacidad", "calibre", "medida", "material", "esterilidad"])


def _has_clinical_type(group: pd.DataFrame) -> bool:
    values = [v for v in group["familia_detectada"].fillna("").tolist() if v]
    return bool(values)


def _candidate_risks(group: pd.DataFrame) -> str:
    risks = []
    if group["nivel_confianza_normalizacion"].eq("BAJA").any():
        risks.append("Atributos incompletos en uno o mas SKU.")
    if group["factor_conversion"].isna().any():
        risks.append("Factor no confiable en parte del grupo.")
    if bool(group["__requiere_validacion_clinica"].any()):
        risks.append("Requiere validacion clinica/tecnica por compatibilidad o descripcion insuficiente.")
    return "; ".join(risks) if risks else "Sin riesgos criticos detectados por reglas automatizadas."


def _candidate_foundation(group: pd.DataFrame, score: int) -> str:
    standard = str(group["__descripcion_estandar"].dropna().iloc[0])
    return f"Se propone agrupar bajo {standard}; los atributos tecnicos extraidos comparten familia y llave comparable. Score {score}/100."


def _score_confidence(score: int) -> str:
    if score >= 90:
        return "ALTA"
    if score >= 75:
        return "MEDIA"
    if score >= 60:
        return "BAJA - VALIDACION CLINICA"
    return "NO HOMOLOGAR AUTOMATICAMENTE"


def _build_opportunities(master: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    comparable = master.dropna(subset=["precio_comparable"])
    for group_key, group in comparable.groupby("__grupo_homologacion", dropna=False):
        if not group_key or len(group) < 2 or group["clinica"].nunique() < 2:
            continue
        prices = [float(v) for v in group["precio_comparable"].tolist() if v and not pd.isna(v)]
        if len(prices) < 2:
            continue
        min_price = min(prices)
        max_price = max(prices)
        avg_price = sum(prices) / len(prices)
        expensive = group.loc[group["precio_comparable"].idxmax()]
        spend = _group_spend(group)
        saving = _estimated_saving(group, min_price)
        assumptions = "Ahorro estimado contra menor precio comparable interno; requiere validar factor, volumen y condiciones comerciales."
        if saving == 0:
            assumptions += " Sin cantidad/monto suficiente para estimar ahorro monetario."
        rows.append({
            "grupo_homologacion": group_key,
            "gasto_total": spend,
            "precio_minimo_comparable": min_price,
            "precio_promedio_comparable": avg_price,
            "precio_maximo_comparable": max_price,
            "clinica_mas_cara": expensive["clinica"],
            "ahorro_potencial_estimado": saving,
            "supuestos": assumptions,
            "prioridad": _opportunity_priority(saving, min_price, max_price),
        })
    rows.sort(key=lambda row: (row["prioridad"] != "ALTA", -row["ahorro_potencial_estimado"], -row["precio_maximo_comparable"]))
    return pd.DataFrame(rows, columns=OPPORTUNITY_COLUMNS)


def _group_spend(group: pd.DataFrame) -> float:
    spend = 0.0
    for _, row in group.iterrows():
        amount = row.get("__monto_num")
        if amount and not pd.isna(amount):
            spend += float(amount)
            continue
        price = row.get("__precio_num")
        quantity = row.get("__cantidad_num")
        if price and quantity and not pd.isna(price) and not pd.isna(quantity):
            spend += float(price) * float(quantity)
    return round(spend, 2)


def _estimated_saving(group: pd.DataFrame, min_price: float) -> float:
    saving = 0.0
    for _, row in group.iterrows():
        price = row.get("precio_comparable")
        quantity = row.get("__cantidad_num")
        factor = row.get("factor_conversion")
        if price and quantity and factor and not pd.isna(price) and not pd.isna(quantity) and not pd.isna(factor):
            saving += max(float(price) - min_price, 0.0) * float(quantity) * float(factor)
    return round(saving, 2)


def _opportunity_priority(saving: float, min_price: float, max_price: float) -> str:
    spread = max_price / min_price if min_price else 0
    if saving >= 10_000_000 or spread >= 2:
        return "ALTA"
    if saving >= 1_000_000 or spread >= 1.3:
        return "MEDIA"
    return "BAJA"


def _build_clinical_validation(master: pd.DataFrame, gaps: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    clinical_gaps = gaps[gaps["gap_detectado"].isin(["Producto requiere validacion clinica", "Descripcion insuficiente", "Diferencia tecnica critica"])] if not gaps.empty else pd.DataFrame()
    for _, row in clinical_gaps.iterrows():
        rows.append({
            "producto": row["descripcion_original"],
            "skus_afectados": f"{row['clinica']}|{row['codigo_producto']}",
            "motivo_validacion": row["gap_detectado"],
            "riesgo_clinico": row["impacto_potencial"],
            "pregunta_para_el_area_clinica": "Confirma si los atributos tecnicos extraidos permiten sustitucion operacional segura?",
            "responsable_sugerido": "QF / Enfermeria / Pabellon / Area tecnica segun familia",
        })
    for _, row in candidates.iterrows():
        if bool(row["requiere_validacion_clinica"]):
            rows.append({
                "producto": row["descripcion_estandar_holding"],
                "skus_afectados": row["skus_incluidos"],
                "motivo_validacion": "Candidato de homologacion con confianza menor a alta o riesgo detectado.",
                "riesgo_clinico": row["riesgos"],
                "pregunta_para_el_area_clinica": "Estos SKU satisfacen la misma necesidad clinica sin cambiar calibre, talla, material, esterilidad, compatibilidad ni uso?",
                "responsable_sugerido": "QF / Usuario clinico experto / Abastecimiento",
            })
    return pd.DataFrame(rows, columns=CLINICAL_COLUMNS).drop_duplicates()


def _build_analytics_summary(master: pd.DataFrame, gaps: pd.DataFrame, candidates: pd.DataFrame, opportunities: pd.DataFrame, clinical: pd.DataFrame) -> pd.DataFrame:
    rows = [
        ("Resumen ejecutivo", "total_registros", len(master)),
        ("Resumen ejecutivo", "productos_con_precio_comparable", int(master["precio_comparable"].notna().sum()) if "precio_comparable" in master else 0),
        ("Resumen ejecutivo", "gaps_detectados", len(gaps)),
        ("Resumen ejecutivo", "candidatos_homologacion", len(candidates)),
        ("Resumen ejecutivo", "oportunidades_economicas", len(opportunities)),
        ("Resumen ejecutivo", "casos_validacion_clinica", len(clinical)),
    ]
    if not gaps.empty:
        for index, (gap, count) in enumerate(Counter(gaps["gap_detectado"].tolist()).most_common(10), start=1):
            rows.append(("Top 10 errores maestros mas criticos", str(index), f"{gap}: {count} casos"))
    if not opportunities.empty:
        top = opportunities.sort_values("ahorro_potencial_estimado", ascending=False).head(10)
        for index, row in enumerate(top.itertuples(), start=1):
            rows.append(("Top 10 oportunidades de ahorro", str(index), f"{row.grupo_homologacion}: ahorro estimado {row.ahorro_potencial_estimado}"))
    if not clinical.empty:
        for index, row in enumerate(clinical.head(10).itertuples(), start=1):
            rows.append(("Productos que no deben homologarse automaticamente", str(index), f"{row.producto}: {row.motivo_validacion}"))
    recommendations = [
        "Exigir unidad de compra y factor explicito cuando la descripcion contenga caja, blister, pack, kit o bolsa.",
        "Separar producto tecnico madre de variante logistica y precio.",
        "Bloquear alta automatica si faltan concentracion, calibre, talla, material, esterilidad o compatibilidad segun familia.",
        "Validar precio_unitario contra monto_total/cantidad_comprada en cada carga.",
    ]
    for index, item in enumerate(recommendations, start=1):
        rows.append(("Recomendaciones para limpiar el maestro", str(index), item))
    limitations = [
        "El precio comparable solo es confiable cuando el factor de conversion fue detectado o informado.",
        "Las equivalencias clinicas de insumos especializados requieren validacion del usuario clinico.",
        "Sin cantidad_comprada o monto_total, el ahorro monetario queda como referencia de brecha de precio.",
    ]
    for index, item in enumerate(limitations, start=1):
        rows.append(("Limitaciones del analisis", str(index), item))
    next_steps = [
        "Completar factores y unidades en los SKU con gaps de severidad alta.",
        "Revisar primero oportunidades con prioridad alta y grupos con precio outlier alto.",
        "Convertir reglas confirmadas por QF/clinica en validaciones de carga obligatorias.",
    ]
    for index, item in enumerate(next_steps, start=1):
        rows.append(("Proximos pasos sugeridos", str(index), item))
    return pd.DataFrame(rows, columns=["seccion", "item", "detalle"])
