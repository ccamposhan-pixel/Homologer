from __future__ import annotations

from dataclasses import dataclass


FAMILIES = [
    "MEDICAMENTO",
    "SOLUCIONES PARENTERALES / SUEROS",
    "INSUMO MEDICO",
    "DISPOSITIVO MEDICO",
    "MATERIAL QUIRURGICO",
    "SUTURA",
    "IMPLANTE",
    "REACTIVO / LABORATORIO",
    "MATERIAL ADMINISTRATIVO O GENERAL",
    "NO CLASIFICABLE / REQUIERE REVISION",
]

ALERT_TERMS = [
    "XR",
    "SR",
    "CR",
    "LP",
    "RETARD",
    "FORTE",
    "PLUS",
    "COMPUESTO",
    "PEDIATRICO",
    "ADULTO",
    "INYECTABLE",
    "POLVO",
    "SOLUCION",
    "SUSPENSION",
    "GOTAS",
    "JARABE",
    "FRASCO AMPOLLA",
    "AMPOLLA",
    "JERINGA PRELLENADA",
    "LIOFILIZADO",
    "RECONSTITUIR",
    "ESTERIL",
    "NO ESTERIL",
    "REFRIGERADO",
    "CONTROLADO",
    "PSICOTROPICO",
    "ESTUPEFACIENTE",
    "LONGLIFE",
    "BIOEQUIVALENTE",
    "CAJA X",
    "BLISTER X",
    "LUER LOCK",
    "LUER SLIP",
]

UNIT_SYNONYMS = {
    "MICROGRAMOS": "MCG",
    "MICROGRAMO": "MCG",
    "UG": "MCG",
    "µG": "MCG",
    "GR": "G",
    "GRAMOS": "G",
    "GRAMO": "G",
    "MILIGRAMOS": "MG",
    "MILIGRAMO": "MG",
    "MGML": "MG/ML",
    "MG/ML": "MG/ML",
    "UI/ML": "UI/ML",
    "U.I.": "UI",
    "U I": "UI",
    "UNIDADES INTERNACIONALES": "UI",
    "LITRO": "L",
    "LITROS": "L",
    "MILILITROS": "ML",
    "MILILITRO": "ML",
    "CC": "ML",
}

FORM_SYNONYMS = {
    "COMPRIMIDO": ["COMP", "COMPR", "COMPRIMIDO", "COMPRIMIDOS", "TAB", "TABLETA", "TABLETAS"],
    "CAPSULA": ["CAP", "CAPS", "CAPSULA", "CAPSULAS"],
    "INYECTABLE": ["SOL INY", "INY", "INYECTABLE", "INJ", "SOLUCION INYECTABLE"],
    "AMPOLLA": ["AMP", "AMPOLLA", "AMPOLLAS"],
    "FRASCO AMPOLLA": ["FA", "F A", "FRASCO AMPOLLA", "VIAL"],
    "JERINGA PRELLENADA": ["JERINGA PRELLENADA", "JP"],
    "BOLSA": ["BOLSA"],
    "FRASCO": ["FCO", "FRASCO"],
    "JARABE": ["JARABE"],
    "GOTAS": ["GOTAS"],
    "SUSPENSION": ["SUSP", "SUSPENSION"],
    "CREMA": ["CREMA"],
    "GEL": ["GEL"],
    "UNGUENTO": ["UNGUENTO", "POMADA"],
}

ROUTE_SYNONYMS = {
    "IV": ["EV", "IV", "ENDOVENOSO", "ENDOVENOSA", "INTRAVENOSO", "INTRAVENOSA"],
    "IM": ["IM", "INTRAMUSCULAR"],
    "SC": ["SC", "SUBCUTANEO", "SUBCUTANEA", "SUBCUT"],
    "ORAL": ["VO", "ORAL", "VIA ORAL"],
    "TOPICA": ["TOP", "TOPICO", "TOPICA"],
    "OFTALMICA": ["OFT", "OFTALMICO", "OFTALMICA"],
    "INHALATORIA": ["INH", "INHALATORIA", "INHALACION"],
}

PRESENTATION_SYNONYMS = {
    "CAJA X": ["CJ X", "CAJA X", "CAJA", "X"],
    "BLISTER X": ["BLIST", "BLISTER X", "BLISTER"],
    "FRASCO AMPOLLA": ["FA", "FRASCO AMPOLLA"],
    "AMPOLLA": ["AMP", "AMPOLLA"],
    "FRASCO": ["FCO", "FRASCO"],
}

FAMILY_KEYWORDS = {
    "SUTURA": ["SUTURA", "VICRYL", "PROLENE", "SEDA", "CATGUT", "NYLON", "MONOCRYL"],
    "IMPLANTE": ["IMPLANTE", "PROTESIS", "STENT", "TORNILLO", "PLACA TITANIO"],
    "REACTIVO / LABORATORIO": ["REACTIVO", "TEST", "KIT", "CALIBRADOR", "CONTROL LAB", "TUBO EDTA"],
    "SOLUCIONES PARENTERALES / SUEROS": ["SUERO", "CLORURO SODIO", "RINGER", "GLUCOSA", "DEXTROSA", "BOLSA"],
    "MATERIAL QUIRURGICO": ["BISTURI", "CAMPO", "GASA", "COMPRESA", "ELECTRODO", "CANULA"],
    "DISPOSITIVO MEDICO": ["CATETER", "SONDA", "JERINGA", "AGUJA", "EQUIPO", "LLAVE 3 VIAS", "LUER"],
    "INSUMO MEDICO": ["GUANTE", "MASCARILLA", "APOSITO", "TAPON", "VENDA", "BAJADA"],
    "MATERIAL ADMINISTRATIVO O GENERAL": ["PAPEL", "LAPIZ", "CARPETA", "TONER", "ARCHIVADOR"],
}


@dataclass(frozen=True)
class RuleDescription:
    nombre: str
    criterio: str
    accion: str


RULE_DESCRIPTIONS = [
    RuleDescription("Principio activo confirmado", "Si no hay componente o principio activo confirmado", "REQUIERE REVISION QF"),
    RuleDescription("Concentracion", "Concentraciones distintas o una concentracion critica faltante", "NO HOMOLOGAR o DESCRIPCION INSUFICIENTE"),
    RuleDescription("Forma farmaceutica/tipo", "Forma, tipo de insumo o estado fisico distinto", "NO HOMOLOGAR"),
    RuleDescription("Via", "Via de administracion distinta", "NO HOMOLOGAR"),
    RuleDescription("Presentacion/factor", "Caja, unidad gestionada, volumen o factor diferente", "REQUIERE VALIDACION LOGISTICA"),
    RuleDescription("Alertas", "Terminos como XR, RETARD, PEDIATRICO, ESTERIL, LIOFILIZADO", "Bloquean homologacion automatica o envian a revision"),
    RuleDescription("Trazabilidad", "Toda decision debe tener fuentes, motivo y atributos comparados", "Registrar fuente y motivo tecnico"),
]
