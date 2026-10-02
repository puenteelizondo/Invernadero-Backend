import math
import re
import statistics
import os
import tempfile
from datetime import datetime, timezone as dt_timezone
from itertools import groupby

import xlsxwriter

# Paleta "invernadero": verde oscuro para encabezados, verde muy claro
# para filas alternadas y bloques de etiquetas, naranja para la línea
# de tendencia (promedio móvil).
COLOR_HEADER_BG = "#2E7D32"
COLOR_HEADER_TEXT = "#FFFFFF"
COLOR_ROW_ALT = "#E8F5E9"
COLOR_BORDER = "#C8C8C8"
COLOR_LINE = "#2E7D32"
COLOR_TREND = "#F59E0B"
COLOR_BAD_BG = "#FDE2E2"
COLOR_BAD_TEXT = "#B42318"

# Cada hoja de sensor lleva una ventana de promedio móvil para suavizar
# el ruido y hacer visible la tendencia.
MOVING_AVG_WINDOW = 10
# Si un sensor tiene más puntos que esto, la gráfica usa una muestra
# (las filas completas siguen en la tabla). Excel se pone lento
# dibujando decenas de miles de puntos y la forma no cambia.
CHART_MAX_POINTS = 2000
HIST_BINS = 10
# Excel solo permite 1,048,576 filas por hoja.
MAX_ROWS_PER_SHEET = 1_048_575

_ICON_DIR = os.path.join(os.path.dirname(__file__), "export_icons")
# (palabras clave en el código/nombre del tipo, archivo de ícono)
_ICON_KEYWORDS = [
    (("temp",), "temperature"),
    (("hum", "moist"), "humidity"),
    (("lux", "light", "lumin", "luz"), "light"),
    (("oxig", "oxyg", "o2"), "oxygen"),
    (("wind", "vient"), "wind"),
]


def _icon_path(sensor):
    """Ícono PNG del sensor según su tipo (genérico si no hay uno propio)."""
    text = f"{sensor.sensor_type.code} {sensor.sensor_type.name}".lower()
    for words, name in _ICON_KEYWORDS:
        if any(w in text for w in words):
            return os.path.join(_ICON_DIR, f"{name}.png")
    return os.path.join(_ICON_DIR, "generic.png")


_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _sheet_name(sensor, used):
    """Nombre de hoja válido (<=31 caracteres, sin []:*?/\\) y único."""
    suffix = f" #{sensor.id}"
    base = _INVALID_SHEET_CHARS.sub("-", sensor.name).strip("' ") or "Sensor"
    name = base[: 31 - len(suffix)] + suffix
    n = 2
    while name.lower() in used:
        tail = f"{suffix}.{n}"
        name = base[: 31 - len(tail)] + tail
        n += 1
    used.add(name.lower())
    return name


def _fmt_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {s} s" if s else f"{minutes} min"
    hours, m = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {m} min" if m else f"{hours} h"
    days, h = divmod(hours, 24)
    return f"{days} d {h} h" if h else f"{days} d"


def _naive_utc(dt):
    return dt.astimezone(dt_timezone.utc).replace(tzinfo=None)


def build_readings_xlsx(queryset):
    """
    Genera el .xlsx de exportación y devuelve (archivo, total_de_lecturas).

    Estructura del libro:
      - "Resumen": una fila por sensor (lecturas, mín, máx, promedio,
        desviación, última lectura, fuera de rango) con enlace a su
        hoja y una gráfica de lecturas por sensor.
      - Una hoja por sensor, con:
          * la tabla completa (fecha, valor, promedio móvil, estado),
          * un bloque de estadísticas (fórmulas de Excel vivas sobre
            la tabla, así siguen bien si filtras o editas),
          * gráfica de línea del valor + tendencia,
          * histograma de la distribución de valores.

    El queryset DEBE venir ordenado por (sensor_id, timestamp): se
    recorre una sola vez en streaming y se agrupa por sensor, de modo
    que en memoria solo vive un sensor a la vez (más lo ya escrito en
    el libro).
    """
    tmp = tempfile.TemporaryFile()
    workbook = xlsxwriter.Workbook(tmp, {"default_date_format": "yyyy-mm-dd hh:mm:ss"})

    # ---------- formatos ----------
    def fmt(**props):
        base = {"valign": "vcenter"}
        base.update(props)
        return workbook.add_format(base)

    header_f = fmt(bold=True, font_color=COLOR_HEADER_TEXT, bg_color=COLOR_HEADER_BG,
                   align="center", border=1, border_color=COLOR_BORDER, text_wrap=True)
    title_f = fmt(bold=True, font_size=16, font_color=COLOR_HEADER_BG)
    subtitle_f = fmt(italic=True, font_color="#6B7280")
    label_f = fmt(bold=True, bg_color=COLOR_ROW_ALT, border=1, border_color=COLOR_BORDER)
    section_f = fmt(bold=True, font_color=COLOR_HEADER_TEXT, bg_color=COLOR_HEADER_BG,
                    border=1, border_color=COLOR_BORDER)
    link_f = fmt(font_color="#1D4ED8", underline=1, border=1, border_color=COLOR_BORDER)

    def cell(num_format=None, alt=False, **extra):
        props = {"border": 1, "border_color": COLOR_BORDER}
        if num_format:
            props["num_format"] = num_format
        if alt:
            props["bg_color"] = COLOR_ROW_ALT
        props.update(extra)
        return fmt(**props)

    text_c = [cell(alt=False), cell(alt=True)]
    date_c = [cell("yyyy-mm-dd hh:mm:ss", False), cell("yyyy-mm-dd hh:mm:ss", True)]
    num_c = [cell("0.00", False, align="right"), cell("0.00", True, align="right")]
    int_c = [cell("#,##0", False, align="right"), cell("#,##0", True, align="right")]
    stat_num = cell("0.00", align="right")
    stat_int = cell("#,##0", align="right")
    stat_date = cell("yyyy-mm-dd hh:mm:ss", align="right")
    stat_text = cell(align="right")
    bad_f = workbook.add_format({"bg_color": COLOR_BAD_BG, "font_color": COLOR_BAD_TEXT})

    summary = workbook.add_worksheet("Resumen")  # primera pestaña
    used_names = {"resumen"}
    summaries = []
    total_rows = 0

    # ---------- una hoja por sensor ----------
    for _sensor_id, group in groupby(queryset.iterator(chunk_size=2000), key=lambda r: r.sensor_id):
        readings = list(group)
        sensor = readings[0].sensor
        unit = sensor.get_unit()
        vmin_ok = sensor.sensor_type.valid_min
        vmax_ok = sensor.sensor_type.valid_max

        truncated = len(readings) > MAX_ROWS_PER_SHEET
        if truncated:
            readings = readings[-MAX_ROWS_PER_SHEET:]  # conserva lo más reciente
        n = len(readings)
        total_rows += n

        times = [_naive_utc(r.timestamp) for r in readings]
        values = [float(r.value) for r in readings]

        sheet_name = _sheet_name(sensor, used_names)
        ws = workbook.add_worksheet(sheet_name)
        ws.set_tab_color(COLOR_LINE)

        # --- tabla de datos (A:D) ---
        ws.write_row(0, 0, ["Fecha y hora (UTC)", f"Valor ({unit})" if unit else "Valor",
                            f"Promedio móvil ({MOVING_AVG_WINDOW})", "Estado"], header_f)
        ws.set_row(0, 30)
        ws.set_column(0, 0, 21)
        ws.set_column(1, 2, 16)
        ws.set_column(3, 3, 15)

        window_sum = 0.0
        out_of_range = 0
        for i, (ts, v) in enumerate(zip(times, values)):
            alt = (i + 1) % 2 == 0
            window_sum += v
            if i >= MOVING_AVG_WINDOW:
                window_sum -= values[i - MOVING_AVG_WINDOW]
            moving = window_sum / min(i + 1, MOVING_AVG_WINDOW)
            bad = (vmin_ok is not None and v < vmin_ok) or (vmax_ok is not None and v > vmax_ok)
            out_of_range += bad
            ws.write_datetime(i + 1, 0, ts, date_c[alt])
            ws.write_number(i + 1, 1, v, num_c[alt])
            ws.write_number(i + 1, 2, moving, num_c[alt])
            ws.write_string(i + 1, 3, "Fuera de rango" if bad else "Normal", text_c[alt])
        last = n + 1  # última fila de datos (numeración Excel, 1-based)
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, n, 3)
        ws.conditional_format(1, 3, n, 3, {
            "type": "cell", "criteria": "==", "value": '"Fuera de rango"', "format": bad_f,
        })

        # --- estadísticas (F:G) ---
        v_min, v_max = min(values), max(values)
        mean = statistics.fmean(values)
        median = statistics.median(values)
        stdev = statistics.stdev(values) if n > 1 else 0.0
        t_min = times[values.index(v_min)]
        t_max = times[values.index(v_max)]
        span = (times[-1] - times[0]).total_seconds()
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
        avg_gap = statistics.fmean(gaps) if gaps else 0.0
        max_gap = max(gaps) if gaps else 0.0
        change = values[-1] - values[0]
        pct_bad = out_of_range / n

        B = f"B2:B{last}"
        A = f"A2:A{last}"
        D = f"D2:D{last}"
        ws.set_column(5, 5, 30)
        ws.set_column(6, 6, 22)
        ws.write(0, 5, f"Estadísticas — {sensor.name}", section_f)
        ws.write(0, 6, "", section_f)
        rows = [
            ("Invernadero", None, sensor.greenhouse.name if sensor.greenhouse_id else "", stat_text),
            ("Tipo de sensor", None, sensor.sensor_type.name, stat_text),
            ("Unidad", None, unit, stat_text),
            ("Lecturas", f"=COUNT({B})", n, stat_int),
            ("Mínimo", f"=MIN({B})", v_min, stat_num),
            ("Fecha del mínimo", f"=INDEX({A},MATCH(MIN({B}),{B},0))", t_min, stat_date),
            ("Máximo", f"=MAX({B})", v_max, stat_num),
            ("Fecha del máximo", f"=INDEX({A},MATCH(MAX({B}),{B},0))", t_max, stat_date),
            ("Promedio", f"=AVERAGE({B})", mean, stat_num),
            ("Mediana", f"=MEDIAN({B})", median, stat_num),
            ("Desviación estándar", f"=IF(COUNT({B})>1,STDEV({B}),0)", stdev, stat_num),
            ("Rango (máx − mín)", f"=MAX({B})-MIN({B})", v_max - v_min, stat_num),
            ("Primera lectura", f"=B2", values[0], stat_num),
            ("Última lectura", f"=B{last}", values[-1], stat_num),
            ("Cambio (última − primera)", f"=B{last}-B2", change, stat_num),
            ("Inicio del periodo", f"=MIN({A})", times[0], stat_date),
            ("Fin del periodo", f"=MAX({A})", times[-1], stat_date),
            ("Duración del periodo", None, _fmt_duration(span), stat_text),
            ("Intervalo promedio entre lecturas", None, _fmt_duration(avg_gap), stat_text),
            ("Hueco más largo sin lecturas", None, _fmt_duration(max_gap), stat_text),
            ("Lecturas fuera de rango", f'=COUNTIF({D},"Fuera de rango")', out_of_range, stat_int),
            ("% fuera de rango", f'=COUNTIF({D},"Fuera de rango")/COUNT({B})', pct_bad,
             cell("0.0%", align="right")),
        ]
        if vmin_ok is not None or vmax_ok is not None:
            lo = "—" if vmin_ok is None else f"{vmin_ok:g}"
            hi = "—" if vmax_ok is None else f"{vmax_ok:g}"
            rows.append(("Rango válido del tipo", None, f"{lo} a {hi} {unit}".strip(), stat_text))
        for i, (label, formula, value, f) in enumerate(rows, start=1):
            ws.write(i, 5, label, label_f)
            if formula is None:
                ws.write(i, 6, value, f)
            elif isinstance(value, datetime):
                ws.write_formula(i, 6, formula, f, (value - datetime(1899, 12, 30)).total_seconds() / 86400)
            else:
                ws.write_formula(i, 6, formula, f, value)
        stats_end = len(rows) + 1
        if truncated:
            ws.write(stats_end + 1, 5, f"Se exportaron solo las últimas {MAX_ROWS_PER_SHEET:,} lecturas.", subtitle_f)

        # --- histograma (tabla F:G debajo de las estadísticas) ---
        hist_top = stats_end + 3 if truncated else stats_end + 2
        bins = HIST_BINS if v_max > v_min else 1
        width = (v_max - v_min) / bins if bins > 1 else 1.0
        counts = [0] * bins
        for v in values:
            idx = min(int((v - v_min) / width), bins - 1) if bins > 1 else 0
            counts[idx] += 1
        ws.write(hist_top, 5, "Distribución de valores", section_f)
        ws.write(hist_top, 6, "Lecturas", section_f)
        for k in range(bins):
            lo = v_min + k * width
            hi = lo + width
            label = f"{lo:.2f} – {hi:.2f}" if bins > 1 else f"{v_min:.2f}"
            ws.write(hist_top + 1 + k, 5, label, label_f)
            ws.write_number(hist_top + 1 + k, 6, counts[k], stat_int)

        # --- datos de la gráfica (muestreados si son demasiados) ---
        if n > CHART_MAX_POINTS:
            step = math.ceil(n / CHART_MAX_POINTS)
            idxs = list(range(0, n, step))
            if idxs[-1] != n - 1:
                idxs.append(n - 1)
            helper_col = 26  # columna AA, fuera de la vista
            ws.write(0, helper_col, "Muestra para la gráfica", section_f)
            ws.write(0, helper_col + 1, "Valor", section_f)
            ws.write(0, helper_col + 2, "Tendencia", section_f)
            ws.set_column(helper_col, helper_col, 21)
            for j, i in enumerate(idxs):
                ws.write_datetime(j + 1, helper_col, times[i], date_c[0])
                ws.write_number(j + 1, helper_col + 1, values[i], num_c[0])
                # tendencia sobre la muestra: promedio móvil local de los datos completos
                lo_i = max(0, i - MOVING_AVG_WINDOW + 1)
                ws.write_number(j + 1, helper_col + 2, statistics.fmean(values[lo_i:i + 1]), num_c[0])
            m = len(idxs)
            cats = [sheet_name, 1, helper_col, m, helper_col]
            vals = [sheet_name, 1, helper_col + 1, m, helper_col + 1]
            trend = [sheet_name, 1, helper_col + 2, m, helper_col + 2]
        else:
            cats = [sheet_name, 1, 0, n, 0]
            vals = [sheet_name, 1, 1, n, 1]
            trend = [sheet_name, 1, 2, n, 2]

        # Dispersión con líneas (X = fecha y hora real): a diferencia de una
        # gráfica de líneas con eje de fechas, no junta en un solo punto las
        # lecturas del mismo día, así que se ven bien aunque lleguen cada segundo.
        line = workbook.add_chart({"type": "scatter", "subtype": "straight"})
        line.add_series({"name": f"{sensor.name}", "categories": cats, "values": vals,
                         "line": {"color": COLOR_LINE, "width": 1.5},
                         "marker": {"type": "none"} if n > 60 else
                                   {"type": "circle", "size": 4,
                                    "fill": {"color": COLOR_LINE}, "border": {"color": COLOR_LINE}}})
        line.add_series({"name": f"Tendencia ({MOVING_AVG_WINDOW} lecturas)", "categories": cats, "values": trend,
                         "line": {"color": COLOR_TREND, "width": 2, "dash_type": "dash"},
                         "marker": {"type": "none"}})
        line.set_title({"name": f"{sensor.name}: valor contra tiempo", "name_font": {"size": 13}})
        # Ejes ajustados a los datos (si no, Excel/LibreOffice arrancan en 0 o
        # agregan márgenes enormes y la variación se ve plana).
        _serial = lambda dt: (dt - datetime(1899, 12, 30)).total_seconds() / 86400
        pad = (v_max - v_min) * 0.1 or max(abs(v_max) * 0.05, 1.0)
        x_min, x_max = _serial(times[0]), _serial(times[-1])
        if x_max <= x_min:
            x_max = x_min + 1 / 1440
        line.set_x_axis({"name": "Fecha y hora (UTC)", "num_format": "dd/mm hh:mm:ss",
                         "min": x_min, "max": x_max,
                         "num_font": {"rotation": -45}, "major_gridlines": {"visible": False}})
        line.set_y_axis({"name": f"{sensor.sensor_type.name} ({unit})" if unit else sensor.sensor_type.name,
                         "min": v_min - pad, "max": v_max + pad, "num_format": "0.0",
                         "major_gridlines": {"visible": True, "line": {"color": "#E5E7EB"}}})
        line.set_legend({"position": "bottom"})
        line.set_size({"width": 760, "height": 360})
        ws.insert_chart(6, 8, line)

        hist = workbook.add_chart({"type": "column"})
        hist.add_series({"name": "Lecturas", "categories": [sheet_name, hist_top + 1, 5, hist_top + bins, 5],
                         "values": [sheet_name, hist_top + 1, 6, hist_top + bins, 6],
                         "fill": {"color": COLOR_LINE}, "gap": 30})
        hist.set_title({"name": "Distribución de valores", "name_font": {"size": 13}})
        hist.set_x_axis({"name": f"Valor ({unit})" if unit else "Valor", "num_font": {"rotation": -45}})
        hist.set_y_axis({"name": "Lecturas", "major_gridlines": {"visible": True, "line": {"color": "#E5E7EB"}}})
        hist.set_legend({"none": True})
        hist.set_size({"width": 760, "height": 300})
        ws.insert_chart(26, 8, hist)

        # --- dibujo del sensor + nombre, arriba de las gráficas ---
        ws.insert_image(0, 8, _icon_path(sensor), {"x_offset": 4, "y_offset": 4, "object_position": 3})
        ws.merge_range(1, 11, 2, 16, sensor.name, title_f)
        ws.merge_range(3, 11, 3, 16, f"{sensor.sensor_type.name}" + (f" · {unit}" if unit else ""), subtitle_f)

        summaries.append({
            "sheet": sheet_name, "sensor": sensor.name, "type": sensor.sensor_type.name,
            "greenhouse": sensor.greenhouse.name if sensor.greenhouse_id else "",
            "unit": unit, "n": n, "min": v_min, "max": v_max, "mean": mean, "stdev": stdev,
            "last": values[-1], "last_at": times[-1], "first_at": times[0], "bad": out_of_range,
        })

    # ---------- hoja Resumen ----------
    summary.set_tab_color(COLOR_TREND)
    summary.write(0, 0, "Exportación de lecturas", title_f)
    generated = datetime.now(dt_timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if summaries:
        period = f"{min(s['first_at'] for s in summaries):%Y-%m-%d %H:%M} a {max(s['last_at'] for s in summaries):%Y-%m-%d %H:%M} (UTC)"
        summary.write(1, 0, f"Generado: {generated}   ·   Periodo con datos: {period}   ·   "
                            f"{len(summaries)} sensores, {total_rows:,} lecturas", subtitle_f)
    else:
        summary.write(1, 0, f"Generado: {generated}", subtitle_f)
        summary.write(3, 0, "No hay lecturas guardadas en el rango pedido.", subtitle_f)

    if summaries:
        heads = ["Invernadero", "Sensor (clic para ir a su hoja)", "Tipo", "Unidad", "Lecturas", "Mínimo",
                 "Máximo", "Promedio", "Desv. estándar", "Última lectura", "Fecha de la última",
                 "Fuera de rango"]
        top = 3
        summary.write_row(top, 0, heads, header_f)
        summary.set_row(top, 30)
        widths = [22, 30, 18, 9, 11, 11, 11, 11, 14, 14, 21, 14]
        for c, w in enumerate(widths):
            summary.set_column(c, c, w)
        for i, s in enumerate(summaries, start=1):
            r = top + i
            alt = i % 2 == 0
            safe = s["sheet"].replace("'", "''")
            summary.write(r, 0, s["greenhouse"], text_c[alt])
            summary.write_url(r, 1, f"internal:'{safe}'!A1", link_f, string=s["sensor"])
            summary.write(r, 2, s["type"], text_c[alt])
            summary.write(r, 3, s["unit"], text_c[alt])
            summary.write_number(r, 4, s["n"], int_c[alt])
            summary.write_number(r, 5, s["min"], num_c[alt])
            summary.write_number(r, 6, s["max"], num_c[alt])
            summary.write_number(r, 7, s["mean"], num_c[alt])
            summary.write_number(r, 8, s["stdev"], num_c[alt])
            summary.write_number(r, 9, s["last"], num_c[alt])
            summary.write_datetime(r, 10, s["last_at"], date_c[alt])
            summary.write_number(r, 11, s["bad"], int_c[alt])
        last_r = top + len(summaries)
        summary.freeze_panes(top + 1, 0)
        summary.conditional_format(top + 1, 11, last_r, 11, {
            "type": "cell", "criteria": ">", "value": 0, "format": bad_f,
        })

        chart = workbook.add_chart({"type": "bar"})
        chart.add_series({
            "name": "Lecturas",
            "categories": ["Resumen", top + 1, 1, last_r, 1],
            "values": ["Resumen", top + 1, 4, last_r, 4],
            "fill": {"color": COLOR_LINE}, "gap": 40,
            "data_labels": {"value": True},
        })
        chart.set_title({"name": "Lecturas por sensor", "name_font": {"size": 13}})
        chart.set_legend({"none": True})
        chart.set_y_axis({"reverse": True})
        chart.set_x_axis({"major_gridlines": {"visible": True, "line": {"color": "#E5E7EB"}}})
        chart.set_size({"width": 720, "height": max(240, 60 + 32 * len(summaries))})
        summary.insert_chart(last_r + 2, 0, chart)

    workbook.close()
    tmp.seek(0)
    return tmp, total_rows
