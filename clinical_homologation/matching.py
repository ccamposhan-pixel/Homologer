from __future__ import annotations

from dataclasses import asdict
from collections import Counter, defaultdict
from typing import Callable

from rapidfuzz import fuzz

from .models import HomologationResult, ProductAttributes


CRITICAL_FIELDS = ["principio_activo_detectado", "concentracion", "forma", "via"]
LOGISTIC_FIELDS = ["presentacion", "unidad_compra", "unidad_consumo", "factor_conversion", "volumen_tamano"]


def score_pair(left: ProductAttributes, right: ProductAttributes) -> int:
    score = 0
    score += 30 if left.principio_activo_detectado and left.principio_activo_detectado == right.principio_activo_detectado else 0
    score += 25 if left.concentracion and left.concentracion == right.concentracion else 0
    score += 20 if left.forma and left.forma == right.forma else 0
    score += 10 if left.via and left.via == right.via else 0
    score += 10 if left.presentacion and left.presentacion == right.presentacion else 0
    score += 5 if (left.unidad_consumo or left.unidad_compra) and (left.unidad_consumo or left.unidad_compra) == (right.unidad_consumo or right.unidad_compra) else 0
    return score


def compare_pair(left: ProductAttributes, right: ProductAttributes) -> HomologationResult:
    result = HomologationResult(
        codigo_origen=left.codigo_origen,
        descripcion_origen=left.descripcion_origen,
        codigo_homologado_sugerido=right.codigo_origen,
        descripcion_homologada_sugerida=right.descripcion_origen,
        fuente_validacion="; ".join(
            source
            for source in [
                "ATRIBUTOS_NORMALIZADOS",
                left.fuente_enriquecimiento or "SIN_FUENTE_EXTERNA_CONFIRMATORIA",
                left.fuente_primaria_requerida,
            ]
            if source
        ),
    )
    differences: list[str] = []
    for field in CRITICAL_FIELDS:
        if getattr(left, field) != getattr(right, field):
            differences.append(f"{field}: '{getattr(left, field)}' vs '{getattr(right, field)}'")
    for field in LOGISTIC_FIELDS:
        if getattr(left, field) != getattr(right, field):
            differences.append(f"{field}: '{getattr(left, field)}' vs '{getattr(right, field)}'")
    result.score = score_pair(left, right)
    result.diferencias_detectadas = "; ".join(differences)

    if left.familia_detectada != right.familia_detectada:
        result.estado_homologacion = "NO HOMOLOGAR"
        result.motivo_decision = "Familia de producto distinta; no se mezcla medicamento, insumo u otra familia."
        result.requiere_validacion_clinica = True
    elif not left.principio_activo_detectado and left.familia_detectada == "MEDICAMENTO":
        result.estado_homologacion = "REQUIERE REVISION QF"
        result.motivo_decision = "Principio activo no confirmado por vademecum local o fuente validada."
        result.requiere_qf = True
    elif any(getattr(left, field) != getattr(right, field) for field in CRITICAL_FIELDS):
        result.estado_homologacion = "NO HOMOLOGAR"
        result.motivo_decision = "Difieren atributos criticos: principio activo/componente, concentracion, forma o via."
        result.requiere_qf = left.familia_detectada == "MEDICAMENTO"
    elif any(getattr(left, field) != getattr(right, field) for field in LOGISTIC_FIELDS):
        result.estado_homologacion = "REQUIERE VALIDACION LOGISTICA"
        result.motivo_decision = "Coinciden atributos clinicos, pero cambia presentacion, volumen, unidad o factor."
        result.requiere_validacion_logistica = True
    elif left.alertas or right.alertas:
        result.estado_homologacion = "REQUIERE REVISION QF" if left.familia_detectada == "MEDICAMENTO" else "REQUIERE VALIDACION CLINICA"
        result.motivo_decision = "Existen terminos de alerta que bloquean homologacion automatica: " + ", ".join(sorted(set(left.alertas + right.alertas)))
        result.requiere_qf = left.familia_detectada == "MEDICAMENTO"
        result.requiere_validacion_clinica = left.familia_detectada != "MEDICAMENTO"
    else:
        result.estado_homologacion = "HOMOLOGADO EXACTO"
        result.motivo_decision = "Coinciden componente/principio activo, concentracion, forma, via, presentacion y unidad gestionada."
        result.nivel_confianza = "ALTO"

    if result.nivel_confianza != "ALTO":
        result.nivel_confianza = "MEDIO" if result.score >= 75 and result.estado_homologacion != "NO HOMOLOGAR" else "BAJO"
    return result


def proposal_preview(result: HomologationResult) -> dict[str, object]:
    data = asdict(result)
    return {
        "codigo": data["codigo_origen"],
        "descripcion": data["descripcion_origen"],
        "sugerido": data["codigo_homologado_sugerido"],
        "estado": data["estado_homologacion"],
        "score": data["score"],
        "motivo": data["motivo_decision"],
    }


def propose_homologations(
    records: list[ProductAttributes],
    progress: Callable[[int, str, dict[str, object] | None], None] | None = None,
) -> list[HomologationResult]:
    grouped: dict[tuple[str, ...], list[ProductAttributes]] = defaultdict(list)
    for record in records:
        grouped[record.technical_key()].append(record)

    proposals: list[HomologationResult] = []
    total = max(len(records), 1)
    for index, record in enumerate(records, start=1):
        if record.familia_detectada == "MEDICAMENTO" and not record.principio_activo_detectado:
            result = HomologationResult(
                codigo_origen=record.codigo_origen,
                descripcion_origen=record.descripcion_origen,
                estado_homologacion="REQUIERE REVISION QF",
                motivo_decision="Medicamento sin principio activo confirmado; no se homologa por similitud textual.",
                requiere_qf=True,
                fuente_validacion="; ".join(filter(None, [record.fuente_enriquecimiento or "SIN_FUENTE_CONFIRMATORIA", record.fuente_primaria_requerida])),
                nivel_confianza="BAJO",
            )
            proposals.append(result)
            _report_proposal_progress(progress, index, total, result)
            continue

        exact_group = [item for item in grouped[record.technical_key()] if item.codigo_origen != record.codigo_origen]
        if exact_group:
            result = compare_pair(record, exact_group[0])
            proposals.append(result)
            _report_proposal_progress(progress, index, total, result)
            continue

        candidate = nearest_candidate(record, records)
        if candidate is None:
            result = insufficient_result(record)
        else:
            result = compare_pair(record, candidate)
        proposals.append(result)
        _report_proposal_progress(progress, index, total, result)
    return proposals


def _report_proposal_progress(
    progress: Callable[[int, str, dict[str, object] | None], None] | None,
    index: int,
    total: int,
    result: HomologationResult,
) -> None:
    if not progress:
        return
    percent = 60 + int(22 * index / total)
    progress(percent, f"Generando propuestas de homologacion ({index}/{total})", {"proposal": proposal_preview(result)})


def nearest_candidate(record: ProductAttributes, records: list[ProductAttributes]) -> ProductAttributes | None:
    candidates = [candidate for candidate in records if candidate.codigo_origen != record.codigo_origen and candidate.familia_detectada == record.familia_detectada]
    if not candidates:
        return None
    def rank(candidate: ProductAttributes) -> tuple[int, int]:
        return (score_pair(record, candidate), fuzz.token_sort_ratio(record.descripcion_normalizada, candidate.descripcion_normalizada))
    best = max(candidates, key=rank)
    if rank(best)[0] == 0 and rank(best)[1] < 80:
        return None
    return best


def insufficient_result(record: ProductAttributes) -> HomologationResult:
    missing = []
    for field in ["principio_activo_detectado", "concentracion", "forma", "via"]:
        if not getattr(record, field):
            missing.append(field)
    status = "DESCRIPCION INSUFICIENTE" if missing else "POSIBLE DUPLICADO"
    return HomologationResult(
        codigo_origen=record.codigo_origen,
        descripcion_origen=record.descripcion_origen,
        estado_homologacion=status,
        motivo_decision="Faltan datos criticos para explicar equivalencia tecnica: " + ", ".join(missing) if missing else "No hay candidato con equivalencia tecnica completa.",
        fuente_validacion="; ".join(filter(None, [record.fuente_enriquecimiento or "SIN_FUENTE_CONFIRMATORIA", record.fuente_primaria_requerida])),
        nivel_confianza="BAJO",
    )


def top_counts(values: list[str], limit: int = 20) -> list[tuple[str, int]]:
    return Counter(value for value in values if value).most_common(limit)
