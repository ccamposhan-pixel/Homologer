from __future__ import annotations

import argparse
import json
from pathlib import Path

from clinical_homologation.pipeline import export_workbook, process_products


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Homologacion meticulosa de productos clinicos, farmaceuticos e insumos medicos en Chile.")
    parser.add_argument("--productos", required=True, help="Archivo Excel o CSV con la base de productos.")
    parser.add_argument("--vademecum", help="Archivo Excel o CSV con vademecum local/institucional.")
    parser.add_argument("--historico", action="append", default=[], help="Archivo historico de homologacion. Puede repetirse.")
    parser.add_argument("--salida", default="outputs/homologacion_productos.xlsx", help="Ruta del Excel final.")
    parser.add_argument("--mapping-json", help="JSON con mapeo de columnas de productos.")
    parser.add_argument("--vademecum-mapping-json", help="JSON con mapeo de columnas del vademecum.")
    return parser.parse_args()


def load_mapping(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def main() -> None:
    args = parse_args()
    frames = process_products(
        args.productos,
        args.vademecum,
        args.historico,
        column_mapping=load_mapping(args.mapping_json),
        vademecum_mapping=load_mapping(args.vademecum_mapping_json),
    )
    output = export_workbook(frames, args.salida)
    print(f"Excel generado: {output.resolve()}")


if __name__ == "__main__":
    main()
