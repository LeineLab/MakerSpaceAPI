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

    # PDF statement generation (typst)
    # Directory containing extra fonts (e.g. albertsans.ttf). Leave empty to use system fonts only.
    TYPST_FONT_DIR: str = ""

    # Currency symbol appended to all monetary amounts (API messages and PDF statements)
    CURRENCY: str = "€"

    @property
    def checkout_box_slug_list(self) -> list[str]:
        return [s.strip() for s in self.CHECKOUT_BOX_SLUGS.split(",") if s.strip()]


settings = Settings()
