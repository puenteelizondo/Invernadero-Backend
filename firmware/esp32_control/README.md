# Firmware de referencia: controlador ESP32

Ejecuta los lazos **On/Off, P, PI y PID** configurados desde el panel *Control* de la web.
El cálculo se hace en el ESP32; el servidor solo guarda y entrega la configuración.

## Qué trae
- WiFi + WebSocket (`/ws/device/`), con token de un solo uso y reconexión con espera progresiva (1 s → 30 s).
- Recibe `config` / `config_update` / `config_remove`, responde `ack` con la versión aplicada.
- Guarda la última configuración válida en `Preferences`: **si se cae la red sigue controlando**.
- PID con anti-windup (límite de integral + corte de integración al saturar), derivada sobre la medición y cambio sin saltos entre modos.
- Salida PWM, o "proporcional en el tiempo" para relevadores (ventana de 10 s).
- Seguridad: salidas apagadas al arrancar, tope absoluto `HARD_MAX_OUTPUT`, apagado si el sensor no da medición válida por 10 s.
- Telemetría (`pv`, `setpoint`, `output`, `p`, `i`, `d`) cada `sample_time_ms`; las lecturas de los sensores se mandan por la ingesta HTTP normal.

## Cómo cargarlo
1. Arduino IDE → instala el soporte **esp32 by Espressif (3.x)**.
2. Gestor de librerías: **WebSockets** (Markus Sattler) y **ArduinoJson** (7.x).
3. Abre `esp32_control.ino` y edita la sección `AJUSTES`:
   - `WIFI_SSID`, `WIFI_PASS`, `SERVER_HOST`, `SERVER_PORT`, `DEVICE_KEY` (la API key del dispositivo, se ve una sola vez al crearlo).
   - La tabla `HW[]`: qué pin maneja cada actuador (por su id en la plataforma) y si es relevador o PWM.
   - `SENSORES[]` y `leerSensor()`: cómo se mide cada sensor.
4. Placa **ESP32 Dev Module** → Subir. Monitor serie a 115200.
5. En la web: crea el lazo en *Control*, elige sensor y actuador (deben estar asignados a este dispositivo), pulsa **Aplicar** y verás "Aplicado por el ESP32".

> Con HTTPS/WSS: `USE_TLS true` y `SERVER_PORT 443`. Para producción valida el certificado (`setCACert`) en lugar de aceptar cualquiera.

## Protocolo (resumen)
| Dirección | Mensaje |
|---|---|
| servidor → ESP32 | `{"event":"config","loops":[…]}` · `{"event":"config_update","loop":{…}}` · `{"event":"config_remove","loop_id":1}` · `{"event":"ping"}` |
| ESP32 → servidor | `{"event":"ack","loop_id":1,"version":7}` · `{"event":"telemetry",…}` · `{"event":"pong"}` |

Sin hardware: usa `scripts/simulate_controller.py`.
