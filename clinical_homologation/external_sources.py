from __future__ import annotations

from urllib.parse import quote_plus

from .models import ProductAttributes


ISP_REFERENCE_URL = "https://registrosanitario.ispch.gob.cl/"
VADEMECUM_CHILE_URL = "https://www.vademecum.es/chile/cl/alfa"


def isp_consultation_note(record: ProductAttributes) -> str:
    if record.familia_detectada != "MEDICAMENTO":
        return ""
    if record.registro_sanitario:
        return f"Validar registro sanitario {record.registro_sanitario} en ISP/ANAMED: {ISP_REFERENCE_URL}"
    term = record.principio_activo_detectado or record.marca_detectada or record.descripcion_normalizada
    return f"Validar medicamento en ISP/ANAMED por nombre, principio activo, titular o registro: {ISP_REFERENCE_URL} ({term})"


def secondary_vademecum_note(record: ProductAttributes) -> str:
    if record.familia_detectada != "MEDICAMENTO" or record.fuente_enriquecimiento == "VADEMECUM_USUARIO":
        return ""
    term = quote_plus(record.marca_detectada or record.descripcion_normalizada)
    return f"Fuente secundaria sugerida para enriquecer marca si falta activo: {VADEMECUM_CHILE_URL} (buscar: {term})"
