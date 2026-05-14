# Agente de homologacion clinica para Chile

App local y motor Python para homologar bases de productos clinicos, farmaceuticos e insumos medicos con una regla conservadora: falsos negativos antes que falsos positivos.

El sistema no decide por parecido textual. Normaliza descripciones, extrae atributos tecnicos, enriquece primero con el vademecum local del usuario, aplica bloqueos y genera un Excel trazable con hojas de revision.

## Uso rapido

```powershell
python app.py
```

Abrir `http://127.0.0.1:8501`, cargar la base de productos y, si existe, el vademecum institucional. La app permite mapear columnas antes de procesar.

Tambien se puede ejecutar por CLI:

```powershell
python cli.py --productos productos.xlsx --vademecum vademecum.xlsx --salida outputs/homologacion_productos.xlsx
```

## Salida Excel

El workbook generado contiene:

- `BASE_NORMALIZADA`
- `PROPUESTA_HOMOLOGACION`
- `NO_HOMOLOGAR`
- `REVISION_QF`
- `REVISION_LOGISTICA`
- `DICCIONARIO_MARCAS`
- `REGLAS_Y_ALERTAS`
- `RESUMEN_EJECUTIVO`

## Fuentes

- Fuente local prioritaria: vademecum cargado por el usuario.
- Fuente primaria externa para validacion de medicamentos en Chile: Registro Sanitario ISP / ANAMED, `https://registrosanitario.ispch.gob.cl/`.
- Fuente secundaria para enriquecimiento de marcas cuando no este el principio activo: Vademecum.es Chile, `https://www.vademecum.es/chile/cl/alfa`.

La version actual deja trazabilidad de fuente local, registro sanitario cuando viene en la base y una nota explicita de validacion primaria contra ISP/ANAMED. Si un principio activo no esta confirmado por fuente local o dato trazable, el caso queda en revision QF.

## Reglas duras

No homologa automaticamente si cambia concentracion, forma farmaceutica, via, familia de producto, volumen critico, presentacion, unidad gestionada o factor de conversion. En medicamentos sin principio activo confirmado, deriva a `REQUIERE REVISION QF`.

## Pruebas

```powershell
python -m pytest
```
