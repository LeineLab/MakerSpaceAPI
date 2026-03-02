# MakerSpaceAPI

Unified REST API and web frontend for makerspace NFC devices — machines, checkout terminals, ATM (Bankomat) and rental stations.

## Features

- **Machine sessions** — NFC-authenticated machine access with per-minute billing and balance checks
- **Product checkout** — EAN barcode scanning, alias EANs, stock tracking, full audit trail
- **Bankomat (ATM)** — top-up, balance transfer between members, PIN-protected payout to booking targets; PDF account statements per booking target
- **Treasurer management** — users with a PIN set are treasurers; PIN can be set/cleared by admins; `has_pin` exposed in all user API responses
- **Rental system** — UHF RFID item tracking with per-user permissions
- **User self-service** — logged-in users can view their own balance, transaction history, current rentals and authorised machines at `/me`
- **OIDC self-service card linking** — devices generate a short-lived QR link; the user scans it and logs in via OIDC to link their NFC card to their account without admin intervention
- **Admin web UI** — dashboard, machine registration, product/inventory management, user list, booking targets, rentals; i18n (English / German, detected from browser)
- **OIDC login** — admin group and optional product-manager group via configurable OIDC claims
- **Device API tokens** — machines authenticate with bearer tokens; separate checkout-only restriction

## Requirements

- Python 3.12+
- MariaDB / MySQL 10.x+
- An OIDC provider (e.g. Authentik, Keycloak) for admin login
- Node.js (optional, only needed to rebuild Tailwind CSS after template changes)

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
| `SECRET_KEY` | `change-me-in-production` | JWT signing key — **change this** |
| `DEBUG` | `false` | Enables HTTP-only session cookies and relaxed cookie security for local dev |
| `BASE_URL` | `http://localhost:8000` | Public base URL |
| `OIDC_CLIENT_ID` | | OIDC client ID |
| `OIDC_CLIENT_SECRET` | | OIDC client secret |
| `OIDC_DISCOVERY_URL` | | OIDC discovery URL (`.well-known/openid-configuration`) |
| `OIDC_REDIRECT_URI` | `http://localhost:8000/auth/callback` | OAuth2 callback URL |
| `OIDC_GROUP_CLAIM` | `groups` | JWT claim that contains group memberships |
| `OIDC_ADMIN_GROUP` | `makerspace-admins` | Group name that grants full admin access |
| `OIDC_PRODUCT_MANAGER_GROUP` | *(empty)* | Group name for product-manager role (optional, see below) |
| `OIDC_LINK_UPDATE_NAME` | `false` | Set `true` to overwrite a user's display name from OIDC claims on self-service card linking |
| `CHECKOUT_BOX_SLUGS` | *(empty)* | Comma-separated machine slugs restricted to checkout operations |

For Docker Compose, also set `DB_ROOT_PASSWORD`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.

> **OIDC redirect URIs:** Register both `{BASE_URL}/auth/callback` and `{BASE_URL}/auth/connect/callback` as allowed redirect URIs in your OIDC provider.

### Product-manager role

Set `OIDC_PRODUCT_MANAGER_GROUP` to a group name to allow members of that group to manage products, stock, categories and EAN aliases without granting full admin access. Leaving this variable empty or unset means only admins can access product management.

## Authentication

### Web (OIDC + JWT cookie)

Admins log in via OIDC. After a successful OIDC callback, the server issues a signed HS256 JWT (8-hour expiry) stored in an `auth_token` httpOnly cookie. The JWT encodes the OIDC claims including group membership, which determines admin/product-manager access. The cookie is sent automatically by the browser on all same-origin requests.

The frontend can query `GET /auth/me` to retrieve the current user's identity and role flags (`is_admin`, `is_product_manager`) without exposing the token.

Any user whose OIDC account is linked to an NFC card can log in and view their own account at `/me`.

### Self-service card linking

A device (e.g. the checkout terminal) calls `POST /api/v1/users/{nfc_id}/connect-link` to generate a 15-minute signed URL. The URL is typically shown as a QR code. The user scans it, is redirected to the OIDC provider, and after a successful login their NFC card is linked to their OIDC account. No admin action is required.

### Devices (API tokens)

Each machine gets a bearer token on registration (`POST /api/v1/machines`). The plaintext token is shown once in the web UI and must be saved immediately. Tokens are stored as SHA-256 hashes.

Machines that need to create users (checkout terminals) must have `machine_type = checkout` or their slug listed in `CHECKOUT_BOX_SLUGS`.

## API overview

All device-facing endpoints live under `/api/v1`. Full interactive docs at `/api/docs`.

### Users

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/users/nfc/{nfc_id}` | Device | Look up user by NFC card UID; includes `has_pin` |
| `POST` | `/api/v1/users` | Checkout device | Register new NFC card |
| `GET` | `/api/v1/users` | Admin | List all users |
| `GET` | `/api/v1/users/{nfc_id}` | Admin | Get user details; includes `has_pin` |
| `PUT` | `/api/v1/users/{nfc_id}/oidc` | Admin | Link OIDC subject to card |
| `POST` | `/api/v1/users/{nfc_id}/connect-link` | Device | Generate 15-min self-service card-linking URL |
| `GET` | `/api/v1/users/me` | OIDC session | Logged-in user's profile and balance |
| `GET` | `/api/v1/users/me/transactions` | OIDC session | Paginated transaction history (`?limit=&offset=`) |
| `GET` | `/api/v1/users/me/rentals` | OIDC session | Currently rented items |
| `GET` | `/api/v1/users/me/machines` | OIDC session | Authorised machines with pricing |

### Machines

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET/POST` | `/api/v1/machines` | Admin | List / register machines |
| `GET/PUT/DELETE` | `/api/v1/machines/{slug}` | Admin | Get / update / deactivate |
| `POST` | `/api/v1/machines/{slug}/token` | Admin | Rotate API token |
| `GET/POST/DELETE` | `/api/v1/machines/{slug}/admins` | Admin | Manage per-machine sub-admins (by OIDC sub) |
| `GET/POST/PUT/DELETE` | `/api/v1/machines/{slug}/authorizations` | Machine manager | Grant / update / revoke user access |
| `GET` | `/api/v1/machines/{slug}/authorize/{nfc_id}` | Device | Check authorisation & pricing |
| `GET` | `/api/v1/machines/my` | OIDC session | Machines the current user manages |

### Sessions

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/sessions` | Device | Start session (charges login fee + first interval) |
| `PUT` | `/api/v1/sessions/{id}` | Device | Extend session (charges next interval or returns 402) |
| `DELETE` | `/api/v1/sessions/{id}` | Device | End session |

### Products

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/products` | Public | List active products |
| `GET` | `/api/v1/products/{ean}` | Public | Get product by EAN or alias |
| `POST` | `/api/v1/products` | Product manager | Create product |
| `PUT` | `/api/v1/products/{ean}` | Product manager | Update product |
| `POST` | `/api/v1/products/{ean}/stock` | Product manager | Adjust stock |
| `POST` | `/api/v1/products/{ean}/stocktaking` | Product manager | Set absolute stock |
| `GET` | `/api/v1/products/{ean}/audit` | Product manager | Change history |
| `GET` | `/api/v1/products/{ean}/popularity` | Product manager | Purchase count (last N days) |
| `GET/POST/DELETE` | `/api/v1/products/{ean}/aliases` | Product manager | Manage alias EANs |
| `POST` | `/api/v1/products/{ean}/purchase` | Checkout device | Purchase product |
| `GET` | `/api/v1/categories` | Public | List product categories |
| `POST` | `/api/v1/categories` | Product manager | Create category |
| `DELETE` | `/api/v1/categories/{name}` | Product manager | Delete category (rejected if in use) |

### Bankomat

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/bankomat/targets` | Public | List booking targets and balances |
| `POST` | `/api/v1/bankomat/targets` | Admin | Create booking target |
| `POST` | `/api/v1/bankomat/topup` | Device | Top up user balance + booking target |
| `POST` | `/api/v1/bankomat/target-topup` | Device | Increase target only (e.g. donation) |
| `POST` | `/api/v1/bankomat/transfer` | Device | Transfer balance between users |
| `POST` | `/api/v1/bankomat/payout` | Device | Withdraw from target (PIN required) |
| `GET` | `/api/v1/bankomat/transactions/{nfc_id}` | Device | Recent transactions for a user |
| `POST` | `/api/v1/bankomat/pin` | Admin | Set or update user PIN (bcrypt stored) |
| `DELETE` | `/api/v1/bankomat/pin/{nfc_id}` | Admin | Clear user PIN (removes treasurer status) |

Users with a PIN set are considered treasurers and are shown in a dedicated section of the Bankomat admin page. The `has_pin` boolean field is included in all user API responses.

### Rentals

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET/POST` | `/api/v1/rentals/items` | Admin | List / create rental items |
| `PUT` | `/api/v1/rentals/items/{id}` | Admin | Edit rental item |
| `GET` | `/api/v1/rentals/items/{uhf_tid}/status` | Device | Check if item is rented |
| `GET` | `/api/v1/rentals/authorize/{nfc_id}` | Device | Check rental permission |
| `POST` | `/api/v1/rentals` | Device | Rent an item |
| `DELETE` | `/api/v1/rentals/{id}` | Device | Return item |
| `GET` | `/api/v1/rentals/active` | Admin | List all currently rented items |
| `GET/POST/DELETE` | `/api/v1/rentals/permissions/{nfc_id}` | Admin | Grant / revoke rental permission |

## Database migrations

Migrations live in `alembic/versions/` and are committed to the repository.

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration after changing a model
alembic revision --autogenerate -m "describe the change"
```

## Web UI

The frontend is served at `/` and uses Tailwind CSS with Alpine.js. Jinja2 renders the HTML shell and navigation; Alpine.js components fetch data from the `/api/v1/` REST endpoints directly. Language is auto-detected from the browser's `Accept-Language` header with English as the fallback. German and English are supported.

| Path | Access | Description |
|---|---|---|
| `/` | Public | Landing page |
| `/products` | Public | Product catalogue |
| `/me` | OIDC session | Self-service: balance, transactions, rentals, authorised machines |
| `/dashboard` | Admin | Stats overview |
| `/machines` | Admin / machine sub-admin | Machine list, registration, authorisations |
| `/users` | Admin | Member list |
| `/bankomat` | Admin | Booking targets and treasurer management |
| `/rentals` | Admin | Rental items and permissions |
| `/products/manage` | Admin / Product manager | Product, category and alias management |

### Rebuilding Tailwind CSS

The compiled CSS lives at `app/web/static/css/tailwind.css` and is committed to the repository, so a Node.js install is not needed to run the app. To regenerate it after changing templates:

```bash
npm install
npm run build:css   # one-shot build
npm run watch:css   # rebuild on template changes
```

## Migrating from legacy systems

A migration script is provided for moving data from the three legacy projects (NFCKasse, MachineUserManager, Bankomat) into MakerSpaceAPI.

```bash
python scripts/migrate_legacy.py \
  --target-url mysql+pymysql://user:pass@host/makerspaceapi \
  --mum-url    mysql+pymysql://user:pass@host/machines \
  --nfc-url    mysql+pymysql://user:pass@host/nfckasse \
  --bankomat-url mysql+pymysql://user:pass@host/bankomat
```

Use `--dry-run` to preview changes without writing. Use `--only machines products targets` to run specific sections. See `python scripts/migrate_legacy.py --help` for all options.

> **Note:** NFCKasse card UIDs were stored as MD5 hashes and cannot be reversed. Cards registered in the legacy NFCKasse system must be re-registered in MakerSpaceAPI.

## Legacy hardware projects

The `projects/` directory contains updated versions of the three original hardware projects, modified to call the MakerSpaceAPI REST endpoints instead of connecting directly to MySQL.

| Project | Config | Key change |
|---|---|---|
| `MachineUserManager/` | Set `DB_TYPE = 'api'`, `API_URL`, `API_TOKEN` in `user_config.py` | New `dbconnectors/db_api.py` connector |
| `Bankomat/` | Set `API_URL`, `API_TOKEN` in `user_config.py` | `unified_kasse.py`, `nfckasse.py`, `machines.py` use REST API |
| `NFCKasse/` | Set `api_url`, `api_token`, `uid_hash = False` in `settings.py` | `database.py` uses REST API; raw integer UIDs |

See the `*.example.py` config files in each project directory for the required settings.

## Project structure

```
app/
├── api/v1/          # REST API endpoints
├── auth/            # OIDC client, JWT helpers, FastAPI dependencies
│   ├── deps.py      # get_session_user, get_current_device, etc.
│   ├── jwt.py       # create_admin_jwt / verify_admin_jwt (HS256 cookie auth)
│   ├── oidc.py      # authlib OIDC client, is_admin / is_product_manager
│   └── tokens.py    # Machine API token generation / SHA-256 verification
├── models/          # SQLAlchemy models
├── schemas/         # Pydantic request/response schemas
└── web/
    ├── locales/     # i18n translation files (en.json, de.json)
    ├── static/
    │   ├── css/     # tailwind.css (CLI-built, committed)
    │   └── js/      # alpine.min.js, htmx.min.js (local copies)
    ├── templates/   # Jinja2 HTML templates (shell + Alpine.js components)
    ├── auth.py      # OIDC login / callback / logout + /auth/me
    ├── i18n.py      # Language detection and translation helpers
    └── router.py    # Web page routes (thin — no DB queries)

alembic/versions/    # Database migration scripts
scripts/             # Utility scripts (migrate_legacy.py)
projects/            # Updated legacy hardware project connectors
tests/               # pytest test suite
package.json         # Tailwind CSS CLI build (optional, for CSS rebuilds)
tailwind.config.js   # Tailwind content paths
```

## Tests

```bash
pytest
```

The test suite uses an in-memory SQLite database and does not require a running MySQL server or OIDC provider.
