from rest_framework.pagination import CursorPagination


class ReadingCursorPagination(CursorPagination):
    # Ordena por -timestamp: coincide con el índice reading_sensor_ts_idx
    # que ya creamos en el modelo (Etapa 3), así que este orden es barato
    # incluso con millones de filas.
    ordering = "-timestamp"
    page_size = 100