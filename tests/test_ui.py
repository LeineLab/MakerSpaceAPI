"""Browser-based UI tests using Playwright.

Run these separately from the unit tests to avoid dependency-override conflicts:

    pytest tests/test_ui.py -v

First-time setup:
    pip install pytest-playwright
    playwright install chromium
"""
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal

import pytest

_HOST = "127.0.0.1"
_PORT = 18765
BASE = f"http://{_HOST}:{_PORT}"

MOBILE_VIEWPORT = {"width": 375, "height": 812}
DESKTOP_VIEWPORT = {"width": 1280, "height": 800}


# ── Session-scoped fixtures ───────────────────────────────────────────────────


@pytest.fixture(scope="session")
def _engine(tmp_path_factory):
    from sqlalchemy import create_engine

    from app.models import Base

    db_path = tmp_path_factory.mktemp("uidb") / "ui_test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def live_server(_engine):
    """Start a real uvicorn server backed by the SQLite test DB."""
    import uvicorn
    from sqlalchemy.orm import sessionmaker

    from app.database import get_db
    from app.main import app

    Session = sessionmaker(bind=_engine)

    def _override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db

    config = uvicorn.Config(app, host=_HOST, port=_PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(100):
        try:
            urllib.request.urlopen(BASE + "/")
            break
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.1)
    else:
        pytest.fail("Live server did not start in time")

    yield BASE

    server.should_exit = True
    thread.join(timeout=5)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="session")
def _admin_jwt():
    from app.auth.jwt import create_admin_jwt
    from app.config import settings

    return create_admin_jwt({
        "sub": "ui-test-admin",
        "name": "UI Test Admin",
        settings.OIDC_GROUP_CLAIM: [settings.OIDC_ADMIN_GROUP],
    })


# ── Per-test fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def admin_page(page, live_server, _admin_jwt):
    """Playwright Page with an admin JWT cookie pre-set."""
    page.context.add_cookies([{
        "name": "auth_token",
        "value": _admin_jwt,
        "url": BASE,
    }])
    return page


# ── DOM helpers ───────────────────────────────────────────────────────────────


def _all_tables_wrapped(page) -> bool:
    """Return True if every <table> in the page has an overflow-x-auto ancestor."""
    return page.evaluate("""() => {
        for (const t of document.querySelectorAll('table')) {
            let el = t.parentElement;
            let found = false;
            while (el && el !== document.body) {
                if (el.classList && el.classList.contains('overflow-x-auto')) {
                    found = true;
                    break;
                }
                el = el.parentElement;
            }
            if (!found) return false;
        }
        return true;
    }""")


def _has_horizontal_overflow(page) -> bool:
    """Return True if at least one .overflow-x-auto element has scrollable content."""
    return page.evaluate("""() => {
        for (const el of document.querySelectorAll('.overflow-x-auto')) {
            if (el.scrollWidth > el.clientWidth + 1) return true;
        }
        return false;
    }""")


# ── Public pages ──────────────────────────────────────────────────────────────


def test_index_loads(page, live_server):
    page.goto(BASE + "/")
    assert page.locator("h1").count() > 0


def test_products_public_page_loads(page, live_server):
    page.goto(BASE + "/products")
    page.wait_for_load_state("networkidle")
    assert page.locator("table").count() > 0


# ── Auth: protected pages show login prompt ───────────────────────────────────


@pytest.mark.parametrize("path", ["/dashboard", "/users", "/bankomat", "/rentals"])
def test_protected_page_requires_login(page, live_server, path):
    response = page.goto(BASE + path)
    assert response.status in (401, 403)
    assert page.locator("text=Login Required").count() > 0


# ── Admin pages render ────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/dashboard",
    "/users",
    "/machines",
    "/bankomat",
    "/rentals",
    "/products/manage",
])
def test_admin_page_renders(admin_page, live_server, path):
    response = admin_page.goto(BASE + path)
    assert response.status == 200
    admin_page.wait_for_load_state("networkidle")
    assert admin_page.locator("h1").count() > 0


# ── Responsive layout: all tables have overflow-x-auto wrappers ───────────────


@pytest.mark.parametrize("path", [
    "/users",
    "/machines",
    "/bankomat",
    "/rentals",
    "/products/manage",
])
def test_all_tables_have_scroll_wrapper(admin_page, live_server, path):
    """Every <table> must be inside an overflow-x-auto container."""
    admin_page.goto(BASE + path)
    admin_page.wait_for_load_state("networkidle")
    assert _all_tables_wrapped(admin_page), (
        f"A <table> on {path} is missing an overflow-x-auto ancestor — "
        "content will be permanently hidden on narrow screens"
    )


@pytest.mark.parametrize("path", [
    "/users",
    "/machines",
    "/rentals",
    "/products/manage",
])
def test_scroll_wrapper_present_at_mobile_viewport(admin_page, live_server, path):
    """At 375 px the overflow-x-auto wrapper must still be in place."""
    admin_page.set_viewport_size(MOBILE_VIEWPORT)
    admin_page.goto(BASE + path)
    admin_page.wait_for_load_state("networkidle")
    assert _all_tables_wrapped(admin_page)


# ── Responsive layout: tables overflow horizontally on mobile with real data ──


def test_users_table_overflows_on_mobile(admin_page, live_server, _engine):
    """With a user row the users table must overflow at 375 px (actions column pushes it wide)."""
    from sqlalchemy.orm import sessionmaker

    from app.models.user import User

    Session = sessionmaker(bind=_engine)
    db = Session()
    if not db.query(User).filter(User.id == 999111001).first():
        db.add(User(
            id=999111001,
            name="Overflow Test User",
            balance=Decimal("5.00"),
            created_at=datetime.now(UTC).replace(tzinfo=None),
        ))
        db.commit()
    db.close()

    admin_page.set_viewport_size(MOBILE_VIEWPORT)
    admin_page.goto(BASE + "/users")
    admin_page.wait_for_load_state("networkidle")
    admin_page.wait_for_function(
        "document.querySelectorAll('tbody tr').length > 0",
        timeout=5000,
    )
    assert _has_horizontal_overflow(admin_page), (
        "Users table does not overflow at 375 px — action buttons in the last "
        "column would be unreachable without horizontal scroll"
    )


def test_machines_table_overflows_on_mobile(admin_page, live_server, _engine):
    """With a machine row the machines table must overflow at 375 px."""
    from sqlalchemy.orm import sessionmaker

    from app.auth.tokens import generate_api_token
    from app.models.machine import Machine

    Session = sessionmaker(bind=_engine)
    db = Session()
    if not db.query(Machine).filter(Machine.slug == "ui-overflow-machine").first():
        _, h = generate_api_token()
        db.add(Machine(
            name="Overflow Test Machine",
            slug="ui-overflow-machine",
            machine_type="machine",
            api_token_hash=h,
            created_at=datetime.now(UTC).replace(tzinfo=None),
            active=True,
        ))
        db.commit()
    db.close()

    admin_page.set_viewport_size(MOBILE_VIEWPORT)
    admin_page.goto(BASE + "/machines")
    admin_page.wait_for_load_state("networkidle")
    admin_page.wait_for_function(
        "document.querySelectorAll('tbody tr').length > 0",
        timeout=5000,
    )
    assert _has_horizontal_overflow(admin_page), (
        "Machines table does not overflow at 375 px"
    )


# ── Navigation ────────────────────────────────────────────────────────────────


def test_admin_sees_dashboard_link(admin_page, live_server):
    admin_page.goto(BASE + "/")
    assert admin_page.locator("a:has-text('Dashboard')").count() > 0


def test_unauthenticated_no_dashboard_link(page, live_server):
    page.goto(BASE + "/")
    assert page.locator("a:has-text('Dashboard')").count() == 0


# ── Modal interactions ────────────────────────────────────────────────────────


def test_register_machine_modal_open_and_close(admin_page, live_server):
    admin_page.goto(BASE + "/machines")
    admin_page.wait_for_load_state("networkidle")

    admin_page.locator("button:has-text('Register')").first.click()
    modal = admin_page.locator(".fixed.inset-0").first
    modal.wait_for(state="visible", timeout=3000)
    assert modal.is_visible()

    admin_page.locator("button:has-text('Cancel')").first.click()
    modal.wait_for(state="hidden", timeout=3000)
    assert not modal.is_visible()


def test_edit_user_modal_opens(admin_page, live_server, _engine):
    from sqlalchemy.orm import sessionmaker

    from app.models.user import User

    Session = sessionmaker(bind=_engine)
    db = Session()
    if not db.query(User).filter(User.id == 888777001).first():
        db.add(User(
            id=888777001,
            name="Modal Test User",
            balance=Decimal("0.00"),
            created_at=datetime.now(UTC).replace(tzinfo=None),
        ))
        db.commit()
    db.close()

    admin_page.goto(BASE + "/users")
    admin_page.wait_for_load_state("networkidle")
    admin_page.wait_for_function(
        "document.querySelectorAll('tbody tr').length > 0",
        timeout=5000,
    )
    admin_page.locator("button:has-text('Edit')").first.click()
    modal = admin_page.locator(".fixed.inset-0").first
    modal.wait_for(state="visible", timeout=3000)
    assert modal.is_visible()


def test_add_product_modal_opens(admin_page, live_server):
    admin_page.goto(BASE + "/products/manage")
    admin_page.wait_for_load_state("networkidle")

    admin_page.locator("button:has-text('Add Product')").first.click()
    modal = admin_page.locator(".fixed.inset-0").first
    modal.wait_for(state="visible", timeout=3000)
    assert modal.is_visible()


# ── Alpine.js data loading ────────────────────────────────────────────────────


def test_products_list_loads_from_api(page, live_server, _engine):
    """Seed a product and verify Alpine.js renders it in the public table."""
    from sqlalchemy.orm import sessionmaker

    from app.models.product import Product, ProductCategory

    Session = sessionmaker(bind=_engine)
    db = Session()
    if not db.query(ProductCategory).filter_by(name="UI-Drinks").first():
        db.add(ProductCategory(name="UI-Drinks"))
        db.commit()
    if not db.query(Product).filter_by(ean="UI-TEST-001").first():
        db.add(Product(
            ean="UI-TEST-001",
            name="UI Test Cola",
            price=Decimal("1.50"),
            stock=5,
            category="UI-Drinks",
            active=True,
        ))
        db.commit()
    db.close()

    page.goto(BASE + "/products")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("td:has-text('UI Test Cola')", timeout=5000)
    assert page.locator("td:has-text('UI Test Cola')").count() > 0


def test_machines_list_loads_from_api(admin_page, live_server, _engine):
    """Seed a machine and verify Alpine.js renders it in the machines table."""
    from sqlalchemy.orm import sessionmaker

    from app.auth.tokens import generate_api_token
    from app.models.machine import Machine

    Session = sessionmaker(bind=_engine)
    db = Session()
    if not db.query(Machine).filter(Machine.slug == "ui-data-machine").first():
        _, h = generate_api_token()
        db.add(Machine(
            name="UI Data Machine",
            slug="ui-data-machine",
            machine_type="machine",
            api_token_hash=h,
            created_at=datetime.now(UTC).replace(tzinfo=None),
            active=True,
        ))
        db.commit()
    db.close()

    admin_page.goto(BASE + "/machines")
    admin_page.wait_for_load_state("networkidle")
    admin_page.wait_for_selector("td:has-text('UI Data Machine')", timeout=5000)
    assert admin_page.locator("td:has-text('UI Data Machine')").count() > 0


def test_users_list_loads_from_api(admin_page, live_server, _engine):
    """Seed a user and verify Alpine.js renders them in the users table."""
    from sqlalchemy.orm import sessionmaker

    from app.models.user import User

    Session = sessionmaker(bind=_engine)
    db = Session()
    if not db.query(User).filter(User.id == 777888001).first():
        db.add(User(
            id=777888001,
            name="Data Load User",
            balance=Decimal("10.00"),
            created_at=datetime.now(UTC).replace(tzinfo=None),
        ))
        db.commit()
    db.close()

    admin_page.goto(BASE + "/users")
    admin_page.wait_for_load_state("networkidle")
    admin_page.wait_for_selector("td:has-text('Data Load User')", timeout=5000)
    assert admin_page.locator("td:has-text('Data Load User')").count() > 0
