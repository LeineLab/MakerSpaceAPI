import pathlib

from fastapi.templating import Jinja2Templates

from app.auth.oidc import is_admin, is_auditor, is_product_manager, is_treasurer
from app.config import settings
from app.web.i18n import get_translator

_templates_dir = pathlib.Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_templates_dir))
templates.env.globals["is_admin"] = lambda u: is_admin(u) if u else False
templates.env.globals["is_product_manager"] = lambda u: is_product_manager(u) if u else False
templates.env.globals["is_treasurer"] = lambda u: is_treasurer(u) if u else False
templates.env.globals["is_auditor"] = lambda u: is_auditor(u) if u else False
templates.env.globals["TZ"] = settings.TIMEZONE
# Default translator (English) — overridden per-request via template context
templates.env.globals["_"] = get_translator("en")
