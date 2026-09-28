# Este archivo quedó sin uso: `CanControlActuators` era un marcador de
# posición ("solo staff puede controlar actuadores") hasta que la Etapa 12
# (apps.memberships) trajo el control real por rol (Owner/Operator/Viewer).
# apps/actuators/views.py ya usa IsGreenhouseOperatorOrAbove en su lugar.
#
# Seguro de borrar: nada en el proyecto importa este módulo (verificado
# con grep antes de vaciarlo). Bórralo con:
#
#   del apps\actuators\permissions.py   (Windows)
#   rm apps/actuators/permissions.py    (Linux/Mac)
