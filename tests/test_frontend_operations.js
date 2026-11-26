"use strict";

const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");

function setup(kind, fetch, previousToken = null, navigation = "reload", photoFile = null, locationSelected = true) {
  const handlers = {};
  const button = {disabled: false, textContent: ""};
  const photoControl = {disabled: false};
  const form = {
    dataset: {}, elements: [],
    addEventListener(name, handler) { handlers[name] = handler; },
    querySelector(selector) { return selector === "#submit" ? button : null; },
    querySelectorAll(selector) {
      return selector === '.photo-picker input[type="file"], .photo-remove' ? [photoControl] : [];
    },
  };
  const status = {textContent: ""};
  const notice = {textContent: ""};
  const fields = {
    "#operation-status": status, "#notice": notice, "#submit": button,
    "#photo": {files: photoFile ? [photoFile] : kind === "customer" ? [{dataUrl: "data:image/jpeg;base64,shop"}] : [], required: true},
    "#partner_id": {value: "7"},
    "#visit-photo": {files: [{size: 1}]}, "#download-timemark": {hidden: false},
  };
  fields[`#${kind}-form`] = form;
  const items = new Map(previousToken ? [[`sales-pwa-${kind}-operation`, previousToken]] : []);
  const storage = {
    getItem: (key) => items.get(key) ?? null,
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: (key) => items.delete(key),
  };
  let token = 0;
  const location = {replace(url) { this.replaced = url; }};
  const context = {
    document: {
      querySelector: (selector) => fields[selector] || null,
      querySelectorAll: () => [],
    },
    window: {}, navigator: {geolocation: {
      getCurrentPosition(resolve) { resolve({coords: {latitude: 1, longitude: 2, accuracy: 3}}); },
    }},
    performance: {getEntriesByType: () => [{type: navigation}]},
    localStorage: storage, sessionStorage: {
      getItem: () => null, setItem() {}, removeItem() {},
    },
    crypto: {randomUUID: () => `new-token-${++token}`},
    FormData: class {
      entries() {
        return Object.entries(kind === "customer"
          ? {name: "Shop", phone: "012345678", function: "Owner", lang: "en_US",
            latitude: locationSelected ? "11.55" : "", longitude: locationSelected ? "104.92" : ""}
          : {name: "Shop", partner_id: "7"});
      }
    },
    FileReader: class {
      readAsDataURL(file) { this.file = file; this.result = file.dataUrl || "data:image/jpeg;base64,photo"; this.onload(); }
    },
    AbortController, fetch, location, setTimeout, URL, console,
  };
  vm.runInNewContext(source, context);
  return {
    button, photoControl, status, notice, storage, form, location, photo: fields["#photo"],
    submit: () => handlers.submit({preventDefault() {}}),
  };
}

function response(data, status = 200) {
  return {ok: status >= 200 && status < 300, json: async () => data};
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return {promise, resolve};
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
}

for (const kind of ["customer", "checkin"]) {
  for (const outcome of ["unknown", "network"]) {
    test(`${kind}: ${outcome} allows a new submission with a new reference`, async () => {
      const requests = [];
      const page = setup(kind, async (url, options) => {
        requests.push(JSON.parse(options.body));
        if (requests.length === 1) {
          if (outcome === "network") throw new Error("Network error");
          return response({status: "unknown"});
        }
        return response({status: "success", partner_id: 42});
      });
      await page.submit();
      assert.equal(page.button.disabled, false);
      assert.equal(page.photoControl.disabled, false);
      assert.match(page.status.textContent, /new-token-1.*submit again.*duplicate/);
      await page.submit();
      assert.deepEqual(requests.map((request) => request.token), ["new-token-1", "new-token-2"]);
      if (kind === "customer") assert.equal(page.form.dataset.partnerId, 42);
      else assert.equal(page.location.replaced, "/");
    });
  }

  for (const outcome of ["unknown", "network", "missing"]) {
    test(`${kind}: restoring ${outcome} leaves the form usable`, async () => {
      const page = setup(kind, async () => {
        if (outcome === "network") throw new Error("Network error");
        if (outcome === "missing") return response({error: "Not found", missing: true}, 404);
        return response({status: "unknown"});
      }, "old-token");
      await flush();
      assert.equal(page.button.disabled, false);
      assert.equal(page.photoControl.disabled, false);
      assert.match(page.status.textContent, /submit again.*duplicate/);
      assert.equal(page.storage.getItem(`sales-pwa-${kind}-operation`), outcome === "missing" ? null : "old-token");
    });
  }

  test(`${kind}: failed submission remains retryable`, async () => {
    const page = setup(kind, async () => response({status: "failed", detail: "Rejected"}));
    await page.submit();
    assert.equal(page.button.disabled, false);
    assert.match(page.status.textContent, /Rejected/);
    assert.equal(page.storage.getItem(`sales-pwa-${kind}-operation`), null);
  });

  test(`${kind}: submitting while restore is pending ignores its late success`, async () => {
    const restore = deferred();
    const post = deferred();
    let restoreSignal;
    let submittedToken;
    const page = setup(kind, (url, options) => {
      if (!options.method) {
        restoreSignal = options.signal;
        return restore.promise;
      }
      submittedToken = JSON.parse(options.body).token;
      return post.promise;
    }, "old-token");
    assert.equal(page.button.disabled, false);
    const submitting = page.submit();
    await flush();
    assert.equal(restoreSignal.aborted, true);
    assert.equal(submittedToken, "new-token-1");
    restore.resolve(response({status: "success", partner_id: 42}));
    await flush();
    assert.equal(page.storage.getItem(`sales-pwa-${kind}-operation`), "new-token-1");
    assert.equal(page.button.disabled, true);
    assert.equal(page.location.replaced, undefined);
    assert.equal(page.form.dataset.partnerId, undefined);
    post.resolve(response({status: "unknown"}));
    await submitting;
    assert.equal(page.button.disabled, false);
  });
}

test("customer: a confirmed previous success restores editing on reload", async () => {
  const page = setup("customer", async () => response({status: "success", partner_id: 42}), "old-token");
  await flush();
  assert.equal(page.form.dataset.partnerId, 42);
  assert.equal(page.form.dataset.token, "old-token");
  assert.equal(page.button.textContent, "Update customer");
  assert.equal(page.photo.required, false);
});

test("customer: uploads the selected original photo without conversion", async () => {
  const photoFile = {dataUrl: "data:image/jpeg;base64,original-photo"};
  let payload;
  const page = setup("customer", async (url, options) => {
    payload = JSON.parse(options.body);
    return response({status: "success", partner_id: 42});
  }, null, "reload", photoFile);
  await page.submit();
  assert.equal(payload.photo, "original-photo");
  assert.equal(page.form.dataset.partnerId, 42);
  assert.equal(page.photo.required, false);
});

test("customer: location is required before submitting", async () => {
  let submitted = false;
  const page = setup("customer", async () => {
    submitted = true;
    return response({status: "success", partner_id: 42});
  }, null, "reload", null, false);
  await page.submit();
  assert.equal(submitted, false);
  assert.match(page.notice.textContent, /location/i);
});

test("checkin: a confirmed previous success returns home", async () => {
  const page = setup("checkin", async () => response({status: "success"}), "old-token");
  await flush();
  assert.equal(page.location.replaced, "/");
});

test("checkin: photo validation failure remains retryable", async () => {
  const page = setup("checkin", async () => response({error: "Invalid photo", validation: true}, 400));
  await page.submit();
  assert.equal(page.button.disabled, false);
  assert.match(page.status.textContent, /Invalid photo/);
  assert.equal(page.storage.getItem("sales-pwa-checkin-operation"), null);
});

test("cancelled restore does not redirect on a late login response", async () => {
  const restore = deferred();
  const post = deferred();
  const page = setup("customer", (url, options) => options.method ? post.promise : restore.promise, "old-token");
  const submitting = page.submit();
  await flush();
  restore.resolve(response({login: true, error: "Expired"}, 401));
  await flush();
  assert.equal(page.location.href, undefined);
  post.resolve(response({status: "unknown"}));
  await submitting;
});

for (const [kind, expectedZoom] of [["checkin", 18], ["customer", 20], ["nearby", 15]]) {
  test(`${kind}: map initializes at the page's default zoom`, () => {
    function element() {
      return {
        handlers: {},
        addEventListener(name, handler) { this.handlers[name] = handler; },
        focus() {},
      };
    }
    const form = {...element(), dataset: {}, querySelectorAll: () => []};
    const dialog = {
      ...element(), open: false,
      showModal() { this.open = true; },
      close() { this.open = false; this.handlers.close(); },
    };
    const fields = {
      "#map": element(), "#notice": element(), "#map-notice": element(),
      "#location-status": element(), "#confirm-location": element(),
      "#open-customer-map": element(), "#close-customer-map": element(),
    };
    if (kind === "nearby") fields["#filter"] = element();
    else {
      fields[`#${kind}-form`] = form;
      fields["#customer-map-dialog"] = dialog;
    }
    const maps = [];
    const context = {
      document: {querySelector: (selector) => fields[selector] || null, querySelectorAll: () => []},
      window: {}, navigator: {},
      google: {maps: {
        Map: class {
          constructor(node, options) { this.zoom = options.zoom; maps.push(this); }
          addListener() {}
          panTo() {}
        },
        InfoWindow: class { close() {} },
        event: {trigger() {}},
      }},
      performance: {getEntriesByType: () => [{type: "reload"}]},
      localStorage: {getItem: () => null},
      sessionStorage: {getItem: () => null, removeItem() {}},
      setTimeout() {},
    };
    vm.runInNewContext(source, context);
    context.window.mapsReady();
    if (kind !== "nearby") {
      assert.equal(maps.length, 0);
      fields["#open-customer-map"].handlers.click();
    }
    assert.equal(maps.length, 1);
    assert.equal(maps[0].zoom, expectedZoom);
    if (kind !== "nearby") {
      maps[0].zoom = 12;
      dialog.close();
      fields["#open-customer-map"].handlers.click();
      assert.equal(maps.length, 1);
      assert.equal(maps[0].zoom, 12);
    }
  });
}
