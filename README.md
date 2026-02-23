# MakerSpaceAPI

Unified REST API and web frontend for makerspace NFC devices — machines, checkout terminals, ATM (Bankomat) and rental stations.

## Features

- **Machine sessions** — NFC-authenticated machine access with per-minute billing and balance checks
- **Product checkout** — EAN barcode scanning, alias EANs, stock tracking, full audit trail
- **Bankomat (ATM)** — top-up, balance transfer between members, PIN-protected payout to booking targets
- **Rental system** — UHF RFID item tracking with per-user permissions
- **Admin web UI** — dashboard, machine registration, product/inventory management, user list, booking targets, rentals; i18n (English / German, detected from browser)
- **OIDC login** — admin group and optional product-manager group via configurable OIDC claims
- **Device API tokens** — machines authenticate with bearer tokens; separate checkout-only restriction

## Requirements

- Python 3.12+
- MariaDB / MySQL 10.x+
- An OIDC provider (e.g. Authentik, Keycloak) for admin login

## Quick start (Docker)

```bash
cp .env.example .env
# Edit .env — set OIDC credentials, SECRET_KEY, database passwords
docker compose up -d
```

The app runs on port `8000`. Alembic migrations are applied automatically on startup.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env

alembic upgrade head
uvicorn app.main:app --reload
```

Interactive API docs: <http://localhost:8000/api/docs>

## Configuration

All settings are loaded from environment variables or a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `mysql+pymysql://makerspace:makerspace@localhost:3306/makerspaceapi` | SQLAlchemy connection string |
| `SECRET_KEY` | `change-me-in-production` | Session signing key — **change this** |
| `DEBUG` | `false` | Enables HTTP-only session cookies for local dev |
| `BASE_URL` | `http://localhost:8000` | Public base URL |
| `OIDC_CLIENT_ID` | | OIDC client ID |
| `OIDC_CLIENT_SECRET` | | OIDC client secret |
| `OIDC_DISCOVERY_URL` | | OIDC discovery URL (`.well-known/openid-configuration`) |
| `OIDC_REDIRECT_URI` | `http://localhost:8000/auth/callback` | OAuth2 callback URL |
| `OIDC_GROUP_CLAIM` | `groups` | JWT claim that contains group memberships |
| `OIDC_ADMIN_GROUP` | `makerspace-admins` | Group name that grants full admin access |
| `OIDC_PRODUCT_MANAGER_GROUP` | *(empty)* | Group name for product-manager role (optional, see below) |
| `CHECKOUT_BOX_SLUGS` | *(empty)* | Comma-separated machine slugs restricted to checkout operations |

For Docker Compose, also set `DB_ROOT_PASSWORD`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.

### Product-manager role

Set `OIDC_PRODUCT_MANAGER_GROUP` to a group name to allow members of that group to manage products, stock, categories and EAN aliases without granting full admin access. Leaving this variable empty or unset means only admins can access product management.

## Authentication

### Web (OIDC)

Admins log in via OIDC. The group membership in the configured `OIDC_ADMIN_GROUP` claim grants full access. `OIDC_PRODUCT_MANAGER_GROUP` grants product-management access only.

### Devices (API tokens)

Each machine gets a bearer token on registration (`POST /api/v1/machines`). The plaintext token is shown once in the web UI and must be saved immediately. Tokens are stored as SHA-256 hashes.

Machines that need to create users (checkout terminals) must have `machine_type = checkout` or their slug listed in `CHECKOUT_BOX_SLUGS`.

## API overview

All device-facing endpoints live under `/api/v1`. Full interactive docs at `/api/docs`.

### Users

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/users/nfc/{nfc_id}` | Device | Look up user by NFC card UID |
| `POST` | `/api/v1/users` | Checkout device | Register new NFC card |
| `GET` | `/api/v1/users` | Admin | List all users |
| `GET` | `/api/v1/users/{nfc_id}` | Admin | Get user details |
| `PUT` | `/api/v1/users/{nfc_id}/oidc` | Admin | Link OIDC subject to card |

### Machines

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET/POST` | `/api/v1/machines` | Admin | List / register machines |
| `GET/PUT/DELETE` | `/api/v1/machines/{slug}` | Admin | Get / update / deactivate |
| `POST` | `/api/v1/machines/{slug}/token` | Admin | Rotate API token |
| `*` | `/api/v1/machines/{slug}/admin-groups` | Admin | Manage machine sub-admins |
| `*` | `/api/v1/machines/{slug}/authorizations` | Machine manager | Grant / update / revoke user access |
| `GET` | `/api/v1/machines/{slug}/authorize/{nfc_id}` | Device | Check authorisation & pricing |

### Sessions

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/sessions` | Device | Start session (charges login fee + first interval) |
| `PUT` | `/api/v1/sessions/{id}` | Device | Extend session (charges next interval) |
| `DELETE` | `/api/v1/sessions/{id}` | Device | End session |

### Products

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/products` | Public | List active products |
| `GET` | `/api/v1/products/{ean}` | Public | Get product by EAN or alias |
| `POST` | `/api/v1/products` | Admin | Create product |
| `PUT` | `/api/v1/products/{ean}` | Admin | Update product |
| `POST` | `/api/v1/products/{ean}/stock` | Admin | Adjust stock |
| `POST` | `/api/v1/products/{ean}/stocktaking` | Admin | Set absolute stock |
| `GET` | `/api/v1/products/{ean}/audit` | Admin | Change history |
| `*` | `/api/v1/products/{ean}/aliases` | Admin | Manage alias EANs |
| `POST` | `/api/v1/products/{ean}/purchase` | Checkout device | Purchase product |

### Bankomat

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/bankomat/targets` | Public | List booking targets and balances |
| `POST` | `/api/v1/bankomat/topup` | Device | Top up user + booking target |
| `POST` | `/api/v1/bankomat/transfer` | Device | Transfer balance between users |
| `POST` | `/api/v1/bankomat/payout` | Device | Withdraw from target (PIN required) |
| `POST` | `/api/v1/bankomat/pin` | Admin | Set user PIN |

### Rentals

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET/POST` | `/api/v1/rentals/items` | Admin | List / create rental items |
| `GET` | `/api/v1/rentals/items/{uhf_tid}/status` | Device | Check if item is rented |
| `GET` | `/api/v1/rentals/authorize/{nfc_id}` | Device | Check rental permission |
| `POST` | `/api/v1/rentals` | Device | Rent an item |
| `DELETE` | `/api/v1/rentals/{id}` | Device | Return item |
| `*` | `/api/v1/rentals/permissions/{nfc_id}` | Admin | Grant / revoke permission |

## Database migrations

Migrations live in `alembic/versions/` and are committed to the repository.

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration after changing a model
alembic revision --autogenerate -m "describe the change"
```

## Web UI

The admin frontend is served at `/` and uses Tailwind CSS with HTMX. Language is auto-detected from the browser's `Accept-Language` header with English as the fallback. German and English are supported.

| Path | Access | Description |
|---|---|---|
| `/` | Public | Landing page |
| `/products` | Public | Product catalogue |
| `/dashboard` | Admin | Stats overview |
| `/machines` | Admin | Machine list and registration |
| `/users` | Admin | Member list |
| `/bankomat` | Admin | Booking targets |
| `/rentals` | Admin | Rental items and permissions |
| `/products/manage` | Admin / Product manager | Product, category and alias management |

## Project structure

```
app/
├── api/v1/          # REST API endpoints
├── auth/            # OIDC client, token verification, FastAPI dependencies
├── models/          # SQLAlchemy models
├── schemas/         # Pydantic request/response schemas
└── web/
    ├── locales/     # i18n translation files (en.json, de.json)
    ├── templates/   # Jinja2 HTML templates
    ├── auth.py      # OIDC login / callback / logout routes
    ├── i18n.py      # Language detection and translation helpers
    └── router.py    # Web page routes

alembic/versions/    # Database migration scripts
tests/               # pytest test suite
```

## Tests

```bash
pytest
```

The test suite uses an in-memory SQLite database and does not require a running MySQL server or OIDC provider.
