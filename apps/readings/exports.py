import tempfile
from datetime import timezone as dt_timezone

import xlsxwriter

# Paleta simple, tema "invernadero": verde oscuro para el encabezado,
# verde muy claro para las filas alternadas (facilita leer horizontal
# sin perder la fila), blanco para las filas pares.
COLOR_HEADER_BG = "#2E7D32"
COLOR_HEADER_TEXT = "#FFFFFF"
COLOR_ROW_ALT = "#E8F5E9"
COLOR_BORDER = "#C8C8C8"


def build_readings_xlsx(queryset):
    """
    Escribe el queryset de Reading a un archivo .xlsx con formato
    (encabezado destacado, filas alternadas, filtro automático,
    encabezado congelado) y devuelve el file-like object ya listo
    para leerse desde el principio.

    Los formatos (colores, bordes, tipo de letra) se aplican celda por
    celda mientras se escribe, exactamente igual que los valores — no
    rompen el modo constant_memory de XlsxWriter, porque siguen siendo
    parte del mismo flujo secuencial de escritura por fila. Lo único
    que se hace DESPUÉS de terminar de escribir todas las filas es el
    autofiltro y el ancho de columnas "congeladas", porque esas dos
    cosas son metadatos sobre el rango completo (necesitan saber
    cuántas filas hay en total), no contenido de celda.
    """
    tmp = tempfile.TemporaryFile()
    workbook = xlsxwriter.Workbook(tmp, {"constant_memory": True})
    sheet = workbook.add_worksheet("Lecturas")

    header_format = workbook.add_format({
        "bold": True,
        "font_color": COLOR_HEADER_TEXT,
        "bg_color": COLOR_HEADER_BG,
        "align": "center",
        "valign": "vcenter",
        "border": 1,
        "border_color": COLOR_BORDER,
    })

    def row_format(extra=None, alt=False):
        props = {
            "border": 1,
            "border_color": COLOR_BORDER,
            "valign": "vcenter",
        }
        if alt:
            props["bg_color"] = COLOR_ROW_ALT
        if extra:
            props.update(extra)
        return workbook.add_format(props)

    # Un formato por combinación (columna de texto / fecha / número) x
    # (fila par / impar) — XlsxWriter necesita un objeto Format
    # distinto por cada combinación de estilos, no se puede "mezclar"
    # dos formatos ya creados sobre la marcha.
    text_fmt = [row_format(alt=False), row_format(alt=True)]
    date_fmt = [
        row_format({"num_format": "yyyy-mm-dd hh:mm:ss"}, alt=False),
        row_format({"num_format": "yyyy-mm-dd hh:mm:ss"}, alt=True),
    ]
    value_fmt = [
        row_format({"num_format": "0.00", "align": "right"}, alt=False),
        row_format({"num_format": "0.00", "align": "right"}, alt=True),
    ]

    headers = ["Invernadero", "Sensor", "Tipo de sensor", "Unidad", "Fecha y hora (UTC)", "Valor"]
    for col, header in enumerate(headers):
        sheet.write(0, col, header, header_format)

    sheet.set_column(0, 1, 24)
    sheet.set_column(2, 3, 16)
    sheet.set_column(4, 4, 20)
    sheet.set_column(5, 5, 12)

    # El encabezado se queda visible aunque hagas scroll hacia abajo.
    sheet.freeze_panes(1, 0)

    row = 1
    for reading in queryset.iterator(chunk_size=2000):
        sensor = reading.sensor
        naive_ts = reading.timestamp.astimezone(dt_timezone.utc).replace(tzinfo=None)
        alt = row % 2 == 0  # filas pares (2, 4, 6...) llevan el verde clarito

        sheet.write(row, 0, sensor.greenhouse.name if sensor.greenhouse_id else "", text_fmt[alt])
        sheet.write(row, 1, sensor.name, text_fmt[alt])
        sheet.write(row, 2, sensor.sensor_type.code, text_fmt[alt])
        sheet.write(row, 3, sensor.get_unit(), text_fmt[alt])
        sheet.write_datetime(row, 4, naive_ts, date_fmt[alt])
        sheet.write_number(row, 5, reading.value, value_fmt[alt])
        row += 1

    last_row = row - 1
    if last_row >= 1:
        # Filtro desplegable en cada encabezado de columna, como el
        # que verías si seleccionas los datos en Excel y le das
        # Datos > Filtro.
        sheet.autofilter(0, 0, last_row, len(headers) - 1)

    workbook.close()
    tmp.seek(0)
    return tmp, last_row