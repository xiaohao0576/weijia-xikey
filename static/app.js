"use strict";

const $ = (selector) => document.querySelector(selector);
const notice = (message) => {
  const el = $("#customer-map-dialog")?.open ? $("#map-notice") : $("#notice");
  if (el) el.textContent = message;
};
let map, marker, infoWindow, currentPosition;
let mapsLoaded = false;
let mapsLoadFailed = false;
let mapsAuthorizationFailed = false;
let customerMarkers = [];

window.gm_authFailure = () => {
  mapsAuthorizationFailed = true;
  notice("Google Maps authorization failed. Please contact IT.");
};

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker").catch(() => notice("App installation support is unavailable."));
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json();
  if (data.login) { location.href = "/"; throw new Error(data.error); }
  if (!response.ok) {
    const error = new Error(data.error || "Request failed.");
    error.validation = data.validation === true;
    error.missing = data.missing === true;
    throw error;
  }
  return data;
}

const loginForm = $("#login-form");
if (loginForm) loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = loginForm.querySelector("button");
  button.disabled = true;
  try {
    await api("/api/login", {method: "POST", body: JSON.stringify({
      pin: loginForm.pin.value,
      employee_id: loginForm.querySelector("#employee")?.value,
    })});
    location.href = "/";
  } catch (error) { notice(error.message); }
  finally { button.disabled = false; }
});

window.mapsReady = () => {
  mapsLoaded = true;
  if (!$("#map")) return;
  if ($("#customer-map-dialog") && !$("#customer-map-dialog").open) return;
  initializeMap();
};

function initializeMap() {
  if (map) return;
  map = new google.maps.Map($("#map"), {center: {lat: 11.5564, lng: 104.9282}, zoom: 14, mapTypeControl: false, streetViewControl: false});
  infoWindow = new google.maps.InfoWindow();
  if ($("#customer-form")) {
    map.addListener("click", (event) => setPosition(event.latLng.lat(), event.latLng.lng()));
    showCommittedLocation();
    return;
  }
  locate();
}

let pendingLocation = null;
function committedLocation() {
  const lat = $("#latitude")?.value, lng = $("#longitude")?.value;
  if (!lat || !lng) return null;
  return {lat: Number(lat), lng: Number(lng), accuracy: $("#accuracy").value};
}

function updateLocationSummary() {
  const label = $("#selected-location");
  if (!label) return;
  const location = committedLocation();
  label.textContent = location ? `📍 ${location.lat.toFixed(5)}, ${location.lng.toFixed(5)}` : "Select location on map";
}

function showCommittedLocation() {
  pendingLocation = null;
  $("#confirm-location").disabled = true;
  const location = committedLocation();
  if (location) setPosition(location.lat, location.lng, location.accuracy);
  else {
    if (marker) marker.setVisible(false);
    locate();
  }
}

const customerMapDialog = $("#customer-map-dialog");
if (customerMapDialog) {
  $("#open-customer-map").addEventListener("click", () => {
    customerMapDialog.showModal();
    $("#map-notice").textContent = "";
    if (mapsAuthorizationFailed) {
      notice("Google Maps authorization failed. Please contact IT.");
    } else if (mapsLoadFailed && !mapsLoaded) {
      notice("Map failed to load. Check your connection and Google Maps key.");
    } else if (!mapsLoaded) {
      notice("Map is loading. Please wait or check your connection.");
    } else if (!map) {
      initializeMap();
    } else if ($("#customer-form")) {
      google.maps.event.trigger(map, "resize");
      showCommittedLocation();
    } else {
      google.maps.event.trigger(map, "resize");
      if (currentPosition) map.panTo({lat: currentPosition.latitude, lng: currentPosition.longitude});
    }
  });
  $("#close-customer-map").addEventListener("click", () => customerMapDialog.close());
  customerMapDialog.addEventListener("close", () => {
    if (infoWindow) infoWindow.close();
    $("#open-customer-map").focus();
  });
}

function customerPinIcon(color) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="44" viewBox="0 0 32 44">` +
    `<path d="M16 1.5C8 1.5 1.5 8 1.5 16c0 10.6 12.4 24.6 13.4 25.7a1.5 1.5 0 0 0 2.2 0C18.1 40.6 30.5 26.6 30.5 16 30.5 8 24 1.5 16 1.5z" fill="${color}" stroke="#fff" stroke-width="2.5"/>` +
    `<circle cx="16" cy="16" r="5.5" fill="#fff"/></svg>`;
  return {
    url: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
    scaledSize: new google.maps.Size(32, 44),
    // The pin's tip marks the customer's exact location.
    anchor: new google.maps.Point(16, 42),
  };
}

function setPosition(lat, lng, accuracy) {
  if ($("#customer-form")) {
    // The form keeps its location only after the user confirms it.
    pendingLocation = {lat, lng, accuracy: accuracy ?? ""};
    $("#confirm-location").disabled = false;
  }
  if (!marker) {
    marker = new google.maps.Marker({map, draggable: Boolean($("#customer-form"))});
    marker.addListener("dragend", (event) => setPosition(event.latLng.lat(), event.latLng.lng()));
  }
  marker.setPosition({lat, lng});
  marker.setVisible(true);
  map.panTo({lat, lng});
  $("#location-status").textContent = `Location: ${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}

$("#confirm-location")?.addEventListener("click", () => {
  if (!pendingLocation) return;
  $("#latitude").value = pendingLocation.lat;
  $("#longitude").value = pendingLocation.lng;
  $("#accuracy").value = pendingLocation.accuracy;
  pendingLocation = null;
  updateLocationSummary();
  saveDraft($("#customer-form"));
  customerMapDialog.close();
});

function locate() {
  if (!mapsLoaded) { notice("Map is not loaded. Check your connection and Maps configuration."); return; }
  if (!navigator.geolocation) { notice("Location is not supported on this device."); return; }
  $("#location-status").textContent = "Finding location…";
  navigator.geolocation.getCurrentPosition(async (position) => {
    currentPosition = position.coords;
    setPosition(position.coords.latitude, position.coords.longitude, position.coords.accuracy);
    if ($("#nearby-page") || $("#filter") || $("#checkin-form")) await loadNearby();
  }, (error) => { $("#location-status").textContent = `Location unavailable: ${error.message}`; },
  {enableHighAccuracy: true, timeout: 20000, maximumAge: 0});
}

function freshPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) { reject(new Error("Location is not supported on this device.")); return; }
    navigator.geolocation.getCurrentPosition(resolve, reject, {enableHighAccuracy: true, timeout: 20000, maximumAge: 0});
  });
}

$("#locate")?.addEventListener("click", locate);
$("#filter")?.addEventListener("change", loadNearby);
if ($("#map")) setTimeout(() => {
  if (!mapsLoaded) {
    mapsLoadFailed = true;
    if (!customerMapDialog || customerMapDialog.open) notice("Map failed to load. Check your connection and Google Maps key.");
  }
}, 15000);

async function loadNearby() {
  if (!currentPosition) return;
  notice("Loading customers…");
  try {
    const filter = $("#filter")?.value || "all";
    const params = new URLSearchParams({lat: currentPosition.latitude, lon: currentPosition.longitude, filter});
    const data = await api(`/api/nearby?${params}`);
    customerMarkers.forEach((item) => item.setMap(null));
    customerMarkers = [];
    for (const customer of data.customers) {
      const lat = Number(customer.partner_latitude), lng = Number(customer.partner_longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lng) || customer.partner_latitude === false || customer.partner_longitude === false) continue;
      const pin = new google.maps.Marker({
        map, position: {lat, lng}, title: customer.name,
        icon: customerPinIcon(customer.mine ? "#1769aa" : "#dc7028"),
      });
      pin.addListener("click", () => showCustomer(customer, pin));
      customerMarkers.push(pin);
    }
    notice(data.limit_reached ? "Showing up to 100 customers." : `${data.customers.length} customers found.`);
  } catch (error) { notice(error.message); }
}

function showCustomer(customer, pin) {
  const card = document.createElement("div");
  card.className = "map-card";
  const title = document.createElement("strong");
  title.textContent = customer.name;
  card.append(title);
  if (customer.image_512) {
    const image = document.createElement("img");
    image.src = `data:image/png;base64,${customer.image_512}`;
    image.alt = "Customer";
    image.className = "card-photo";
    card.append(image);
  }
  if (customer.barcode) {
    const barcode = document.createElement("p");
    barcode.textContent = `Barcode: ${customer.barcode}`;
    card.append(barcode);
  }
  if ($("#checkin-form")) {
    const select = document.createElement("button");
    select.type = "button";
    select.textContent = "Select customer";
    select.className = "app-button primary";
    select.addEventListener("click", () => {
      $("#partner_id").value = customer.id;
      $("#selected-customer").textContent = customer.name;
      customerMapDialog.close();
    });
    card.append(select);
  } else {
    const link = document.createElement("a");
    const destination = `${customer.partner_latitude},${customer.partner_longitude}`;
    link.href = `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(destination)}`;
    link.textContent = "Navigate";
    link.className = "app-button secondary";
    link.target = "_blank";
    link.rel = "noopener";
    card.append(link);
  }
  infoWindow.setContent(card);
  infoWindow.open({map, anchor: pin});
}

async function compressedPhoto(file) {
  if (!file) return "";
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, 1920 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const result = canvas.toDataURL("image/webp", 0.8);
    if (!result.startsWith("data:image/webp;base64,")) throw new Error("WebP image compression is unavailable on this device.");
    return result.split(",")[1];
  } finally { bitmap.close(); }
}

const phone = $("#phone");
if (phone) phone.addEventListener("input", () => {
  let digits = phone.value.replace(/\D/g, "");
  if (digits && !digits.startsWith("0")) digits = `0${digits}`;
  digits = digits.slice(0, 10);
  phone.value = digits.slice(0, 3)
    + (digits.length > 3 ? ` ${digits.slice(3, 6)}` : "")
    + (digits.length > 6 ? ` ${digits.slice(6)}` : "");
});

function values(form) {
  const data = new FormData(form);
  return Object.fromEntries(data.entries());
}

document.querySelectorAll(".photo-picker").forEach((picker) => {
  const input = picker.querySelector('input[type="file"]');
  const previews = picker.querySelector(".photo-previews");
  const add = picker.querySelector(".photo-add");
  const maxPhotos = input.multiple ? 3 : 1;
  let files = [];
  let urls = [];

  function render() {
    urls.forEach((url) => URL.revokeObjectURL(url));
    urls = [];
    previews.replaceChildren();
    files.forEach((file, index) => {
      const item = document.createElement("div");
      item.className = "photo-preview";
      const image = document.createElement("img");
      image.alt = `Selected photo ${index + 1}`;
      image.src = URL.createObjectURL(file);
      urls.push(image.src);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "photo-remove";
      remove.setAttribute("aria-label", `Remove photo ${index + 1}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => {
        files.splice(index, 1);
        syncInput();
        render();
      });
      item.append(image, remove);
      previews.append(item);
    });
    add.hidden = files.length >= maxPhotos;
  }

  function syncInput() {
    const selection = new DataTransfer();
    files.forEach((file) => selection.items.add(file));
    input.files = selection.files;
  }

  input.addEventListener("change", () => {
    const selected = [...input.files];
    if (files.length + selected.length > maxPhotos) {
      notice(`Select up to ${maxPhotos} photos.`);
    } else {
      files = files.concat(selected);
    }
    syncInput();
    render();
  });
});

function photoBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(new Error("Could not read the selected photo."));
    reader.readAsDataURL(file);
  });
}

function operationKey(kind) { return `sales-pwa-${kind}-operation`; }
function reference() { return crypto.randomUUID(); }
function setStatus(message) { $("#operation-status").textContent = message; }
function setBusy(form, busy) {
  form.querySelector("#submit").disabled = busy;
  form.querySelectorAll('.photo-picker input[type="file"], .photo-remove')
    .forEach((control) => { control.disabled = busy; });
}

const checkinSuccess = $("#checkin-success");
if (checkinSuccess && sessionStorage.getItem("sales-pwa-checkin-success")) {
  sessionStorage.removeItem("sales-pwa-checkin-success");
  checkinSuccess.hidden = false;
}

function finishCheckin() {
  sessionStorage.setItem("sales-pwa-checkin-success", "1");
  localStorage.removeItem(operationKey("checkin"));
  location.replace("/");
}

async function restoreOperation(form, kind) {
  const token = localStorage.getItem(operationKey(kind));
  if (!token) return;
  setBusy(form, true);
  try {
    const result = await api(`/api/operations/${encodeURIComponent(token)}`);
    if (result.status === "success" && kind === "customer" && performance.getEntriesByType("navigation")[0]?.type !== "reload") {
      localStorage.removeItem(operationKey(kind));
      setStatus("Previous customer was created. Start a new customer.");
      setBusy(form, false);
    } else if (result.status === "success" && kind === "customer") {
      form.dataset.token = token;
      form.dataset.partnerId = result.partner_id;
      setStatus(`Customer created: #${result.partner_id}. You can edit on this page.`);
      $("#submit").textContent = "Update customer";
      setBusy(form, false);
    } else if (result.status === "failed") {
      localStorage.removeItem(operationKey(kind));
      setStatus(`Request failed (${token}). ${result.detail || ""} You may try again.`);
      setBusy(form, false);
    } else if (result.status === "success") {
      finishCheckin();
    } else {
      setStatus(`Outcome not confirmed. Contact IT with reference ${token}. Do not submit again.`);
    }
  } catch (error) {
    if (error.missing) {
      // The server never recorded this request, so nothing was sent to Odoo.
      localStorage.removeItem(operationKey(kind));
      setStatus("The previous submission was not received. Please submit again.");
      setBusy(form, false);
    } else {
      setStatus(`Outcome not confirmed. Contact IT with reference ${token}. ${error.message}`);
    }
  }
}

const customerForm = $("#customer-form");
let savedCustomerPhoto = false;
function saveDraft(form) {
  const fields = {};
  for (const element of form.elements) {
    if (!element.name || element.type === "file") continue;
    if (element.type === "radio" && !element.checked) continue;
    fields[element.name] = element.value;
  }
  sessionStorage.setItem("sales-pwa-customer-draft", JSON.stringify(fields));
}

if (customerForm) {
  const isReload = performance.getEntriesByType("navigation")[0]?.type === "reload";
  if (isReload) {
    const draft = JSON.parse(sessionStorage.getItem("sales-pwa-customer-draft") || "{}");
    for (const [name, value] of Object.entries(draft)) {
      for (const field of customerForm.querySelectorAll(`[name="${CSS.escape(name)}"]`)) {
        if (field.type === "radio") field.checked = field.value === value;
        else field.value = value;
      }
    }
    if (Object.keys(draft).length) notice("Draft restored. Please reselect any photo if needed.");
  } else {
    sessionStorage.removeItem("sales-pwa-customer-draft");
  }
  updateLocationSummary();
  customerForm.addEventListener("input", () => saveDraft(customerForm));
  customerForm.addEventListener("change", () => saveDraft(customerForm));
  restoreOperation(customerForm, "customer");
  customerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = values(customerForm);
    const digits = (input.phone || "").replace(/\D/g, "");
    if (digits && (digits.length < 8 || digits.length > 10)) { notice("Phone must contain 8–10 digits including the leading 0."); return; }
    setBusy(customerForm, true);
    notice("");
    let payload;
    try {
      payload = {...input, phone: digits ? `+855${digits.slice(1)}` : "",
        tags: [...customerForm.querySelectorAll("select[name^='tag_']")].filter((s) => s.value).map((s) => Number(s.value)),
        photo: await compressedPhoto($("#photo").files[0])};
    } catch (error) { notice(`Image preparation failed: ${error.message}`); setBusy(customerForm, false); return; }
    const editing = Boolean(customerForm.dataset.partnerId);
    payload.remove_photo = editing && savedCustomerPhoto && !payload.photo;
    const token = editing ? customerForm.dataset.token : reference();
    payload.token = token;
    if (!editing) localStorage.setItem(operationKey("customer"), token);
    setStatus(editing ? "Updating…" : "Creating…");
    try {
      const result = await api(editing ? `/api/customer/${token}` : "/api/customer",
        {method: editing ? "PUT" : "POST", body: JSON.stringify(payload)});
      if (result.status === "success") {
        if (payload.photo) savedCustomerPhoto = true;
        else if (payload.remove_photo) savedCustomerPhoto = false;
        customerForm.dataset.partnerId = result.partner_id;
        customerForm.dataset.token = token;
        $("#submit").textContent = "Update customer";
        setStatus(`Saved customer #${result.partner_id}.`);
        setBusy(customerForm, false);
      } else if (result.status === "failed") {
        localStorage.removeItem(operationKey("customer"));
        setStatus(`Request failed (${token}). ${result.detail || ""} You may try again.`);
        setBusy(customerForm, false);
      } else {
        setStatus(`Outcome not confirmed. Contact IT with reference ${token}. Do not submit again.`);
      }
    } catch (error) {
      setStatus(editing ? `Update outcome may be unknown. Check Odoo before retrying. ${error.message}`
        : `Outcome not confirmed. Contact IT with reference ${token}. ${error.message}`);
      if (editing) setBusy(customerForm, false);
    }
  });
  document.querySelectorAll("a[href='/']").forEach((a) => a.addEventListener("click", () => {
    if (customerForm.dataset.partnerId) localStorage.removeItem(operationKey("customer"));
  }));
}

const checkinForm = $("#checkin-form");
if (checkinForm) {
  restoreOperation(checkinForm, "checkin");
  checkinForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!$("#partner_id").value) { notice("Select a customer."); return; }
    const files = [...$("#visit-photo").files];
    if (files.length < 1 || files.length > 3) { notice("Select 1 to 3 TimeMark photos."); return; }
    if (files.some((file) => file.size > 20 * 1024 * 1024)) { notice("Each photo must be 20 MB or smaller."); return; }
    setBusy(checkinForm, true);
    notice("");
    $("#download-timemark").hidden = true;
    let photos;
    try {
      setStatus("Getting current location…");
      currentPosition = (await freshPosition()).coords;
      photos = await Promise.all(files.map(photoBase64));
    } catch (error) { notice(`Location or image preparation failed: ${error.message}`); setStatus(""); setBusy(checkinForm, false); return; }
    const token = reference();
    localStorage.setItem(operationKey("checkin"), token);
    const payload = {...values(checkinForm), token, photos, latitude: currentPosition.latitude,
      longitude: currentPosition.longitude, accuracy: currentPosition.accuracy};
    setStatus("Submitting check-in…");
    try {
      const result = await api("/api/checkin", {method: "POST", body: JSON.stringify(payload)});
      if (result.status === "success") {
        finishCheckin();
      } else if (result.status === "failed") {
        localStorage.removeItem(operationKey("checkin"));
        setStatus(`Check-in failed (${token}). ${result.detail || ""}`);
        setBusy(checkinForm, false);
      } else setStatus(`Outcome not confirmed. Contact IT with reference ${token}. Do not submit again.`);
    } catch (error) {
      if (error.validation) {
        localStorage.removeItem(operationKey("checkin"));
        setStatus(error.message);
        $("#download-timemark").hidden = false;
        setBusy(checkinForm, false);
      } else {
        setStatus(`Outcome not confirmed. Contact IT with reference ${token}. ${error.message}`);
      }
    }
  });
}
