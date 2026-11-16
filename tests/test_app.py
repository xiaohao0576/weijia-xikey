import os
import re
import uuid
import base64
import io
from datetime import datetime, timezone
from PIL import Image
import pytest

os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("DATA_DIR", "/tmp/sales-pwa-tests")

import app as application
from geo import encode, nearby_prefixes
from odoo_client import OdooError
from starlette.testclient import TestClient
from store import Store
import visit_photos


class FakeOdoo:
    def __init__(self):
        self.calls = []
        self.fail_method = None
        self.employee_photos = {1: False, 2: False}

    def call(self, model, method, **kwargs):
        self.calls.append((model, method, kwargs))
        if self.fail_method == method:
            raise OdooError("Connection lost", uncertain=True)
        if model == "hr.employee":
            if method == "search_read":
                return [{"id": 1, "name": "Salesperson"}, {"id": 2, "name": "Second Employee"}]
            if kwargs["fields"] == ["image_512"]:
                return [{"image_512": self.employee_photos[kwargs["ids"][0]]}]
            if kwargs["ids"] == [1]:
                return [{"name": "Salesperson", "pin": "1234", "active": True,
                         "work_contact_id": [101, "Salesperson"]}]
            if kwargs["ids"] == [2]:
                return [{"name": "Second Employee", "pin": "5678", "active": True,
                         "work_contact_id": [102, "Second Employee"]}]
            return []
        if model == "res.partner.category":
            return [{"id": 10, "name": "Tag", "parent_id": [2, "Group"]}]
        if method == "search_read":
            return [{"id": 7, "name": "Shop", "image_512": False, "barcode": False,
                     "partner_latitude": 11.55, "partner_longitude": 104.92, "x_employee_id": [1, "Salesperson"]}]
        if method == "create":
            return [42] if model == "res.partner" else list(range(55, 55 + len(kwargs["vals_list"])))
        if method == "write":
            return True
        if method == "message_post":
            return 65
        raise AssertionError((model, method))


def client(monkeypatch, tmp_path):
    fake = FakeOdoo()
    monkeypatch.setattr(application, "odoo", fake)
    monkeypatch.setattr(application, "store", Store(str(tmp_path / "operations.sqlite3")))
    browser = TestClient(application.app)
    response = browser.post("/api/login", json={"employee_id": 1, "pin": "1234"})
    assert response.status_code == 200
    return browser, fake


def image_data(software="Timemark v10.0.316"):
    image = Image.new("RGB", (4, 4), "white")
    exif = Image.Exif()
    if software is not None:
        exif[305] = software
    content = io.BytesIO()
    image.save(content, format="JPEG", exif=exif)
    return base64.b64encode(content.getvalue()).decode()


def test_login_and_pages(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    home = browser.get("/").text
    assert "New customer" in home
    assert 'id="checkin-success"' in home and "Check-in successful" in home
    assert re.search(r'<div[^>]*id="checkin-success"[^>]*hidden|<div[^>]*hidden[^>]*id="checkin-success"', home)
    customer = browser.get("/customer")
    assert customer.status_code == 200
    assert "Group" in customer.text and "Tag" in customer.text
    assert 'class="app-button primary full"' in customer.text
    assert 'class="app-button secondary"' in customer.text
    assert 'id="customer-form"' in customer.text
    assert 'class="photo-picker"' in customer.text
    assert 'class="photo-previews"' in customer.text
    assert 'aria-label="Add Shop photo"' in customer.text
    assert 'id="phone-prefix"' in customer.text and "+855" in customer.text
    assert 'aria-describedby="phone-prefix"' in customer.text
    assert "Phone (+855)" not in customer.text
    form_end = customer.text.index("</form>")
    for marker in ('id="latitude"', 'id="longitude"', 'id="accuracy"', 'id="open-customer-map"'):
        assert customer.text.index(marker) < form_end
    for marker in ('id="customer-map-dialog"', 'id="map"', 'id="locate"', 'id="confirm-location"'):
        assert customer.text.index(marker) > form_end
    assert re.search(r'<button[^>]*id="confirm-location"[^>]*disabled|<button[^>]*disabled[^>]*id="confirm-location"', customer.text)
    assert fake.calls[-1][2]["context"] == {"lang": "km_KH"}
    nearby = browser.get("/nearby")
    assert nearby.status_code == 200
    assert re.search(r'<option[^>]*value="all"[^>]*selected|<option[^>]*selected[^>]*value="all"', nearby.text)
    assert not re.search(r'<option[^>]*value="mine"[^>]*selected|<option[^>]*selected[^>]*value="mine"', nearby.text)
    checkin = browser.get("/checkin")
    assert checkin.status_code == 200
    assert 'id="checkin-form"' in checkin.text
    assert 'id="customer-map-dialog"' in checkin.text
    assert 'id="open-customer-map"' in checkin.text
    assert 'id="close-customer-map"' in checkin.text
    assert 'class="app-button primary full"' in checkin.text
    assert checkin.text.count('type="file"') == 1
    assert 'id="visit-photo"' in checkin.text
    assert 'class="photo-picker"' in checkin.text
    assert 'class="photo-previews"' in checkin.text
    assert 'aria-label="Add TimeMark photos (1–3)"' in checkin.text
    assert 'accept="image/*"' in checkin.text
    assert "multiple" in checkin.text
    assert "https://landing.timemark.com/go?" in checkin.text
    assert 'id="download-timemark"' in checkin.text
    assert "/static/timemark-logo.webp" in checkin.text
    assert checkin.text.index('id="operation-status"') < checkin.text.index('id="download-timemark"') < checkin.text.index('id="submit"')
    assert re.search(r'<a [^>]*\bhidden\b[^>]*id="download-timemark"', checkin.text)
    assert browser.get("/static/timemark-logo.webp").status_code == 200
    assert 'capture=' not in checkin.text
    assert browser.get("/static/app.js").status_code == 200
    assert browser.get("/service-worker").status_code == 200
    assert browser.get("/manifest.webmanifest").json()["start_url"] == "/"


def test_checkin_photo_heading_and_accessible_picker(monkeypatch, tmp_path):
    browser, _ = client(monkeypatch, tmp_path)
    checkin = browser.get("/checkin").text

    assert re.search(r'<h2[^>]*>\s*ផ្ទុករូបភាពឡើង\s*<img[^>]*src="/static/timemark-logo.webp"',
                     checkin)
    assert not re.search(r'<label[^>]*for="visit-photo"', checkin)
    assert 'aria-label="Add TimeMark photos (1–3)"' in checkin
    assert re.search(r'<input(?=[^>]*id="visit-photo")(?=[^>]*\brequired\b)[^>]*>', checkin)
    assert browser.get("/static/timemark-logo.webp").status_code == 200


def test_welcome_photo_uses_current_employee_and_not_cookie(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    photo = image_data()
    fake.employee_photos[1] = photo

    home = browser.get("/")
    assert home.status_code == 200
    assert f'data:image/png;base64,{photo}' in home.text
    assert 'class="hero-avatar-image"' in home.text
    assert "WELCOME BACK" not in home.text
    assert "Your daily work, all in one place." not in home.text
    assert re.search(r'class="hero-profile">.*?class="hero-avatar".*?</div>\s*<h2>Salesperson</h2>',
                     home.text, re.DOTALL)
    assert fake.calls[-1] == ("hr.employee", "read", {"ids": [1], "fields": ["image_512"]})
    assert photo not in home.headers.get("set-cookie", "")

    browser.cookies.clear()
    fake.employee_photos[2] = photo
    assert browser.post("/api/login", json={"employee_id": 2, "pin": "5678"}).status_code == 200
    assert f'data:image/png;base64,{photo}' in browser.get("/").text
    assert fake.calls[-1][2]["ids"] == [2]


def test_welcome_photo_placeholder_and_lookup_failure(monkeypatch, tmp_path, caplog):
    browser, fake = client(monkeypatch, tmp_path)
    home = browser.get("/")
    assert 'class="hero-avatar"' in home.text
    assert 'src="/static/avatar-placeholder.svg"' in home.text
    assert "Photo unavailable" not in home.text
    assert browser.get("/static/avatar-placeholder.svg").status_code == 200

    fake.fail_method = "read"
    home = browser.get("/")
    assert home.status_code == 200
    assert "Photo unavailable" in home.text and "New customer" in home.text
    assert "Employee photo lookup failed" in caplog.text


def test_welcome_photo_not_fetched_before_login_or_after_expiration(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    now = application.time.time()
    monkeypatch.setattr(application.time, "time", lambda: now + 3601)
    fake.calls.clear()
    assert 'id="login-form"' in browser.get("/").text
    assert not [call for call in fake.calls if call[1] == "read"]


def test_create_deduplicates_and_edit_keeps_owner(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    token = str(uuid.uuid4())
    payload = {"token": token, "name": "Shop", "phone": "+85512345678",
               "latitude": 11.55, "longitude": 104.92, "tags": [10]}
    first = browser.post("/api/customer", json=payload).json()
    second = browser.post("/api/customer", json=payload).json()
    assert first["status"] == second["status"] == "success"
    assert first["partner_id"] == 42
    creates = [call for call in fake.calls if call[:2] == ("res.partner", "create")]
    assert len(creates) == 1
    vals = creates[0][2]["vals_list"][0]
    assert vals["x_employee_id"] == 1 and vals["zip"] == "w649ew87t"
    assert vals["category_id"] == [[6, 0, [10]]]
    assert browser.put(f"/api/customer/{token}", json=payload).json()["status"] == "success"
    write = [c for c in fake.calls if c[1] == "write"][0]
    assert write[2]["ids"] == [42] and "x_employee_id" not in write[2]["vals"]
    assert write[2]["vals"]["zip"] == "w649ew87t"


def test_customer_update_removes_photo_only_when_requested(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    token = str(uuid.uuid4())
    payload = {"token": token, "name": "Shop", "photo": "original"}
    assert browser.post("/api/customer", json=payload).json()["status"] == "success"

    assert browser.put(f"/api/customer/{token}", json={"name": "Shop"}).status_code == 200
    assert "image_1920" not in fake.calls[-1][2]["vals"]

    assert browser.put(f"/api/customer/{token}", json={"name": "Shop", "remove_photo": True}).status_code == 200
    assert fake.calls[-1][2]["vals"]["image_1920"] is False

    assert browser.put(f"/api/customer/{token}", json={"name": "Shop", "photo": "replacement", "remove_photo": True}).status_code == 200
    assert fake.calls[-1][2]["vals"]["image_1920"] == "replacement"


def test_uncertain_create_is_persisted_and_never_retried(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    fake.fail_method = "create"
    token = str(uuid.uuid4())
    first = browser.post("/api/customer", json={"token": token, "name": "Shop"}).json()
    fake.fail_method = None
    second = browser.post("/api/customer", json={"token": token, "name": "Shop"}).json()
    assert first["status"] == second["status"] == "unknown"
    assert browser.get(f"/api/operations/{token}").json()["status"] == "unknown"
    assert len([c for c in fake.calls if c[1] == "create"]) == 1
    assert Store(str(tmp_path / "operations.sqlite3")).get(token)["status"] == "unknown"


def test_nearby_and_checkin(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    result = browser.get("/api/nearby?lat=11.55&lon=104.92&filter=mine").json()
    assert result["customers"][0]["mine"]
    query = [c for c in fake.calls if c[0] == "res.partner" and c[1] == "search_read"][0]
    assert query[2]["limit"] == 100
    assert ["x_employee_id", "=", 1] in query[2]["domain"]
    token = str(uuid.uuid4())
    photo = image_data()
    payload = {"token": token, "partner_id": 7, "latitude": 11.55, "longitude": 104.92,
               "accuracy": 5, "comment": "<script>", "photos": [photo, photo, photo]}
    assert browser.post("/api/checkin", json=payload).json()["status"] == "success"
    assert browser.post("/api/checkin", json=payload).json()["status"] == "success"
    posted = [c for c in fake.calls if c[1] == "message_post"]
    assert len(posted) == 1 and posted[0][2]["attachment_ids"] == [55, 56, 57]
    assert posted[0][2]["author_id"] == 101
    assert posted[0][2]["subject"] == "APP现场拍照打卡"
    assert ("hr.employee", "read", {"ids": [1], "fields": ["work_contact_id"]}) in fake.calls
    assert "&lt;script&gt;" in posted[0][2]["body"]
    attachment = [c for c in fake.calls if c[0] == "ir.attachment"][0]
    assert len(attachment[2]["vals_list"]) == 3
    assert attachment[2]["vals_list"][0]["raw"] == photo
    assert attachment[2]["vals_list"][0]["mimetype"] == "image/jpeg"
    assert "datas" not in attachment[2]["vals_list"][0]


def test_checkin_uses_current_employees_work_contact(monkeypatch, tmp_path):
    fake = FakeOdoo()
    monkeypatch.setattr(application, "odoo", fake)
    monkeypatch.setattr(application, "store", Store(str(tmp_path / "operations.sqlite3")))
    browser = TestClient(application.app)
    assert browser.post("/api/login", json={"employee_id": 2, "pin": "5678"}).status_code == 200
    payload = {"token": str(uuid.uuid4()), "partner_id": 7, "latitude": 11.55,
               "longitude": 104.92, "photos": [image_data()]}
    assert browser.post("/api/checkin", json=payload).json()["status"] == "success"
    posted = next(call for call in fake.calls if call[1] == "message_post")
    assert posted[2]["author_id"] == 102
    assert ("hr.employee", "read", {"ids": [2], "fields": ["work_contact_id"]}) in fake.calls


@pytest.mark.parametrize("contact", [False, [], [False, "Invalid"], [0, "Invalid"], None])
def test_checkin_without_work_contact_fails_before_creating_records(monkeypatch, tmp_path, contact):
    browser, fake = client(monkeypatch, tmp_path)
    original = fake.call

    def missing_contact(model, method, **kwargs):
        if model == "hr.employee" and kwargs.get("fields") == ["work_contact_id"]:
            fake.calls.append((model, method, kwargs))
            return [{"work_contact_id": contact}]
        return original(model, method, **kwargs)

    monkeypatch.setattr(fake, "call", missing_contact)
    payload = {"token": str(uuid.uuid4()), "partner_id": 7, "latitude": 11.55,
               "longitude": 104.92, "photos": [image_data()]}
    result = browser.post("/api/checkin", json=payload).json()
    assert result["status"] == "failed"
    assert "work contact" in result["detail"]
    assert not [c for c in fake.calls if c[0] == "ir.attachment" or c[1] == "message_post"]
    assert browser.post("/api/checkin", json=payload).json() == result


def test_checkin_work_contact_lookup_failure_is_definite(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    fake.fail_method = "read"
    payload = {"token": str(uuid.uuid4()), "partner_id": 7, "latitude": 11.55,
               "longitude": 104.92, "photos": [image_data()]}
    result = browser.post("/api/checkin", json=payload).json()
    assert result["status"] == "failed"
    assert "work contact" in result["detail"]
    assert not [c for c in fake.calls if c[0] == "ir.attachment" or c[1] == "message_post"]


def test_checkin_message_contains_local_time_and_clickable_coordinates(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 15, 7, 25, 54, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(application, "datetime", FixedDateTime)
    payload = {"token": str(uuid.uuid4()), "partner_id": 7,
               "latitude": 11.516441, "longitude": 104.9669491, "comment": "<script>",
               "photos": [image_data()]}
    assert browser.post("/api/checkin", json=payload).json()["status"] == "success"
    posted = next(call for call in fake.calls if call[1] == "message_post")
    body = posted[2]["body"]
    assert "Time (Phnom Penh): 2026-11-15 14:25:54" in body
    assert "Time (UTC)" not in body and "07:25:54" not in body
    assert ('<a href="https://www.google.com/maps/search/?api=1&amp;query=11.516441%2C104.9669491">'
            '11.516441, 104.9669491</a>') in body
    assert "&lt;script&gt;" in body
    assert posted[2]["body_is_html"] is True


def test_geohash_coverage_across_boundaries():
    for lat, lon in [(11.55, 104.92), (11.5576171874, 104.9414062499)]:
        prefixes = nearby_prefixes(lat, lon)
        for dy in (-0.026, 0, 0.026):
            for dx in (-0.026, 0, 0.026):
                for precision in (7, 8, 9):
                    assert encode(lat + dy, lon + dx, precision).startswith(tuple(prefixes))


def test_nearby_query_uses_common_prefix_for_old_and_new_hashes(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    assert browser.get("/api/nearby?lat=11.55&lon=104.92&filter=all").status_code == 200
    query = next(c for c in fake.calls if c[0] == "res.partner" and c[1] == "search_read")
    prefixes = [term[2][:-1] for term in query[2]["domain"] if isinstance(term, list) and term[:2] == ["zip", "=like"]]
    assert "w649e" in prefixes
    assert "w649ew8".startswith(tuple(prefixes))
    assert "w649ew87".startswith(tuple(prefixes))
    assert "w649ew87t".startswith(tuple(prefixes))


def test_session_expiration(monkeypatch, tmp_path):
    browser, _ = client(monkeypatch, tmp_path)
    now = application.time.time()
    monkeypatch.setattr(application.time, "time", lambda: now + 3601)
    assert browser.get("/api/nearby?lat=11&lon=104").status_code == 401
    sign_in = browser.get("/")
    assert 'id="employee"' not in sign_in.text
    assert browser.post("/api/login", json={"pin": "1234"}).status_code == 200


def test_wrong_pin_does_not_authenticate(monkeypatch, tmp_path):
    fake = FakeOdoo()
    monkeypatch.setattr(application, "odoo", fake)
    monkeypatch.setattr(application, "store", Store(str(tmp_path / "operations.sqlite3")))
    browser = TestClient(application.app)
    assert browser.post("/api/login", json={"employee_id": 1, "pin": "wrong"}).status_code == 401
    assert 'id="employee"' in browser.get("/").text
    assert browser.get("/api/nearby?lat=11&lon=104").status_code == 401


def test_employee_selection_and_clear_data(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    browser.cookies.clear()
    initial = browser.get("/?employee=1")
    assert 'id="employee"' in initial.text
    assert "Salesperson" in initial.text and "Second Employee" in initial.text
    query = fake.calls[-1]
    assert query[0:2] == ("hr.employee", "search_read")
    assert query[2]["fields"] == ["id", "name"]
    assert browser.post("/api/login", json={"employee_id": 2, "pin": "5678"}).status_code == 200
    assert "Second Employee" in browser.get("/").text
    browser.cookies.clear()
    assert 'id="employee"' in browser.get("/").text


def test_remembered_employee_cannot_be_changed_by_url_or_login_payload(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    assert "Salesperson" in browser.get("/?employee=2").text
    now = application.time.time()
    monkeypatch.setattr(application.time, "time", lambda: now + 3601)
    assert browser.post("/api/login", json={"employee_id": 2, "pin": "5678"}).status_code == 401
    assert fake.calls[-1][2]["ids"] == [1]
    assert browser.post("/api/login", json={"employee_id": 2, "pin": "1234"}).status_code == 200
    assert "Salesperson" in browser.get("/").text


def test_employee_list_failure_is_visible(monkeypatch, tmp_path):
    fake = FakeOdoo()
    fake.fail_method = "search_read"
    monkeypatch.setattr(application, "odoo", fake)
    monkeypatch.setattr(application, "store", Store(str(tmp_path / "operations.sqlite3")))
    browser = TestClient(application.app)
    response = browser.get("/")
    assert "Unable to load employees" in response.text
    assert "Try again" in response.text


def test_missing_or_unrecognized_employee_is_not_remembered(monkeypatch, tmp_path):
    browser, _ = client(monkeypatch, tmp_path)
    browser.cookies.clear()
    assert browser.post("/api/login", json={"pin": "1234"}).status_code == 400
    assert browser.post("/api/login", json={"employee_id": 999, "pin": "1234"}).status_code == 401
    assert 'id="employee"' in browser.get("/").text


def test_no_employees_shows_actionable_message(monkeypatch, tmp_path):
    class EmptyEmployees(FakeOdoo):
        def call(self, model, method, **kwargs):
            if model == "hr.employee" and method == "search_read":
                return []
            return super().call(model, method, **kwargs)

    monkeypatch.setattr(application, "odoo", EmptyEmployees())
    monkeypatch.setattr(application, "store", Store(str(tmp_path / "operations.sqlite3")))
    response = TestClient(application.app).get("/")
    assert "No employees available" in response.text
    assert "Try again" in response.text


def test_checkin_message_timeout_does_not_duplicate(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    fake.fail_method = "message_post"
    token = str(uuid.uuid4())
    payload = {"token": token, "partner_id": 7, "latitude": 11.55, "longitude": 104.92, "photos": [image_data()]}
    assert browser.post("/api/checkin", json=payload).json()["status"] == "unknown"
    assert "55" in browser.get(f"/api/operations/{token}").json()["detail"]
    fake.fail_method = None
    assert browser.post("/api/checkin", json=payload).json()["status"] == "unknown"
    assert len([c for c in fake.calls if c[1] == "message_post"]) == 1
    assert len([c for c in fake.calls if c[0] == "ir.attachment"]) == 1


@pytest.mark.parametrize(("photos", "reason"), [
    ([], "1 to 3"),
    ([image_data()] * 4, "1 to 3"),
    ([image_data(None)], "Photo 1"),
    ([image_data("Other camera")], "TimeMark"),
    ([image_data("Time Mark")], "TimeMark"),
    ([image_data(), image_data(None)], "Photo 2"),
    (["not base64"], "invalid image data"),
    ([base64.b64encode(b"not an image").decode()], "unreadable"),
])
def test_invalid_visit_photos_rejected_before_odoo(monkeypatch, tmp_path, photos, reason):
    browser, fake = client(monkeypatch, tmp_path)
    token = str(uuid.uuid4())
    response = browser.post("/api/checkin", json={
        "token": token, "partner_id": 7, "latitude": 11.55, "longitude": 104.92, "photos": photos,
    })
    assert response.status_code == 400
    assert response.json()["validation"] is True
    assert reason in response.json()["error"]
    assert application.store.get(token) is None
    assert not [c for c in fake.calls if c[0] in ("ir.attachment", "res.partner")]


def test_photo_size_limit_in_bytes(monkeypatch):
    photo = image_data()
    size = len(base64.b64decode(photo))
    monkeypatch.setattr(visit_photos, "MAX_PHOTO_BYTES", size)
    assert len(visit_photos.validate_visit_photos([photo])) == 1
    monkeypatch.setattr(visit_photos, "MAX_PHOTO_BYTES", size - 1)
    with pytest.raises(visit_photos.PhotoValidationError, match="larger than 20 MB"):
        visit_photos.validate_visit_photos([photo])


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_supported_visit_image_formats(image_format):
    image = Image.new("RGB", (4, 4))
    exif = Image.Exif()
    exif[305] = "Timemark v10.0.316"
    content = io.BytesIO()
    image.save(content, format=image_format, exif=exif)
    photos = visit_photos.validate_visit_photos([base64.b64encode(content.getvalue()).decode()])
    assert len(photos) == 1


def test_truncated_jpeg_rejected():
    content = base64.b64decode(image_data())[:-10]
    with pytest.raises(visit_photos.PhotoValidationError, match="unreadable"):
        visit_photos.validate_visit_photos([base64.b64encode(content).decode()])


def test_existing_checkin_token_ignores_missing_photos(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    payload = {"token": str(uuid.uuid4()), "partner_id": 7, "latitude": 11.55,
               "longitude": 104.92, "photos": [image_data()]}
    assert browser.post("/api/checkin", json=payload).json()["status"] == "success"
    payload.pop("photos")
    assert browser.post("/api/checkin", json=payload).json()["status"] == "success"
    assert len([c for c in fake.calls if c[1] == "message_post"]) == 1


@pytest.mark.parametrize("response", [[55], [55, 55], [55, False], None])
def test_incomplete_attachment_result_is_unknown(monkeypatch, tmp_path, response):
    browser, fake = client(monkeypatch, tmp_path)
    original = fake.call

    def incomplete(model, method, **kwargs):
        if (model, method) == ("ir.attachment", "create"):
            fake.calls.append((model, method, kwargs))
            return response
        return original(model, method, **kwargs)

    monkeypatch.setattr(fake, "call", incomplete)
    payload = {"token": str(uuid.uuid4()), "partner_id": 7, "latitude": 11.55,
               "longitude": 104.92, "photos": [image_data(), image_data()]}
    result = browser.post("/api/checkin", json=payload).json()
    assert result["status"] == "unknown"
    if isinstance(response, list) and 55 in response:
        assert "55" in result["detail"]
    assert browser.post("/api/checkin", json=payload).json()["status"] == "unknown"
    assert len([c for c in fake.calls if c[0] == "ir.attachment"]) == 1
    assert not [c for c in fake.calls if c[1] == "message_post"]


def test_message_failure_retains_all_attachment_ids(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    original = fake.call

    def fail_message(model, method, **kwargs):
        if method == "message_post":
            raise OdooError("Rejected", uncertain=False)
        return original(model, method, **kwargs)

    monkeypatch.setattr(fake, "call", fail_message)
    payload = {"token": str(uuid.uuid4()), "partner_id": 7, "latitude": 11.55,
               "longitude": 104.92, "photos": [image_data()] * 3}
    result = browser.post("/api/checkin", json=payload).json()
    assert result["status"] == "failed"
    assert "attachment_ids=[55, 56, 57]" in result["detail"]


def test_attachment_timeout_is_unknown_and_not_retried(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    fake.fail_method = "create"
    payload = {"token": str(uuid.uuid4()), "partner_id": 7, "latitude": 11.55,
               "longitude": 104.92, "photos": [image_data()] * 3}
    assert browser.post("/api/checkin", json=payload).json()["status"] == "unknown"
    fake.fail_method = None
    assert browser.post("/api/checkin", json=payload).json()["status"] == "unknown"
    assert len([c for c in fake.calls if c[0] == "ir.attachment"]) == 1
    assert not [c for c in fake.calls if c[1] == "message_post"]


def test_png_without_exif_is_validation_error(monkeypatch, tmp_path):
    browser, fake = client(monkeypatch, tmp_path)
    content = io.BytesIO()
    Image.new("RGB", (4, 4)).save(content, format="PNG")
    token = str(uuid.uuid4())
    response = browser.post("/api/checkin", json={
        "token": token, "partner_id": 7, "latitude": 11.55, "longitude": 104.92,
        "photos": [base64.b64encode(content.getvalue()).decode()],
    })
    assert response.status_code == 400 and response.json()["validation"] is True
    assert "TimeMark" in response.json()["error"]
    assert not [c for c in fake.calls if c[0] == "ir.attachment"]


def test_unknown_operation_is_reported_as_missing(monkeypatch, tmp_path):
    browser, _ = client(monkeypatch, tmp_path)
    response = browser.get(f"/api/operations/{uuid.uuid4()}")
    assert response.status_code == 404 and response.json()["missing"] is True


@pytest.mark.parametrize("software", ["Timemark v10.0.316", "TIMEMARK", "TimeMarker",
                                      "Timemark-Pro", "Timemark/10.0", "My Timemark app"])
def test_software_containing_timemark_is_accepted(software):
    assert len(visit_photos.validate_visit_photos([image_data(software)])) == 1
