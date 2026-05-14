# Despliegue en Render sin Docker local

No necesitas instalar Docker en el PC del trabajo. Render construye y ejecuta la app en la nube desde GitHub.

## Pasos

1. Sube este proyecto a un repositorio GitHub privado.
2. En Render, elige `New > Blueprint`.
3. Conecta el repositorio.
4. Render detectara `render.yaml`.
5. Define el secreto `APP_PASSWORD` cuando Render lo pida.
6. Crea el servicio.

La app quedara online con:

- `HOST=0.0.0.0`
- `DATA_DIR=/data`
- Disco persistente de 10 GB en `/data`
- Health check en `/healthz`

## Importante

Usa un plan que no duerma ni reinicie por inactividad. En Render, evita Free para procesos largos, porque puede suspender la app. El `render.yaml` deja `plan: standard` para reducir ese riesgo.

El disco persistente conserva uploads y Excel generados. Si el proveedor reinicia el servicio durante un procesamiento largo, el job en curso puede cortarse, pero los archivos ya generados quedan en `/data/outputs`.

## Uso

Cuando Render termine el deploy:

1. Abre la URL publica del servicio.
2. Ingresa la clave `APP_PASSWORD`.
3. Carga base actual, vademecum e historicos.
4. Descarga el workbook final.

## Seguridad

No uses una clave simple. Recomendado:

```text
APP_PASSWORD=una-frase-larga-con-numeros-y-simbolos
```

Para datos clinicos reales, usa repositorio privado y no compartas la URL publica sin control de acceso.
