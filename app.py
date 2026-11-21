import asyncio
import hmac
import html
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fasthtml.common import (
    A, Button, Dialog, Div, Form, H1, H2, Img, Input, Label, Link, Main, Meta,
    Option, P, Script, Select, Span, Textarea, Title, fast_app,
)
from monsterui.all import Theme
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from geo import encode, nearby_prefixes
from odoo_client import OdooClient, OdooError
from store import Store
from visit_photos import PhotoValidationError, validate_visit_photos

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sales-pwa")

secret = os.environ.get("SESSION_SECRET", "")
if not secret:
    raise RuntimeError("SESSION_SECRET must be configured")

app, rt = fast_app(
    hdrs=(
        *Theme.blue.headers(),
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="theme-color", content="#1769aa"),
        Link(rel="manifest", href="/manifest.webmanifest"),
        Link(rel="stylesheet", href="/static/app.css?v=14"),
        Script(src="/static/app.js?v=15", defer=True),
    ),
    secret_key=secret,
    max_age=31536000,
    sess_https_only=os.getenv("COOKIE_SECURE", "true").lower() == "true",
    same_site="lax",
    pico=False,
)
app.mount("/static", StaticFiles(directory="static"), name="static")
store = Store(str(Path(os.getenv("DATA_DIR", "data")) / "operations.sqlite3"))
odoo = OdooClient(os.getenv("ODOO_URL", ""), os.getenv("ODOO_DATABASE", ""), os.getenv("ODOO_API_KEY", ""))
maps_key = os.getenv("GOOGLE_MAPS_KEY", "")
NEARBY_LIMIT = 300


def page(title, *content, home=False):
    return Title(title), Main(
        Div(
            A(Span("‹", aria_hidden="true"), Span("Home", cls="sr-only"),
              href="/", cls="back", aria_label="Back to home") if not home else
            Div("W", cls="brand-mark", aria_hidden="true"),
            Div(P("SALES WORKSPACE", cls="eyebrow"), H1(title), cls="heading-title"),
            cls="heading",
        ),
        *content,
        P("", id="notice", role="status", cls="notice"),
        cls="app-shell",
    )


def logged_in(request: Request):
    session = request.session
    last = session.get("last_active", 0)
    if not session.get("authenticated") or time.time() - last >= 3600:
        session.pop("authenticated", None)
        return False
    session["last_active"] = time.time()
    return True


def denied():
    return JSONResponse({"error": "Session expired. Please sign in again.", "login": True}, status_code=401)


def notice(message, status=400):
    return JSONResponse({"error": message}, status_code=status)


async def call(model, method, **kwargs):
    return await asyncio.to_thread(odoo.call, model, method, **kwargs)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def operation_result(row):
    status = row["status"]
    if status == "pending":
        status = "unknown"
    return {"status": status, "reference": row["token"], "partner_id": row["partner_id"], "detail": row["detail"]}


@rt("/health")
def get():
    return Response("ok", media_type="text/plain")


@rt("/manifest.webmanifest")
def get():
    return JSONResponse({
        "name": "Sales Visits", "short_name": "Sales",
        "id": "/", "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#f4f8fc", "theme_color": "#1769aa",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }, media_type="application/manifest+json")


@rt("/service-worker")
def get():
    return Response("self.addEventListener('fetch', () => {});", media_type="text/javascript", headers={"Service-Worker-Allowed": "/"})


@rt("/")
async def get(request: Request):
    if not logged_in(request):
        remembered = bool(request.session.get("employee_id") and request.session.get("employee_name"))
        employee_field = ()
        if not remembered:
            try:
                employees = await call("hr.employee", "search_read",
                                       domain=[["active", "=", True]], fields=["id", "name"],
                                       order="name, id")
            except OdooError:
                log.exception("Employee list lookup failed")
                return page("Sign in",
                            Div(H2("Unable to load employees"),
                                P("Please check your connection and try again.", cls="muted"),
                                A("Try again", href="/", cls="app-button primary full"),
                                cls="panel login-panel"), home=True)
            if not employees:
                return page("Sign in",
                            Div(H2("No employees available"),
                                P("Please contact IT to check employee access.", cls="muted"),
                                A("Try again", href="/", cls="app-button primary full"),
                                cls="panel login-panel"), home=True)
            employee_field = (
                Label("Employee / បុគ្គលិក", fr="employee"),
                Select(Option("Select an employee", value="", selected=True, disabled=True),
                       *(Option(f"{employee['name']} (#{employee['id']})", value=str(employee["id"]))
                         for employee in employees),
                       name="employee_id", id="employee", required=True),
            )
        return page("Sign in",
                    Div(Div("🔐", cls="login-icon", aria_hidden="true"),
                        H2("Welcome back"),
                        P("Enter your PIN / បញ្ចូលលេខសម្ងាត់" if remembered else
                          "Select your name and enter your PIN.", cls="muted"),
                        Form(*employee_field, Label("PIN / លេខសម្ងាត់", fr="pin"),
                             Input(type="password", name="pin", id="pin", inputmode="numeric",
                                   autocomplete="current-password", required=True, placeholder="••••"),
                             Button("Sign in", type="submit", cls="app-button primary full"),
                             id="login-form"),
                        cls="panel login-panel"), home=True)
    employee_name = request.session.get("employee_name", "")
    photo_error = False
    try:
        employees = await call("hr.employee", "read", ids=[request.session["employee_id"]],
                               fields=["image_512"])
        if not employees or not isinstance(employees[0], dict) or "image_512" not in employees[0]:
            raise OdooError("Employee photo lookup returned an invalid response.")
        photo = employees[0]["image_512"]
        if photo and not isinstance(photo, str):
            raise OdooError("Employee photo lookup returned an invalid image.")
    except OdooError:
        log.exception("Employee photo lookup failed")
        photo = None
        photo_error = True
    avatar = Img(src=f"data:image/png;base64,{photo}" if photo else "/static/avatar-placeholder.svg",
                 alt="", cls="hero-avatar-image")
    return page("Sales",
                Div(Span("✓", cls="success-icon", aria_hidden="true"),
                    Div(H2("Check-in successful"), P("Your visit has been recorded.")),
                    id="checkin-success", cls="success-banner", role="status", hidden=True),
                Div(Div(Div(avatar, cls="hero-avatar"),
                        H2(employee_name), cls="hero-profile"),
                    cls="welcome-card"),
                P("Photo unavailable", cls="hero-photo-error", role="status") if photo_error else (),
                Div(H2("Your tools", cls="section-title"),
                    P("Choose an action to get started", cls="section-subtitle"), cls="section-heading"),
                Div(
                    A(Span("➕", cls="tile-icon tile-icon-blue", aria_hidden="true"),
                      Span("New customer", cls="tile-title"), Span("Add a shop", cls="tile-caption"),
                      Span("↗", cls="tile-arrow", aria_hidden="true"), href="/customer", cls="tile"),
                    A(Span("📍", cls="tile-icon tile-icon-orange", aria_hidden="true"),
                      Span("Nearby customers", cls="tile-title"), Span("Explore the map", cls="tile-caption"),
                      Span("↗", cls="tile-arrow", aria_hidden="true"), href="/nearby", cls="tile"),
                    A(Span("📷", cls="tile-icon tile-icon-green", aria_hidden="true"),
                      Span("Check in", cls="tile-title"), Span("Record a visit", cls="tile-caption"),
                      Span("↗", cls="tile-arrow", aria_hidden="true"), href="/checkin", cls="tile"),
                    cls="tiles"), home=True)


@rt("/api/login")
async def post(request: Request):
    data = await request.json()
    remembered = bool(request.session.get("employee_id") and request.session.get("employee_name"))
    employee_id = request.session.get("employee_id") if remembered else data.get("employee_id")
    if isinstance(employee_id, bool) or not str(employee_id).isascii() or not str(employee_id).isdecimal() or int(employee_id) <= 0:
        return notice("Select an employee.")
    employee_id = int(employee_id)
    pin = str(data.get("pin", ""))
    if not pin:
        return notice("Enter your PIN.")
    try:
        employees = await call("hr.employee", "read", ids=[employee_id], fields=["name", "pin", "active"])
    except OdooError:
        log.exception("Employee lookup failed")
        return notice("Unable to verify employee. Please contact IT.", 502)
    if not employees or not employees[0].get("active") or not employees[0].get("pin") or not hmac.compare_digest(str(employees[0]["pin"]), pin):
        return notice("Invalid employee or PIN.", 401)
    request.session.update(employee_id=employee_id, authenticated=True,
                           employee_name=employees[0]["name"], last_active=time.time())
    return JSONResponse({"ok": True})


def hidden_position_fields():
    return (Input(type="hidden", name="latitude", id="latitude"),
            Input(type="hidden", name="longitude", id="longitude"),
            Input(type="hidden", name="accuracy", id="accuracy"))


def photo_picker(input_id, label, *, multiple=False, required=False, name=None, show_label=True):
    return Div(
        Label(label, fr=input_id) if show_label else (),
        Div(Div(cls="photo-previews"),
            Label(Span("+", aria_hidden="true"),
                  Input(type="file", name=name, accept="image/*", id=input_id,
                        multiple=multiple, required=required,
                        aria_label=f"Add {label.split(' | ', 1)[0]}"),
                  cls="photo-add"),
            cls="photo-picker"),
        cls="field")


def customer_validation_error(data, *, require_photo):
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        return "Shop name is required."
    if not isinstance(data.get("phone"), str) or not data["phone"].strip():
        return "Phone is required."
    if data.get("function") not in {"Owner", "Manager", "Staff"}:
        return "Select a position."
    if data.get("lang") not in {"zh_CN", "en_US", "km_KH"}:
        return "Select a customer language."
    tags = data.get("tags")
    if not isinstance(tags, list) or not tags or any(
            not isinstance(tag, int) or isinstance(tag, bool) or tag <= 0 for tag in tags):
        return "Select at least one tag."
    if data.get("latitude") in (None, "") or data.get("longitude") in (None, ""):
        return "Select a customer location."
    try:
        encode(float(data["latitude"]), float(data["longitude"]))
    except (TypeError, ValueError):
        return "Invalid location."
    photo = data.get("photo")
    if require_photo and (not isinstance(photo, str) or not photo):
        return "Shop photo is required."
    if photo not in (None, "") and not isinstance(photo, str):
        return "Invalid shop photo."
    if data.get("remove_photo") is True and not photo:
        return "A shop photo is required."
    return None


def map_fields():
    return (Button("⌖  Find my location", type="button", id="locate", cls="app-button secondary"),
            Div(id="map", cls="map"), P("", id="location-status", cls="helper-text", role="status"))


def map_dialog(title, hint, *actions):
    return Dialog(Div(
        Div(H2(title, id="customer-map-title"),
            Button("✕", type="button", id="close-customer-map", cls="map-close",
                   aria_label="Close map"), cls="map-dialog-header"),
        P(hint, cls="helper-text"),
        *map_fields(), P("", id="map-notice", role="status", cls="notice"), *actions,
        cls="map-dialog-content"),
        id="customer-map-dialog", cls="customer-map-dialog", aria_labelledby="customer-map-title")


def maps_script():
    return Script(src=f"https://maps.googleapis.com/maps/api/js?key={quote(maps_key)}&callback=mapsReady", defer=True)


@rt("/customer")
async def get(request: Request):
    if not logged_in(request):
        return RedirectResponse("/", status_code=303)
    try:
        categories = await call("res.partner.category", "search_read",
                                domain=[["active", "=", True], ["parent_id", "!=", False]],
                                fields=["id", "name", "parent_id"], context={"lang": "km_KH"})
    except OdooError:
        log.exception("Tag lookup failed")
        return page("New customer", P("Cannot load tags. Please try again later."))
    groups = {}
    for category in categories:
        parent = category["parent_id"]
        if isinstance(parent, list) and len(parent) >= 2:
            groups.setdefault((parent[0], parent[1]), []).append(category)
    tags = [
        Div(Label(name), Select(Option("—", value=""),
                                *(Option(c["name"], value=str(c["id"])) for c in options),
                                name=f"tag_{group_id}", required=True), cls="field")
        for (group_id, name), options in groups.items()
    ]
    return page("New customer",
        P("Create a new shop profile.", cls="page-subtitle"),
        Form(Div(
            Div(P("01", cls="section-number"), H2("Shop details | ព័ត៌មានហាង", cls="section-title"), cls="card-heading"),
            Div(Label("Shop name | ឈ្មោះហាង", fr="name"), Input(name="name", id="name", required=True), cls="field"),
            Div(Label("Phone | ទូរស័ព្ទ", fr="phone"),
                Div(Span("+855", cls="phone-prefix", id="phone-prefix"),
                    Input(name="phone", id="phone", inputmode="numeric", autocomplete="tel-national",
                          required=True, aria_describedby="phone-prefix"),
                    cls="phone-input"), cls="field"),
            Div(Label("Position | តួនាទី"), Div(*(Label(Input(type="radio", name="function", value=value, required=True), text, cls="choice")
                 for value, text in [("Owner", "👑 Owner"), ("Manager", "🧑‍💼 Manager"), ("Staff", "🧑 Staff")]), cls="choices"), cls="field"),
            Div(Label("Customer language | ភាសាអតិថិជន"), Div(*(Label(Input(type="radio", name="lang", value=value, required=True), text, cls="choice")
                 for value, text in [("zh_CN", "🇨🇳 中文"), ("en_US", "🇺🇸 English"), ("km_KH", "🇰🇭 ខ្មែរ")]), cls="choices"), cls="field"),
            photo_picker("photo", "Shop photo | រូបភាពហាង", name="photo", required=True),
            cls="panel form-panel"),
            Div(Div(P("02", cls="section-number"), H2("Customer location | ទីតាំងអតិថិជន", cls="section-title"), cls="card-heading"),
                *hidden_position_fields(),
                Button(Span("Select location on map", id="selected-location"),
                       Span("›", aria_hidden="true", cls="customer-select-arrow"),
                       type="button", id="open-customer-map", cls="customer-select",
                       aria_haspopup="dialog", aria_controls="customer-map-dialog"),
                cls="panel form-panel"),
            Div(Div(P("03", cls="section-number"), H2("Tags / ស្លាក", cls="section-title"), cls="card-heading"), *tags,
                Div(Label("Notes | កំណត់ចំណាំ", fr="comment"), Textarea(name="comment", id="comment"), cls="field"),
                cls="panel form-panel"),
            P("", id="operation-status", role="status", cls="notice"),
            Button("Create customer", type="submit", id="submit", cls="app-button primary full"),
            id="customer-form"),
        map_dialog("Select location", "Tap the map or drag the pin to the shop's exact location.",
                   Button("Confirm location", type="button", id="confirm-location",
                          cls="app-button primary full", disabled=True)),
        maps_script())


@rt("/nearby")
def get(request: Request):
    if not logged_in(request):
        return RedirectResponse("/", status_code=303)
    return page("Nearby customers",
        P("Discover shops around your location.", cls="page-subtitle"),
        Div(Div(Label("Show customers", fr="filter"), Select(Option("My customers", value="mine"),
            Option("All customers", value="all", selected=True), id="filter"), cls="field"),
            Div(Span("●", cls="legend-mine"), " My customers", Span("●", cls="legend-other"),
                " Other customers", cls="map-legend"),
            *map_fields(), Div(id="customer-card"), cls="panel form-panel"), maps_script())


@rt("/checkin")
def get(request: Request):
    if not logged_in(request):
        return RedirectResponse("/", status_code=303)
    return page("Check in",
        P("Record your visit in a few steps.", cls="page-subtitle"),
        Form(Input(type="hidden", name="partner_id", id="partner_id"),
             Div(Div(P("01", cls="section-number"), H2("ជ្រើសរើសអតិថិជន", cls="section-title"), cls="card-heading"),
                 Button(Span("Select a customer", id="selected-customer"),
                        Span("›", aria_hidden="true", cls="customer-select-arrow"),
                        type="button", id="open-customer-map", cls="customer-select",
                        aria_haspopup="dialog", aria_controls="customer-map-dialog"),
                 cls="panel form-panel"),
             Div(Div(P("02", cls="section-number"),
                     H2("ផ្ទុករូបភាពឡើង",
                        Img(src="/static/timemark-logo.webp", alt="TimeMark", width="24", height="24",
                            cls="section-logo"),
                        cls="section-title"), cls="card-heading"),
                 photo_picker("visit-photo", "TimeMark photos (1–3)", multiple=True, required=True,
                              show_label=False),
                 P("Select original photos from TimeMark. Up to 3 photos, 20 MB each.", cls="helper-text"),
                 Div(Label("Notes", fr="checkin-notes"), Textarea(name="comment", id="checkin-notes"), cls="field"),
                 cls="panel form-panel"),
             P("", id="operation-status", role="status", cls="notice"),
             A(Img(src="/static/timemark-logo.webp", alt="", width="24", height="24", cls="button-logo"),
               Span("Download TimeMark"),
               href="https://landing.timemark.com/go?us=official_website&um=direct&uc=owqr_zh-hant",
               target="_blank", rel="noopener noreferrer", id="download-timemark", hidden=True,
               cls="app-button secondary full download-timemark"),
             Button("Check in", type="submit", id="submit", cls="app-button primary full"), id="checkin-form"),
        map_dialog("Select a customer", "Tap a pin and confirm your choice."),
        maps_script())


@rt("/api/nearby")
async def get(request: Request):
    if not logged_in(request):
        return denied()
    try:
        lat = float(request.query_params["lat"])
        lon = float(request.query_params["lon"])
        prefixes = nearby_prefixes(lat, lon)
    except (KeyError, ValueError):
        return notice("Invalid location.")
    employee_id = request.session["employee_id"]
    fields = ["id", "name", "image_512", "barcode", "partner_latitude", "partner_longitude", "x_employee_id"]
    try:
        # The salesperson's own customers are always included, regardless of distance.
        records = await call("res.partner", "search_read",
                             domain=[["x_employee_id", "=", employee_id]], limit=NEARBY_LIMIT, fields=fields)
        if request.query_params.get("filter") != "mine" and len(records) < NEARBY_LIMIT:
            domain = (["&", ["x_employee_id", "!=", employee_id]]
                      + ["|"] * (len(prefixes) - 1) + [["zip", "=like", p + "%"] for p in prefixes])
            others = await call("res.partner", "search_read", domain=domain,
                                limit=NEARBY_LIMIT - len(records), fields=fields)
            mine_ids = {record["id"] for record in records}
            records += [record for record in others if record["id"] not in mine_ids]
    except OdooError:
        log.exception("Nearby lookup failed")
        return notice("Could not load customers from Odoo.", 502)
    for record in records:
        owner = record.pop("x_employee_id", False)
        record["mine"] = (owner[0] if isinstance(owner, list) else owner) == request.session["employee_id"]
    return JSONResponse({"customers": records, "limit_reached": len(records) == NEARBY_LIMIT})


@rt("/api/operations/{token}")
def get(request: Request, token: str):
    if not logged_in(request):
        return denied()
    row = store.get(token)
    if not row:
        return JSONResponse({"error": "Operation not found.", "missing": True}, status_code=404)
    if row["employee_id"] != request.session["employee_id"]:
        return notice("Operation not found.", 404)
    return JSONResponse(operation_result(row))


@rt("/api/customer")
async def post(request: Request):
    if not logged_in(request):
        return denied()
    data = await request.json()
    token = str(data.get("token", ""))
    try:
        uuid.UUID(token)
    except ValueError:
        return notice("Invalid request reference.")
    old = store.get(token)
    if old:
        if old["employee_id"] != request.session["employee_id"] or old["kind"] != "customer":
            return notice("Invalid request reference.", 403)
        return JSONResponse(operation_result(old))
    error = customer_validation_error(data, require_photo=True)
    if error:
        return JSONResponse({"error": error, "validation": True}, status_code=400)
    values = {"name": data.get("name", ""), "phone": data.get("phone", ""),
              "function": data.get("function") or False, "lang": data.get("lang") or False,
              "comment": html.escape(data.get("comment", "")).replace("\n", "<br>"),
              "category_id": [[6, 0, data.get("tags", [])]]}
    if data.get("photo"):
        values["image_1920"] = data["photo"]
    try:
        if data.get("latitude") not in (None, "") and data.get("longitude") not in (None, ""):
            lat, lon = float(data["latitude"]), float(data["longitude"])
            values.update(partner_latitude=lat, partner_longitude=lon, zip=encode(lat, lon))
    except ValueError:
        return notice("Invalid location.")
    employee = request.session["employee_id"]
    values["x_employee_id"] = employee
    if not store.start(token, "customer", employee, values["name"], utc_now()):
        row = store.get(token)
        if row["employee_id"] != employee or row["kind"] != "customer":
            return notice("Invalid request reference.", 403)
        return JSONResponse(operation_result(row))
    try:
        result = await call("res.partner", "create", vals_list=[values])
        partner_id = result[0] if isinstance(result, list) and len(result) == 1 else result
        if not isinstance(partner_id, int) or isinstance(partner_id, bool) or partner_id <= 0:
            raise OdooError("Unexpected create response", uncertain=True)
        store.finish(token, "success", partner_id=partner_id)
    except OdooError as exc:
        log.exception("Customer creation failed for %s", token)
        store.finish(token, "unknown" if exc.uncertain else "failed", str(exc))
    return JSONResponse(operation_result(store.get(token)))


@rt("/api/customer/{token}")
async def put(request: Request, token: str):
    if not logged_in(request):
        return denied()
    row = store.get(token)
    if not row or row["employee_id"] != request.session["employee_id"] or row["kind"] != "customer" or row["status"] != "success":
        return notice("Customer editing is unavailable.", 403)
    data = await request.json()
    error = customer_validation_error(data, require_photo=False)
    if error:
        return JSONResponse({"error": error, "validation": True}, status_code=400)
    values = {"name": data.get("name", ""), "phone": data.get("phone", ""),
              "function": data.get("function") or False, "lang": data.get("lang") or False,
              "comment": html.escape(data.get("comment", "")).replace("\n", "<br>"),
              "category_id": [[6, 0, data.get("tags", [])]]}
    if data.get("photo"):
        values["image_1920"] = data["photo"]
    try:
        if data.get("latitude") not in (None, "") and data.get("longitude") not in (None, ""):
            lat, lon = float(data["latitude"]), float(data["longitude"])
            values.update(partner_latitude=lat, partner_longitude=lon, zip=encode(lat, lon))
        result = await call("res.partner", "write", ids=[row["partner_id"]], vals=values)
        if result is not True:
            return notice("Update outcome is unknown. Please check Odoo.", 502)
    except ValueError:
        return notice("Invalid location.")
    except OdooError as exc:
        log.exception("Customer update failed")
        return notice("Update outcome is unknown. Please check Odoo." if exc.uncertain else "Update failed. Please try again.", 502)
    return JSONResponse({"status": "success", "partner_id": row["partner_id"]})


@rt("/api/checkin")
async def post(request: Request):
    if not logged_in(request):
        return denied()
    data = await request.json()
    token = str(data.get("token", ""))
    try:
        uuid.UUID(token)
        partner_id = int(data["partner_id"])
        lat, lon = float(data["latitude"]), float(data["longitude"])
        encode(lat, lon)
        if partner_id <= 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        return notice("Select a customer and obtain your location.")
    employee = request.session["employee_id"]
    old = store.get(token)
    if old:
        if old["employee_id"] != employee or old["kind"] != "checkin":
            return notice("Invalid request reference.", 403)
        return JSONResponse(operation_result(old))
    try:
        photos = validate_visit_photos(data.get("photos"))
    except PhotoValidationError as exc:
        return JSONResponse({"error": str(exc), "validation": True}, status_code=400)
    if not store.start(token, "checkin", employee, str(partner_id), utc_now(), partner_id):
        row = store.get(token)
        if row["employee_id"] != employee or row["kind"] != "checkin":
            return notice("Invalid request reference.", 403)
        return JSONResponse(operation_result(row))
    instant = datetime.now(timezone.utc)
    local_stamp = instant.astimezone(ZoneInfo("Asia/Phnom_Penh")).strftime("%Y-%m-%d %H:%M:%S")
    maps_url = "https://www.google.com/maps/search/?" + urlencode({"api": "1", "query": f"{lat},{lon}"})
    body = (f"<p>Employee: {html.escape(request.session['employee_name'])} (ID {employee})<br>"
            f"Time (Phnom Penh): {local_stamp}<br>"
            f'Location: <a href="{html.escape(maps_url, quote=True)}">{lat}, {lon}</a><br>'
            f"Accuracy (m): {html.escape(str(data.get('accuracy', '')))}<br>"
            f"Notes: {html.escape(str(data.get('comment', ''))).replace(chr(10), '<br>')}</p>")
    attachment_ids = []
    try:
        try:
            employees = await call("hr.employee", "read", ids=[employee], fields=["work_contact_id"])
        except OdooError as exc:
            raise OdooError("Unable to read employee work contact. Please contact IT.") from exc
        contact = employees[0].get("work_contact_id") if employees and isinstance(employees[0], dict) else False
        if (not isinstance(contact, (list, tuple)) or not contact or
                not isinstance(contact[0], int) or isinstance(contact[0], bool) or contact[0] <= 0):
            raise OdooError("Employee work contact is missing or invalid. Please contact IT.")
        values = [{
            "name": f"visit-{index}.{photo.extension}", "raw": photo.encoded,
            "res_model": "res.partner", "res_id": partner_id, "mimetype": photo.mimetype,
        } for index, photo in enumerate(photos, start=1)]
        result = await call("ir.attachment", "create", vals_list=values)
        if isinstance(result, list):
            attachment_ids = [ident for ident in result
                              if isinstance(ident, int) and not isinstance(ident, bool) and ident > 0]
        if (not isinstance(result, list) or len(result) != len(photos) or
                len(attachment_ids) != len(photos) or len(set(attachment_ids)) != len(photos)):
            raise OdooError("Unexpected attachment response", uncertain=True)
        store.finish(token, "pending", f"attachment_ids={attachment_ids}")
        result = await call("res.partner", "message_post", ids=[partner_id], body=body,
                            subject="APP现场拍照打卡",
                            body_is_html=True, message_type="comment", subtype_xmlid="mail.mt_activities",
                            attachment_ids=attachment_ids, author_id=contact[0])
        message_id = result[0] if isinstance(result, list) and len(result) == 1 else result
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            raise OdooError("Unexpected message response", uncertain=True)
        store.finish(token, "success", f"message_id={message_id}; attachment_ids={attachment_ids}")
    except OdooError as exc:
        log.exception("Check-in failed for %s", token)
        status = "unknown" if exc.uncertain else "failed"
        store.finish(token, status, f"{exc}; attachment_ids={attachment_ids}")
    return JSONResponse(operation_result(store.get(token)))
