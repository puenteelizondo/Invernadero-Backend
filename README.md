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

**Autenticación.** DRF con `SessionAuthentication` + `BasicAuthentication` para usuarios; un esquema propio por API key (header `X-Device-Key`) para dispositivos físicos que solo envían lecturas. No hay JWT ni endpoints de registro/login propios (ver [Autenticación](#autenticación)).

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
│   ├── common/            # Utilidad compartida: publish_event() (puente hacia WebSocket)
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
| `apps/realtime` | El consumer de WebSocket y su enrutado (`routing.py`), importado desde `config/asgi.py`. |
| `apps/common` | Un único archivo, `realtime.py`, con la función `publish_event()` — el único punto del código que sabe cómo hablar con el channel layer de Channels. |

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
| `REDIS_CACHE_URL` | No | URL de Redis para el **backend de caché** de Django (política de persistencia de lecturas, "último valor conocido"). Por defecto `redis://redis:6379/1` (nota: no aparece en `.env.example`, ver [Observaciones](#observaciones--pendientes)). | `redis://redis:6379/1` |

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

**`sensors.SensorType`** — catálogo global de tipos de sensor (temperatura, humedad, etc.). Agregar un tipo nuevo es insertar una fila, no escribir código.

| Campo | Tipo | Notas |
|---|---|---|
| `code` | SlugField(50) | único, ej. `"temperature"` |
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

**`actuators.ActuatorType`** — catálogo global de tipos de actuador (ventilador, bomba, válvula...). Mismo patrón que `SensorType`.

| Campo | Tipo | Notas |
|---|---|---|
| `code` | SlugField(50) | único, ej. `"fan"` |
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
- **No hay JWT, ni tokens de acceso/refresh, ni verificación de email, ni recuperación de contraseña.** `apps/users/views.py` existe pero está vacío (solo el `# Create your views here.` que deja `startapp`); no hay endpoint de registro público.
- **Creación de usuarios**: únicamente vía `python manage.py createsuperuser` o el panel `/admin/` (con un usuario staff ya existente). No hay forma de que alguien se registre por sí mismo a través de la API.
- Por defecto (`IsAuthenticated`), **todo** endpoint exige estar autenticado, salvo que la vista lo declare explícitamente distinto.

Ejemplo de cómo debe autenticarse el frontend (Basic Auth, que es lo único disponible hoy):

```http
GET /api/v1/greenhouses/ HTTP/1.1
Host: localhost:8000
Authorization: Basic ZWxwYXRyb246bWljbGF2ZQ==
```

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
| Leer catálogos globales (`sensor-types`, `actuator-types`) | Sí | — | — | — |
| Escribir catálogos globales | Solo staff (`IsAdminUser`) | — | — | — |
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
Catálogo global. Lectura: cualquier autenticado. Escritura: solo staff (`IsAdminUser`).

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
**Errores**: `400` si `zone` no pertenece al mismo `greenhouse`; `403` si no eres Owner.

---

### Actuadores (`apps/actuators`)

#### `GET/POST /api/v1/actuator-types/`, detalle
Catálogo global, mismas reglas que `sensor-types` (lectura abierta, escritura solo staff).

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

**Response 200**: archivo binario `.xlsx` (`Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`), nombre `lecturas_<YYYYMMDD>_<YYYYMMDD>.xlsx`, con columnas *Invernadero, Sensor, Tipo de sensor, Unidad, Fecha y hora (UTC), Valor*. El header de respuesta `X-Row-Count` trae el número de filas escritas — útil para el frontend sin tener que abrir el archivo.

**Errores**: `400` si el rango de fechas es inválido o supera 366 días, o si `sensor` no existe.

---

### Membresías (`apps/memberships`)

#### `GET/POST /api/v1/memberships/`, `GET/PUT/PATCH/DELETE /api/v1/memberships/{id}/`
Quién tiene acceso a qué invernadero. Solo el Owner del invernadero en cuestión (o staff) puede **ver, crear o borrar** sus membresías — el queryset ya viene filtrado a "invernaderos donde soy Owner", así que un no-Owner recibe `404` (no `403`) al intentar acceder a una membership que no puede ni ver.

**Request (POST, requiere el `id` numérico del usuario)**:
```json
{ "user": 4, "greenhouse": 1, "role": "operator" }
```
**Response 201**:
```json
{ "id": 7, "user": 4, "username": "cliente1", "greenhouse": 1, "role": "operator", "created_at": "2026-09-21T12:00:00Z" }
```
**Errores**: `403` si no eres Owner (al intentar crear/borrar sabiendo el id de un invernadero ajeno donde tampoco eres miembro); `404` si el recurso no está en tu queryset visible.

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
| `404 Not Found` | Recurso que no existe, o que existe pero no está en tu queryset visible (multi-tenant) |
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
2. El backend arma el `.xlsx` en memoria con `XlsxWriter` (modo `constant_memory`, para no acumular todo el libro en RAM) y lo devuelve como descarga.

---

## WebSockets / tiempo real

- **Tecnología**: Django Channels 4 sobre Daphne (ASGI), con Redis como *channel layer* (`channels_redis.core.RedisChannelLayer`) — necesario para que los eventos se distribuyan aunque haya varios procesos/workers Django corriendo.
- **Endpoint**: `ws://localhost:8000/ws/greenhouses/<greenhouse_id>/` (o `wss://` en producción). Definido en `apps/realtime/routing.py` e incluido en `config/asgi.py`.
- **Autenticación / autorización**: **no hay ninguna** dentro del consumer (`apps/realtime/consumers.py`) — `connect()` acepta la conexión sin comprobar quién es el usuario ni si tiene `Membership` en ese invernadero. La única validación que existe es de **origen** (`AllowedHostsOriginValidator`), y solo se activa cuando `DEBUG=False`; en desarrollo se omite a propósito para poder probar con Postman/wscat, que no mandan header `Origin`. Ver [Observaciones](#observaciones--pendientes) — esto es una brecha real de seguridad para producción.
- **Al conectar**: el servidor manda inmediatamente un evento `snapshot` con el último valor conocido de cada sensor activo (leído de Redis, no de PostgreSQL) y el estado actual de cada actuador activo del invernadero — así el cliente no arranca "en blanco".
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
| `actuator_state_changed` | Un actuador cambia de estado de verdad (no si ya estaba en ese estado) | `{"actuator_id", "name", "greenhouse_id", "state", "changed_by", "source"}` |

- **Reconexión**: no identificado en el código — es responsabilidad del cliente; el backend no manda ningún mensaje de "resume" ni conserva mensajes perdidos durante una desconexión (al reconectar simplemente vuelves a recibir un `snapshot` fresco).
- **Errores**: el consumer no envía mensajes de error estructurados; una conexión rechazada por origen inválido (en producción) se cierra a nivel de protocolo WebSocket, no con un mensaje JSON.

Ejemplo mínimo de conexión desde JavaScript:

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/greenhouses/1/");
ws.onmessage = (msg) => {
  const { event, timestamp, payload } = JSON.parse(msg.data);
  if (event === "snapshot") { /* estado inicial */ }
  if (event === "sensor_reading") { /* actualizar gráfica */ }
  if (event === "actuator_state_changed") { /* actualizar botón */ }
};
```

---

## Docker

`docker-compose.yml` define tres servicios:

| Servicio | Imagen | Qué hace | Puertos | Persistencia |
|---|---|---|---|---|
| `db` | `postgres:18` | Base de datos | `127.0.0.1:5432:5432` (solo accesible desde tu propia máquina, para un cliente SQL) | Volumen nombrado `postgres_data` → `/var/lib/postgresql` |
| `redis` | `redis:8-alpine` | Channel layer de WebSockets + caché de Django | No expuesto al host | **Sin volumen** — los datos de Redis son efímeros; si el contenedor se reinicia, se pierden (aceptable: son cachés reconstruibles, no la fuente de verdad) |
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

**No identificado en el código**: no existe un `config/settings/prod.py`, ni un `Dockerfile`/`docker-compose` separado para producción, ni configuración de Nginx, ni de HTTPS/dominios, ni de `STATIC_ROOT`/recolección de estáticos (`collectstatic`) con un servidor de archivos estáticos real.

Lo que **sí** está preparado en el código, pensando en que se agregue después:

- `DEBUG` controla explícitamente si se activa `AllowedHostsOriginValidator` en el WebSocket (solo en producción).
- `SECRET_KEY`, credenciales de base de datos y URLs de Redis ya salen de variables de entorno, no están hardcodeadas.
- `DJANGO_ALLOWED_HOSTS` ya es una variable de entorno lista para poblarse con el/los dominio(s) reales.
- `config/wsgi.py` existe (por si se sirve con un servidor WSGI tradicional para la parte HTTP), aunque el proyecto está pensado para ASGI (Daphne) porque necesita WebSockets — usar solo WSGI dejaría sin funcionar `/ws/...`.

Lo que haría falta definir para desplegar en producción (no existe en el proyecto hoy, así que no se documenta como si existiera):
- Un `prod.py` que herede de `base.py` con `DEBUG=False`, `ALLOWED_HOSTS` reales, y probablemente `SECURE_*` (HSTS, cookies seguras, etc.).
- Reemplazar el `CMD` del `Dockerfile` (hoy `runserver`, servidor de desarrollo) por Daphne invocado directamente, o un proceso equivalente detrás de un proxy (Nginx u otro).
- Manejo de archivos estáticos (`collectstatic` + servidor de estáticos).
- Redis con persistencia si se decide que el caché de "último valor conocido" deba sobrevivir un reinicio (hoy no la tiene, ver tabla de Docker).

---

## CORS y frontend

**No identificado en el código**: no hay `django-cors-headers` en `requirements.txt`, ni `CORS_ALLOWED_ORIGINS`/`CORS_ALLOW_ALL_ORIGINS` ni middleware de CORS alguno en `config/settings/base.py` o `dev.py`.

Esto significa, en la práctica: **hoy, un frontend corriendo en un origen distinto (por ejemplo `http://localhost:5173` durante desarrollo, o cualquier dominio en producción) no va a poder llamar a esta API desde el navegador** — el navegador bloqueará las peticiones por la política de mismo origen, porque el backend no manda los headers `Access-Control-Allow-Origin` necesarios. Esto habrá que resolverlo (típicamente agregando `django-cors-headers` y configurándolo) antes de que el frontend pueda consumir la API desde un origen distinto al del propio backend.

Aparte de ese punto — que es un bloqueante real, no un detalle — esto es lo que el frontend necesita saber hoy:

- **URL base (desarrollo)**: `http://localhost:8000/api/v1/`
- **URL base (producción)**: No identificado en el código.
- **Autenticación**: Basic Auth (usuario/contraseña en cada petición) o cookie de sesión tras iniciar sesión — no hay token JWT que guardar en `localStorage`. Si el frontend usa Basic Auth, el usuario y contraseña deben mandarse en cada petición.
- **Headers requeridos**: `Content-Type: application/json` en los `POST`/`PUT`/`PATCH`; `Authorization: Basic ...` para autenticarse.
- **Formato de errores**: DRF estándar — un diccionario `{campo: [mensajes]}` en `400`, `{"detail": "mensaje"}` en `401`/`403`/`404`.
- **WebSocket**: `ws://localhost:8000/ws/greenhouses/<id>/` (o `wss://` en producción), sin autenticación a nivel de conexión hoy (ver [Observaciones](#observaciones--pendientes)).
- **Paginación**: por número de página (`?page=`) en la mayoría de los endpoints (`PageNumberPagination`, `PAGE_SIZE=20`); por cursor (`?cursor=`) específicamente en `/readings/` — no se puede saltar a una página arbitraria ahí, solo avanzar/retroceder con el `next`/`previous` que trae la respuesta.

---

## Manejo de errores

- **Validación de datos**: a cargo de los serializers de DRF (`ModelSerializer` estándar, más validación explícita en `validate()`/`validate_<campo>()` donde el dominio lo exige — ej. que una zona pertenezca al mismo invernadero que el sensor, que un timestamp no esté fuera de tolerancia, que un valor esté dentro del rango físico del tipo de sensor). Un dato inválido produce `400` con el detalle por campo.
- **Permisos**: las clases de `apps/memberships/permissions.py` devuelven `403` cuando el usuario está autenticado pero no tiene el rol necesario; DRF devuelve `401` cuando ni siquiera hay autenticación válida.
- **Visibilidad multi-tenant**: un recurso que existe pero no es tuyo no da `403` sino `404` — el `get_queryset()` filtrado hace que, para ti, simplemente no exista (esto es consistente en toda la API salvo en `IsGreenhouseMember.has_object_permission`, que si el objeto sí resultó visible pero el método es de escritura, ahí sí da `403`).
- **Formato de error estándar de DRF**: `{"detail": "mensaje"}` para errores de permiso/autenticación/no encontrado; `{"campo": ["mensaje"]}` para errores de validación de un serializer.
- **Errores no manejados explícitamente**: no hay un `exception_handler` personalizado en `REST_FRAMEWORK`, así que cualquier excepción no prevista cae en el manejo por defecto de Django/DRF (`500` en producción con `DEBUG=False`, página de traceback completa si `DEBUG=True`).

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
- **CORS / el frontend no puede llamar a la API desde el navegador** — no está configurado (ver [CORS y frontend](#cors-y-frontend)); hay que agregarlo antes de conectar un frontend en otro origen.
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

Lo que **no** está implementado (constatado por ausencia en el código, no una opinión):

- **CORS**: no configurado (ver [CORS y frontend](#cors-y-frontend)).
- **Rate limiting / throttling**: no hay `DEFAULT_THROTTLE_CLASSES` ni `DEFAULT_THROTTLE_RATES` en `REST_FRAMEWORK`; nada limita cuántas peticiones por minuto puede mandar un cliente (ni de login, ni de ingesta).
- **HTTPS**: no hay `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE` ni configuración similar en `base.py`/`dev.py` — depende enteramente de cómo se despliegue en producción (fuera del alcance de este código).
- **Autenticación en el WebSocket**: ninguna (ver [WebSockets](#websockets--tiempo-real)) — cualquiera que sepa o adivine un `greenhouse_id` puede conectarse y recibir sus eventos en tiempo real, sin necesidad de token ni sesión.
- **JWT / tokens de acceso**: no implementado; la única forma de autenticarse como usuario es Basic Auth o sesión de Django.
- **Registro de usuarios / recuperación de contraseña**: no implementado.

---

## Checklist para desarrollar el frontend

- [ ] **URL base**: `http://localhost:8000/api/v1/` en desarrollo (producción: no identificado en el código, defínela cuando exista un despliegue real).
- [ ] **Resolver CORS primero**: hoy no está configurado; sin esto, el navegador bloqueará las llamadas desde un frontend en otro origen.
- [ ] **Autenticación**: Basic Auth (usuario + contraseña en cada petición) o sesión de Django. No hay JWT — no hay token que renovar ni guardar de forma especial; si usas Basic Auth, decide cómo vas a guardar/enviar la contraseña de forma segura en el cliente.
- [ ] **Cómo iniciar sesión**: no hay endpoint de login de API dedicado más allá de `/api-auth/login/` (pensado para navegar la API en desarrollo, con `SessionAuthentication`). Para un frontend real, lo disponible hoy es mandar `Authorization: Basic ...` en cada petición.
- [ ] **Endpoints disponibles**: ver [API completa](#api-completa) — invernaderos, zonas, tipos de sensor, dispositivos, sensores, tipos de actuador, actuadores (+ acción `state` y `history`), lecturas (+ `ingest` y `export`), membresías.
- [ ] **Headers requeridos**: `Content-Type: application/json` en escrituras; `Authorization: Basic ...` en todo lo demás salvo la ingesta de dispositivos (`X-Device-Key`, que no es para el frontend sino para hardware).
- [ ] **Formato de request/response**: JSON estándar; ver ejemplos reales en cada endpoint de la sección [API completa](#api-completa).
- [ ] **Manejo de errores**: `{detail: "..."}` para permisos/autenticación/no-encontrado; `{campo: ["..."]}` para validación. Ver [Manejo de errores](#manejo-de-errores).
- [ ] **Paginación**: por página (`?page=`) casi en todo; por cursor (`?cursor=`, solo avance/retroceso) en `/readings/`.
- [ ] **WebSocket**: `ws://localhost:8000/ws/greenhouses/<id>/`; conecta uno por invernadero que el usuario esté viendo; escucha los eventos `snapshot`, `sensor_reading`, `actuator_state_changed` (ver [WebSockets](#websockets--tiempo-real)). Hoy no requiere ni valida ningún tipo de token al conectar.
- [ ] **Roles**: el frontend debería ocultar/deshabilitar acciones de escritura según el rol del usuario en cada invernadero (Owner/Operator/Viewer) — el backend las rechaza igual (`403`), pero conviene reflejarlo en la interfaz para no ofrecer botones que van a fallar.
- [ ] **Variables de entorno del frontend**: no identificado en el código (este repositorio es solo el backend).
- [ ] **Diferencias desarrollo/producción**: hoy solo existe configuración de desarrollo; cuando exista un entorno de producción, la URL base, el esquema (`wss://` para WebSocket) y probablemente la forma de autenticarse deberán actualizarse.

---

## Observaciones / Pendientes

Cosas que valen la pena que sepas antes de construir el frontend o de llevar esto a producción — constatadas leyendo el código, no opiniones sobre cómo "debería" estar hecho.

- **Sin CORS configurado** (`apps/`, `config/settings/`) — ningún frontend en otro origen podrá llamar a la API desde el navegador hasta que se agregue `django-cors-headers` (o equivalente) y se configure.
- **WebSocket sin autenticación ni autorización** (`apps/realtime/consumers.py::GreenhouseConsumer.connect`) — cualquiera que conozca o adivine un `greenhouse_id` puede conectarse y recibir sus lecturas/eventos en tiempo real, sin sesión ni token. La única protección (validación de `Origin`) solo se activa en producción (`DEBUG=False`), nunca hoy en desarrollo.
- **`apps/actuators/permissions.py::CanControlActuators` es código muerto** — la clase sigue en el archivo (con un docstring que dice "marcador de posición hasta la Etapa 12"), pero `ActuatorViewSet` ya no la importa ni la usa; fue reemplazada por `IsGreenhouseOperatorOrAbove` de `apps/memberships/permissions.py`. No rompe nada, pero puede confundir a quien lea el código pensando que sigue activa.
- **`REDIS_CACHE_URL` no está documentada en `.env.example`** — existe como variable en `config/settings/base.py` con un valor por defecto (`redis://redis:6379/1`), pero no aparece listada junto a `REDIS_URL` en la plantilla de entorno, así que es fácil no darse cuenta de que existe.
- **No hay endpoint de registro ni de login de API "real"** — crear usuarios depende de `createsuperuser`/shell/admin; no hay forma de que un cliente se registre por sí mismo, ni de recuperar contraseña.
- **Invitar a un usuario a un invernadero requiere su `id` numérico** (`POST /api/v1/memberships/`, campo `user`) — no hay forma de invitar por `username` o email desde la API; hay que consultarlo aparte (hoy, por shell o admin).
- **El `Dockerfile` sigue usando el servidor de desarrollo de Django** (`CMD ["python", "manage.py", "runserver", ...]`) — un comentario en el propio archivo dice *"Lo cambiaremos por Daphne (servidor ASGI) en la Etapa 7"*, pero el `CMD` no se actualizó; en la práctica funciona porque `runserver` delega en Daphne automáticamente al detectar `channels`/`ASGI_APPLICATION`, pero Django sigue emitiendo la advertencia de que no es apto para producción.
- **No hay `prod.py`, ni Dockerfile/compose de producción** — ver [Producción](#producción).
- **Redis no tiene volumen persistente** en `docker-compose.yml` — si el contenedor se reinicia, se pierde el "último valor conocido" de cada sensor (el snapshot que ve un cliente al conectarse) y el estado de la política de persistencia (aunque no las lecturas ya guardadas en PostgreSQL, que sí persisten).
- **Sin rate limiting** en ningún endpoint, incluida la ingesta de lecturas y el login — un dispositivo mal configurado (o un ataque) podría mandar peticiones sin límite alguno.
- **Sin tests automatizados** en ninguna app.

---

*Generado a partir de una lectura directa del código fuente del proyecto (no de documentación externa ni de supuestos). Cualquier comportamiento no descrito aquí no fue encontrado en el código al momento de escribir este documento.*
