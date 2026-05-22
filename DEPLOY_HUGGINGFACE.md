# Despliegue gratis en Hugging Face Spaces

Esta opcion deja la app en una URL publica sin depender del PC local. Es util para demo o uso liviano.

## Limitaciones importantes

- El hardware gratis de Spaces es CPU basico.
- El disco gratis no es persistente: si el Space se reinicia, los archivos subidos o generados pueden perderse.
- En hardware gratis el Space puede dormir si no se usa.
- Para datos clinicos reales, usa Space privado/protegido y controla colaboradores.

## Pasos

1. Entra a https://huggingface.co/spaces.
2. Crea un nuevo Space.
3. SDK: `Docker`.
4. Visibilidad: `Private` si vas a cargar datos sensibles.
5. Sube o conecta este repositorio.
6. En `Settings > Variables and secrets`, define:

```text
APP_PASSWORD=una-clave-segura
HOST=0.0.0.0
PORT=7860
DATA_DIR=/data
```

7. Espera el build.
8. Abre la URL del Space y entra con `APP_PASSWORD`.

## Recomendacion operativa

Para pruebas sin pago, Hugging Face Spaces es mejor que Render Free en este caso porque permite Docker y CPU gratis. Para produccion con archivos persistentes, trabajos largos y datos clinicos, usa un servicio pagado con disco persistente, control de acceso y politica de retencion.

