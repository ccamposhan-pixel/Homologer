from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProductAttributes:
    clinica: str = ""
    codigo_origen: str = ""
    descripcion_origen: str = ""
    descripcion_normalizada: str = ""
    familia_detectada: str = ""
    marca_detectada: str = ""
    principio_activo_detectado: str = ""
    cantidad_total: str = ""
    concentracion: str = ""
    forma: str = ""
    via: str = ""
    volumen_tamano: str = ""
    presentacion: str = ""
    unidad_compra: str = ""
    unidad_consumo: str = ""
    unidad_medida_maestro: str = ""
    factor_conversion: str = ""
    precio: str = ""
    cantidad_comprada: str = ""
    monto_total: str = ""
    subcategoria: str = ""
    fecha_compra: str = ""
    bodega: str = ""
    cuenta_contable: str = ""
    laboratorio_titular: str = ""
    registro_sanitario: str = ""
    fuente_enriquecimiento: str = ""
    fuente_primaria_requerida: str = ""
    fuente_secundaria_sugerida: str = ""
    observaciones: str = ""
    alertas: list[str] = field(default_factory=list)

    def technical_key(self) -> tuple[str, ...]:
        return (
            self.familia_detectada,
            self.principio_activo_detectado,
            self.cantidad_total or self.concentracion,
            self.forma,
            self.via,
            self.presentacion,
        )


@dataclass
class HomologationResult:
    codigo_origen: str
    descripcion_origen: str
    codigo_homologado_sugerido: str = ""
    descripcion_homologada_sugerida: str = ""
    estado_homologacion: str = "DESCRIPCION INSUFICIENTE"
    score: int = 0
    motivo_decision: str = ""
    diferencias_detectadas: str = ""
    requiere_qf: bool = False
    requiere_validacion_clinica: bool = False
    requiere_validacion_logistica: bool = False
    fuente_validacion: str = ""
    nivel_confianza: str = "BAJO"
