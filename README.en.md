# Roadtrip Ledger MVP

[简体中文](README.md) | [English](README.en.md)

Current application version: v3.2.1. Frontend asset revision: v3.2.1.

## Versioning

The application and frontend asset revision use semantic versions (`vmajor.minor.patch`, for example `v3.1.1`); standalone incrementing asset revisions are no longer used. The database `schema_version` changes only with database migrations and is maintained separately.

## Release history

- **v3.2.1 (current):** fixes terminal bare amounts when speech-to-text omits a currency unit, such as “吃了一碗面 30”, while conservatively excluding mileage, quantities, and identifiers.
- **v3.2.0:** adds optional DeepSeek Flash semantic completion for transcribed text. Local rules, offline entry, and final save-time validation remain the baseline safeguards.
- **v3.1.3:** completed local acceptance of the mobile UI, GPS, offline cache, Excel export, and the full ledger lifecycle; see the [current acceptance report](docs/testing/TEST-REVIEW-2026-09-16.md).
- **v1.9 (sealed baseline):** the recoverable production baseline before V2.1 development. Its former `v27` label was a browser-cache revision, not a product version; see the [seal record](docs/releases/RELEASE-v1.9-FINAL-2026-09-15.md).

The complete historical release, testing, and audit materials are archived in [docs/releases](docs/releases/), [docs/testing](docs/testing/), and [docs/audits](docs/audits/). Earlier `v25`, `v35`, and `v36` labels identify phases or static-asset revisions; use each record’s product-version note as the source of truth.

A text-based travel expense tracker deployed with Docker on a ZSpace NAS and used from an Android browser. Speech-to-text is handled locally on the phone: the app accepts text only, does not access the microphone, and does not upload recordings. This is English project documentation; the application interface and text parsing are designed for Chinese.

## Optional DeepSeek semantic completion

The local keyword and rule parser remains the default and works offline. When DeepSeek is configured, only the transcribed text you submit is sent for completion of conversational category, location, item, and fuel full-tank state. Amount, liters, odometer, fuel grade, and timestamp remain subject to local parsing and save-time validation. AI-completed fields are shown for review; the model never writes an entry directly.

Create a permission-`600` `.env` file beside the Compose file on the NAS (never commit it):

```dotenv
DEEPSEEK_API_KEY=your_DeepSeek_API_key
DEEPSEEK_MODEL=deepseek-flash
```

Rebuild and start Compose afterwards. Keys and raw model responses are never written to SQLite, Excel exports, Git, or application logs. The transcribed original sentence continues to follow the existing ledger behavior: it is stored with the entry and can be exported or edited. Missing configuration, network failure, timeout, or invalid model JSON automatically falls back to local parsing.

## Features

- Start and finish trips. A full tank is assumed at departure, and the starting odometer reading is required.
- Enter expenses by category or parse text transcribed on your phone. Categories cover meals, clothing, accommodation, fuel, tolls, parking, attractions and entertainment, daily supplies, shopping and souvenirs, other transport, travel services, and vehicle expenses.
- The home screen shows total spending, vehicle cost, distance traveled, fuel consumption per 100 km, and cost per kilometer, with spending totals by category.
- Tolls are labeled “ETC.” Each expense includes a description of what was purchased or paid for, with category-specific examples.
- Every expense has an editable date and time and an optional location. Over HTTPS, you can explicitly request GPS positioning. The app estimates an approximate Chinese region offline and stores the region, coordinates, and accuracy. It does not track your location automatically or call a third-party map service.
- Once the app and current trip have been cached, you can add expenses by category offline. Records are stored in the phone browser and synchronized when the NAS becomes reachable. Stable record identifiers prevent duplicate submissions on retry.
- Edit or delete existing expenses in an active trip. Edits use the same validation as new entries; deletion requires confirmation, and successful changes update the statistics.
- The starting odometer is the vehicle dashboard’s cumulative mileage at departure, not zero for the trip. The trip title and starting odometer can be edited, with related statistics recalculated.
- Fuel entries support payment amount, fuel grade, liters, optional posted price per liter, and current odometer. Posted price, calculated fuel amount, and discount are stored separately. Full-tank status defaults to unconfirmed; fuel consumption is calculated only from reliable consecutive full-tank intervals.
- Export the active trip as a standard Excel file (`.xlsx`) with six worksheets: trip summary, expenses, odometer readings, daily itinerary, recycle bin, and revision history. Exporting all historical trips requires `?all=1`.
- Edit, delete, or export pending offline records as CSV. Original text and confirmation fields are saved as a draft and cleared only after the server confirms a successful save. For a single new, unsubmitted entry, you can edit the original sentence and parse it again, with confirmation to protect manual changes.
- Deleted expenses and independent odometer readings enter a recoverable 30-day recycle bin. Individual permanent deletion is available online after confirmation. It also removes that record’s revision history while retaining a minimal tombstone to prevent an old offline submission from restoring it.
- Missing-field guidance distinguishes required information, information affecting statistics, and suggested information, with explanations of the impact.
- Gasoline grades are limited to 95 and 98, with 95 as the default. Common numeric and Chinese transcription formats are supported.
- Fuel spending remains included in total spending and vehicle cost even when full-tank status is unknown or the tank was not filled. Consumption is not guessed when reliable boundaries are missing.
- Data is stored in SQLite, with `./data` mounted persistently in Docker.
- Entries with the same amount, category, and location within three minutes trigger a possible-duplicate warning; saving is still possible after confirmation.
- Responsive layouts use available viewport width, including a two-column layout for suitable unfolded screens. Draft and form state are preserved across layout changes.

## Deploying on ZSpace

1. Copy the `roadtrip-ledger` folder to a persistent directory on the NAS.
2. Import `compose.yaml` into a Docker Compose project on ZSpace.
   When DeepSeek is enabled, place the `.env` from the section above next to the Compose file; do not put a key in `compose.yaml`.
3. Compose binds port `18080` only to the NAS loopback address, `127.0.0.1`. Configure Tailscale Serve to proxy your own tailnet HTTPS address to `http://127.0.0.1:18080`, then access it from a phone enrolled in that tailnet.
4. Do not expose port `18080`, the database directory, or this application directly to the public internet. The app has no built-in login system: access control relies on tailnet membership and device security. Same-origin writes through Tailscale Serve HTTPS are supported; cross-site browser write requests are rejected.
5. Open the page in an Android browser and optionally add it to the home screen. Use the phone’s input method for local speech-to-text, then submit the resulting text to the app.

The port can be changed in `compose.yaml`, but keep it bound only to `127.0.0.1`. Back up `data/roadtrip.db` before upgrading.

If the NAS cannot pull `python:3.12-alpine` from Docker Hub but already has `nousresearch/hermes-agent:v2026.8.31`, use `compose.zspace-offline.yaml` instead. This reuses the image’s read-only base layers; it does not connect to or modify a running Hermes container.

## Mileage, fuel consumption, and vehicle cost

The starting tank is recorded as full. For an active trip, fuel entries and independent odometer readings determine the current mileage; a finished trip uses its ending odometer. Vehicle cost includes all valid fuel, ETC, and parking expenses in the trip, including fuel purchased at the starting odometer reading.

```text
Distance traveled = current (or ending) odometer − starting odometer
Vehicle cost = fuel spending + ETC + parking
Cost per kilometer = vehicle cost ÷ distance traveled
Fuel consumption (L/100 km) = liters across reliable full-tank intervals
                            ÷ distance across those intervals × 100
```

Vehicle cost and cost per kilometer update with valid records without requiring a final full tank. Cost per kilometer is unavailable when distance is zero or missing. Fuel consumption uses only reliable consecutive intervals with confirmed full tanks and complete liters and odometer readings. A partial fill, unknown full-tank status, or missing required field interrupts the interval; the next confirmed full tank establishes a new boundary. Without a reliable interval, consumption is reported as insufficient data rather than estimated from total fuel purchases.

## Local development and testing

```bash
python3 app.py
python3 -m unittest discover -s tests -v
# When Playwright is installed: node tests/browser_full_acceptance.cjs
```

`browser_full_acceptance.cjs` creates its own temporary SQLite database and a loopback-only `127.0.0.1` server. It covers the full record lifecycle, GPS mocks, and narrow/wide viewport and software-keyboard reachability without connecting to production or a phone.

Open `http://127.0.0.1:8080`.

## Limitations

- Refund and deposit workflows are not implemented. The app explicitly blocks them from being saved as ordinary expenses.
- Shared expense splitting, receipt recognition, GPS route tracking, precise street or business reverse geocoding, and user accounts are not included. Chinese region names are approximate offline estimates based on bundled administrative coordinates.
- Total spending includes all valid expenses in the trip. Vehicle cost includes fuel purchases, ETC, and parking; it is not a measurement of fuel actually consumed based on tank levels. Cost per kilometer divides vehicle cost by distance traveled.
- Fuel consumption requires reliable full-tank intervals with liters and odometer readings; missing information is surfaced to the user.
- The app can be added to the home screen. Offline forms are cleared only after the record is persisted in the phone browser, and synchronization uses stable identifiers. Recovery from browser storage deletion or device loss still depends on exported backups.
- Offline queue access across multiple tabs is serialized using the browser’s Web Locks API. Older browsers without this capability reject offline writes to prevent one tab from overwriting another’s records.

Real expense databases, exports, backups, credentials, and local environment files must not be committed to this repository.
