from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "mysql+pymysql://makerspace:makerspace@localhost:3306/makerspaceapi"

    # OIDC
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""
    OIDC_DISCOVERY_URL: str = ""
    OIDC_ADMIN_GROUP: str = "makerspace-admins"
    OIDC_PRODUCT_MANAGER_GROUP: str = ""
    OIDC_TREASURER_GROUP: str = ""
    OIDC_AUDITOR_GROUP: str = ""
    OIDC_GROUP_CLAIM: str = "groups"
    OIDC_REDIRECT_URI: str = "http://localhost:8000/auth/callback"

    # App
    SECRET_KEY: str = "change-me-in-production"
    DEBUG: bool = False
    BASE_URL: str = "http://localhost:8000"

    # NFC self-service linking
    OIDC_LINK_UPDATE_NAME: bool = False

    # Devices
    CHECKOUT_BOX_SLUGS: str = ""

    # Allow purchases when stock is 0 (stock goes negative)
    ALLOW_NEGATIVE_STOCK: bool = False

    # PDF statement generation (typst)
    # Directory containing extra fonts (e.g. albertsans.ttf). Leave empty to use system fonts only.
    TYPST_FONT_DIR: str = ""

    # Currency symbol appended to all monetary amounts (API messages and PDF statements)
    CURRENCY: str = "€"

    # Require an EÜR-Sphäre (ideell/Vermögensverwaltung/Zweckbetrieb/wirtschaftlicher
    # Geschäftsbetrieb) on every ledger category. Only relevant for gemeinnützige
    # Vereine — disable for commercial installations (e.g. a for-profit FabLab)
    # that don't need Sphärentrennung.
    LEDGER_SPHERES_ENABLED: bool = True

    # Paperless-ngx document linking for ledger entries. Empty = disabled (search
    # UI hides itself; POST/GET a paperless_document_id still works as a plain
    # opaque string either way).
    PAPERLESS_URL: str = ""
    PAPERLESS_API_TOKEN: str = ""

    # Manual FinTS bank statement live-pull (Phase 3). Empty = disabled — no
    # scheduled automation regardless, this only gates whether the UI/API
    # accept a manual pull at all. python-fints requires a registered product
    # ID (see the Deutsche Kreditwirtschaft's FinTS registration process);
    # there's no bundled test ID to fall back to.
    FINTS_PRODUCT_ID: str = ""

    # IANA timezone for timestamp display in the web frontend (e.g. Europe/Berlin, UTC)
    TIMEZONE: str = "Europe/Berlin"

    @property
    def checkout_box_slug_list(self) -> list[str]:
        return [s.strip() for s in self.CHECKOUT_BOX_SLUGS.split(",") if s.strip()]


settings = Settings()
