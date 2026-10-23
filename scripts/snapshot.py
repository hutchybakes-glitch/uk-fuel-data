# UK Fleet Fuel Watch - Fuel Finder snapshotter.
# Downloads every UK forecourt and its current prices from the statutory Fuel Finder API (OGL v3),
# writes the current station list and prices, and appends each price change to a monthly history file.
# Power BI reads these files from raw.githubusercontent.com, so it never needs the API credentials.
# Credentials: environment variables FUEL_FINDER_CLIENT_ID and FUEL_FINDER_CLIENT_SECRET (GitHub Actions secrets).
# Run locally:  python scripts/snapshot.py      (writes into the data/ folder of this repo)

# Import os to read the credentials from environment variables
import os
# Import sys to exit with a clear error code
import sys
# Import csv to write correctly quoted CSV files
import csv
# Import time to pause between calls and time the run
import time
# Import statistics to work out national median prices
import statistics
# Import date and time handling for UTC timestamps
from datetime import datetime, timezone, timedelta
# Import path handling for the output folders
from pathlib import Path
# Import requests to call the API
import requests

# The single fixed host for every Fuel Finder call
BASE = "https://www.fuel-finder.service.gov.uk"
# The repo's data folder, one level up from this script
DATA = Path(__file__).resolve().parent.parent / "data"
# The folder holding the monthly price-change history
HISTORY = DATA / "history"
# Make sure both folders exist
HISTORY.mkdir(parents=True, exist_ok=True)
# Rows per batch the API returns; a shorter batch is the last one
BATCH_SIZE = 500
# Safety cap on batches so a fault can never loop forever
MAX_BATCHES = 60
# Pause between calls: the API allows one request at a time and 100 a minute
PAUSE_SECONDS = 0.7
# A price more than this fraction away from the fuel's national median is flagged as an outlier
OUTLIER_FRACTION = 0.20
# A price not changed for more than this many days is flagged as stale (unchanged is not wrong, so this is generous)
STALE_DAYS = 60
# The fuel codes the report uses, in display order
FUELS = ["E10", "E5", "B7_STANDARD", "B7_PREMIUM", "B10", "HVO"]
# The station amenities kept because fleets care about them
AMENITIES = ["adblue_pumps", "adblue_packaged", "lpg_pumps", "twenty_four_hour_fuel", "car_wash", "customer_toilets"]

# Brand spellings seen in the feed mapped to one tidy name (matched on the start of the upper-case brand)
BRAND_MAP = [
    # Major oil company brands
    ("ESSO", "Esso"), ("BP", "BP"), ("SHELL", "Shell"), ("TEXACO", "Texaco"), ("JET", "Jet"), ("PHILLIPS 66", "Jet"),
    # More oil company and fuel retailer brands
    ("GULF", "Gulf"), ("VALERO", "Valero"), ("MURCO", "Murco"), ("ESSAR", "Essar"), ("TOTAL", "TotalEnergies"),
    # Regional and independent-network brands
    ("MAXOL", "Maxol"), ("APPLEGREEN", "Applegreen"), ("CIRCLE K", "Circle K"), ("HIGHLAND", "Highland Fuels"),
    # Forecourt operators and convenience brands
    ("EG ON THE MOVE", "EG On The Move"), ("PACE", "Pace"), ("SOLO", "Solo"), ("GO FORECOURT", "Go"), ("SPAR", "Spar"),
    # Motorway service operators
    ("WELCOME BREAK", "Welcome Break"), ("MOTO", "Moto"), ("ROADCHEF", "Roadchef"), ("EXTRA", "Extra"),
    # Supermarkets and warehouse clubs
    ("TESCO", "Tesco"), ("SAINSBURY", "Sainsbury's"), ("MORRISONS", "Morrisons"), ("ASDA", "Asda"), ("COSTCO", "Costco"),
    # Co-operative societies
    ("THE CO-OPERATIVE", "Co-op"), ("CO-OP", "Co-op"), ("EAST OF ENGLAND CO-OP", "Co-op"),
    # Harvest Energy in both spellings
    ("HARVEST", "Harvest Energy"),
]
# Brand values that really mean "no brand"
UNBRANDED = {"", "NONE", "INDEPENDENT", "INDEPENDANT", "UNBRANDED", "N/A", "NA"}


# Turn a raw brand into one tidy display name
def tidy_brand(raw):
    # Upper-case and trim the raw value so spellings compare equal
    key = (raw or "").strip().upper()
    # Empty and placeholder brands become Independent
    if key in UNBRANDED:
        # Return the shared label
        return "Independent"
    # The exact value "GO" is the Go brand (prefix matching would catch other words)
    if key == "GO":
        # Return the tidy name
        return "Go"
    # Check each known brand prefix in order
    for prefix, name in BRAND_MAP:
        # A match on the start of the brand wins
        if key.startswith(prefix):
            # Return the tidy name
            return name
    # Anything else keeps its own name in title case
    return key.title()


# Get an access token using the client-credentials flow
def get_token():
    # Read the client ID from the environment
    client_id = os.environ.get("FUEL_FINDER_CLIENT_ID", "").strip()
    # Read the client secret from the environment
    client_secret = os.environ.get("FUEL_FINDER_CLIENT_SECRET", "").strip()
    # Stop with a plain message if either is missing
    if not client_id or not client_secret:
        # Explain what is missing
        fail("Missing FUEL_FINDER_CLIENT_ID or FUEL_FINDER_CLIENT_SECRET.")
    # Send the credentials as JSON, as the API expects
    r = requests.post(BASE + "/api/v1/oauth/generate_access_token", json={
        # The OAuth grant type for machine-to-machine access
        "grant_type": "client_credentials",
        # The client ID
        "client_id": client_id,
        # The client secret
        "client_secret": client_secret,
        # The read-only scope
        "scope": "fuelfinder.read"}, timeout=60)
    # A 403 here usually means the request came from outside the UK
    if r.status_code == 403:
        # Stop with a message that names the likely cause
        fail("Token request refused (403). Fuel Finder may block non-UK IP addresses.")
    # Any other failure stops with its status code only (never the body)
    if r.status_code != 200:
        # Explain the failure
        fail(f"Token request failed with HTTP {r.status_code}.")
    # Hand back the token
    return r.json()["data"]["access_token"]


# Page through one endpoint and return every row plus the number of calls made
def page_all(path, token):
    # Send the token as a bearer header
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    # Collect every row here
    rows = []
    # Count the calls for the run log
    calls = 0
    # Try batch numbers from 1 upwards
    for batch in range(1, MAX_BATCHES + 1):
        # Try each batch up to three times for temporary server errors
        for attempt in range(3):
            # Request this batch
            r = requests.get(BASE + path, params={"batch-number": batch}, headers=headers, timeout=120)
            # Count the call
            calls += 1
            # Retry only rate-limit and server errors
            if r.status_code in (429, 500, 502, 503, 504):
                # Back off a little longer each time
                time.sleep(5 * (attempt + 1))
                # Try again
                continue
            # Anything else ends the retry loop
            break
        # A 404 means we have run past the last batch
        if r.status_code == 404:
            # Stop paging
            break
        # A 403 on data calls usually means the IP address is not in the UK
        if r.status_code == 403:
            # Stop with a message that names the likely cause
            fail(f"{path} refused (403). Fuel Finder may block non-UK IP addresses.")
        # Any other error stops the run
        if r.status_code != 200:
            # Explain the failure
            fail(f"{path} batch {batch} failed with HTTP {r.status_code}.")
        # Parse the body
        body = r.json()
        # Accept either a bare list or a list wrapped in a data key
        chunk = body.get("data", []) if isinstance(body, dict) else body
        # Add this batch's rows
        rows.extend(chunk)
        # An empty or short batch is the last one
        if len(chunk) < BATCH_SIZE:
            # Stop paging
            break
        # Pause politely before the next call
        time.sleep(PAUSE_SECONDS)
    # Hand back the rows and the call count
    return rows, calls


# Stop the run with a plain message, also shown as a GitHub annotation so it is visible without opening the log
def fail(message):
    # Print the message in the GitHub Actions annotation format
    print(f"::error::{message}")
    # Exit with an error code and the same message
    sys.exit(message)


# Read an ISO UTC timestamp from the API into a datetime (or None)
def parse_utc(text):
    # Missing values stay missing
    if not text:
        # Nothing to parse
        return None
    # Replace the trailing Z so Python reads it as UTC
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# Format a datetime as a compact UTC text value Power BI reads easily
def fmt_utc(dt):
    # Missing values become empty cells
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


# Write a list of dictionaries to a CSV file with fixed columns
def write_csv(path, columns, rows):
    # Open the file for writing with Windows-safe newlines
    with open(path, "w", newline="", encoding="utf-8") as f:
        # Create a writer that uses the column list as the header
        w = csv.DictWriter(f, fieldnames=columns)
        # Write the header row
        w.writeheader()
        # Write every data row
        w.writerows(rows)


# Note the run start time in UTC
run_utc = datetime.now(timezone.utc).replace(microsecond=0)
# Note the start time for the duration
started = time.perf_counter()
# Get a token
token = get_token()
# Download every station
stations, station_calls = page_all("/api/v1/pfs", token)
# Download every station's prices
prices, price_calls = page_all("/api/v1/pfs/fuel-prices", token)
# Stop if the feed looks broken, so a bad run never overwrites good files
if len(stations) < 7000 or len(prices) < 7000:
    # Explain why nothing was written
    fail(f"Too few rows (stations {len(stations)}, prices {len(prices)}); not writing.")

# Build the tidy station rows
station_rows = []
# Go through each station record
for s in stations:
    # The nested location block holds the address and coordinates
    loc = s.get("location") or {}
    # The station's amenity list (may be missing)
    amen = set(s.get("amenities") or [])
    # Build one output row
    station_rows.append({
        # The station's unique ID
        "node_id": s.get("node_id"),
        # The trading name in title case for readability
        "name": (s.get("trading_name") or "").strip().title(),
        # The tidy brand
        "brand": tidy_brand(s.get("brand_name")),
        # The first address line in title case
        "address": (loc.get("address_line_1") or "").strip().title(),
        # The town in title case
        "town": (loc.get("city") or "").strip().title(),
        # The postcode in upper case with single spaces
        "postcode": " ".join((loc.get("postcode") or "").upper().split()),
        # Latitude rounded to about 1 metre
        "lat": round(float(loc.get("latitude")), 5) if loc.get("latitude") is not None else "",
        # Longitude rounded to about 1 metre
        "lon": round(float(loc.get("longitude")), 5) if loc.get("longitude") is not None else "",
        # Whether the station is at a supermarket (1/0)
        "supermarket": int(bool(s.get("is_supermarket_service_station"))),
        # Whether the station is on a motorway (1/0)
        "motorway": int(bool(s.get("is_motorway_service_station"))),
        # Whether the station is temporarily closed (1/0)
        "temp_closed": int(bool(s.get("temporary_closure"))),
        # Whether the station is permanently closed (1/0)
        "perm_closed": int(bool(s.get("permanent_closure"))),
        # One 1/0 column per fleet-relevant amenity
        **{a: int(a in amen) for a in AMENITIES},
    })
# The file that gives every station a short permanent number (the API's 64-character IDs bloat every file)
ids_path = DATA / "station_ids.csv"
# Existing station numbers keyed by node ID
sid = {}
# Read the existing numbers if the file exists
if ids_path.exists():
    # Open it for reading
    with open(ids_path, newline="", encoding="utf-8") as f:
        # Store each saved number
        sid = {row["node_id"]: int(row["sid"]) for row in csv.DictReader(f)}
# The next free number
next_sid = max(sid.values(), default=0) + 1
# Give any new station the next free number (numbers are never reused)
for r in sorted(station_rows, key=lambda r: r["node_id"]):
    # Only stations without a number need one
    if r["node_id"] not in sid:
        # Assign the number
        sid[r["node_id"]] = next_sid
        # Move on to the next free number
        next_sid += 1
# Save the full list of numbers, including stations that have left the feed
write_csv(ids_path, ["sid", "node_id"], [{"sid": v, "node_id": k} for k, v in sorted(sid.items(), key=lambda kv: kv[1])])
# Put the short number first on every station row and drop the long ID (it lives in station_ids.csv)
station_rows = [{"sid": sid[r["node_id"]], **{k: v for k, v in r.items() if k != "node_id"}} for r in station_rows]
# Sort stations by number so unchanged files produce no git changes
station_rows.sort(key=lambda r: r["sid"])

# Flatten every station's price list into (station, fuel, price, changed, effective) rows
flat = []
# Go through each station's price record
for p in prices:
    # Go through each fuel price at that station
    for fp in p.get("fuel_prices") or []:
        # Read the price as a number, skipping blanks
        try:
            # Convert to a float
            price = float(fp.get("price"))
        # Skip values that are not numbers
        except (TypeError, ValueError):
            # Move to the next fuel
            continue
        # Keep the row with upper-case fuel codes
        flat.append({"node_id": p.get("node_id"), "fuel": (fp.get("fuel_type") or "").upper(), "price": round(price, 1),
                     # When the price was last updated
                     "updated": parse_utc(fp.get("price_last_updated")),
                     # When the current price took effect
                     "effective": parse_utc(fp.get("price_change_effective_timestamp"))})
# Keep only the fuels the report knows about
flat = [r for r in flat if r["fuel"] in FUELS]
# Remove duplicate station-fuel pairs, keeping the most recently updated one
latest = {}
# Go through each price row
for r in flat:
    # The key is station plus fuel
    k = (r["node_id"], r["fuel"])
    # Keep this row if it is the first or newer than the one held
    if k not in latest or (r["updated"] or run_utc) > (latest[k]["updated"] or run_utc):
        # Store it
        latest[k] = r
# Work out the national median price per fuel from plausible values (50-350p)
medians = {f: statistics.median([r["price"] for r in latest.values() if r["fuel"] == f and 50 <= r["price"] <= 350] or [0]) for f in FUELS}
# The cut-off time for a stale price
stale_before = run_utc - timedelta(days=STALE_DAYS)
# Build the tidy current-price rows with a quality flag
price_rows = []
# Go through each latest price
for (node, fuel), r in sorted(latest.items()):
    # The fuel's national median
    med = medians[fuel]
    # Flag prices far from the median as outliers
    if med and abs(r["price"] / med - 1) > OUTLIER_FRACTION:
        # Mark as an outlier
        flag = "outlier"
    # Flag prices not changed for a long time as stale
    elif r["updated"] and r["updated"] < stale_before:
        # Mark as stale
        flag = "stale"
    # Everything else is fine
    else:
        # Mark as OK
        flag = "ok"
    # Add the output row
    price_rows.append({"sid": sid.get(node, ""), "fuel": fuel, "price": r["price"], "updated_utc": fmt_utc(r["updated"]),
                       # The effective time and the flag
                       "effective_utc": fmt_utc(r["effective"]), "flag": flag})

# Write the current station list (replaced every run)
write_csv(DATA / "stations.csv", list(station_rows[0].keys()), station_rows)
# Write the current prices (replaced every run)
write_csv(DATA / "prices_latest.csv", ["sid", "fuel", "price", "updated_utc", "effective_utc", "flag"], price_rows)

# Append each price change to the history file for the month it took effect
by_month = {}
# Group current prices by the month their effective time falls in
for r in price_rows:
    # Use the effective time, else the updated time, else the run time
    when = r["effective_utc"] or r["updated_utc"] or fmt_utc(run_utc)
    # The month key, e.g. 2026-10
    by_month.setdefault(when[:7], []).append({"sid": r["sid"], "fuel": r["fuel"], "price": r["price"], "effective_utc": when})
# Merge each month's changes into its file without duplicates
for month, rows in by_month.items():
    # The history file for this month
    path = HISTORY / f"prices_{month}.csv"
    # Existing rows keyed by station, fuel and effective time
    existing = {}
    # Read the existing file if there is one
    if path.exists():
        # Open it for reading
        with open(path, newline="", encoding="utf-8") as f:
            # Go through each saved row
            for row in csv.DictReader(f):
                # Store it under its key
                existing[(str(row["sid"]), row["fuel"], row["effective_utc"])] = row
    # Add the new rows (same key means same change, so it is overwritten harmlessly)
    for row in rows:
        # Store it under its key
        existing[(str(row["sid"]), row["fuel"], row["effective_utc"])] = row
    # Write the merged file sorted for stable diffs
    write_csv(path, ["sid", "fuel", "price", "effective_utc"], sorted(existing.values(), key=lambda x: (x["effective_utc"], int(x["sid"]), x["fuel"])))

# Append one line to the run log with counts and national medians
log = DATA / "snapshots.csv"
# The run log columns
log_cols = ["run_utc", "stations", "prices", "calls", "seconds"] + [f"median_{f}" for f in FUELS] + [f"count_{f}" for f in FUELS] + ["flagged"]
# Write the header first if the log is new
new_log = not log.exists()
# Open the log for appending
with open(log, "a", newline="", encoding="utf-8") as f:
    # Create a writer for the log columns
    w = csv.DictWriter(f, fieldnames=log_cols)
    # Write the header for a new file
    if new_log:
        # Header row
        w.writeheader()
    # Write this run's summary row
    w.writerow({"run_utc": fmt_utc(run_utc), "stations": len(station_rows), "prices": len(price_rows), "calls": station_calls + price_calls + 1,
                # Duration of the run in seconds
                "seconds": round(time.perf_counter() - started, 1),
                # National median per fuel
                **{f"median_{f}": medians[f] for f in FUELS},
                # Number of stations with each fuel
                **{f"count_{f}": sum(1 for r in price_rows if r["fuel"] == f) for f in FUELS},
                # Number of prices flagged as outlier or stale
                "flagged": sum(1 for r in price_rows if r["flag"] != "ok")})

# Print a short summary for the Actions log
print(f"OK {fmt_utc(run_utc)}: {len(station_rows)} stations, {len(price_rows)} prices, {station_calls + price_calls + 1} calls, medians {medians}")
