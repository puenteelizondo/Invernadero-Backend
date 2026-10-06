/*
 * Controlador de referencia para el panel "Control" del Invernadero.
 * ESP32 (núcleo Arduino-ESP32 3.x) · WiFi · WebSocket · PID/PI/P/On-Off calculado AQUÍ.
 *
 * Qué hace
 *   1. Se conecta al WiFi y pide un token a POST /api/v1/devices/ws-token/ (header X-Device-Key).
 *   2. Abre el WebSocket  /ws/device/?token=...  y recibe `config` / `config_update`.
 *   3. Aplica cada lazo, lo guarda en memoria no volátil (Preferences) y responde `ack`.
 *   4. Cada `sample_time_ms` lee la variable de proceso, calcula la salida y la aplica
 *      (PWM, o "proporcional en el tiempo" para relevadores). Manda `telemetry`.
 *   5. Manda las lecturas de los sensores por HTTP a /api/v1/readings/ingest/ (la ingesta normal).
 *   6. Si se cae la red SIGUE controlando con la última configuración válida guardada.
 *
 * El servidor NUNCA calcula la salida del lazo: solo guarda y entrega la configuración.
 *
 * Librerías (Gestor de librerías):  "WebSockets" de Markus Sattler (Links2004)  y  "ArduinoJson" 7.x
 *
 * Qué debes ajustar:   los #define de abajo y la tabla HW[] (qué pin maneja cada actuador)
 * y la función leerSensor() (cómo se mide cada sensor).
 */
#include <WiFi.h>
#include <HTTPClient.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <math.h>

// Estado de cada lazo. Va ANTES de los ajustes a propósito: Arduino inserta los
// prototipos de las funciones antes de la primera función del archivo
// (leerSensor), y ahí el tipo Lazo ya tiene que existir.
struct Lazo {
  bool used = false;
  int id = 0, version = 0, sensorId = 0, actuatorId = 0;
  String mode = "off", direction = "direct";
  float setpoint = 0, hysteresis = 0.5, kp = 1, ki = 0, kd = 0;
  float outMin = 0, outMax = 100, integLimit = 100;
  uint32_t sampleMs = 1000;
  bool enabled = false;
  // estado interno
  float integ = 0, out = 0, prevPv = NAN, p = 0, i = 0, d = 0;
  bool onoffOn = false;
  bool bumpless = false;
  uint32_t lastRun = 0, lastValidPv = 0;
  uint32_t windowStart = 0;
};

// ======================= AJUSTES =======================
#define WIFI_SSID     "TU_WIFI"
#define WIFI_PASS     "TU_CLAVE"
#define SERVER_HOST   "192.168.1.50"     // IP o dominio del backend
#define SERVER_PORT   8000
#define USE_TLS       false              // true si el backend está detrás de HTTPS/WSS (puerto 443)
#define DEVICE_KEY    "PEGA_AQUI_LA_API_KEY_DEL_DISPOSITIVO"

#define MAX_LOOPS         4
#define SENSOR_TIMEOUT_MS 10000          // sin medición válida por este tiempo => salida a 0 (seguro)
#define PWM_FREQ          1000
#define PWM_BITS          10
#define RELAY_WINDOW_MS   10000          // ventana de "proporcional en el tiempo" para relevadores
#define INGEST_EVERY_MS   5000           // cada cuánto se mandan las lecturas por HTTP
#define HARD_MAX_OUTPUT   100.0f         // tope absoluto de seguridad, pase lo que pase en la config

// Qué pin maneja cada actuador (ids = los de la plataforma). relay=true => proporcional en el tiempo.
struct Hw { int actuatorId; int pin; bool relay; bool activeLow; };
Hw HW[] = {
  { 1, 26, true,  false },    // ej. calefactor con relevador
  { 2, 27, false, false },    // ej. ventilador con PWM
};

// Ids de sensor que se miden y se mandan por ingesta.
int SENSORES[] = { 1, 2 };

// Mide un sensor. Devuelve NAN si no hay medición válida. REEMPLAZA con tu sensor real.
float leerSensor(int sensorId) {
  // Ejemplo (NO es un sensor real): lectura analógica 0..4095 -> 0..50 °C
  // return analogRead(34) * 50.0f / 4095.0f;
  return NAN;
}
// =======================================================


Lazo LAZOS[MAX_LOOPS];
Preferences prefs;
WebSocketsClient ws;
bool wsUp = false;
uint32_t nextConnect = 0, backoffMs = 1000, lastIngest = 0;

// ---------------------------------------------------------------- utilidades
Lazo* buscar(int id) {
  for (auto& l : LAZOS) if (l.used && l.id == id) return &l;
  return nullptr;
}
Lazo* nuevo() {
  for (auto& l : LAZOS) if (!l.used) return &l;
  return nullptr;
}
Hw* hwDe(int actuatorId) {
  for (auto& h : HW) if (h.actuatorId == actuatorId) return &h;
  return nullptr;
}
float limitar(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

// ---------------------------------------------------------------- persistencia
void guardarLazo(const Lazo& l, const String& json) {
  prefs.putString(("l" + String(l.id)).c_str(), json);
  String ids = prefs.getString("ids", "");
  if (ids.indexOf("," + String(l.id) + ",") < 0) {
    if (ids.length() == 0) ids = ",";
    ids += String(l.id) + ",";
    prefs.putString("ids", ids);
  }
}
void borrarLazoGuardado(int id) {
  prefs.remove(("l" + String(id)).c_str());
  String ids = prefs.getString("ids", "");
  ids.replace("," + String(id) + ",", ",");
  prefs.putString("ids", ids);
}

// ---------------------------------------------------------------- aplicar configuración
// `cfg` es el objeto del lazo tal como lo manda el servidor.
void aplicarLazo(JsonObjectConst cfg, bool persistir) {
  int id = cfg["id"] | 0;
  if (id <= 0) return;
  Lazo* l = buscar(id);
  bool esNuevo = (l == nullptr);
  if (esNuevo) { l = nuevo(); if (!l) return; l->used = true; l->id = id; }

  bool estabaActivo = l->enabled && l->mode != "off";
  String modoAnt = l->mode, dirAnt = l->direction;

  l->version    = cfg["version"] | l->version;
  l->sensorId   = cfg["sensor_id"] | l->sensorId;
  l->actuatorId = cfg["actuator_id"] | l->actuatorId;
  l->mode       = String((const char*)(cfg["mode"] | "off"));
  l->direction  = String((const char*)(cfg["direction"] | "direct"));
  l->setpoint   = cfg["setpoint"] | 0.0f;
  l->hysteresis = cfg["hysteresis"] | 0.5f;
  l->kp = cfg["kp"] | 0.0f;  l->ki = cfg["ki"] | 0.0f;  l->kd = cfg["kd"] | 0.0f;
  l->outMin = cfg["output_min"] | 0.0f;
  l->outMax = limitar(cfg["output_max"] | 100.0f, 0, HARD_MAX_OUTPUT);
  l->integLimit = cfg["integral_limit"] | 100.0f;
  l->sampleMs = limitar(cfg["sample_time_ms"] | 1000, 100, 60000);
  l->enabled = cfg["enabled"] | false;
  if (l->outMin >= l->outMax) l->outMin = 0;     // configuración incoherente: protege

  // Cambio sin saltos entre modos activos; al encender desde apagado no hay nada que preservar.
  bool activo = l->enabled && l->mode != "off";
  l->bumpless = estabaActivo && activo && (modoAnt != l->mode || dirAnt != l->direction);
  if (!activo) { l->integ = 0; l->out = 0; l->onoffOn = false; }

  if (persistir) { String s; serializeJson(cfg, s); guardarLazo(*l, s); }
}

void enviarAck(const Lazo& l) {
  if (!wsUp) return;
  JsonDocument d;
  d["event"] = "ack"; d["loop_id"] = l.id; d["version"] = l.version;
  String s; serializeJson(d, s); ws.sendTXT(s);
}

// ---------------------------------------------------------------- el lazo (PID, PI, P, On/Off)
float calcular(Lazo& l, float pv, float dt) {
  if (!l.enabled || l.mode == "off") {
    l.out = 0; l.integ = 0; l.onoffOn = false; l.p = l.i = l.d = 0; l.prevPv = pv; return 0;
  }
  float sign = (l.direction == "direct") ? 1.0f : -1.0f;
  float err = sign * (l.setpoint - pv);

  if (l.mode == "on_off") {
    float h = l.hysteresis / 2.0f;
    if (err > h) l.onoffOn = true; else if (err < -h) l.onoffOn = false;
    l.out = l.onoffOn ? l.outMax : 0;
    l.p = l.i = l.d = 0; l.prevPv = pv;
    return l.out;
  }

  l.p = l.kp * err;
  // Derivada sobre la MEDICIÓN: un salto de setpoint no produce un pico en la salida.
  if (l.mode == "pid" && !isnan(l.prevPv) && dt > 0) l.d = -l.kd * sign * (pv - l.prevPv) / dt;
  else l.d = 0;
  l.prevPv = pv;

  bool usaI = (l.mode == "pi" || l.mode == "pid");
  if (l.bumpless) {                       // la salida continúa donde estaba
    l.bumpless = false;
    l.integ = usaI ? limitar(l.out - l.p - l.d, -l.integLimit, l.integLimit) : 0;
  }
  if (usaI) {
    float sinSat = l.p + l.integ + l.d;
    // Anti-windup: no se integra mientras la salida está saturada y el error empuja más.
    bool saturada = (sinSat >= l.outMax && err > 0) || (sinSat <= l.outMin && err < 0);
    if (!saturada) l.integ += l.ki * err * dt;
    l.integ = limitar(l.integ, -l.integLimit, l.integLimit);
    l.i = l.integ;
  } else l.i = 0;

  l.out = limitar(l.p + l.i + l.d, l.outMin, l.outMax);
  return l.out;
}

// ---------------------------------------------------------------- salida física
void aplicarSalida(Lazo& l, float pct) {
  Hw* h = hwDe(l.actuatorId);
  if (!h) return;
  pct = limitar(pct, 0, HARD_MAX_OUTPUT);
  if (h->relay) {
    // Proporcional en el tiempo: encendido durante pct% de la ventana.
    uint32_t now = millis();
    if (now - l.windowStart >= RELAY_WINDOW_MS) l.windowStart = now;
    bool on = (now - l.windowStart) < (uint32_t)(RELAY_WINDOW_MS * pct / 100.0f);
    digitalWrite(h->pin, (on != h->activeLow) ? HIGH : LOW);
  } else {
    uint32_t duty = (uint32_t)((pct / 100.0f) * ((1 << PWM_BITS) - 1));
    ledcWrite(h->pin, h->activeLow ? ((1 << PWM_BITS) - 1) - duty : duty);
  }
}

void apagarTodo() {
  for (auto& h : HW) {
    if (h.relay) digitalWrite(h.pin, h.activeLow ? HIGH : LOW);
    else ledcWrite(h.pin, h.activeLow ? ((1 << PWM_BITS) - 1) : 0);
  }
}

// ---------------------------------------------------------------- WebSocket
void alEventoWS(WStype_t tipo, uint8_t* payload, size_t len) {
  switch (tipo) {
    case WStype_CONNECTED:
      wsUp = true; backoffMs = 1000;
      Serial.println("[WS] conectado");
      break;
    case WStype_DISCONNECTED:
      if (wsUp) Serial.println("[WS] desconectado; se sigue controlando con la última config");
      wsUp = false;
      nextConnect = millis() + backoffMs;
      backoffMs = min<uint32_t>(backoffMs * 2, 30000);   // espera progresiva
      break;
    case WStype_TEXT: {
      JsonDocument d;
      if (deserializeJson(d, payload, len)) return;
      const char* ev = d["event"] | "";
      if (!strcmp(ev, "config")) {
        for (JsonObjectConst c : d["loops"].as<JsonArrayConst>()) {
          aplicarLazo(c, true);
          if (Lazo* l = buscar(c["id"] | 0)) enviarAck(*l);
        }
      } else if (!strcmp(ev, "config_update")) {
        JsonObjectConst c = d["loop"];
        aplicarLazo(c, true);
        if (Lazo* l = buscar(c["id"] | 0)) enviarAck(*l);
      } else if (!strcmp(ev, "config_remove")) {
        int id = d["loop_id"] | 0;
        if (Lazo* l = buscar(id)) { l->used = false; borrarLazoGuardado(id); }
      } else if (!strcmp(ev, "ping")) {
        ws.sendTXT("{\"event\":\"pong\"}");
      }
      break;
    }
    default: break;
  }
}

String pedirToken() {
  HTTPClient http;
  String url = String(USE_TLS ? "https://" : "http://") + SERVER_HOST + ":" + SERVER_PORT + "/api/v1/devices/ws-token/";
  http.begin(url);
  http.addHeader("X-Device-Key", DEVICE_KEY);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST("{}");
  String token = "";
  if (code == 200) {
    JsonDocument d;
    if (!deserializeJson(d, http.getString())) token = String((const char*)(d["token"] | ""));
  } else Serial.printf("[token] HTTP %d\n", code);
  http.end();
  return token;
}

void conectarWS() {
  if (WiFi.status() != WL_CONNECTED) { nextConnect = millis() + 2000; return; }
  String token = pedirToken();                       // un token de un solo uso por conexión
  if (token.length() == 0) {
    nextConnect = millis() + backoffMs; backoffMs = min<uint32_t>(backoffMs * 2, 30000); return;
  }
  String path = "/ws/device/?token=" + token;
  if (USE_TLS) ws.beginSSL(SERVER_HOST, SERVER_PORT, path); else ws.begin(SERVER_HOST, SERVER_PORT, path);
  ws.onEvent(alEventoWS);
  ws.setReconnectInterval(0);                        // la reconexión la manejamos nosotros (token nuevo)
  nextConnect = millis() + 15000;                    // si no conecta en 15 s, se vuelve a intentar
}

// ---------------------------------------------------------------- ingesta HTTP de lecturas
void enviarLecturas() {
  if (WiFi.status() != WL_CONNECTED) return;
  JsonDocument d;
  JsonArray arr = d["readings"].to<JsonArray>();
  for (int sid : SENSORES) {
    float v = leerSensor(sid);
    if (isnan(v)) continue;
    JsonObject o = arr.add<JsonObject>(); o["sensor_id"] = sid; o["value"] = roundf(v * 100) / 100;
  }
  if (arr.size() == 0) return;
  String body; serializeJson(d, body);
  HTTPClient http;
  http.begin(String(USE_TLS ? "https://" : "http://") + SERVER_HOST + ":" + SERVER_PORT + "/api/v1/readings/ingest/");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  http.addHeader("Content-Type", "application/json");
  http.POST(body);
  http.end();
}

// ---------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  for (auto& h : HW) {
    if (h.relay) { pinMode(h.pin, OUTPUT); }
    else { ledcAttach(h.pin, PWM_FREQ, PWM_BITS); }
  }
  apagarTodo();                                      // arranca SIEMPRE con las salidas apagadas

  // Última configuración válida guardada: el lazo funciona aunque no haya red.
  prefs.begin("control", false);
  String ids = prefs.getString("ids", "");
  int from = 1;
  while (from < (int)ids.length()) {
    int to = ids.indexOf(',', from);
    if (to < 0) break;
    String id = ids.substring(from, to);
    JsonDocument d;
    if (!deserializeJson(d, prefs.getString(("l" + id).c_str(), "{}"))) aplicarLazo(d.as<JsonObjectConst>(), false);
    from = to + 1;
  }

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void loop() {
  ws.loop();
  uint32_t now = millis();

  if (!wsUp && now >= nextConnect) conectarWS();

  for (auto& l : LAZOS) {
    if (!l.used || now - l.lastRun < l.sampleMs) {
      if (l.used) aplicarSalida(l, l.out);           // el relevador necesita refrescarse más seguido
      continue;
    }
    float dt = (l.lastRun == 0) ? l.sampleMs / 1000.0f : (now - l.lastRun) / 1000.0f;
    l.lastRun = now;

    float pv = leerSensor(l.sensorId);
    if (!isnan(pv)) l.lastValidPv = now;

    float salida;
    if (isnan(pv) && now - l.lastValidPv > SENSOR_TIMEOUT_MS) {
      salida = 0; l.out = 0; l.integ = 0;            // sin medición confiable: apagado seguro
    } else if (isnan(pv)) {
      salida = l.out;                                // fallo breve: mantiene la última salida
    } else {
      salida = calcular(l, pv, dt);
    }
    aplicarSalida(l, salida);

    if (wsUp) {
      JsonDocument d;
      d["event"] = "telemetry"; d["loop_id"] = l.id; d["version"] = l.version;
      if (!isnan(pv)) d["pv"] = roundf(pv * 1000) / 1000;
      d["setpoint"] = l.setpoint; d["output"] = roundf(salida * 100) / 100;
      if (!isnan(pv)) d["error"] = roundf((l.setpoint - pv) * 1000) / 1000;
      d["p"] = l.p; d["i"] = l.i; d["d"] = l.d;
      d["mode"] = l.enabled ? l.mode : String("off");
      String s; serializeJson(d, s); ws.sendTXT(s);
    }
  }

  if (now - lastIngest >= INGEST_EVERY_MS) { lastIngest = now; enviarLecturas(); }
}
