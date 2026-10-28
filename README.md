# UK fuel price data

Snapshots of UK forecourt fuel prices from the statutory **Fuel Finder** scheme, collected six times a day and committed automatically (that's what the frequent "Snapshot" commits are). They feed the [UK Fuel Watch](https://github.com/swhutchinson/uk-fuel-watch) Power BI report.

Fuel Finder refuses requests from GitHub's cloud servers (non-UK addresses), so the snapshot runs as a scheduled task on a UK Windows PC (`scripts/run_snapshot.ps1`); the GitHub Actions workflow is kept for manual re-tests.

| File | What it holds |
|---|---|
| `data/stations.csv` | Every forecourt now in the feed: short station number (`sid`), name, brand (tidied), address, postcode, latitude/longitude, supermarket/motorway flags, closures, and fleet amenities (AdBlue pumps, LPG, 24-hour fuel) |
| `data/prices_latest.csv` | The current price per station and fuel (`E10`, `E5`, `B7_STANDARD`, `B7_PREMIUM`, `B10`, `HVO`) in pence per litre, with a quality flag: `ok`, `outlier` (more than 20% from the national median) or `stale` (unchanged for 60+ days) |
| `data/history/prices_YYYY-MM.csv` | Every price change seen, by the month it took effect |
| `data/snapshots.csv` | One line per run: counts, national median per fuel, flagged prices |
| `data/station_ids.csv` | Maps the short `sid` to Fuel Finder's `node_id` (numbers are never reused) |

**Licence:** Contains Fuel Finder data from the Department for Energy Security and Net Zero, licensed under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). Prices are reported by retailers and not checked by this project. Spotted a wrong price? Report it to Fuel Finder: https://www.fuel-finder.service.gov.uk/motorist/price-report
