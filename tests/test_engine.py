from __future__ import annotations

import pandas as pd

from clinical_homologation.matching import compare_pair
from clinical_homologation.pipeline import build_workbook_frames, process_products
from clinical_homologation.models import ProductAttributes


def med(code: str, concentration: str = "500 MG", form: str = "COMPRIMIDO", route: str = "ORAL", presentation: str = "CAJA X 10") -> ProductAttributes:
    return ProductAttributes(
        codigo_origen=code,
        descripcion_origen=f"PARACETAMOL {concentration} {form}",
        descripcion_normalizada=f"PARACETAMOL {concentration} {form}",
        familia_detectada="MEDICAMENTO",
        principio_activo_detectado="PARACETAMOL",
        concentracion=concentration,
        forma=form,
        via=route,
        presentacion=presentation,
        unidad_consumo="UN",
        factor_conversion="10",
        fuente_enriquecimiento="VADEMECUM_USUARIO",
    )


def test_blocks_different_concentration() -> None:
    result = compare_pair(med("A", "500 MG"), med("B", "750 MG"))
    assert result.estado_homologacion == "NO HOMOLOGAR"
    assert "atributos criticos" in result.motivo_decision


def test_logistic_validation_for_different_presentation() -> None:
    result = compare_pair(med("A", presentation="CAJA X 10"), med("B", presentation="CAJA X 20"))
    assert result.estado_homologacion == "REQUIERE VALIDACION LOGISTICA"
    assert result.requiere_validacion_logistica is True


def test_local_vademecum_enriches_brand(tmp_path) -> None:
    products = tmp_path / "productos.xlsx"
    vademecum = tmp_path / "vademecum.xlsx"
    pd.DataFrame([{"codigo": "1", "descripcion": "TAPSIN 500 MG COMP", "unidad de consumo": "UN"}]).to_excel(products, index=False)
    pd.DataFrame([{"marca": "TAPSIN", "principio activo": "PARACETAMOL", "concentracion": "500 MG", "forma": "COMPRIMIDO"}]).to_excel(vademecum, index=False)
    frames = process_products(products, vademecum)
    row = frames["HOMOLOGACION_MULTICLINICA"].iloc[0]
    assert row["principio_activo_detectado"] == "PARACETAMOL"
    assert row["codigo_madre"] == "PARACETAMOL 500MG COMPRIMIDO"


def test_workbook_contains_required_sheets() -> None:
    record = med("A")
    record.cantidad_total = "500 MG"
    frames = build_workbook_frames([record], [], pd.DataFrame())
    assert set(frames) == {
        "CODIGOS_MADRE",
        "HOMOLOGACION_MULTICLINICA",
        "REVISION_QF",
        "REVISION_LOGISTICA",
        "NO_HOMOLOGAR",
        "DICCIONARIO_MARCAS",
        "ALERTAS",
        "MATRIZ_HOMOLOGADOS_CLINICAS",
        "RESUMEN",
        "MAESTRO_NORMALIZADO",
        "GAPS_DETECTADOS",
        "CANDIDATOS_HOMOLOGACION",
        "OPORTUNIDADES_ECONOMICAS",
        "VALIDACION_CLINICA",
        "RESUMEN_ANALITICO",
        "PATRONES_HISTORICOS_APRENDIDOS",
        "DICCIONARIO_INSUMOS",
        "CONFLICTOS_HISTORICOS",
        "HOMOLOGACION_INSUMOS",
        "VARIANTES_LOGISTICAS",
    }


def test_sugammadex_multiclinic_mother_code_groups_volume_missing(tmp_path) -> None:
    products = tmp_path / "productos.xlsx"
    pd.DataFrame(
        [
            {"clinica": "CALAMA", "codigo": "40005745", "descripcion": "SUGAMMADEX 200MG. FCO. AMP."},
            {"clinica": "CHILLAN", "codigo": "40006860", "descripcion": "SUGAMMADEX 200MG/2ML FCO. AMP"},
            {"clinica": "CONCEPCION", "codigo": "4000974", "descripcion": "REBRIVE SOL. 200MG (SUGAMMADEX)"},
            {"clinica": "CONCEPCION", "codigo": "40005745", "descripcion": "SUGAMMADEX 200MG FRASCO AMPOLLA (BRIDION)"},
            {"clinica": "CONCEPCION", "codigo": "80000013", "descripcion": "SUGADION SOLUCION INYECTABLE 200mg/2mL (SUGAMMADEX)"},
            {"clinica": "PUERTO MONTT", "codigo": "40005745", "descripcion": "(SUGAMMADEX) 200MG. FCO. AMP."},
        ]
    ).to_excel(products, index=False)
    frames = process_products(products)
    mothers = frames["CODIGOS_MADRE"]
    homologation = frames["HOMOLOGACION_MULTICLINICA"]
    assert len(mothers) == 1
    assert mothers.iloc[0]["codigo_madre"] == "SUGAMMADEX 200MG FRASCO AMPOLLA"
    assert len(homologation) == 6
    assert "HOMOLOGADO CON DATOS INCOMPLETOS" in set(homologation["estado_homologacion"])


def test_different_informed_volumes_go_to_qf_review(tmp_path) -> None:
    products = tmp_path / "productos.xlsx"
    pd.DataFrame(
        [
            {"clinica": "A", "codigo": "1", "descripcion": "SUGAMMADEX 200MG/2ML FCO AMP"},
            {"clinica": "B", "codigo": "2", "descripcion": "SUGAMMADEX 200MG/5ML FCO AMP"},
        ]
    ).to_excel(products, index=False)
    frames = process_products(products)
    assert frames["CODIGOS_MADRE"].empty
    assert len(frames["REVISION_QF"]) == 2
    assert len(frames["NO_HOMOLOGAR"]) == 2


def test_insumo_jeringa_groups_by_technical_attributes_and_logistics(tmp_path) -> None:
    products = tmp_path / "insumos.xlsx"
    pd.DataFrame(
        [
            {"clinica": "A", "codigo": "1", "descripcion": "JERINGA 10 ML LUER LOCK ESTERIL CAJA X 100", "unidad de compra": "CJ", "factor": "100"},
            {"clinica": "B", "codigo": "2", "descripcion": "JERINGA DESECHABLE 10CC LL EST", "unidad de compra": "UN", "factor": "1"},
            {"clinica": "C", "codigo": "3", "descripcion": "JERINGA 10ML LUER SLIP ESTERIL"},
        ]
    ).to_excel(products, index=False)
    frames = process_products(products)
    insumos = frames["HOMOLOGACION_INSUMOS"]
    assert "JERINGA 10ML LUER LOCK ESTERIL" in set(insumos["codigo_madre"])
    assert "JERINGA 10ML LUER SLIP ESTERIL" in set(insumos["codigo_madre"])
    assert len(frames["VARIANTES_LOGISTICAS"]) >= 2


def test_historical_learning_outputs_patterns_and_conflicts(tmp_path) -> None:
    current = tmp_path / "current.xlsx"
    historical = tmp_path / "historico.xlsx"
    pd.DataFrame([{"clinica": "A", "codigo": "1", "descripcion": "JERINGA 10ML LUER LOCK ESTERIL"}]).to_excel(current, index=False)
    pd.DataFrame(
        [
            {"CODIGO SAP": "H1", "DESCRIPCION MATERIAL SAP": "JERINGA 10 ML", "CODIGO MEDIC": "1", "DESCRIPCION PRODUCTO MEDIC": "JERINGA 10 ML LUER LOCK"},
            {"CODIGO SAP": "H1", "DESCRIPCION MATERIAL SAP": "JERINGA 10 ML", "CODIGO MEDIC": "2", "DESCRIPCION PRODUCTO MEDIC": "JERINGA 10 ML LUER SLIP"},
        ]
    ).to_excel(historical, index=False)
    frames = process_products(current, historical_paths=[historical])
    assert len(frames["PATRONES_HISTORICOS_APRENDIDOS"]) == 2
    assert not frames["DICCIONARIO_INSUMOS"].empty
    assert len(frames["CONFLICTOS_HISTORICOS"]) >= 1


def test_analytics_detects_pack_factor_and_master_unit_gap(tmp_path) -> None:
    products = tmp_path / "compras.xlsx"
    pd.DataFrame(
        [
            {
                "clinica": "A",
                "codigo": "1",
                "descripcion": "PARACETAMOL 500 MG CAJA X30 COMPRIMIDOS",
                "unidad medida": "UN",
                "precio unitario": 3000,
                "cantidad comprada": 10,
                "monto total": 30000,
            },
            {
                "clinica": "B",
                "codigo": "2",
                "descripcion": "PARACETAMOL 500MG COMP CJ X 30",
                "unidad medida": "CAJA",
                "precio unitario": 4500,
                "cantidad comprada": 5,
                "monto total": 22500,
            },
        ]
    ).to_excel(products, index=False)
    frames = process_products(products)
    master = frames["MAESTRO_NORMALIZADO"]
    assert set(master["factor_conversion"]) == {30}
    assert set(master["unidad_comparable_recomendada"]) == {"COMPRIMIDO"}
    gaps = frames["GAPS_DETECTADOS"]
    assert "Unidad de medida posiblemente erronea" in set(gaps["gap_detectado"])
    candidates = frames["CANDIDATOS_HOMOLOGACION"]
    assert len(candidates) == 1
