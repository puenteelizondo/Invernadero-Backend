# Invernadero-Backend

Backend de un sistema de monitoreo y control de invernaderos: ingesta de lecturas de sensores, control de actuadores, distribución en tiempo real por WebSocket, exportación a Excel, y un modelo multi-tenant (varios invernaderos, cada uno con sus propios dueños, operadores y usuarios de solo lectura).

Construido con Django + Django REST Framework + Django Channels, sobre PostgreSQL y Redis, empaquetado con Docker Compose.

---

## Tabla de contenido

1. [Resumen del backend](#resumen-del-backend)
2. [Tecnologías](#tecnologías)
3. [Arquitectura](#arquitectura)
4. [Estructura de carpetas](#estructura-de-carpetas)
5. [Instalación](#instalación)
6. [Variables de entorno](#variables-de-entorno)
7. [Base de datos y modelos](#base-de-datos-y-modelos)
8. [Autenticación](#autenticación)
9. [Usuarios y permisos](#usuarios-y-permisos)
10. [API completa](#api-completa)
11. [Flujos importantes](#flujos-importantes)
12. [WebSockets / tiempo real](#websockets--tiempo-real)
13. [Docker](#docker)
14. [Producción](#producción)
15. [CORS y frontend](#cors-y-frontend)
16. [Manejo de errores](#manejo-de-errores)
17. [Comandos útiles](#comandos-útiles)
18. [Testing](#testing)
19. [Solución de problemas](#solución-de-problemas)
20. [Seguridad](#seguridad)
21. [Checklist para desarrollar el frontend](#checklist-para-desarrollar-el-frontend)
22. [Observaciones / Pendientes](#observaciones--pendientes)

---

## Resumen del backend

**Qué problema resuelve.** Un invernadero tiene sensores (temperatura, humedad, etc.) que reportan lecturas constantemente, y actuadores (ventiladores, bombas, etc.) que hay que poder encender o apagar. El backend recibe esas lecturas, decide cuáles guardar permanentemente, las distribuye en tiempo real a quien esté viendo el panel, permite controlar los actuadores, y deja exportar el histórico a Excel — todo separado por invernadero, de forma que varios clientes puedan usar el mismo sistema sin ver los datos de los demás.

**Arquitectura.** Django + Django REST Framework para la API HTTP; Django Channels + Redis para WebSockets; PostgreSQL como base de datos relacional; Redis también como caché (política de guardado de lecturas y "último valor conocido").

**Tecnologías.** Python 3.13, Django 6.1.1, DRF 3.18.1, Channels 4.3.2 + channels-redis, Daphne (servidor ASGI), PostgreSQL 18, Redis 8, Docker Compose.

**Base de datos.** PostgreSQL, con 6 apps de Django que se reparten los modelos: `users`, `greenhouses`, `sensors`, `actuators`, `readings`, `memberships`.

**Autenticación.** DRF con `SessionAuthentication` + `BasicAuthentication` para usuarios, más gestión de cuentas solo para administradores (`/admin/users/`; el registro público está cerrado), login/logout de sesión con cookie + CSRF (`/auth/login/`, `/auth/logout/`, `/auth/csrf/`, `/auth/me/`) y recuperación de contraseña (`/auth/password-reset/`); un esquema propio por API key (header `X-Device-Key`) para dispositivos físicos que solo envían lecturas. No hay JWT (ver [Autenticación](#autenticación)).

**API.** REST bajo `/api/v1/`, organizada por recurso: invernaderos, zonas, tipos de sensor, dispositivos, sensores, tipos de actuador, actuadores, lecturas, membresías.

**Tiempo real.** Un WebSocket por invernadero (`/ws/greenhouses/<id>/`) que recibe lecturas de sensores y cambios de estado de actuadores según ocurren.

**Despliegue.** Todo el sistema (Django, PostgreSQL, Redis) corre con `docker compose up`. Solo existe configuración de **desarrollo** (`config/settings/dev.py`); no hay `prod.py` ni Dockerfile/compose separados para producción todavía (ver [Producción](#producción) y [Observaciones](#observaciones--pendientes)).

---

## Tecnologías

Tomado directamente de `requirements.txt` y `config/settings/base.py`:

| Tecnología | Versión | Para qué se usa en este proyecto |
|---|---|---|
| Python | 3.13 (`Dockerfile`) | Lenguaje base |
| Django | 6.1.1 | Framework web, ORM, admin, sistema de usuarios |
| djangorestframework | 3.18.1 | API REST (serializers, viewsets, permisos, paginación) |
| psycopg[binary] | 3.3.3 | Driver de PostgreSQL para Django |
| django-environ | 0.13.0 | Leer configuración desde variables de entorno / `.env` |
| django-filter | 26.1 | Filtros de query params en los endpoints de listado |
| channels | 4.3.2 | WebSockets sobre Django (protocolo ASGI) |
| channels-redis | 4.3.0 | Backend de Redis para el "channel layer" de Channels (necesario para que los mensajes se distribuyan entre procesos/workers) |
| daphne | 4.2.3 | Servidor ASGI que sirve HTTP y WebSocket en el mismo proceso |
| XlsxWriter | 3.2.9 | Generar los archivos `.xlsx` de exportación |
| PostgreSQL | 18 (imagen Docker `postgres:18`) | Base de datos relacional, persistencia de todo el dominio |
| Redis | 8 (imagen Docker `redis:8-alpine`) | Channel layer de Channels **y** backend de caché de Django (dos usos distintos, ver más abajo) |
| Docker / Docker Compose | — | Empaquetado y orquestación de los 3 servicios (web, db, redis) |

No hay Celery, ni cola de mensajes, ni ningún otro servicio en `docker-compose.yml` más allá de los tres listados.

---

## Arquitectura

```mermaid
flowchart TD
    DEVICE["Dispositivo físico\n(ESP32, etc.)"] -->|"POST /readings/ingest/\nheader X-Device-Key"| API
    FRONTEND["Frontend / Cliente HTTP"] -->|"Basic Auth o sesión"| API["Django + DRF\n(Daphne, ASGI)"]
    API --> AUTHN["Autenticación\n(Session / Basic / DeviceKey)"]
    AUTHN --> PERM["Permisos\n(IsGreenhouseMember, roles Owner/Operator/Viewer)"]
    PERM --> LOGIC["Lógica de negocio\n(viewsets, serializers, persistence.py)"]
    LOGIC --> PG[(PostgreSQL)]
    LOGIC --> REDIS_CACHE[(Redis - caché\npolítica de guardado)]
    LOGIC --> PUBLISH["publish_event()"]
    PUBLISH --> REDIS_LAYER[(Redis - channel layer)]
    REDIS_LAYER --> WS["WebSocket\n/ws/greenhouses/id/"]
    WS --> FRONTEND
```

**Flujo de una petición HTTP típica** (ej. crear un sensor):

```text
Cliente
   ↓
Endpoint (/api/v1/sensors/)
   ↓
Autenticación (Session o Basic) → identifica al usuario
   ↓
Permiso (IsGreenhouseMember) → ¿está autenticado? ¿es miembro del invernadero?
   ↓
get_queryset() del ViewSet → filtra a solo los invernaderos donde el usuario tiene Membership
   ↓
Serializer → valida los datos del body
   ↓
perform_create() → exige además ser Owner del invernadero (GreenhouseScopedMixin)
   ↓
PostgreSQL → guarda el registro
   ↓
Respuesta JSON
```

**Flujo de una lectura de sensor** (el caso más específico del sistema):

```text
Dispositivo → POST /readings/ingest/ (X-Device-Key)
   ↓
Se valida: sensor existe, pertenece a ESE dispositivo, está activo, valor dentro de rango físico, timestamp no absurdo
   ↓
should_persist() consulta Redis: ¿toca guardar esta lectura según la política del sensor?
   ↓
  Sí → se agrega a un bulk_create() hacia PostgreSQL (Reading)
  (siempre, se haya guardado o no) → mark_latest() actualiza Redis con el valor más reciente
   ↓
publish_event() → Redis (channel layer) → todos los WebSocket conectados a ese invernadero reciben el evento "sensor_reading"
```

---

## Estructura de carpetas

```text
Invernadero-Backend/
├── apps/
│   ├── actuators/        # Tipos de actuador, actuadores, historial de estado
│   ├── common/            # Utilidad compartida: publish_event() (puente hacia WebSocket) y manejador de excepciones (409)
│   ├── greenhouses/       # Invernadero y Zona
│   ├── memberships/       # Modelo multi-tenant: quién tiene qué rol en qué invernadero
│   ├── readings/          # Lecturas: ingesta, consulta, exportación a Excel, política de persistencia
│   ├── realtime/           # Consumer y routing de WebSocket (Django Channels)
│   ├── sensors/           # Tipos de sensor, dispositivos físicos (Device), sensores
│   └── users/              # Modelo de usuario personalizado
├── config/
│   ├── settings/
│   │   ├── base.py         # Configuración común (todo lo que no cambia entre entornos)
│   │   └── dev.py           # Configuración de desarrollo (hoy solo importa base.py)
│   ├── asgi.py              # Punto de entrada ASGI: HTTP + WebSocket
│   ├── urls.py              # Enrutado raíz de la API
│   └── wsgi.py              # Punto de entrada WSGI (no usado en desarrollo; ver Producción)
├── .env.example             # Plantilla de variables de entorno (sin secretos reales)
├── docker-compose.yml       # Servicios: db (PostgreSQL), redis, web (Django)
├── Dockerfile                # Imagen de la app Django
├── manage.py
└── requirements.txt
```

Cada app de `apps/` sigue el patrón estándar de Django (`models.py`, `views.py`, `serializers.py`, `urls.py`, `admin.py`, `migrations/`), con estas piezas propias que vale la pena señalar:

| Carpeta | Responsabilidad específica |
|---|---|
| `apps/users` | Solo el modelo `User` (hereda de `AbstractUser`, sin campos nuevos). No tiene lógica de negocio propia. |
| `apps/greenhouses` | El invernadero como unidad organizativa raíz, y sus zonas internas. |
| `apps/sensors` | Catálogo de tipos de sensor, dispositivos físicos (con su autenticación por API key) y los sensores mismos. |
| `apps/readings` | Todo lo relacionado con las lecturas: el modelo `Reading`, el endpoint de ingesta, la política de qué se guarda (`persistence.py`), la exportación a Excel (`exports.py`) y un comando de management para generar datos sintéticos de prueba. |
| `apps/actuators` | Catálogo de tipos de actuador, los actuadores y su historial de cambios de estado. |
| `apps/memberships` | El modelo multi-tenant: quién (`User`) puede hacer qué (`Role`) en cuál invernadero (`Greenhouse`). Aporta los permisos y mixins que usan **todas** las demás apps para filtrar por invernadero. |
| `apps/alerts` | Alertas por umbral: reglas por sensor (`AlertRule`), episodios de alerta (`Alert`), el motor que las evalúa durante la ingesta (`engine.py`) y los avisos por WebSocket y correo. |
| `apps/realtime` | El consumer de WebSocket y su enrutado (`routing.py`), importado desde `config/asgi.py`. |
| `apps/common` | `realtime.py`, con la función `publish_event()` — el único punto del código que sabe cómo hablar con el channel layer de Channels — y `exceptions.py`, el manejador de excepciones de la API (convierte `ProtectedError` en `409`). |

---

## Instalación

### Requisitos

- Docker y Docker Compose (todo el sistema, incluyendo PostgreSQL y Redis, corre en contenedores — no se necesita instalar Python, PostgreSQL ni Redis directamente en tu máquina).

### Clonar el proyecto

No identificado en el código (no hay una URL de repositorio remoto documentada en el proyecto en sí; el repositorio Git local existe, pero su remoto no forma parte del código fuente).

### Variables de entorno

Copia la plantilla y complétala:

```bash
cp .env.example .env
```

Edita `.env` con tus propios valores (ver la tabla completa en [Variables de entorno](#variables-de-entorno)). Como mínimo debes definir `DJANGO_SECRET_KEY`, `POSTGRES_DB`, `POSTGRES_USER` y `POSTGRES_PASSWORD` — el proyecto **no arranca** sin ellos (no tienen valor por defecto en el código).

Para generar una `DJANGO_SECRET_KEY` válida:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

### Levantar los servicios

```bash
docker compose up --build
```

Esto construye la imagen de `web` (Python 3.13 + dependencias de `requirements.txt`) y levanta tres contenedores: `db` (PostgreSQL 18), `redis` (Redis 8) y `web` (Django, servido por el `runserver` de Django — que delega automáticamente en Daphne por tener `channels`/`daphne` instalados y `ASGI_APPLICATION` configurado). `web` espera a que `db` y `redis` reporten estar saludables (`healthcheck`) antes de arrancar.

### Base de datos: migraciones

Con los contenedores corriendo, en otra terminal:

```bash
docker compose exec web python manage.py migrate
```

Esto crea todas las tablas a partir de las migraciones ya versionadas en cada app (`apps/*/migrations/`). No se necesita `makemigrations` para un primer arranque — las migraciones iniciales ya existen en el repositorio; solo usarías `makemigrations` si tú mismo modificas un `models.py`.

### Crear un usuario administrador

```bash
docker compose exec web python manage.py createsuperuser
```

Este es el único mecanismo para crear el primer usuario — no hay un endpoint público de registro (ver [Autenticación](#autenticación)).

### Verificar que funciona

- API: `http://localhost:8000/api/v1/greenhouses/` (pide autenticación)
- Panel de administración: `http://localhost:8000/admin/`
- PostgreSQL expuesto en `127.0.0.1:5432` (solo accesible desde tu propia máquina, para conectar con un cliente como DBeaver o pgAdmin)

---

## Variables de entorno

Tomadas de `config/settings/base.py` y `.env.example`. Ninguno de los valores mostrados aquí es un secreto real.

| Variable | Obligatoria | Descripción | Ejemplo / valor por defecto |
|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | No | Qué módulo de configuración usar. Tiene un valor por defecto escrito directamente en `manage.py`/`asgi.py`/`wsgi.py` (`config.settings.dev`), así que aunque no esté en `.env` el proyecto arranca en modo desarrollo. | `config.settings.dev` |
| `DJANGO_SECRET_KEY` | **Sí** | Clave secreta de Django (firmas de sesión, tokens CSRF, etc.). Sin ella, Django se niega a arrancar. | *(generar con `secrets.token_urlsafe`)* |
| `DEBUG` | No | Modo debug de Django. Por defecto `False` (falla hacia el lado seguro si se te olvida definirla). | `True` en desarrollo |
| `DJANGO_ALLOWED_HOSTS` | No | Lista de hosts permitidos, separados por coma. Por defecto, lista vacía. | `localhost,127.0.0.1` |
| `POSTGRES_DB` | **Sí** | Nombre de la base de datos. | `invernadero` |
| `POSTGRES_USER` | **Sí** | Usuario de PostgreSQL. | `invernadero` |
| `POSTGRES_PASSWORD` | **Sí** | Contraseña de PostgreSQL. | *(pon una propia)* |
| `POSTGRES_HOST` | **Sí** | Host de PostgreSQL. Dentro de Docker Compose es el nombre del servicio. | `db` |
| `POSTGRES_PORT` | No | Puerto de PostgreSQL. Por defecto `5432`. | `5432` |
| `REDIS_URL` | No | URL de Redis para el **channel layer** de Django Channels (WebSockets). Por defecto `redis://redis:6379/0`. | `redis://redis:6379/0` |
| `REDIS_CACHE_URL` | No | URL de Redis para el **backend de caché** de Django (política de persistencia de lecturas, "último valor conocido", y tokens de un solo uso del WebSocket). Por defecto `redis://redis:6379/1`. | `redis://redis:6379/1` |
| `CORS_ALLOWED_ORIGINS` | No | Lista de orígenes permitidos para CORS, separados por coma. Por defecto, lista vacía (ningún origen externo permitido hasta configurarlo). | `http://localhost:5173,http://127.0.0.1:5173` |
| `CSRF_TRUSTED_ORIGINS` | No | Solo `config.settings.prod`. Dominios desde los que se acepta un POST/PUT/PATCH/DELETE autenticado por sesión. Por defecto, lista vacía. | `https://tu-dominio.com` |
| `SECURE_HSTS_SECONDS` | No | Solo `config.settings.prod`. Segundos que le pide al navegador recordar "entra siempre por HTTPS". Por defecto, 7 días (`604800`). | `604800` |
| `EMAIL_BACKEND` | No | Backend de envío de correo (recuperación de contraseña). Por defecto, el de consola (imprime el correo en los logs en vez de enviarlo). | `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST` | No | Host SMTP. Solo importa si `EMAIL_BACKEND` es el de SMTP. Por defecto `localhost`. | `smtp.tu-proveedor.com` |
| `EMAIL_PORT` | No | Puerto SMTP. Por defecto `25`. | `587` |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | No | Credenciales SMTP. Por defecto vacías. | *(las que te dé tu proveedor de correo)* |
| `EMAIL_USE_TLS` | No | Si la conexión SMTP usa TLS. Por defecto `True`. | `True` |
| `DEFAULT_FROM_EMAIL` | No | Remitente de los correos que manda la API. Por defecto `no-responder@invernadero.local`. | `no-responder@tu-dominio.com` |

`.env` está excluido de Git (`.gitignore`) y de la imagen Docker (`.dockerignore`); solo `.env.example` se versiona.

---

## Base de datos y modelos

PostgreSQL 18, acceso vía el ORM de Django (driver `psycopg`). No hay SQL manual en el proyecto: todo pasa por migraciones de Django (`apps/*/migrations/`).

### Diagrama de relaciones

```mermaid
erDiagram
    USER ||--o{ MEMBERSHIP : tiene
    GREENHOUSE ||--o{ MEMBERSHIP : tiene
    GREENHOUSE ||--o{ ZONE : contiene
    GREENHOUSE ||--o{ DEVICE : tiene
    GREENHOUSE ||--o{ SENSOR : tiene
    GREENHOUSE ||--o{ ACTUATOR : tiene
    ZONE ||--o{ SENSOR : opcional
    ZONE ||--o{ ACTUATOR : opcional
    DEVICE ||--o{ SENSOR : reporta
    DEVICE ||--o{ ACTUATOR : controla
    SENSOR_TYPE ||--o{ SENSOR : clasifica
    ACTUATOR_TYPE ||--o{ ACTUATOR : clasifica
    SENSOR ||--o{ READING : genera
    ACTUATOR ||--o{ ACTUATOR_STATE_HISTORY : registra
    USER ||--o{ ACTUATOR_STATE_HISTORY : "cambió (opcional)"
```

### Modelos

**`users.User`** — usuario del sistema. Hereda de `AbstractUser` (Django) sin campos adicionales; es un modelo propio (no `auth.User`) para poder extenderlo en el futuro sin migrar toda la base de datos.

**`greenhouses.Greenhouse`** — un invernadero físico, nivel más alto de organización.

| Campo | Tipo | Notas |
|---|---|---|
| `name` | CharField(100) | único |
| `description` | TextField | opcional |
| `timezone` | CharField(64) | default `"UTC"`; validado contra `zoneinfo.available_timezones()`. Solo se usa para *mostrar* fechas — todo se guarda en UTC. |
| `is_active` | BooleanField | default `True` |
| `created_at` / `updated_at` | DateTimeField | automáticos |

**`greenhouses.Zone`** — subdivisión opcional de un invernadero.

| Campo | Tipo | Notas |
|---|---|---|
| `greenhouse` | FK → Greenhouse | `on_delete=PROTECT` |
| `name` | CharField(100) | único junto con `greenhouse` |
| `description` | TextField | opcional |

**`sensors.SensorType`** — catálogo de tipos de sensor (temperatura, humedad, etc.) con dos alcances: **global** (`greenhouse` nulo, lo gestiona el staff y lo ven todos) o **propio de un invernadero** (lo crea su Owner y solo lo ven los miembros de ese invernadero). Agregar un tipo nuevo es insertar una fila, no escribir código.

| Campo | Tipo | Notas |
|---|---|---|
| `greenhouse` | FK → Greenhouse | nulo = global; `CASCADE`; no se puede cambiar después de crear |
| `code` | SlugField(50) | único entre los globales y único por invernadero (`UniqueConstraint`); un tipo propio no puede repetir el código de uno global. Ej. `"temperature"` |
| `name` | CharField(100) | ej. `"Temperatura"` |
| `default_unit` | CharField(20) | ej. `"°C"` |
| `valid_min` / `valid_max` | FloatField | opcionales; rango físico plausible, usado para rechazar lecturas absurdas |
| `description` | TextField | opcional |

**`sensors.Device`** — un dispositivo físico (ej. un ESP32) que agrupa sensores y/o actuadores y se autentica con su propia API key.

| Campo | Tipo | Notas |
|---|---|---|
| `name` | CharField(100) | |
| `greenhouse` | FK → Greenhouse | `on_delete=PROTECT` |
| `key_prefix` | CharField(8) | único, primeros 8 caracteres de la API key, usado para búsquedas rápidas |
| `api_key_hash` | CharField(128) | hash de la API key (nunca se guarda en texto plano) |
| `is_active` | BooleanField | default `True` |
| `last_seen_at` | DateTimeField | se actualiza en cada ingesta exitosa |

**`sensors.Sensor`** — un sensor individual (modelo genérico, no hay una tabla por tipo de sensor).

| Campo | Tipo | Notas |
|---|---|---|
| `name` | CharField(100) | |
| `sensor_type` | FK → SensorType | `on_delete=PROTECT` |
| `device` | FK → Device | opcional, `on_delete=SET_NULL` |
| `greenhouse` | FK → Greenhouse | `on_delete=PROTECT` |
| `zone` | FK → Zone | opcional, `on_delete=SET_NULL`; debe pertenecer al mismo invernadero que el sensor (validado en `clean()` y en el serializer) |
| `unit` | CharField(20) | opcional; si está vacío, se usa `sensor_type.default_unit` |
| `is_active` | BooleanField | default `True` |
| `reading_interval_seconds` | PositiveIntegerField | default `60`; cada cuánto se espera una lectura (informativo) |
| `persist_interval_seconds` | PositiveIntegerField | opcional; ver [política de persistencia](#política-de-persistencia-de-lecturas) |
| `persist_deadband` | FloatField | opcional; ver política de persistencia |
| `config` | JSONField | default `{}`; configuración específica del tipo de sensor (calibración, pin, ganancia...) que no se filtra ni se consulta |

**`actuators.ActuatorType`** — catálogo de tipos de actuador (ventilador, bomba, válvula...). Mismo patrón y mismos alcances que `SensorType`.

| Campo | Tipo | Notas |
|---|---|---|
| `greenhouse` | FK → Greenhouse | nulo = global (igual que `SensorType`) |
| `code` | SlugField(50) | único entre globales y por invernadero, ej. `"fan"` |
| `name` | CharField(100) | ej. `"Ventilador"` |
| `description` | TextField | opcional |

**`actuators.Actuator`** — un actuador controlable.

| Campo | Tipo | Notas |
|---|---|---|
| `name` | CharField(100) | |
| `actuator_type` | FK → ActuatorType | `on_delete=PROTECT` |
| `device` | FK → Device | opcional, `on_delete=SET_NULL` |
| `greenhouse` | FK → Greenhouse | `on_delete=PROTECT` |
| `zone` | FK → Zone | opcional, `on_delete=SET_NULL` |
| `is_active` | BooleanField | default `True` |
| `state` | BooleanField | default `False`. **No se edita directamente** — solo a través del método `set_state()`, que además crea el historial y publica el evento de WebSocket |
| `config` | JSONField | default `{}` |

**`actuators.ActuatorStateHistory`** — bitácora de cada cambio de estado (no solo el estado actual).

| Campo | Tipo | Notas |
|---|---|---|
| `actuator` | FK → Actuator | `on_delete=PROTECT` |
| `state` | BooleanField | |
| `changed_by` | FK → User | opcional, `on_delete=SET_NULL` |
| `source` | CharField, choices | `manual` (default) o `automation` |
| `changed_at` | DateTimeField | automático; índice compuesto `(actuator, -changed_at)` |

**`readings.Reading`** — una lectura histórica persistida. Tabla intencionalmente angosta (pocas columnas) para que quepan más filas por página de disco.

| Campo | Tipo | Notas |
|---|---|---|
| `sensor` | FK → Sensor | `on_delete=PROTECT` |
| `timestamp` | DateTimeField | del propio dato del sensor, no de cuándo llegó al servidor |
| `value` | FloatField | |
| `created_at` | DateTimeField | automático |

Restricciones: único por `(sensor, timestamp)` (evita duplicados exactos); índice `(sensor, -timestamp)` para las consultas por sensor + rango de fechas, que son el patrón principal de consulta.

**`memberships.Membership`** — quién tiene acceso a qué invernadero y con qué rol. Es la pieza central del modelo multi-tenant.

| Campo | Tipo | Notas |
|---|---|---|
| `user` | FK → User | `on_delete=CASCADE` |
| `greenhouse` | FK → Greenhouse | `on_delete=CASCADE` |
| `role` | CharField, choices | `owner`, `operator`, `viewer` (default `viewer`) |
| `created_at` | DateTimeField | automático |

Único por `(user, greenhouse)`: un usuario tiene como máximo un rol por invernadero.

### Política de persistencia de lecturas

No todas las lecturas que llegan se guardan en PostgreSQL — decidido por sensor, con dos campos:

- `persist_interval_seconds`:
  - vacío (`null`) → nunca se guarda (solo tiempo real).
  - `0` → se guarda siempre.
  - `N` → como máximo una lectura guardada cada `N` segundos.
- `persist_deadband`: si está definido, se guarda igual (aunque no haya pasado el intervalo) cuando el valor cambia al menos ese tanto respecto a la **última lectura guardada**.

El "último guardado" se consulta en Redis (`apps/readings/persistence.py`), no en PostgreSQL — es un dato efímero de alta frecuencia que no necesita durabilidad ni una consulta SQL extra por cada lectura. La comparación de tiempo usa el `timestamp` que trae la propia lectura, no el reloj del servidor.

---

## Autenticación

Configurado en `REST_FRAMEWORK` dentro de `config/settings/base.py`. Existen **dos esquemas de autenticación completamente separados**, para dos tipos de clientes distintos:

### 1. Usuarios (personas)

```python
"DEFAULT_AUTHENTICATION_CLASSES": [
    "rest_framework.authentication.SessionAuthentication",
    "rest_framework.authentication.BasicAuthentication",
],
"DEFAULT_PERMISSION_CLASSES": [
    "rest_framework.permissions.IsAuthenticated",
],
```

- **BasicAuthentication**: usuario y contraseña en cada petición (header `Authorization: Basic <base64(usuario:contraseña)>`). Es lo que se usa para probar la API con Postman o desde un script.
- **SessionAuthentication**: usa la cookie de sesión de Django. Permite navegar la API desde el navegador después de iniciar sesión en `/admin/` o en `/api-auth/login/` (esta última ruta, montada en `config/urls.py`, es explícitamente solo para navegar la API en desarrollo — no la usa Postman ni un frontend real).
- **No hay JWT, ni tokens de acceso/refresh, ni verificación de email.**
- **Registro cerrado**: ya no existe `POST /api/v1/auth/register/` (responde 404). Las cuentas las crea un administrador (`is_staff`) desde `/api/v1/admin/users/` (`apps/users/views.py::AdminUserViewSet`) o desde el admin de Django. El primer administrador se crea con `python manage.py createsuperuser`. Al crear una cuenta se puede mandar una contraseña o dejar que el servidor genere una temporal, que se devuelve **una sola vez**.

  **Request**:
  ```json
  { "username": "nuevo_cliente", "email": "nuevo@ejemplo.com", "password": "unaContraseñaSegura123" }
  ```
  (`email` es opcional; `username` y `password` son obligatorios.)

  **Response 201**:
  ```json
  { "id": 8, "username": "nuevo_cliente", "email": "nuevo@ejemplo.com" }
  ```
  **Errores**: `400` si `username` ya existe, si `email` ya existe (aunque el modelo de Django no lo exige único por defecto, este endpoint sí lo valida), o si la contraseña no pasa las validaciones (ej. `{"password": ["Esta contraseña es demasiado común."]}`).

  Comparte el `AnonRateThrottle` global (`60/minute` por IP, ver [Seguridad](#seguridad)) — no tiene un límite propio.
- **Recuperación de contraseña**: dos pasos, público, sin autenticación previa (obviamente — si pudieras loguearte no necesitarías esto).

  **1. `POST /api/v1/auth/password-reset/`** — pides el reseteo con tu email:
  ```json
  { "email": "nuevo@ejemplo.com" }
  ```
  **Response 200, SIEMPRE el mismo mensaje exista o no el email**:
  ```json
  { "detail": "Si el email está registrado, se mandó un correo con instrucciones." }
  ```
  Que la respuesta no cambie según si el email existe es intencional — si variara, cualquiera podría usar este endpoint para averiguar qué correos tienen cuenta (enumeración de usuarios). El correo en sí (con un `uid` y un `token` de un solo uso, generados con el mismo mecanismo que usan las vistas de Django desde hace años — `default_token_generator`) solo se manda si el usuario existe.

  Con la configuración por defecto (sin `EMAIL_BACKEND` en tu `.env`), el correo **no se envía de verdad** — se imprime en los logs (`docker compose logs web`), así puedes probar el flujo completo en desarrollo sin credenciales SMTP reales. Ver [Variables de entorno](#variables-de-entorno) para configurar un SMTP real.

  **2. `POST /api/v1/auth/password-reset/confirm/`** — con el `uid`/`token` que llegaron por correo, pones la contraseña nueva:
  ```json
  { "uid": "OA", "token": "cz3x1a-1234567890abcdef1234567890ab", "password": "unaContraseñaNuevaSegura123" }
  ```
  **Response 200**: `{ "detail": "Contraseña actualizada." }`

  **Errores**: `400` si `uid`/`token` son inválidos o ya expiraron (`PASSWORD_RESET_TIMEOUT` de Django, 3 días por defecto — no lo cambiamos), o si la contraseña nueva no pasa `AUTH_PASSWORD_VALIDATORS`. El token es de un solo uso: en cuanto cambias la contraseña, `default_token_generator` lo invalida solo (no hace falta guardar/borrar nada aparte).
- **Login de sesión (cookie) para un frontend en el navegador**: Basic Auth manda usuario/contraseña en cada petición, lo cual es aceptable desde Postman/un script pero no algo que un frontend en el navegador deba hacer (implicaría guardar la contraseña en el cliente). Por eso, además de Basic Auth, existen 4 endpoints en `apps/users/views.py` que usan la `SessionAuthentication` que ya estaba declarada en `DEFAULT_AUTHENTICATION_CLASSES` desde el principio:

  - **`GET /api/v1/auth/csrf/`** — pone la cookie `csrftoken` en el navegador y de paso la devuelve en el body (`{"csrfToken": "..."}`) por si el cliente la necesita explícitamente. Hay que llamarlo una vez, antes del primer `POST`/`PATCH`/`DELETE`, porque Django rechaza esas peticiones con `403` si no existe la cookie todavía (protección CSRF estándar de Django, que sigue activa para `SessionAuthentication`).
  - **`POST /api/v1/auth/login/`** — body `{ "username": "...", "password": "..." }`. Valida credenciales con `django.contrib.auth.authenticate()` y, si son correctas, crea la sesión (cookie `sessionid`) con `django.contrib.auth.login()`. Responde `200` con `{ "id", "username", "email", "is_staff" }`, o `400` con `{"non_field_errors": ["Usuario o contraseña incorrectos."]}` si fallan. Hay que mandar la cookie CSRF de vuelta como header `X-CSRFToken` (Django lo exige en cualquier `POST` mientras haya una cookie `csrftoken`, incluso en este endpoint que técnicamente no requiere estar ya autenticado).
  - **`GET /api/v1/auth/me/`** — devuelve el usuario de la sesión actual (o de Basic Auth, si se manda así). `401` si no hay sesión ni credenciales válidas — así es como el frontend sabe si debe mostrar el login.
  - **`POST /api/v1/auth/logout/`** — cierra la sesión (`django.contrib.auth.logout()`). Requiere estar autenticado (con la sesión que se va a cerrar) y el header `X-CSRFToken`.

  Estos 4 endpoints son los que usa el frontend real (`Invernadero-Frontend`, ver [Checklist para desarrollar el frontend](#checklist-para-desarrollar-el-frontend)); Postman sigue usando Basic Auth por simplicidad (ver la colección, carpeta "Autenticación").
- **Creación de usuarios de staff/admin**: sigue siendo únicamente vía `python manage.py createsuperuser` o el panel `/admin/` — ni el registro público ni la recuperación de contraseña crean o tocan usuarios `is_staff`.
- Por defecto (`IsAuthenticated`), **todo** endpoint exige estar autenticado, salvo que la vista lo declare explícitamente distinto.

Ejemplo de cómo se autentica un script o Postman (Basic Auth):

```http
GET /api/v1/greenhouses/ HTTP/1.1
Host: localhost:8000
Authorization: Basic ZWxwYXRyb246bWljbGF2ZQ==
```

El frontend real, en cambio, usa el login de sesión de arriba (cookie `sessionid` + `X-CSRFToken`) en vez de Basic Auth — ver `Invernadero-Frontend/README.md`, sección "Flujo de autenticación", para el detalle de por qué (y cómo evita el problema de cookies entre orígenes con un proxy de desarrollo de Vite).

### 2. Dispositivos físicos (sensores/actuadores)

Esquema propio, `apps.sensors.authentication.DeviceKeyAuthentication`, usado **solo** en el endpoint de ingesta de lecturas:

```http
POST /api/v1/readings/ingest/ HTTP/1.1
X-Device-Key: <api_key completa que se mostró al crear el Device>
```

El dispositivo se identifica por los primeros 8 caracteres de su clave (`key_prefix`, indexado para búsqueda rápida) y se valida el resto contra un hash (`check_password`, el mismo mecanismo que usa Django para contraseñas de usuario — la clave nunca se guarda ni se puede recuperar en texto plano). Si es válida, se actualiza `last_seen_at` y la petición continúa con `request.auth` apuntando al `Device` (no hay un `User` real detrás de esta autenticación).

No hay expiración ni renovación de la API key: se usa hasta que alguien la rota explícitamente (`POST /api/v1/devices/{id}/rotate-key/`, que exige ser Owner del invernadero).

---

## Usuarios y permisos

El sistema es **multi-tenant por invernadero**: no hay un único rol global de "cliente" — el rol de cada usuario se define **por invernadero**, a través del modelo `Membership`.

### Roles (`memberships.Membership.Role`)

| Rol | Puede leer | Puede crear/editar recursos (sensores, actuadores, dispositivos, zonas) | Puede controlar actuadores (encender/apagar) | Puede administrar membresías (invitar/quitar usuarios) |
|---|---|---|---|---|
| **Owner** | Sí | Sí | Sí | Sí |
| **Operator** | Sí | No | Sí | No |
| **Viewer** | Sí | No | No | No |

Un usuario `is_staff` (marcado como staff en Django, ej. un superusuario) se trata como **Owner implícito de cualquier invernadero**, sin necesitar una fila de `Membership` — pensado para soporte técnico.

### Cómo se obtiene un rol

- Al crear un invernadero (`POST /api/v1/greenhouses/`), quien lo crea se vuelve automáticamente su **Owner** (se crea una `Membership` con `role=owner` en el mismo request — ver `GreenhouseViewSet.perform_create`).
- Para que alguien más tenga acceso a ESE invernadero, el Owner (o un staff) debe crear una `Membership` a mano vía `POST /api/v1/memberships/`, indicando el `id` numérico del usuario, el invernadero y el rol.

### Piezas que implementan esto (`apps/memberships/`)

- **`scoping.visible_greenhouse_ids(user)`**: devuelve la lista de ids de invernadero donde el usuario tiene `Membership` (o `None` si es staff, señal de "sin filtro"). La usa cada `get_queryset()` del sistema para que un usuario **nunca vea, ni pueda referenciar por id**, un recurso de un invernadero ajeno.
- **`mixins.GreenhouseScopedMixin`**: mixin para los ViewSets cuyos objetos pertenecen a un invernadero (Zone, Device, Sensor, Actuator, Reading). Filtra el queryset con `visible_greenhouse_ids` y, en `perform_create()`, exige además ser Owner del invernadero antes de permitir la creación.
- **`permissions.IsGreenhouseMember`**: permiso por defecto de casi todos los ViewSets. Lectura (`GET`/`HEAD`/`OPTIONS`) requiere cualquier rol; escritura requiere ser Owner.
- **`permissions.IsGreenhouseOperatorOrAbove`**: usado únicamente en la acción de cambiar el estado de un actuador — Owner **u** Operator, no Viewer.

### Qué endpoint puede usar cada rol (resumen)

| Endpoint | Cualquier autenticado | Miembro (cualquier rol) | Operator o superior | Solo Owner (o staff) |
|---|---|---|---|---|
| Leer tipos globales (`sensor-types`, `actuator-types`) | Sí | — | — | — |
| Leer tipos propios de un invernadero | — | Sí | — | — |
| Escribir tipos globales | Solo staff | — | — | — |
| Escribir tipos propios de un invernadero | — | — | — | Sí |
| Leer invernaderos/zonas/dispositivos/sensores/actuadores propios | — | Sí | — | — |
| Crear/editar/borrar invernaderos/zonas/dispositivos/sensores/actuadores | — | — | — | Sí |
| Cambiar el estado de un actuador (`POST /actuators/{id}/state/`) | — | — | Sí | Sí |
| Leer lecturas (`/readings/`) o exportarlas (`/readings/export/`) | — | Sí | — | — |
| Ver/crear/borrar membresías de un invernadero | — | — | — | Sí |
| Enviar lecturas (`/readings/ingest/`) | *(autenticación de dispositivo, no de usuario)* | | | |

---

## API completa

Prefijo común para todo lo siguiente: `http://localhost:8000/api/v1/` en desarrollo. Definido en `config/urls.py`.

Además de la API, existen:
- `/admin/` — panel de administración de Django.
- `/api-auth/` — login/logout de sesión de DRF, solo para navegar la API en el navegador durante desarrollo.

### Autenticación de usuarios (`apps/users`)

Ver [Autenticación](#autenticación) para el detalle completo de cada uno; resumen rápido:

| Endpoint | Método | Auth previa | Para qué |
|---|---|---|---|
| `/api/v1/admin/users/` | GET, POST | Staff | Listar (con `?search=`, `?is_active=`, `?is_staff=`, `?ordering=`) y crear cuentas. Si no mandas `password`, responde con `temporary_password` una sola vez. |
| `/api/v1/admin/users/{id}/` | GET, PATCH | Staff | Ver / editar (nombre, email, `is_active`, `is_staff`). Un admin no puede desactivarse ni quitarse el staff a sí mismo. No hay DELETE: se desactiva. |
| `/api/v1/admin/users/{id}/reset-password/` | POST | Staff | Genera una contraseña temporal nueva (o usa la que mandes). |
| `/api/v1/auth/csrf/` | GET | Ninguna | Poner la cookie `csrftoken` antes de un login de sesión. |
| `/api/v1/auth/login/` | POST | Ninguna | Crear una sesión (cookie `sessionid`) con usuario/contraseña. |
| `/api/v1/auth/logout/` | POST | Sesión o Basic | Cerrar la sesión actual. |
| `/api/v1/auth/me/` | GET | Sesión o Basic | Ver quién está autenticado (`401` si nadie). |
| `/api/v1/auth/password-reset/` | POST | Ninguna | Pedir el enlace de recuperación por email. |
| `/api/v1/auth/password-reset/confirm/` | POST | Ninguna (el `uid`/`token` del enlace hacen de credencial) | Poner la contraseña nueva. |

### Invernaderos y zonas (`apps/greenhouses`)

#### `GET /api/v1/greenhouses/`
Lista los invernaderos donde el usuario tiene `Membership` (o todos, si es staff). Filtros: `?is_active=true`. Búsqueda: `?search=<texto>` (sobre `name`). Orden: `?ordering=name` o `?ordering=created_at`.
- **Auth**: requerida. **Permiso**: cualquier miembro.

**Response 200** (paginado, `PageNumberPagination`, `PAGE_SIZE=20`):
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 1,
      "name": "Invernadero A",
      "description": "",
      "timezone": "America/Mexico_City",
      "is_active": true,
      "zones": [],
      "created_at": "2026-09-21T12:00:00Z",
      "updated_at": "2026-09-21T12:00:00Z"
    }
  ]
}
```

#### `POST /api/v1/greenhouses/`
Crea un invernadero. **Quien lo crea se vuelve automáticamente su Owner.**
- **Auth**: requerida. **Permiso**: cualquier autenticado (no requiere ser miembro de nada, porque está creando uno nuevo).

**Request**:
```json
{ "name": "Invernadero A", "timezone": "America/Mexico_City" }
```
**Response 201**: igual forma que el `GET` de detalle.
**Errores**: `400` si `timezone` no es una zona IANA válida o `name` ya existe (único).

#### `GET/PUT/PATCH/DELETE /api/v1/greenhouses/{id}/`
Detalle/edición/borrado de un invernadero. Escritura exige ser Owner.
- **Errores**: `403` si no eres miembro o no eres Owner (según el método); `404` si no es visible para ti (queryset ya filtrado).

#### `GET/POST /api/v1/zones/`, `GET/PUT/PATCH/DELETE /api/v1/zones/{id}/`
Zonas de un invernadero. Filtro: `?greenhouse=<id>`.

**Request (POST)**:
```json
{ "greenhouse": 1, "name": "Mesa Norte", "description": "" }
```
Mismas reglas de permisos que invernaderos (lectura = miembro, escritura = Owner).

---

### Sensores (`apps/sensors`)

#### `GET/POST /api/v1/sensor-types/`, `GET/PUT/PATCH/DELETE /api/v1/sensor-types/{id}/`
Catálogo con dos alcances. El listado devuelve los globales más los propios de los invernaderos del usuario; se puede filtrar con `?greenhouse=<id>`.

- Lectura: cualquier autenticado (solo ve los globales y los de sus invernaderos).
- Escritura de un tipo global (`greenhouse: null` u omitido): solo staff; si no, `403` «Solo el staff puede crear o modificar tipos globales.».
- Escritura de un tipo propio (`greenhouse: <id>`): Owner de ese invernadero o staff; si no, `403`.
- Cada objeto incluye `greenhouse` y `can_edit` (booleano calculado para el usuario que consulta).
- `400` si un tipo propio repite el código de un tipo global, o si se intenta cambiar `greenhouse` al editar.
- Un sensor solo acepta tipos globales o del mismo invernadero.
- Migración: `sensors.0002` y `actuators.0003`; los tipos existentes quedan como globales.

**Request (POST, solo staff)**:
```json
{ "code": "temperature", "name": "Temperatura", "default_unit": "°C", "valid_min": -20, "valid_max": 80 }
```

#### `GET/POST /api/v1/devices/`, `GET/PUT/PATCH/DELETE /api/v1/devices/{id}/`
Dispositivos físicos. Filtros: `?greenhouse=<id>`, `?is_active=true`.

**Request (POST, requiere ser Owner del invernadero)**:
```json
{ "name": "ESP32-Zona-Norte", "greenhouse": 1 }
```
**Response 201** — **única vez que se ve la API key en texto plano**:
```json
{
  "id": 3,
  "name": "ESP32-Zona-Norte",
  "greenhouse": 1,
  "key_prefix": "aB3dE9fG",
  "is_active": true,
  "last_seen_at": null,
  "created_at": "2026-09-21T12:00:00Z",
  "api_key": "aB3dE9fG_resto-de-la-clave-completa...",
  "warning": "Guarda esta clave ahora. No se volverá a mostrar."
}
```
**Errores**: `403` si no eres Owner del invernadero indicado.

#### `POST /api/v1/devices/{id}/rotate-key/`
Genera una nueva API key para el dispositivo (invalida la anterior). Requiere ser Owner.
**Response 200**: mismo formato `{id, key_prefix, api_key, warning}`.

#### `GET/POST /api/v1/sensors/`, `GET/PUT/PATCH/DELETE /api/v1/sensors/{id}/`
Filtros: `?greenhouse=`, `?zone=`, `?sensor_type=`, `?device=`, `?is_active=`. Búsqueda: `?search=` (sobre `name`).

**Request (POST, requiere ser Owner)**:
```json
{
  "name": "Sensor Temp Zona Norte",
  "sensor_type": 1,
  "device": 3,
  "greenhouse": 1,
  "zone": null,
  "persist_interval_seconds": 60,
  "persist_deadband": 0.5
}
```
**Errores**: `400` si `zone` no pertenece al mismo `greenhouse`; `403` si no eres Owner; `409` al hacer `DELETE` si el sensor ya tiene lecturas guardadas (`Reading.sensor` es `PROTECT`; ver [Borrados protegidos](#borrados-protegidos-409)). Para dejar de usar un sensor conservando su historial, desactívalo con `PATCH {"is_active": false}`.

#### `POST /api/v1/sensors/{id}/purge/`
Borrado **explícito e irreversible** de un sensor **junto con todas sus lecturas**. Existe para limpiar sensores de prueba o creados por error; el `DELETE` normal sigue negándose si hay historial.

**Request**:
```json
{ "confirm_name": "Sensor Temp Zona Norte" }
```
`confirm_name` debe coincidir exactamente con el nombre actual del sensor (como confirmación).

**Auth**: solo Owner del invernadero (o staff).

**Response 200**: `{ "sensor_id": 7, "name": "Sensor Temp Zona Norte", "readings_deleted": 1240 }`. Las lecturas y el sensor se borran en una sola transacción, y se limpian sus claves de caché (último valor / último guardado).

**Errores**: `400` si `confirm_name` falta o no coincide; `403` si no eres Owner; `404` si el sensor no existe o no es visible para ti.

---

### Actuadores (`apps/actuators`)

#### `GET/POST /api/v1/actuator-types/`, detalle
Mismos alcances y reglas que `sensor-types` (globales del staff, propios del Owner del invernadero).

#### `GET/POST /api/v1/actuators/`, `GET/PUT/PATCH/DELETE /api/v1/actuators/{id}/`
Filtros: `?greenhouse=`, `?zone=`, `?actuator_type=`, `?is_active=`, `?state=`.

**Nota importante**: el campo `state` es **de solo lectura** en estos endpoints — cambiarlo por `PATCH` **no tiene efecto** (queda ignorado). El único camino para prender/apagar es la acción dedicada de abajo.

#### `POST /api/v1/actuators/{id}/state/`
Cambia el estado (enciende/apaga) el actuador. **Permiso: Owner u Operator** (`IsGreenhouseOperatorOrAbove`) — un Viewer puede leer el actuador pero esta acción le da `403`.

**Request**:
```json
{ "state": true }
```
**Response 200**:
```json
{
  "actuator_id": 2,
  "name": "Ventilador principal",
  "state": true,
  "changed": true,
  "updated_at": "2026-09-21T12:30:00Z"
}
```
`changed` es `false` si el actuador ya estaba en ese estado (no hace nada, no genera historial ni evento). Cuando sí cambia, también se dispara un evento `actuator_state_changed` por WebSocket (ver [Tiempo real](#websockets--tiempo-real)).

#### `GET /api/v1/actuators/{id}/history/`
Últimos 100 cambios de estado del actuador (`ActuatorStateHistory`), del más reciente al más antiguo.

**Response 200**:
```json
[
  { "id": 10, "state": true, "changed_by_username": "jesus", "source": "manual", "changed_at": "2026-09-21T12:30:00Z" },
  { "id": 9, "state": false, "changed_by_username": null, "source": "automation", "changed_at": "2026-09-21T11:00:00Z" }
]
```

---

### Lecturas (`apps/readings`)

#### `POST /api/v1/readings/ingest/`
Recibe lecturas de un dispositivo. **Auth: `X-Device-Key`, no usuario.**

Acepta una sola lectura o varias bajo `"readings"`:
```json
{ "sensor_id": 5, "value": 24.5 }
```
o
```json
{
  "readings": [
    { "sensor_id": 5, "value": 24.5, "timestamp": "2026-09-21T12:30:00Z" },
    { "sensor_id": 6, "value": 61.2 }
  ]
}
```
`timestamp` es opcional (por defecto, la hora del servidor al recibir la petición). Se valida que no esté a más de 300 segundos en el futuro ni más de 7 días en el pasado (constantes `READING_TIMESTAMP_MAX_FUTURE_SECONDS` / `READING_TIMESTAMP_MAX_PAST_SECONDS` en `settings/base.py`), que el sensor exista, **pertenezca al dispositivo autenticado**, esté activo, y que el valor sea un número finito y esté dentro de `valid_min`/`valid_max` del tipo de sensor (si están definidos).

**Response 200** (siempre 200, incluso si algunas lecturas se rechazan):
```json
{
  "accepted": 1,
  "persisted": 1,
  "rejected": 1,
  "results": [
    { "index": 0, "status": "accepted", "persisted": true },
    { "index": 1, "status": "rejected", "errors": { "sensor_id": ["Este sensor no está asignado a tu dispositivo."] } }
  ]
}
```
Toda lectura *aceptada* se publica por WebSocket de inmediato, se guarde o no en PostgreSQL (`persisted` puede ser `false` si la política del sensor decide que no toca guardar esta vez).

#### `GET /api/v1/readings/`
Solo lectura (`ReadOnlyModelViewSet`). **Paginación por cursor** (no por página numerada, distinto al resto de la API) — pensado para listas largas ordenadas por fecha sin degradar con el volumen. Filtros: `?sensor=<id>`, `?date_from=<iso>`, `?date_to=<iso>`.

**Response 200**:
```json
{
  "next": "http://localhost:8000/api/v1/readings/?cursor=...",
  "previous": null,
  "results": [
    {
      "id": 100, "sensor": 5, "sensor_name": "Sensor Temp Zona Norte",
      "sensor_type": "temperature", "unit": "°C", "greenhouse": 1,
      "timestamp": "2026-09-21T12:30:00Z", "value": 24.5
    }
  ]
}
```

#### `GET /api/v1/readings/export/`
Genera un archivo `.xlsx` con las lecturas del rango pedido.

**Query params**: `date_from` (requerido), `date_to` (requerido), `sensor` (opcional).

- `date_to` debe ser posterior a `date_from`.
- El rango **no puede superar 366 días** (`READING_EXPORT_MAX_DAYS` en `settings/base.py`).
- Si mandas `sensor`, debe existir.
- El queryset se limita SIEMPRE a los invernaderos donde eres miembro — con o sin `?sensor=` — así que pedir un sensor ajeno simplemente da un archivo con 0 filas, no un error.

**Auth**: usuario autenticado (no hay `permission_classes` explícito en esta vista, así que aplica el default `IsAuthenticated`; cualquier rol de membership puede exportar, no solo Owner).

**Response 200**: archivo binario `.xlsx` (`Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`), nombre `lecturas_<YYYYMMDD>_<YYYYMMDD>.xlsx`. El header de respuesta `X-Row-Count` trae el total de lecturas escritas (suma de todas las hojas de sensor) — útil para el frontend sin tener que abrir el archivo.

**Estructura del libro** (`apps/readings/exports.py`). Solo aparecen los sensores que tienen lecturas en el rango pedido:

- **Hoja `Resumen`** (primera pestaña): una fila por sensor con invernadero, tipo, unidad, número de lecturas, mínimo, máximo, promedio, desviación estándar, última lectura y su fecha, y cuántas lecturas cayeron fuera del rango válido del tipo (en rojo si hay alguna). El nombre del sensor es un enlace a su hoja. Incluye una gráfica de barras con las lecturas por sensor.
- **Una hoja por sensor**, llamada `<nombre> #<sensor_id>` (máx. 31 caracteres, sin caracteres inválidos para Excel). Contiene:
  - **Tabla** (columnas A–D): fecha y hora (UTC), valor, promedio móvil de 10 lecturas y estado (`Normal` / `Fuera de rango`, según `valid_min`/`valid_max` del tipo de sensor). Con autofiltro y encabezado congelado.
  - **Estadísticas** (columnas F–G), como fórmulas de Excel sobre la tabla (se recalculan si filtras o editas): lecturas, mínimo y máximo con su fecha, promedio, mediana, desviación estándar, rango, primera/última lectura y su cambio, inicio/fin y duración del periodo, intervalo promedio entre lecturas, hueco más largo sin lecturas, y cantidad y porcentaje de lecturas fuera de rango.
  - **Gráfica de línea** del valor en el tiempo con una línea de tendencia (promedio móvil), y un **histograma** de la distribución de valores (10 intervalos).
  - Si el sensor tiene más de 2,000 lecturas, la gráfica usa una muestra guardada en las columnas AA–AC de la hoja (la tabla conserva todas las filas).
- Límite de Excel: 1,048,575 filas por hoja; si un sensor las supera, se exportan las más recientes y se indica en la hoja.

**Errores**: `400` si el rango de fechas es inválido o supera 366 días, o si `sensor` no existe.

---

### Alertas (`apps/alerts`)

Avisan cuando un sensor sale de los límites definidos para él. Se evalúan **durante la ingesta** (`POST /readings/ingest/`), sobre cada lectura aceptada.

**Modelos**
- `AlertRule` — regla de UN sensor, de dos clases (`rule_type`): **`threshold`** (fuera de límites): `min_value` y/o `max_value` (al menos uno) y `duration_seconds` (la condición debe sostenerse ese tiempo antes de abrir la alerta; `0` = al primer valor); y **`no_signal`** (sin señal): `duration_seconds` = segundos sin lecturas que se toleran (mínimo 30), sin límites. Además: `severity` (`warning`/`critical`), `is_active`, `notify_email`. Guarda su propio estado de evaluación: `breach_since` (desde cuándo está fuera de límites) y `active_alert` (la alerta abierta). Así sobrevive a reinicios y la ingesta lo recibe junto con la regla, sin consultas extra.
- `Alert` — un episodio: `kind` (`high`/`low`), `status` (`active`/`resolved`), `threshold`, `trigger_value`, `peak_value` (el valor más extremo mientras estuvo abierta), `opened_at` (el inicio de la violación, no el momento en que se sostuvo la duración), `resolved_at`, `acknowledged_at/by`. `Alert.rule` es `SET_NULL`: borrar una regla conserva el historial; borrar un sensor (o su purge) elimina sus reglas y alertas.

**Ciclo de vida.** Valor fuera de límites → se marca `breach_since`. Si se sostiene `duration_seconds` → se abre la `Alert`, se publica `alert_opened` y, si `notify_email`, se envía un correo a los propietarios del invernadero. Mientras siga abierta solo se actualiza el pico. Al volver a la normalidad se cierra sola (`alert_resolved`). Desactivar o borrar la regla con una alerta abierta también la cierra. Una lectura normal sin alerta abierta no cuesta ninguna escritura a la base.

**Endpoints**

| Endpoint | Quién | Notas |
|---|---|---|
| `GET /api/v1/alert-rules/` | Cualquier miembro | Filtros `?greenhouse=`, `?sensor=`, `?is_active=`. Incluye `has_active_alert`. |
| `POST/PUT/PATCH/DELETE /api/v1/alert-rules/{id}/` | Solo Owner (o staff) | `greenhouse` se toma del sensor. No se puede cambiar el sensor de una regla. `400` si no hay ningún límite o `min_value >= max_value`. |
| `GET /api/v1/alerts/` | Cualquier miembro | Solo lectura. Filtros `?greenhouse=`, `?status=active\|resolved`, `?sensor=`, `?severity=`, `?kind=high\|low\|stale`. Orden: más recientes primero. |
| `POST /api/v1/alerts/{id}/acknowledge/` | Owner u Operator | Marca "ya la vi" (idempotente) y publica `alert_acknowledged`. |

**Alertas de «sin señal» (`rule_type=no_signal`).** La ingesta solo se entera de lo que *llega*, así que detectar el silencio lo hace un proceso aparte: `python manage.py monitor_alerts [--interval 15] [--once]`, que cada 15 s revisa las reglas activas de sensores activos. En Docker lo ejecuta el servicio **`alerts-monitor`** de `docker-compose.yml` y `docker-compose.prod.yml` (`restart: unless-stopped`); **si ese servicio no está corriendo, estas alertas no se generan** (las de límites siguen funcionando porque viven en la ingesta).
- El «último dato» de un sensor sale de la caché del último valor recibido (cubre también lecturas que no se guardan en la base); si Redis la perdió, de la última lectura guardada; si el sensor nunca reportó, se cuenta desde que se creó.
- Se abre una `Alert` con `kind="stale"` cuando el silencio supera la tolerancia (`threshold` = segundos tolerados; `trigger_value`/`peak_value` = segundos sin datos, que se va actualizando en cada revisión; `opened_at` = el momento en que se superó la tolerancia) y se publica `alert_opened`; con `notify_email` se envía un correo de «Sin señal».
- **Se cierra sola** en cuanto llega cualquier lectura de ese sensor (lo hace la ingesta, sin esperar al monitor). Desactivar o borrar la regla también la cierra.
- Seguro ante varios monitores a la vez: la apertura se hace bajo `select_for_update` sobre la regla.
- Los sensores inactivos (`is_active=False`) se ignoran. Tolerancia recomendada: bastante mayor que el intervalo con el que reporta el dispositivo.

**Limpieza del historial.** Las alertas resueltas se conservan hasta que se borren:
- *Manual*: `POST /api/v1/alerts/purge/` con `{"greenhouse": <id>, "older_than_days": <opcional>}` (solo Owner o staff). Borra solo alertas **resueltas** de ese invernadero —nunca las activas—; sin `older_than_days` borra todas las resueltas. Responde `{"deleted": N}`. En el frontend es el botón «Limpiar historial» de la pestaña Historial.
- *Automática*: las resueltas con más de `ALERT_RETENTION_DAYS` días (por defecto **90**, `0` la desactiva; se define en `.env`) se borran solas. No hay programador de tareas en el proyecto, así que la limpieza se dispara como mucho una vez al día, cuando se resuelve una alerta (`engine.maybe_cleanup`, protegida con `cache.add` para que solo un proceso la ejecute).
- *Por consola / cron*: `python manage.py purge_old_alerts [--days N]`.

**Correo.** Usa la configuración `EMAIL_*` de `settings/base.py` (por defecto imprime en consola; define `EMAIL_BACKEND`, `EMAIL_HOST`, etc. en `.env` para enviar de verdad). Se envía en un hilo aparte para no retrasar la ingesta, y un fallo de envío nunca afecta la ingesta. Solo se envía al **abrirse** la alerta, no al resolverse.

**Límites conocidos.** No hay histéresis (si el valor oscila justo en el límite se abren y cierran alertas seguidas; usa `duration_seconds` para amortiguarlo). Rendimiento medido (PostgreSQL y Redis reales, un proceso, 20 dispositivos × 10 sensores): ~390 lecturas/s sin reglas, ~320 con una regla por sensor que no se incumple y ~290 en el peor caso (todas las lecturas cambiando el estado de la alerta constantemente).

### Membresías (`apps/memberships`)

#### `GET/POST /api/v1/memberships/`, `GET/PUT/PATCH/DELETE /api/v1/memberships/{id}/`
Quién tiene acceso a qué invernadero. Solo el Owner del invernadero en cuestión (o staff) puede **ver, crear o borrar** sus membresías — el queryset ya viene filtrado a "invernaderos donde soy Owner", así que un no-Owner recibe `404` (no `403`) al intentar acceder a una membership que no puede ni ver.

**Request (POST) — dos formas de decir a quién invitas**:

Por `id` numérico del usuario (como antes):
```json
{ "user": 4, "greenhouse": 1, "role": "operator" }
```
Por `username` o `email` (`invite`, sin necesitar el id):
```json
{ "invite": "cliente1", "greenhouse": 1, "role": "operator" }
```
Si mandas ambos, `user` gana. Si no mandas ninguno, `400` con `{"user": ["Manda 'user' (id numérico) o 'invite' (username o email)..."]}`. Si `invite` no coincide con ningún usuario, `400` con `{"invite": ["No existe ningún usuario con username o email '...'."]}`.

**Response 201** (igual en ambos casos):
```json
{ "id": 7, "user": 4, "username": "cliente1", "greenhouse": 1, "role": "operator", "created_at": "2026-09-21T12:00:00Z" }
```
**Errores**: `400` si no se puede resolver a qué usuario invitar (ver arriba); `403` si no eres Owner (al intentar crear/borrar sabiendo el id de un invernadero ajeno donde tampoco eres miembro); `404` si el recurso no está en tu queryset visible.

---

### Tiempo real (`apps/realtime`)

#### `POST /api/v1/realtime/ws-token/`
Emite un token de un solo uso para abrir el WebSocket de tiempo real (ver [WebSockets](#websockets--tiempo-real)). Requiere estar autenticado (`IsAuthenticated`) por cualquiera de los métodos normales (Basic Auth o sesión); no valida rol ni invernadero aquí — eso lo hace `GreenhouseConsumer.connect()` al conectar con el token.

**Request**: sin cuerpo.

**Response 200**:
```json
{ "token": "kQ2f9x...s8h3", "expires_in": 30 }
```

**Errores**: `401` si no estás autenticado.

---

### Resumen de códigos de estado por tipo de operación

Códigos que realmente puede producir la API (no una lista genérica):

| Código | Cuándo aparece |
|---|---|
| `200 OK` | Lecturas exitosas, acciones (`state`, `rotate-key`), ingesta (aunque incluya rechazos parciales), export |
| `201 Created` | Creación exitosa de cualquier recurso |
| `204 No Content` | `DELETE` exitoso |
| `400 Bad Request` | Datos inválidos (serializer), rango de fechas inválido en export, timestamp fuera de tolerancia en ingesta |
| `401 Unauthorized` | Sin credenciales o credenciales inválidas (Basic/Session) |
| `403 Forbidden` | Autenticado pero sin el rol/permiso necesario (ej. no ser Owner para escribir, no ser Operator+ para controlar un actuador) |
| `409 Conflict` | Se intentó borrar algo que todavía tiene elementos que dependen de él (llaves foráneas `PROTECT`). Ver [Borrados protegidos](#borrados-protegidos-409) |
| `404 Not Found` | Recurso que no existe, o que existe pero no está en tu queryset visible (multi-tenant) |
| `429 Too Many Requests` | Se superó el rate limit (`AnonRateThrottle`/`UserRateThrottle`/`DeviceRateThrottle`, ver [Seguridad](#seguridad)). Trae el header `Retry-After` con los segundos a esperar. |
| `500 Internal Server Error` | No documentado explícitamente en el código como manejado — cualquier excepción no capturada cae aquí (comportamiento por defecto de Django/DRF) |

---

## Flujos importantes

### Registrar un invernadero nuevo y volverse su dueño
1. `POST /api/v1/greenhouses/` autenticado como cualquier usuario.
2. El backend crea el invernadero y, en la misma operación, una `Membership` con `role=owner` para quien lo creó.
3. Desde ahí, ese usuario puede crear zonas, dispositivos, sensores y actuadores dentro de su invernadero.

### Dar de alta un dispositivo y empezar a reportar lecturas
1. El Owner crea el `Device` (`POST /api/v1/devices/`) → recibe la API key **una sola vez**.
2. El Owner crea uno o más `Sensor` asociados a ese `Device` y a su invernadero.
3. El dispositivo físico manda `POST /api/v1/readings/ingest/` con el header `X-Device-Key` y el `sensor_id` de cada sensor que reporta.

### Invitar a alguien más a un invernadero
1. El Owner necesita el `id` numérico del usuario a invitar (no hay búsqueda por username en el endpoint; ver [Observaciones](#observaciones--pendientes)).
2. `POST /api/v1/memberships/` con `{"user": <id>, "greenhouse": <id>, "role": "operator" | "viewer" | "owner"}`.
3. Ese usuario, desde su próxima petición, ya ve el invernadero y puede actuar según su rol.

### Controlar un actuador y ver el cambio en tiempo real
1. Un cliente conectado por WebSocket a `/ws/greenhouses/<id>/`.
2. Alguien con rol Owner u Operator manda `POST /api/v1/actuators/{id}/state/`.
3. `Actuator.set_state()` actualiza el campo, crea una fila en `ActuatorStateHistory`, y llama a `publish_event()`.
4. Todos los WebSocket conectados a ese invernadero reciben el evento `actuator_state_changed` casi de inmediato.

### Exportar un histórico a Excel
1. `GET /api/v1/readings/export/?date_from=...&date_to=...` (rango ≤ 366 días).
2. El backend recorre las lecturas ordenadas por `(sensor, fecha)` una sola vez y arma el `.xlsx` con `XlsxWriter`: una hoja `Resumen` más una hoja por sensor con su tabla, estadísticas y gráficas (ver la estructura en el endpoint de exportación). En memoria solo vive un sensor a la vez, y el libro se devuelve como descarga.

### Borrados protegidos (409)

Varias llaves foráneas usan `on_delete=PROTECT` para no perder datos por accidente: un invernadero con dispositivos, sensores o actuadores; un sensor con lecturas; un tipo de sensor/actuador en uso. Django lanza `ProtectedError` al intentar borrarlos, y antes eso terminaba en un `500` con la página de depuración.

Ahora `apps/common/exceptions.py` (`custom_exception_handler`, registrado en `REST_FRAMEWORK["EXCEPTION_HANDLER"]` de `config/settings/base.py`) lo convierte en un `409 Conflict` con un mensaje en español que dice qué lo bloquea:

```json
{ "detail": "No se puede eliminar porque todavía tiene elementos que dependen de él (5 dispositivos, 12 sensores, 3 actuadores). Elimínalos o muévelos primero." }
```

Es un handler global: aplica a cualquier `DELETE` de la API, no solo a invernaderos o sensores. El resto de las excepciones siguen el comportamiento normal de DRF.

---

## WebSockets / tiempo real

- **Tecnología**: Django Channels 4 sobre Daphne (ASGI), con Redis como *channel layer* (`channels_redis.core.RedisChannelLayer`) — necesario para que los eventos se distribuyan aunque haya varios procesos/workers Django corriendo.
- **Endpoint**: `ws://localhost:8000/ws/greenhouses/<greenhouse_id>/` (o `wss://` en producción). Definido en `apps/realtime/routing.py` e incluido en `config/asgi.py`.
- **Autenticación / autorización**: mediante un **token corto, de un solo uso** (`apps/realtime/tokens.py`), emitido por `POST /api/v1/realtime/ws-token/` (`apps/realtime/views.py::WebSocketTokenView`) — un endpoint HTTP normal protegido con `IsAuthenticated`, que se llama ya autenticado (Basic Auth o sesión) antes de abrir el WebSocket, porque el handshake de WebSocket no puede mandar el header `Authorization`. El token vive en el caché de Django (Redis), expira solo a los `WS_TOKEN_TTL_SECONDS = 30` segundos, y se borra en cuanto se usa una vez.
  - El cliente lo manda como query param al conectar: `ws://localhost:8000/ws/greenhouses/<id>/?token=<token>`.
  - `GreenhouseConsumer.connect()` (`apps/realtime/consumers.py`) valida el token con `_authenticate()`; si falta, ya expiró o ya se usó, cierra la conexión con código **4401**.
  - Si el token es válido pero el usuario no tiene `Membership` en ese invernadero (y no es staff), `_user_can_view()` lo detecta y cierra con código **4403**. El criterio de autorización es el mismo que `IsGreenhouseMember` para lectura: cualquier rol (Owner, Operator o Viewer) alcanza.
  - Además de esto, sigue existiendo la validación de **origen** (`AllowedHostsOriginValidator`), activa solo cuando `DEBUG=False`.
- **Al conectar** (una vez pasada la autenticación): el servidor manda inmediatamente un evento `snapshot` con el último valor conocido de cada sensor activo (leído de Redis, no de PostgreSQL) y el estado actual de cada actuador activo del invernadero — así el cliente no arranca "en blanco".
- **Formato de todos los mensajes** (mismo sobre para cualquier evento):
```json
{
  "event": "sensor_reading",
  "timestamp": "2026-09-21T12:30:00.123456+00:00",
  "payload": { "...": "..." }
}
```
- **Eventos que existen hoy** (`apps/common/realtime.py` es el único punto que los publica):

| `event` | Se dispara cuando... | `payload` |
|---|---|---|
| `snapshot` | Te conectas | `{"sensors": [...], "actuators": [...]}` (ver estructura completa en `consumers.py::_build_snapshot`) |
| `sensor_reading` | Llega una lectura aceptada por `/readings/ingest/` (se haya guardado en PostgreSQL o no) | `{"sensor_id", "sensor_name", "sensor_type", "unit", "value", "timestamp", "persisted"}` |
| `alert_opened` | Una regla de alerta se incumplió (y se sostuvo su duración) | `{"alert_id", "rule_id", "sensor_id", "sensor_name", "unit", "kind", "severity", "threshold", "value", "opened_at"}` |
| `alert_resolved` | El valor volvió a la normalidad, o la regla se desactivó/borró | igual que `alert_opened` más `"resolved_at"` |
| `alert_acknowledged` | Un Owner u Operator reconoció la alerta | `{"alert_id", "sensor_id", "acknowledged_by"}` |
| `actuator_state_changed` | Un actuador cambia de estado de verdad (no si ya estaba en ese estado) | `{"actuator_id", "name", "greenhouse_id", "state", "changed_by", "source"}` |

- **Reconexión**: no identificado en el código — es responsabilidad del cliente; el backend no manda ningún mensaje de "resume" ni conserva mensajes perdidos durante una desconexión (al reconectar simplemente vuelves a recibir un `snapshot` fresco). Como el token es de un solo uso, cada reconexión necesita pedir un token nuevo con `POST /api/v1/realtime/ws-token/`.
- **Errores**: el consumer no envía mensajes de error estructurados; una conexión rechazada se cierra a nivel de protocolo WebSocket con un código de cierre (**4401** sin token/token inválido, **4403** sin permiso sobre el invernadero), no con un mensaje JSON. Una conexión rechazada por origen inválido (en producción) también se cierra a nivel de protocolo.

Ejemplo mínimo de conexión desde JavaScript:

```javascript
// 1) Pedir un token de un solo uso (ya autenticado por HTTP)
const res = await fetch("/api/v1/realtime/ws-token/", {
  method: "POST",
  credentials: "include", // o el header Authorization/Basic que use tu cliente
});
const { token } = await res.json();

// 2) Conectar el WebSocket con el token en la URL
const ws = new WebSocket(`ws://localhost:8000/ws/greenhouses/1/?token=${token}`);
ws.onclose = (event) => {
  if (event.code === 4401) { /* token ausente/expirado/ya usado: pedir uno nuevo */ }
  if (event.code === 4403) { /* usuario sin Membership en este invernadero */ }
};
ws.onmessage = (msg) => {
  const { event, timestamp, payload } = JSON.parse(msg.data);
  if (event === "snapshot") { /* estado inicial */ }
  if (event === "sensor_reading") { /* actualizar gráfica */ }
  if (event === "actuator_state_changed") { /* actualizar botón */ }
};
```

---

## Control automático (lazos PID)

App nueva: `apps/control/`. **El servidor no calcula el PID**: el cálculo corre en el ESP32. El servidor solo guarda la configuración de cada lazo, la versiona, se la entrega al dispositivo por WebSocket y reenvía la telemetría a la web. La telemetría **no** se guarda como `Reading` (solo vive 120 s en caché).

### Modelo

`ControlLoop` (un lazo por actuador, restricción única): `greenhouse`, `name`, `sensor`, `actuator`, `device` (se deduce del sensor/actuador; si son de dispositivos distintos → 400), `mode` (`off`, `on_off`, `p`, `pi`, `pid`), `direction` (`direct` = la salida sube la variable, p. ej. calefactor; `reverse` = la baja, p. ej. ventilador), `setpoint` (validado contra `valid_min/valid_max` del tipo de sensor), `hysteresis`, `kp`, `ki`, `kd`, `output_min`/`output_max` (0–100 %, min < max), `integral_limit`, `sample_time_ms` (100–60000), `enabled`, `version`, `applied_version`, `applied_at`, `updated_by`.

- `version` sube **solo** si cambió algún parámetro de configuración; un PATCH con los mismos valores no hace nada.
- `ControlLoopChange` guarda cada cambio como `{campo: {before, after}}` con usuario y fecha.
- Migración: `control.0001_initial` (solo agrega tablas).

### Permisos

Owner y operator crean y editan lazos; viewer solo los ve; staff todo. Se valida en el backend (`CanEditControlLoops`). Las escrituras tienen throttle propio `control_write` (60/min).

`GreenhouseSerializer` expone ahora `my_role` (`owner` / `operator` / `viewer`; el staff cuenta como `owner`) para que la web sepa qué mostrar editable.

### Endpoints

| Endpoint | Método | Auth | Para qué |
|---|---|---|---|
| `/api/v1/control-loops/?greenhouse=<id>` | GET, POST | Sesión (miembro) | Listar / crear lazos. |
| `/api/v1/control-loops/{id}/` | GET, PATCH, DELETE | Sesión | Ver / editar (manda solo lo que cambia) / borrar. |
| `/api/v1/control-loops/{id}/history/` | GET | Sesión | Últimos cambios de configuración. |
| `/api/v1/devices/ws-token/` | POST | `X-Device-Key` | Token de un solo uso (30 s) para abrir el WebSocket del dispositivo. |
| `/api/v1/devices/control-config/` | GET | `X-Device-Key` | Respaldo HTTP: la configuración actual de todos los lazos del dispositivo. |

### WebSocket del dispositivo: `/ws/device/?token=<token>`

Todos los mensajes son JSON con la clave `"event"`.

Servidor → dispositivo:

| `event` | Contenido | Cuándo |
|---|---|---|
| `config` | `{"loops": [...]}` | Al conectar (todos los lazos del dispositivo). |
| `config_update` | `{"loop": {...}}` | Al crear o cambiar un lazo. |
| `config_remove` | `{"loop_id": 3}` | Al borrar un lazo. |
| `ping` | — | Cada 25 s. Si no llega nada del dispositivo en 75 s, se cierra con código 4408. |

Dispositivo → servidor:

| `event` | Contenido | Efecto |
|---|---|---|
| `hello` | `{"firmware": "..."}` | Informativo. |
| `ack` | `{"loop_id", "version"}` | Marca `applied_version`; la web muestra "Aplicado por el ESP32". |
| `telemetry` | `{"loop_id", "version", "pv", "setpoint", "output", "error", "p", "i", "d", "mode"}` | Se reenvía a la web (máx. 1 cada 0.5 s por lazo). Si `output > 1 %` el actuador se marca encendido (`source="automation"`), si no, apagado; solo cuando cambia. |
| `pong` | — | Mantiene viva la conexión. |

El WebSocket de dispositivos no pasa por el chequeo de `Origin` (ni siquiera en producción): un ESP32 no es un navegador y no lo manda; su protección es el token de un solo uso.

Al grupo del invernadero (el WebSocket de la web) se le mandan `control_loop_updated`, `control_loop_applied`, `control_loop_deleted`, `control_telemetry` y `device_connection`.

### Probar sin hardware: simulador

`scripts/simulate_controller.py` se comporta como un ESP32: pide el token, abre el WebSocket, aplica la configuración que le llega, simula una planta térmica de primer orden, calcula el PID localmente y manda `ack` + `telemetry`.

```bash
pip install websockets
python scripts/simulate_controller.py --key <API_KEY_DEL_DISPOSITIVO> --speed 5
# --url http://localhost:8000 (por defecto)   --speed acelera el tiempo simulado
```

La variable simulada se manda como lectura por la ingesta normal (`POST /api/v1/readings/ingest/` con `X-Device-Key`), igual que un sensor físico, nunca por el WebSocket. Para probar con un sensor real, no lo uses en un dispositivo que ya reporta lecturas. Otras opciones: `--ambient`, `--gain`, `--tau`, `--delay`, `--noise`, `--ingest-interval`.

### Firmware de referencia (ESP32)

`firmware/esp32_control/esp32_control.ino` + su `README.md` (librerías, pines, cómo cargarlo con Arduino IDE o PlatformIO). PID en forma posicional con derivada sobre la medición, anti-windup, transferencia sin salto entre modos, On/Off con histéresis, salida PWM o relé por tiempo proporcional, y **fail-safe**: salida a 0 si pasan 10 s sin una lectura válida del sensor o si se pierde la configuración. Compila sin errores ni advertencias con el núcleo esp32 3.3 (placa *ESP32 Dev Module*), pero *todavía no se ha probado en hardware real*. La página **Control** de la web genera este mismo programa con los ids, pines y sensores de tu dispositivo ya puestos.

## Docker

`docker-compose.yml` define tres servicios:

| Servicio | Imagen | Qué hace | Puertos | Persistencia |
|---|---|---|---|---|
| `db` | `postgres:18` | Base de datos | `127.0.0.1:5432:5432` (solo accesible desde tu propia máquina, para un cliente SQL) | Volumen nombrado `postgres_data` → `/var/lib/postgresql` |
| `redis` | `redis:8-alpine` | Channel layer de WebSockets + caché de Django | No expuesto al host | **Con volumen** (`redis_data:/data`) — los snapshots RDB que Redis ya guarda por su cuenta ahora sobreviven aunque se recree el contenedor (`down`/`up --build`), no solo un `restart`. Sigue siendo un caché reconstruible (no la fuente de verdad; las lecturas ya guardadas viven en PostgreSQL), pero perder el "último valor conocido" de cada sensor en cada rebuild era una molestia innecesaria en desarrollo. |
| `web` | Construida desde `Dockerfile` | Django/DRF/Channels servido por Daphne (vía `runserver`) | `8000:8000` | Código montado en vivo desde tu carpeta local (`.:/app`) — editas y Django recarga solo |

`web` espera (`depends_on: condition: service_healthy`) a que `db` y `redis` pasen su `healthcheck` antes de arrancar.

**Variables de entorno**: `db` las recibe interpoladas desde tu `.env` raíz (`${POSTGRES_DB}`, etc., vía Docker Compose); `web` carga el archivo completo con `env_file: .env`.

**Dockerfile**: imagen `python:3.13-slim`. Copia primero `requirements.txt` e instala dependencias (capa cacheada por Docker mientras no cambien), luego copia el resto del código. Corre como usuario no-root (`appuser`, uid 1000) — no como root. Comando por defecto: `python manage.py runserver 0.0.0.0:8000` (el propio log del contenedor avisa: *"This is a development server. Do not use it in a production setting."* — ver [Producción](#producción)).

**Comandos**:
```bash
docker compose up --build       # construir y levantar todo
docker compose up -d            # levantar en segundo plano
docker compose logs -f web      # ver logs de Django en vivo
docker compose exec web bash    # shell dentro del contenedor de Django
docker compose down             # detener y quitar contenedores (conserva el volumen de Postgres)
docker compose down -v          # detener y BORRAR también el volumen de Postgres
```

---

## Producción

`config/settings/prod.py` (nuevo) + `docker-compose.prod.yml` (nuevo) dan una base real de producción — no es "todo lo que necesitas para desplegar en cualquier hosting", pero ya no es cero.

### `config/settings/prod.py`

Hereda de `base.py` (`from .base import *`) y solo agrega lo que es distinto en producción. Se activa con `DJANGO_SETTINGS_MODULE=config.settings.prod` (ya viene puesto así en `docker-compose.prod.yml`):

| Setting | Valor | Por qué |
|---|---|---|
| `DEBUG` | `False` (forzado) | No depende de que nadie recuerde poner `DEBUG=False` en el `.env` de producción. |
| `SECURE_SSL_REDIRECT` | `True` por defecto (`env.bool`) | Redirige HTTP → HTTPS automáticamente. |
| `SECURE_PROXY_SSL_HEADER` | `("HTTP_X_FORWARDED_PROTO", "https")` | Asume un proxy (Nginx u otro) delante que termina TLS y reenvía este header. Si no tienes proxy delante, quítalo. |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `True` | Las cookies de sesión/CSRF solo viajan por HTTPS. |
| `SECURE_HSTS_SECONDS` | 7 días por defecto (`env.int`) | Le dice al navegador "entra siempre por HTTPS a este dominio". Empieza corto a propósito, no en el año que sugiere Django para producción madura, para no dejarte un dominio inaccesible por HTTP mucho tiempo si algo queda mal configurado. |
| `CSRF_TRUSTED_ORIGINS` | `env.list(...)`, default vacío | Necesario en Django 4+ para que el frontend pueda mandar peticiones autenticadas por sesión desde su propio dominio. |
| `STATIC_ROOT` | `BASE_DIR / "staticfiles"` | Para que `collectstatic` tenga dónde juntar los estáticos del admin y de la browsable API de DRF. |

**No identificado / fuera de alcance a propósito**: cómo *servir* esa carpeta `STATIC_ROOT` (Nginx, un bucket, `whitenoise`, etc.) — no se agregó una dependencia nueva al proyecto sin que lo decidas tú.

### `docker-compose.prod.yml`

```
docker compose -f docker-compose.prod.yml up -d --build
```

Diferencias contra `docker-compose.yml` (desarrollo):
- `DJANGO_SETTINGS_MODULE=config.settings.prod`.
- Sin bind-mount del código (`.:/app`) — corre lo que quedó `COPY`-eado en la imagen al hacer build, nunca tu carpeta en vivo.
- El `command` corre `migrate` y `collectstatic --noinput` antes de levantar **Daphne directamente** (`daphne -b 0.0.0.0 -p 8000 config.asgi:application`), sin el autoreloader de `runserver`.
- El puerto de Postgres no se expone al host (en desarrollo se expone por conveniencia, para DBeaver/pgAdmin).

**Sigue faltando** (fuera de alcance de este archivo, depende de tu hosting real): un reverse proxy (Nginx u otro) delante de este contenedor para TLS y para servir `STATIC_ROOT` — Daphne solo no hace ninguna de las dos cosas de forma eficiente; backups del volumen de Postgres; un registry de imágenes si compilas en una máquina distinta a donde despliegas.

### `Dockerfile`

El `CMD` por defecto (`runserver`, el que usa `docker-compose.yml` de desarrollo) **no cambió** — sigue siendo el servidor de desarrollo, y sigue sirviendo WebSockets bien porque `runserver` delega en Daphne automáticamente al detectar `channels`/`ASGI_APPLICATION` (lo ves en los logs: *"Starting ASGI/Daphne..."*). El comentario viejo que decía *"Lo cambiaremos por Daphne en la Etapa 7"* ya no aplica — se corrigió para explicar esto, en vez de prometer un cambio que además habría roto el flujo de desarrollo (el bind-mount y el autoreload dependen de `runserver`).

Lo que sí se agregó al `Dockerfile`: `chown -R appuser:appuser /app` antes de cambiar de usuario — sin esto, `collectstatic` (que corre como `appuser`, no como root) no podía escribir en `/app/staticfiles`.

### Lo que sigue sin existir (constatado por ausencia, no una opinión)

- `config/wsgi.py` existe (por si algún día se sirve con un servidor WSGI tradicional para la parte HTTP), aunque el proyecto está pensado para ASGI (Daphne) porque necesita WebSockets — usar solo WSGI dejaría sin funcionar `/ws/...`.
- No hay reverse proxy (Nginx u otro) configurado en ningún `docker-compose*.yml`.
- No hay pipeline de CI/CD, ni definición de dónde correría `docker-compose.prod.yml` (tu propia máquina, un VPS, un servicio administrado) — eso depende de dónde decidas desplegar.

---

## CORS y frontend

**CORS configurado** mediante `django-cors-headers==4.9.0` (`requirements.txt`):

- `"corsheaders"` está en `INSTALLED_APPS` y `"corsheaders.middleware.CorsMiddleware"` en `MIDDLEWARE` (`config/settings/base.py`), colocado justo después de `SecurityMiddleware` y antes de `SessionMiddleware`/`CommonMiddleware` (requisito del propio middleware de CORS).
- `CORS_ALLOWED_ORIGINS` se lee de la variable de entorno `CORS_ALLOWED_ORIGINS` (`env.list(...)`), con **default vacío** — mismo criterio "fallar cerrado" que ya usa el proyecto para `DEBUG`/`ALLOWED_HOSTS`: si no configuras nada, ningún origen distinto puede llamar a la API. `.env.example` trae de ejemplo `CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173` (Vite por defecto).
- `CORS_ALLOW_CREDENTIALS = True` — permite mandar cookies de sesión entre orígenes, seguro aquí porque `CORS_ALLOWED_ORIGINS` es una lista explícita, nunca un wildcard (`CORS_ALLOW_ALL_ORIGINS` no se usa).

En la práctica: para que un frontend en otro origen (por ejemplo `http://localhost:5173` en desarrollo) pueda llamar a la API desde el navegador, su origen debe estar en `CORS_ALLOWED_ORIGINS` — si no, el navegador seguirá bloqueando las peticiones por la política de mismo origen.

Aparte de ese punto — que es un bloqueante real, no un detalle — esto es lo que el frontend necesita saber hoy:

- **URL base (desarrollo)**: `http://localhost:8000/api/v1/`
- **URL base (producción)**: No identificado en el código.
- **Autenticación**: Basic Auth (usuario/contraseña en cada petición) o cookie de sesión tras iniciar sesión — no hay token JWT que guardar en `localStorage`. Si el frontend usa Basic Auth, el usuario y contraseña deben mandarse en cada petición.
- **Headers requeridos**: `Content-Type: application/json` en los `POST`/`PUT`/`PATCH`; `Authorization: Basic ...` para autenticarse.
- **Formato de errores**: DRF estándar — un diccionario `{campo: [mensajes]}` en `400`, `{"detail": "mensaje"}` en `401`/`403`/`404`.
- **WebSocket**: `ws://localhost:8000/ws/greenhouses/<id>/?token=<token>` (o `wss://` en producción), con token de un solo uso obtenido antes vía `POST /api/v1/realtime/ws-token/` (ver [WebSockets](#websockets--tiempo-real)).
- **Paginación**: por número de página (`?page=`) en la mayoría de los endpoints (`PageNumberPagination`, `PAGE_SIZE=20`); por cursor (`?cursor=`) específicamente en `/readings/` — no se puede saltar a una página arbitraria ahí, solo avanzar/retroceder con el `next`/`previous` que trae la respuesta.

---

## Manejo de errores

- **Validación de datos**: a cargo de los serializers de DRF (`ModelSerializer` estándar, más validación explícita en `validate()`/`validate_<campo>()` donde el dominio lo exige — ej. que una zona pertenezca al mismo invernadero que el sensor, que un timestamp no esté fuera de tolerancia, que un valor esté dentro del rango físico del tipo de sensor). Un dato inválido produce `400` con el detalle por campo.
- **Permisos**: las clases de `apps/memberships/permissions.py` devuelven `403` cuando el usuario está autenticado pero no tiene el rol necesario; DRF devuelve `401` cuando ni siquiera hay autenticación válida.
- **Visibilidad multi-tenant**: un recurso que existe pero no es tuyo no da `403` sino `404` — el `get_queryset()` filtrado hace que, para ti, simplemente no exista (esto es consistente en toda la API salvo en `IsGreenhouseMember.has_object_permission`, que si el objeto sí resultó visible pero el método es de escritura, ahí sí da `403`).
- **Formato de error estándar de DRF**: `{"detail": "mensaje"}` para errores de permiso/autenticación/no encontrado; `{"campo": ["mensaje"]}` para errores de validación de un serializer.
- **Borrados protegidos**: hay un `exception_handler` propio (`apps/common/exceptions.py`) que convierte el `ProtectedError` de Django en un `409 Conflict` con mensaje claro (ver [Borrados protegidos](#borrados-protegidos-409)).
- **Errores no manejados explícitamente**: cualquier otra excepción imprevista sigue el manejo por defecto de Django/DRF (`500` en producción con `DEBUG=False`, página de traceback completa si `DEBUG=True`).

---

## Comandos útiles

```bash
# Instalar dependencias (si trabajas fuera de Docker; dentro de Docker ya están en la imagen)
pip install -r requirements.txt

# Levantar todo
docker compose up --build

# Migraciones
docker compose exec web python manage.py makemigrations   # solo si tú modificaste un models.py
docker compose exec web python manage.py migrate

# Crear un usuario administrador
docker compose exec web python manage.py createsuperuser

# Crear un usuario normal (sin acceso al admin) desde el shell
docker compose exec web python manage.py shell -c "from apps.users.models import User; User.objects.create_user(username='cliente1', password='una-contraseña-segura')"

# Shell interactivo de Django
docker compose exec web python manage.py shell

# Generar lecturas sintéticas para pruebas de rendimiento (Etapa 10)
docker compose exec web python manage.py seed_readings <sensor_id> --count 200000 --interval-seconds 10

# Ver logs de Django en vivo
docker compose logs -f web

# Ejecutar tests (ver siguiente sección: no hay tests implementados)
docker compose exec web python manage.py test
```

---

## Testing

No hay tests implementados. Cada app tiene un `tests.py` generado por `startapp`, con únicamente el comentario por defecto de Django (`# Create your tests here.`) — ninguna prueba real está escrita en el proyecto en este momento.

---

## Solución de problemas

- **`CommandError: You have not set ASGI_APPLICATION`** — falta la configuración de Channels; en este proyecto ya está resuelto en `base.py` (`ASGI_APPLICATION = "config.asgi.application"`), pero si aparece de nuevo, revisa que no se haya sobreescrito en `dev.py`.
- **El WebSocket rechaza la conexión (`WebSocket REJECT`)** — antes de que `ASGI_APPLICATION`/`config/asgi.py` estén bien configurados, Daphne rechaza cualquier intento de conexión a `/ws/...`. Confirma en los logs que el servidor arrancó como *"ASGI/Daphne"* y no como *"WSGI development server"*.
- **`Timeout reading from redis` / `Connection lost` en el WebSocket** — el servicio `redis` no está disponible o no terminó su healthcheck todavía. Confirma con `docker compose ps` que `redis` está `healthy`, y que `REDIS_URL`/`REDIS_CACHE_URL` apuntan al host correcto (`redis`, el nombre del servicio, no `localhost`, cuando corres dentro de Docker).
- **`relation "..." does not exist`** — falta aplicar migraciones de alguna app nueva. Corre `python manage.py migrate`; si el modelo es nuevo y no tiene migración todavía, primero `python manage.py makemigrations <app>`.
- **`AssertionError: basename argument not specified...` al registrar rutas** — un ViewSet usado con `router.register(..., ViewSet)` sin `basename` explícito necesita un atributo `queryset` de clase (aunque el `get_queryset()` real sea otro, como en varios ViewSets de este proyecto) para que DRF pueda inferir el nombre de las rutas.
- **CORS bloquea al frontend (`Access-Control-Allow-Origin` faltante)** — el origen del frontend no está en `CORS_ALLOWED_ORIGINS` (variable de entorno); agrégalo y reinicia el contenedor `web` (ver [CORS y frontend](#cors-y-frontend)).
- **Puerto ocupado (`8000` o `5432`)** — otro proceso ya está usando ese puerto en tu máquina; cambia el mapeo de puertos en `docker-compose.yml` o cierra el proceso que lo esté usando.
- **`DJANGO_SECRET_KEY` / variables de Postgres faltantes** — Django se niega a arrancar (no tienen valor por defecto). Revisa que tu `.env` exista y tenga todas las variables marcadas como obligatorias en la tabla de [Variables de entorno](#variables-de-entorno).
- **Ingesta rechaza todas las lecturas con "El timestamp está demasiado atrasado/adelantado"** — el reloj del dispositivo que envía los datos está desincronizado respecto al servidor por más de la tolerancia configurada (5 minutos adelante / 7 días atrás).

---

## Seguridad

Medidas que **sí** están implementadas en el código:

- **Hashing de contraseñas**: el mecanismo por defecto de Django (`AbstractUser` + `AUTH_PASSWORD_VALIDATORS`: similitud con datos del usuario, longitud mínima, contraseñas comunes, no-solo-numérica).
- **API keys de dispositivo hasheadas**: igual que una contraseña (`make_password`/`check_password`); solo se muestran en texto plano una vez, al crearse o rotarse.
- **Autorización por rol y por invernadero**: todo el sistema de `apps/memberships` (ver [Usuarios y permisos](#usuarios-y-permisos)).
- **Aislamiento multi-tenant a nivel de queryset**: un usuario no solo no puede *escribir* en un invernadero ajeno — no puede ni *verlo* ni referenciarlo por id en la mayoría de los endpoints.
- **CSRF**: `CsrfViewMiddleware` de Django está activo (aplica a peticiones autenticadas por sesión; `BasicAuthentication`, al no depender de cookies, no requiere token CSRF).
- **Secretos fuera del código**: `SECRET_KEY`, credenciales de base de datos y URLs de Redis se leen del entorno (`django-environ`), nunca están escritos en el código fuente; `.env` está excluido de Git y de la imagen Docker.
- **Contenedor sin root**: el `Dockerfile` crea y usa un usuario no-privilegiado (`appuser`) para correr la aplicación.
- **Validación de rango físico en lecturas**: rechaza valores absurdos según `valid_min`/`valid_max` del tipo de sensor.
- **CORS con allowlist explícita**: `django-cors-headers`, con `CORS_ALLOWED_ORIGINS` leído del entorno y default vacío (nada permitido hasta configurarlo explícitamente); ver [CORS y frontend](#cors-y-frontend).
- **Autenticación en el WebSocket**: token de un solo uso, de vida corta (`WS_TOKEN_TTL_SECONDS = 30`), emitido por un endpoint HTTP protegido con `IsAuthenticated`, más autorización por `Membership` sobre el invernadero al conectar; ver [WebSockets](#websockets--tiempo-real).
- **Rendimiento de la ingesta** (`POST /readings/ingest/`): medido con PostgreSQL 16 y Redis reales, un solo proceso `daphne`, 20 dispositivos × 10 sensores en paralelo. Antes de optimizar: ~5 peticiones/s (~50 lecturas/s) y ~400 ms por petición, porque cada petición verificaba la API key con PBKDF2. Después: ~40 peticiones/s (~400 lecturas/s) y ~20 ms por petición suelta. Qué se hizo: (1) la verificación PBKDF2 de la clave se recuerda 5 minutos en caché (clave de caché = SHA-256 de la API key; el valor es el hash guardado del dispositivo, así que rotar la clave o desactivar el dispositivo invalida al instante); (2) `last_seen_at` solo se escribe si pasaron más de 30 s; (3) los sensores del lote se cargan en una consulta (antes una por lectura); (4) los eventos WebSocket del lote se publican con un solo `async_to_sync`, en serie (en paralelo desbordaba el pool de `channels_redis`). Límites conocidos: un solo proceso satura su CPU hacia ~400 lecturas/s; para más, varios procesos o contenedores detrás de un balanceador. Tras reiniciar el backend o vaciar la caché, la primera petición de cada dispositivo vuelve a pagar el PBKDF2 (~0.4 s de CPU), así que cientos de dispositivos reconectando a la vez forman una cola breve.
- **Rate limiting básico**: `REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]` aplica `AnonRateThrottle` (`60/minute`, por IP) y `UserRateThrottle` (`300/minute`, por usuario autenticado) a toda la API por defecto — cubre `POST /api/v1/realtime/ws-token/` y cualquier intento de fuerza bruta contra Basic Auth. La ingesta de dispositivos (`POST /api/v1/readings/ingest/`) usa su propio throttle (`DeviceRateThrottle`, `120/minute` por dispositivo, `apps/sensors/throttling.py`) en vez del de usuario, porque ahí `request.user` siempre es `AnonymousUser` (se autentica por `X-Device-Key`, no como usuario) — con los throttles estándar, todos los dispositivos detrás de la misma IP (ej. varios ESP32 en la misma red) compartirían un solo límite en vez de tener cada uno el suyo.

Lo que **no** está implementado (constatado por ausencia en el código, no una opinión):

- **HTTPS**: no hay `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE` ni configuración similar en `base.py`/`dev.py` — depende enteramente de cómo se despliegue en producción (fuera del alcance de este código).
- **JWT / tokens de acceso para la API HTTP**: no implementado; la única forma de autenticarse como usuario contra la API REST es Basic Auth o sesión de Django (el token de un solo uso descrito arriba es exclusivo del handshake de WebSocket, no reemplaza la autenticación HTTP).
- ~~Registro de usuarios~~ — **cambiado**: el registro público se cerró; las cuentas las crea un administrador en `/api/v1/admin/users/`, ver [Autenticación](#autenticación).
- ~~Recuperación de contraseña~~ — **resuelto**: `POST /api/v1/auth/password-reset/` + `POST /api/v1/auth/password-reset/confirm/`, con envío de correo (`EMAIL_BACKEND`, por defecto a consola en desarrollo); ver [Autenticación](#autenticación) y [Variables de entorno](#variables-de-entorno).

---

## Checklist para desarrollar el frontend

- [ ] **URL base**: `http://localhost:8000/api/v1/` en desarrollo (producción: no identificado en el código, defínela cuando exista un despliegue real).
- [ ] **CORS**: el origen del frontend debe estar en `CORS_ALLOWED_ORIGINS` (variable de entorno del backend, no del frontend) — pídele al equipo de backend que agregue tu origen si el navegador bloquea las llamadas.
- [x] **Autenticación**: login de sesión con cookie + CSRF (`/auth/csrf/`, `/auth/login/`, `/auth/logout/`, `/auth/me/`) pensado para un frontend en el navegador, además de Basic Auth (para Postman/scripts). No hay JWT — no hay token que renovar ni guardar de forma especial. Ver [Autenticación](#autenticación).
- [x] **Cómo iniciar sesión**: `POST /api/v1/auth/login/` con `{username, password}` (después de pedir la cookie CSRF con `GET /api/v1/auth/csrf/`). Ver el flujo completo, con el problema de cookies entre orígenes resuelto vía proxy de Vite, en `Invernadero-Frontend/README.md`.
- [ ] **Endpoints disponibles**: ver [API completa](#api-completa) — autenticación, invernaderos, zonas, tipos de sensor, dispositivos, sensores, tipos de actuador, actuadores (+ acción `state` y `history`), lecturas (+ `ingest` y `export`), membresías.
- [ ] **Headers requeridos**: `Content-Type: application/json` en escrituras; cookie de sesión + `X-CSRFToken` (frontend) o `Authorization: Basic ...` (Postman/scripts) en todo lo demás salvo la ingesta de dispositivos (`X-Device-Key`, que no es para el frontend sino para hardware).
- [ ] **Formato de request/response**: JSON estándar; ver ejemplos reales en cada endpoint de la sección [API completa](#api-completa).
- [ ] **Manejo de errores**: `{detail: "..."}` para permisos/autenticación/no-encontrado; `{campo: ["..."]}` para validación. Ver [Manejo de errores](#manejo-de-errores).
- [ ] **Paginación**: por página (`?page=`) casi en todo; por cursor (`?cursor=`, solo avance/retroceso) en `/readings/`.
- [ ] **WebSocket**: primero `POST /api/v1/realtime/ws-token/` (autenticado) para obtener un token de un solo uso, luego conectar a `ws://localhost:8000/ws/greenhouses/<id>/?token=<token>`; conecta uno por invernadero que el usuario esté viendo; escucha los eventos `snapshot`, `sensor_reading`, `actuator_state_changed` (ver [WebSockets](#websockets--tiempo-real)). El token expira en 30s y es de un solo uso, así que pide uno nuevo justo antes de cada conexión/reconexión.
- [ ] **Roles**: el frontend debería ocultar/deshabilitar acciones de escritura según el rol del usuario en cada invernadero (Owner/Operator/Viewer) — el backend las rechaza igual (`403`), pero conviene reflejarlo en la interfaz para no ofrecer botones que van a fallar.
- [x] **Frontend real**: existe en `Invernadero-Frontend/` (carpeta hermana de este repo) — React + Vite + TypeScript + TanStack Query, con proxy de desarrollo para evitar el problema de cookies entre orígenes. Ver su propio `README.md` para cómo correrlo y cómo está organizado. No manda lecturas manualmente (eso es trabajo de los controladores físicos vía `X-Device-Key`); sí administra invernaderos, sensores, actuadores (con tipos "predesignados" con ícono) y membresías, y muestra todo en vivo por WebSocket.
- [ ] **Variables de entorno del frontend**: no aplica desde este repo — ver `Invernadero-Frontend/README.md`.
- [ ] **Diferencias desarrollo/producción**: hoy solo existe configuración de desarrollo; cuando exista un entorno de producción, la URL base, el esquema (`wss://` para WebSocket) y cómo se sirven juntos frontend/backend (mismo dominio, `CSRF_TRUSTED_ORIGINS`, etc.) deberán definirse según el hosting real.

---

## Observaciones / Pendientes

Cosas que valen la pena que sepas antes de construir el frontend o de llevar esto a producción — constatadas leyendo el código, no opiniones sobre cómo "debería" estar hecho.

- ~~Sin CORS configurado~~ — **resuelto**: `django-cors-headers` agregado y configurado con allowlist explícita (`CORS_ALLOWED_ORIGINS`, default vacío); ver [CORS y frontend](#cors-y-frontend). Falta que quien despliegue en producción agregue ahí el origen real del frontend.
- ~~WebSocket sin autenticación ni autorización~~ — **resuelto**: `GreenhouseConsumer.connect()` ahora exige un token de un solo uso emitido por `POST /api/v1/realtime/ws-token/` y valida `Membership` sobre el invernadero; ver [WebSockets](#websockets--tiempo-real).
- **`apps/actuators/permissions.py::CanControlActuators` — resuelto/limpiado**: el archivo ya no contiene la clase, solo un comentario explicando por qué quedó sin uso (fue reemplazada por `IsGreenhouseOperatorOrAbove` de `apps/memberships/permissions.py`) y el comando para borrar el archivo por completo cuando quieras (`del apps\actuators\permissions.py` en Windows / `rm apps/actuators/permissions.py` en Linux/Mac). Nada en el proyecto lo importa.
- ~~`REDIS_CACHE_URL` no está documentada en `.env.example`~~ — **resuelto**: ya aparece listada junto a `REDIS_URL` en `.env.example`, con comentario.
- ~~No hay endpoint de registro ni de login de API "real"~~ — **resuelto**: `/api/v1/admin/users/` para que un administrador cree cuentas (el registro público se cerró), `POST /api/v1/auth/password-reset/` + `/confirm/` para recuperación, y `POST /api/v1/auth/login/` + `/logout/` + `GET /api/v1/auth/me/` + `/csrf/` para login de sesión (cookie) pensado para un frontend en el navegador. Ver [Autenticación](#autenticación). Sigue dependiendo de `createsuperuser`/admin solo para crear usuarios `is_staff` — eso es intencional.
- ~~Invitar a un usuario a un invernadero requiere su `id` numérico~~ — **resuelto**: `POST /api/v1/memberships/` ahora también acepta `invite` (`username` o `email`) en vez de `user`; ver [Membresías](#membresías-appsmemberships).
- ~~El `Dockerfile` sigue usando el servidor de desarrollo / comentario desactualizado~~ — **resuelto**: el comentario ahora explica por qué `CMD` sigue siendo `runserver` en desarrollo (no es un descuido); producción usa Daphne directo vía `docker-compose.prod.yml`.
- ~~No hay `prod.py`, ni Dockerfile/compose de producción~~ — **resuelto** (con alcance acotado): ver [Producción](#producción). Sigue faltando un reverse proxy real y CI/CD, que dependen de dónde despliegues.
- ~~Redis no tiene volumen persistente~~ — **resuelto**: `docker-compose.yml` ahora monta `redis_data:/data`, así que el "último valor conocido" de cada sensor y el estado de la política de persistencia sobreviven aunque se recree el contenedor (`down`/`up --build`), no solo un `restart`. Sigue siendo un caché reconstruible, no la fuente de verdad — las lecturas ya guardadas en PostgreSQL nunca dependieron de esto.
- ~~Sin rate limiting~~ — **resuelto**: `AnonRateThrottle`/`UserRateThrottle` en toda la API (`60/minute`, `300/minute`) y `DeviceRateThrottle` propio para la ingesta (`120/minute` por dispositivo); ver [Seguridad](#seguridad). Los números son un punto de partida razonable, no medidos contra tráfico real — ajústalos en `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` (`config/settings/base.py`) si en la práctica resultan muy estrictos o muy laxos.
- **Sin tests automatizados** en ninguna app.

---

*Generado a partir de una lectura directa del código fuente del proyecto (no de documentación externa ni de supuestos). Cualquier comportamiento no descrito aquí no fue encontrado en el código al momento de escribir este documento.*
