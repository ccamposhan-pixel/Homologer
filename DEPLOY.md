# Despliegue online

Esta app puede correr online sin instalar Docker en tu PC. La opcion recomendada es Render usando `render.yaml`; ver [DEPLOY_RENDER.md](DEPLOY_RENDER.md).

Tambien puede correr en cualquier servicio que acepte Python o Docker: un VPS, Railway, Fly.io, Azure Container Apps, Google Cloud Run, etc.

## Variables necesarias

- `HOST=0.0.0.0`
- `PORT=8501`
- `DATA_DIR=/data`
- `APP_PASSWORD=<clave privada>`

`APP_PASSWORD` protege la app con una clave simple. No publiques la app sin clave si vas a cargar bases reales.

## Persistencia

Monta un disco persistente en `/data`. Ahí quedan:

- `/data/uploads`
- `/data/outputs`

Si no montas disco persistente, los archivos generados pueden perderse cuando el servicio reinicie.

## Docker local

```powershell
docker build -t homologacion-clinica .
docker run --rm -p 8501:8501 -e APP_PASSWORD=clave -v ${PWD}/data:/data homologacion-clinica
```

Abrir:

```text
http://localhost:8501
```

## Recomendacion de arquitectura

Para uso liviano, este contenedor basta. Para procesos muy largos o bases grandes, lo ideal es separar:

- Web app
- Worker de procesamiento
- Cola de trabajos
- Storage persistente

La version actual mantiene el job activo mientras el proceso del servidor vive. Si el proveedor reinicia el contenedor durante un procesamiento, hay que relanzar el job. Con disco persistente no se pierden archivos ya generados.
