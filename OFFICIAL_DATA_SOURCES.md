# FloodSense official alert and river-level sources

Research checked 11 October 2026. FloodSense treats these as source evidence only; this integration does not assign a flood-risk category.

## NDMA SACHET CAP alert feed

- **Authority and coverage:** National Disaster Management Authority (NDMA), Government of India. SACHET describes a pan-India Common Alert Protocol (CAP) system and says alerts are geo-targeted and published to an India RSS feed.
- **Access:** Public feed: <https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml>. The NDMA portal links to its RSS page and user guide. The World Meteorological Organization also lists this exact URL as India's NDMA CAP feed. No account or API credential is required by the published RSS link.
- **Types and timing:** Multi-hazard public warnings; CAP feed is updated as alerts are issued. An alert's own effective and expiry timestamps are retained when present. This is not a flood-only feed, so the source's event, severity, and wording are shown without reclassifying it as a flood warning.
- **Geographic match:** Only a CAP polygon or circle containing the selected WGS84 coordinate is treated as a match. CAP circles use the standard's radius-in-kilometres convention. District/area text without machine-readable geometry is not spatially guessed. Only CAP `Actual` messages of type `Alert` with a valid period count as active; cancellations and unresolved updates are not promoted to active warnings. A feed response with no matched record is not an all-clear.
- **Historical data:** The reviewed RSS page/feed describes dissemination; it does not document a complete historical archive or retention period. FloodSense does not claim historical completeness.
- **Integration notes:** Feed failures, invalid XML, or missing validity times remain unavailable/unknown, never “no alert.” The fetcher sends conditional `If-None-Match` when an ETag is supplied and reuses its in-process cached feed on HTTP 304. The NDMA integration guide recommends ETag caching for its CAP XML endpoint.

## CWC river telemetry published by NWIC

- **Authority and coverage:** Central Water Commission (CWC), Ministry of Jal Shakti, via the National Water Informatics Centre's National Water Data Portal (NWDP). CWC describes its national hydrological observation network; the implemented resource is specifically the **Cauvery basin CWC telemetry hourly 2026–2030 CSV**, not the full national station network.
- **Access:** Public machine-readable CSV with an “Other (Open)” license on the NWDP resource page. The portal describes open datasets and developer APIs. The resource download is used directly and requires no credential in the published download URL.
- **Fields and convention:** The resource schema publishes station, agency, state/district, river/basin, latitude/longitude, `Data Acquisition Time`, and `River Water Level Telemetry Hourly (meter)`. Thus levels are displayed in metres and station position comes from the dataset itself. The reviewed schema does not specify the timezone for its sample `DD-MM-YYYY HH:MM` observation time; FloodSense preserves that timestamp verbatim, reports exact age as unknown, and only marks it stale if it is older than the freshness window even under a conservative civil-time offset bound. Records carrying an explicit UTC offset use that offset for age.
- **Frequency and history:** The resource is labeled hourly and its NWDP metadata showed a last-update date of 8 October 2026 at research time. NWDP lists separate historical resources (including 2021–2025 and longer historical periods), but the app currently connects only the current Cauvery telemetry resource. In the live check on 11 October, the nearest available station rows were dated 30 May–3 June 2026 and were correctly marked stale; the portal's resource update date does not guarantee every station has a current observation.
- **Geographic match:** CWC station coordinates are source-provided. FloodSense orders available station records by straight-line distance and displays the nearest candidates and exact distance; this is only a proximity ranking. It does not assert the selected point is in the Cauvery basin or hydrologically connected upstream/downstream. No arbitrary radius or danger threshold is used. Candidate readings are context-only and are not treated as locally verified assessment inputs.

## Existing provider field and freshness contracts

- **Open-Meteo rainfall:** Query uses the requested latitude/longitude, `current=precipitation,rain`, `hourly=precipitation,rain`, `past_days=1`, `forecast_days=2`, `timezone=UTC`. Output preserves the provider-resolved weather-grid latitude/longitude; `current` contains `time_utc`, `precipitation_mm`, `rain_mm`; each hourly record contains the same time plus precipitation and rain values, in mm. Retrieval time is app fetch time. The risk feature layer uses `rain_mm` only, separates rows at or before provider `current.time_utc` as observations and later rows as forecast, rejects unsupported units, reports 1h/3h/24h completeness, and marks an observation older than three hours stale. This short weather history is not long-term flood-event history. Provider documentation: [Open-Meteo API](https://open-meteo.com/en/docs).
- **Open-Elevation:** One requested coordinate is looked up; output has provider-resolved latitude/longitude, `elevation_m`, units in metres, and app retrieval time in UTC. The provider response does not provide a terrain observation timestamp in this integration, so retrieval time must not be interpreted as measurement vintage. Point elevation is terrain context only; it does not encode drainage paths or flood depth.
- **NDMA SACHET:** RSS/CAP records retain source alert wording, sender, event, severity when published, CAP status, identifier, geometry/area description, effective and expiry times, instructions, and source link. Feed retrieval time is distinct from alert validity. Only documented machine-readable geometry matched against the selected WGS84 point is treated as a spatial match; text-only place names are not guessed.
- **CWC/NWIC live telemetry:** The current Cauvery CSV fields include station, agency, state/district, river/basin, source latitude/longitude, acquisition timestamp, and river water level in metres. App retrieval time, source observation time, and freshness are separate. Naive source times are preserved raw; absent verified timezone means exact age is unknown unless conservatively definitely stale. The CSV's resource-level update date does not guarantee each station row is current. Station values are candidate gauge-stage observations and are not directly comparable across stations without datum/metadata.
- **Provider failures:** Each source may return unavailable/error independently; `/monitor` and `/assess-risk` keep partial results. Missing values are not converted to zero, and no provider's absence is treated as a flood all-clear.

## Investigated, not integrated

- **CWC FloodWatch India / flood forecast portal:** CWC's public materials describe the FloodWatch India app and a flood-forecast website, but the reviewed official materials did not provide a documented public machine-readable endpoint suitable for this application. FloodSense does not scrape the app or invent an API. CWC materials describe broad station coverage and forecasts; these remain available through CWC's official channels.
- **IMD district warning API:** IMD documents the district-wise warning endpoint and the district identifiers, issue times, warning codes, colors, and five-day fields. Access is through the IMD API Management portal, which provides account registration; no project credential was supplied. It is weather-warning data, not by itself a CWC flood alert, so it is not integrated in this phase.

## Phase 4: hydrological geography and historical validation sources

Research checked 11 October 2026. The sources below are documented and suitable for a future ingestion, but this release does not download or spatially process them. Availability in a catalog is not evidence that a selected point or gauge is connected.

- **Basin, sub-basin, and watershed boundaries:** NWIC's [Hydrological Boundaries catalog](https://data.gov.in/catalog/hydrological-boundaries) provides India-wide shapefiles and names the Ministry of Jal Shakti/NWIC as contributor. It is cataloged under NDSAP; the portal describes Government Open Data License India. A basin assignment must come from spatially joining the selected point and station coordinates to the same versioned geometry. Boundary membership alone still does not establish an upstream/downstream path.
- **River network geometry:** NWIC's [River Network dataset](https://nwdp.nwic.gov.in/dataset/river-line) lists KML, GeoJSON, and SHP resources and names CWC as producer. Its description is a linear representation of rivers. It offers geometry, but the retrieved catalog does not establish a complete routable flow-direction/topology contract for verifying gauge representativeness; therefore no network connection is asserted here.
- **CWC gauge metadata:** CWC's [Hydro-Meteorological Observation Stations Book 2025](https://cwc.gov.in/sites/default/files/ho-metadata-book-2025final-publishedsigned.pdf) describes its station inventory and site parameters. The live NWDP Cauvery CSV itself supplies candidate station coordinates. Neither the inventory nor distance establishes a selected-point connection.
- **Historical CWC river levels:** NWDP publishes downloadable Cauvery hourly telemetry resources for [1961–1990](https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc/resource/), [1991–2020](https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc/resource/), and [2021–2025](https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc/resource/b5776a31-0583-4c27-a2d9-ee81e3b3a03a). The 2021–2025 CSV was acquired and quality-reviewed in Phase 5 (see its audit below); the earlier resources have not been ingested. Timestamp conventions and unit metadata must be checked against each downloaded resource before normalization.
- **Historical flood affected areas:** CWC's [Assessment of Area Affected due to Floods in India, Part II](https://cwc.gov.in/sites/default/files/final-faa-website.pdf) presents satellite-derived *aggregated* affected extents for 1986–2022 at sub-district level, not time-stamped local event labels. The [CWC 2023 flood statistical report](https://cwc.gov.in/sites/default/files/flood-management-india-statistical-report-2023.pdf) includes annual damage aggregates. These can support contextual screening, but cannot be treated as an event-by-event binary training/validation truth set for a point forecast.
- **River basin atlas:** NRSC describes the CWC/ISRO joint [River Basin Atlas of India](https://www.nrsc.gov.in/nrscnew/resources_atlas.php?lang_code=en) and links basin-specific atlases, including Cauvery. These are useful authoritative basin context; this app has not ingested their spatial data.

### Historical preparation and evaluation

`backend/hydrological_validation.py` provides canonical record validation, duplicate detection, timezone-preserving normalization (unknown timezone remains unknown), explicit unit checks, gap reporting, event-level confusion metrics, false-alarm rate, coverage/missingness, event lead time, and chronological event-group splits. `backend/prepare_historical.py` is the CSV ingestion entry point. It requires the operator to provide a downloaded, versioned source and explicit column mappings; it does not fetch or invent data. Excluded rows and quality issues are reported. Forecast and observation record types remain distinct. The Phase 5 acquisition and audit below are the first real historical ingestion; no real-world predictive validation result is claimed.

### Phase 5: acquired history and backtest readiness

The [NWDP CWC Cauvery 2021–2025 resource](https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc/resource/b5776a31-0583-4c27-a2d9-ee81e3b3a03a) was downloaded and audited. Its source CSV and generated normalized dataset are stored under ignored `backend/data/` so the large source files and derived records do not enter source control. The normalized report is `backend/data/processed/cwc_cauvery_telemetry_2021_2025.prepared_report.json`; it records retrieval time, source URL, byte size, SHA-256, field mapping, station inventory, exclusions, missingness, and temporal gaps.

- Source file: `rwl_tel_hr_cwc_009_2021_2025.csv` (22,215,609 bytes; SHA-256 `d248a03e8fcde52bead5bb0e1a0c656e71180a083580fb6658208d3230495469`). The catalog lists CSV and “Other (Open)” and says the resource was updated 24 January 2026.
- The file has 131,610 rows and 23 fields. Eight usable Cauvery stations are present: AKKIHEBBAL, BHAVANI BRIDGE, BILIGUNDULU, Bhavani Sagar Dam, Harangi Reservoir, Hemavathy Reservoir, K.M. VADI, and Kabini Reservoir. Valid observation timestamps span 1 January 2021 10:30 through 30 December 2025 05:00 as written in the source.
- 130,993 records were prepared; 617 rows were excluded (616 literal `NaN` measurements and one incomplete trailing record). There are no duplicate station/timestamp keys. The audit found 11,943 gaps relative to an hourly cadence. Source timestamps have no stated timezone, so their original text is retained and no UTC conversion is claimed. The source declares level in metres; values are not shifted between gauge datum and mean sea level.
- One malformed trailing row causes a `Ca`/`Cauvery` river metadata conflict for K.M. VADI; it is excluded from the observations. Missing ancillary fields and per-station coverage are included in the generated report.

Official event-label review found one suitable station-threshold event in the [CWC Appraisal Report 2024](https://cwc.gov.in/sites/default/files/appraisal-report-2024-publicationcompressed.pdf), Annex XIII, page 125: Musiri was reported above its 82.12 m warning level from 31 July 2024 19:00 through 7 August 2024 02:00, and above its 83.12 m danger level from 1 August 2024 08:00 through 3 August 2024 23:00 (report time basis; timezone not stated). This is a gauge-threshold interval, not an inundation or loss label. Musiri is absent from the acquired eight-station dataset, and the source report does not specify timezone, so exact station/time linkage fails. No other periods are treated as negative labels. Backtesting is therefore unavailable; metrics are null and readiness remains `not_assessed`.

### Phase 6: event-to-station linkage audit

`backend/event_linkage_audit.py` creates a deterministic pairwise report from the Phase 5 station inventory, event candidates, and the raw telemetry CSV. Run it from `backend` with `python event_linkage_audit.py`; its JSON output is written to ignored `backend/data/processed/cwc_cauvery_event_linkage_audit.json`. All candidate pairs remain in the report with geographic, temporal, hydrological, and label evidence plus explicit reasons. Unit tests use fixtures/mocks and do not contact external services.

The [CWC Hydro-Meteorological Observation Stations Book 2025](https://cwc.gov.in/sites/default/files/ho-metadata-book-2025final-publishedsigned.pdf) identifies Musiri as CWC site `CW1CAL000207` at 10.943°N, 78.435°E on the Cauvery, with district Tiruchirapalli. Comparing that official station location to the coordinates in the CWC/NWDP telemetry CSV gives a nearest station of BHAVANI BRIDGE at 99.00 km straight-line distance. The seven other distances are also recorded in the pair report. The CWC [River Network catalog](https://nwdp.nwic.gov.in/dataset/river-line) describes a linear river representation; its catalog metadata does not establish flow direction and a connected path for these station pairs. Therefore the CWC basin/river metadata is classified as **same basin, connection unverified**, while distances remain **proximity only**. No telemetry station has the Musiri site code or exact Musiri name.

The report-period calendar-clock scan finds readings during the Musiri warning interval at AKKIHEBBAL (146), BHAVANI BRIDGE (94), BILIGUNDULU (118), Harangi Reservoir (7), Hemavathy Reservoir (16), and Kabini Reservoir (150). It finds none at Bhavani Sagar Dam or K.M. VADI. These counts are not exact temporal matches: the NWDP timestamps and CWC report interval both lack a verified common timezone. The nominally overlapping readings are unlabeled at those other gauges because station thresholds and hydrological representativeness cannot be transferred from Musiri. K.M. VADI also retains a source metadata conflict (`Ca` versus `Cauvery`).

The event itself qualifies as a source-documented interval above Musiri's 2024 warning threshold (82.12 m); the source also lists its danger threshold (83.12 m). This label applies only to the named Musiri station and that published period. It does not provide negative labels outside the period, point inundation, or labels for the eight other telemetry stations. The linkage audit records one event, eight stations, eight pairs, zero verified links, six unresolved pairs, and two excluded pairs for no records in the reported calendar interval. It leaves predictive evaluation `not_run` and blocked, with metrics null; risk remains `not_assessed`.

Example for a downloaded historical gauge CSV after checking its exact header names and unit/time metadata:

```powershell
python prepare_historical.py .\data\cauvery-history.csv --kind gauge --source-name "CWC/NWDP Cauvery telemetry" --source-url "https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc" --period "2021-2025 resource; downloaded YYYY-MM-DD" --id-column Station --timestamp-column "Data Acquisition Time" --value-column "River Water Level Telemetry Hourly (meter)" --declared-unit m --expected-unit m --output .\data\prepared-cauvery.json
```

Use `--declared-unit` only after verifying the resource's unit metadata; when the CSV supplies a unit column, map that column with `--unit-column` instead. Match field names against the downloaded resource because historical resource headers may differ from the current telemetry CSV.

Use independent flood events as evaluation units. Assign whole events and all overlapping windows to one chronological split only. The false-alarm-rate denominator is the set of negative evaluation units (FP + TN); do not report hourly false-alarm counts as independent flood events. Synthetic labels in unit tests validate code paths only and are never bundled as production data.

`POST /validation-readiness` takes the same `{ "latitude": ..., "longitude": ... }` body as `/monitor`. It reports live provider availability, candidate gauge freshness/relevance, documented historical sources, runtime integration status, missing evidence, and why classification remains blocked. The detailed local-history and event/station audit is generated separately by `backend/event_linkage_audit.py`; a local historical download is not treated as connected live-service evidence. `/assess-risk` includes the same readiness summary; risk continues to be `not_assessed`.

## API contract changes

`POST /monitor` and `POST /assess-risk` keep the existing coordinate request and rainfall/elevation fields and add:

- `official_alerts`: provider `status`, source and retrieval time, location match state, matched alert wording/severity/validity, and an explicit non-all-clear message when no feed record matches. Provider failure includes an `error` and means alert state is unknown.
- `river_gauges`: provider `status`, source and retrieval time, coverage, up to five closest CWC station candidates, source coordinates, measured value/unit/time, distance, freshness where verifiable, and the explicit unverified-connectivity reason. Provider failure never becomes a zero reading.

The overall monitoring `data_status` now reflects all four providers. `assessment.status` remains `not_assessed` and `risk_category` remains `null` until a locally valid model, thresholds, and verified hydrological evidence are available.

## Phase 7: Musiri-specific monitoring

`GET /stations/musiri/status` reads the exact `MUSIRI` row from the current NWDP/CWC Cauvery hourly telemetry CSV. It does not substitute a nearby station. The published feed row matches Tamil Nadu, Tiruchirapalli district, Cauvery river/basin, and is located at 10.93805556, 78.44027778. The CWC 2025 station inventory lists Musiri site code `CW1CAL000207` at approximately 10.943, 78.435. The feed itself does not publish that code, and its coordinates differ by about 0.8 km; an exact instrument crosswalk is therefore unverified.

The CSV reports level in metres and includes `RL_of_zeroGauge=78.0000` and `MeanSeaLevel=92.00` for Musiri. These fields do not establish compatibility with the datum of the threshold source. CWC's general stage documentation describes levels against mean sea level, but that general convention does not prove this feed row's threshold datum. The telemetry timestamp has no stated timezone. Freshness is conservatively unknown until a timestamp can be interpreted; an old sample is still shown as a historical observation, never as current.

The CWC 2024 appraisal report documents Musiri warning 82.12 m and danger 83.12 m, with peak 84.10 m in the reported 2024 event. CWC's 2023 appraisal also documents the warning value, but the sources do not establish validity for 2026 or a verified telemetry station/datum crosswalk. Accordingly, 82.12 m is reference-only and the threshold service always returns `threshold_not_verified` for this live feed. When evaluating a future fully verified configuration, equality is treated as not exceeding; this strict rule follows the source's description as a level “above” warning threshold. A below-threshold sample is never presented as “safe” or as an all-clear.

The preserved 2026–2030 resource snapshot was retrieved 2026-10-11 05:25:32 UTC, size 3,760,289 bytes, SHA-256 `ba99c4befd75cdb64b5303178855ce6748bb31b9cf355075c327cb214adf16f5`. It contains 1,934 timestamped MUSIRI rows from 2026-01-01 05:00 through 2026-06-03 23:00, including 30 missing level values. The latest sample is several months old relative to retrieval, so it is stale. Observed numeric values include an extreme high outlier; no unverified plausibility cutoff was invented. `backend/replay_musiri_history.py` reproduces the audit in the ignored local output `backend/data/processed/musiri_2026_telemetry_replay.json`. Since threshold validity is unresolved, replay reports time coverage and gaps but returns null crossings and returns-below; it does not manufacture alert events. Naive timestamps are used only to calculate relative ordering and gap estimates, not labeled UTC.

The threshold result is separate from `/assess-risk`; overall flood risk stays `not_assessed`. The dashboard calls this an informational gauge record, not a forecast or public warning. The NWDP catalog and original [CWC telemetry resource](https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc), [CWC Hydro-Meteorological Observation Stations Book 2025](https://cwc.gov.in/sites/default/files/ho-metadata-book-2025final-publishedsigned.pdf), [CWC Appraisal Report 2024](https://cwc.gov.in/sites/default/files/appraisal-report-2024-publicationcompressed.pdf), and [CWC stage-discharge manual](https://cwc.gov.in/sites/default/files/stage-dischargecompressed.pdf) provide the source records and limitations summarized above.

Run the replay from `backend` with `python replay_musiri_history.py` after placing the published CSV and its provenance manifest in `backend/data/raw/`.

## Phase 8: operational reliability and data integrity

`GET /health/providers` reports process-local, **request-driven** health for Open-Meteo rainfall, Open-Elevation, NDMA SACHET, CWC Cauvery candidate gauges, and exact Musiri CWC telemetry. It does not poll providers or promise continuous monitoring; an unqueried provider is `unknown`. Status definitions: `healthy` = latest request succeeded with fresh applicable observations or source freshness not applicable; `degraded` = request succeeded but data quality/freshness is incomplete or unknown; `stale` = request succeeded but returned measurements exceed the application freshness policy; `unavailable` = latest request failed; `unknown` = no request recorded since this API process started. Observation freshness is reported separately from reachability. Error details are reduced to categories such as `timeout`, `http_error`, or `malformed_response`. A recovery flag turns true after a successful request follows a failure. State is in-memory and resets on process restart.

Health writes are ordered by request start time so an older slow request cannot overwrite a newer attempt's health outcome. CWC station selection now retains the latest timestamp even if its level is missing/invalid; it does not fall back to an older numeric level. Conflicting equal-time latest rows are excluded from level conclusions. The NDMA ETag cache also protects against older in-flight responses replacing a newer cache snapshot; a syntactically valid but empty alert feed is reported unavailable rather than as no matching active alert.

Freshness rules are application policies, not source-issued alert limits: Open-Meteo rainfall uses the pre-existing three-hour maximum age in `backend/risk_assessment.py`; CWC gauges and Musiri use the existing three-hour gauge window. The CWC CSV timestamp has no documented timezone; its provider marks a reading stale only when it is older than three hours even after allowing a conservative 14-hour civil-time offset. A timestamp with an explicit UTC offset uses that offset. Rainfall requests explicitly ask Open-Meteo for UTC, and its naive ISO times are interpreted under that request contract. Open-Elevation supplies no observation timestamp in this integration; its retrieval time records the lookup, not the terrain model vintage. SACHET freshness is determined from CAP effective/expiry/status fields, not a guessed maximum feed age. Provider reachability can therefore be healthy while observation freshness is stale.

Rainfall and elevation now validate response shape, finite numeric values, WGS84 coordinates, timestamps where available, and expected measurement units. Zero remains a valid measurement; absent, non-finite, negative rainfall, and malformed values remain missing/invalid rather than becoming zero. Partial Open-Meteo arrays produce a partial quality flag. Existing `/monitor` and `/assess-risk` partial-success behavior is preserved; failed SACHET retrieval remains unknown and is never represented as no active alert.

`GET /health` retains its existing response. `GET /health/providers` returns `{ "collection_mode": "request_driven", "providers": [...] }`; each provider object includes name/source, status and reason, last attempt/result, last successful retrieval, observation freshness, safe error category, and recovery state.

`backend/musiri_station_verification.py` regenerates the ignored report `backend/data/processed/musiri_station_verification.json` from the preserved CWC resource and manifest. It classifies the station name and river as matching, the exact site-code crosswalk and threshold applicability/datum as unknown, and the approximately 0.8 km coordinate difference as conflicting. The district spelling differs between the source rows and is left unknown rather than normalized silently. Both sources label levels in metres, but that does not establish a common vertical datum. The report preserves raw coordinates and citations and leaves 82.12 m reference-only.

To generate the report, run `python musiri_station_verification.py` from `backend`. Start the API there with `\.venv\Scripts\python.exe -m uvicorn main:app --reload`; start the dashboard from `frontend` with `npm run dev`.

## References

- [NDMA SACHET portal](https://sachet.ndma.gov.in/)
- [NDMA CAP XML Feed Integration Guide](https://sachet.ndma.gov.in/docs/Integration_Guide_For_Agencies.pdf)
- [WMO Register entry for India's NDMA CAP feed](https://alertingauthority.wmo.int/authorities.php?recId=331)
- [OASIS CAP 1.2 specification](https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html)
- [NWDP CWC hourly telemetry dataset](https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc)
- [NWDP CWC Cauvery telemetry 2026–2030 resource](https://nwdp.nwic.gov.in/en/dataset/river-water-level-telemetry-hourly-central-water-commission-cwc/resource/d027c5ac-379d-4ac2-8ced-97b02b6edbc0)
- [CWC hydrological observation network](https://cwc.gov.in/hydro-meteorological-observation)
- [IMD API reference](https://api.imd.gov.in/public/api_reference.html)
- [IMD API account registration](https://api.imd.gov.in/public/register.php)
- [PIB: FloodWatch India 2.0](https://www.pib.gov.in/Pressreleaseshare.aspx?PRID=2044887&lang=2&reg=48)
- [PIB: CWC seven-day advisory dissemination](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2201535&lang=1&reg=3)

## Run FloodSense

Start the API from the `backend` directory:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Start the web dashboard from `frontend` in a second terminal:

```powershell
npm run dev
```

To inspect readiness through the API docs, open `http://localhost:8000/docs` and call `POST /validation-readiness` with the selected coordinates.
# Phase 9: Cauvery-wide station registry and monitoring

The station registry is generated in memory from the preserved CWC/NWIC telemetry files under `backend/data/raw/`. It currently contains 27 distinct source station names: 8 in `cwc_cauvery_telemetry_2021_2025.csv` (coverage spans 2021-01-01 through 2025-12-30), and 24 in `cwc_cauvery_telemetry_2026_2030.csv`; 5 names occur in both files. The three names present only in the preserved historical file are Bhavani Sagar Dam, BILIGUNDULU, and Hemavathy Reservoir. Coverage and the last row date are per station and per file; the registry does not imply that a station is currently reporting. The UI separately labels saved-file presence and the selected station's live request result.

Station IDs such as `musiri` and `upper-anicut` are normalized local route keys derived from the exact CWC source name. The telemetry does not publish a stable CWC site code in its station rows, so `station_id_verification_status` and the source-name-to-instrument crosswalk remain `unknown`. The name and individual source fields are preserved without silently resolving conflicts. Field statuses use `verified`, `conflicting`, `unknown`, and `not_available`; `metadata_verification_status` summarizes the station metadata, not provider availability.

Registry metadata includes the source name and source URLs, coordinates and their verification state, CWC river/basin labels, units, datum limitation, per-file observation coverage, gap/invalid/missing-value counts, threshold provenance and applicability, relationship status, conflicts, and unresolved fields. The source header documents the level unit as metres; the time zone and a compatible vertical datum are not verified. K.M. VADI has conflicting historical river strings (`Ca` and `Cauvery`). Musiri's telemetry location differs from the referenced CWC inventory location, and the site-code crosswalk remains unverified. The 82.12 m Musiri level is retained as a 2024 historical reference only; current applicability is unknown. No threshold is available for other stations.

All 27 rows carry the CWC basin label `Cauvery` in the preserved data. That supports a same-basin label, but the available sources do not verify a connected river-network path or flow order for any pair. The UI does not infer upstream/downstream order from distance. Musiri map coordinates use the telemetry location and are marked conflicting with inventory metadata. The 2024 event-linkage audit still reports zero verified links; no station observation is represented as evidence for a different location.

## Station APIs

- `GET /stations` returns every station in the preserved datasets with per-file coverage and current-process station-provider health.
- `GET /stations/{station_id}` returns one registry entry; an unknown local ID returns 404.
- `GET /stations/{station_id}/observations?limit=24` fetches the exact source station name from the current CWC resource, validates the name against the selected registry entry, and returns observation/retrieval time, raw and numeric value, units, datum fields, quality, freshness, source, metadata state, threshold state, relationship state, and station-specific health. `limit` is 1–200.
- When a station is absent from the current source but is present in the preserved 2021–2025 file, its records are returned as `availability: historical_only` and labeled `historical_snapshot`; they are not described as current. A missing/invalid latest level is preserved and is not replaced by an older numeric reading. The API never substitutes another station.
- `/stations/musiri/status` remains available for existing clients and continues its guarded station-specific threshold response. The general risk assessment remains `not_assessed` unless independently verified evidence and validated local rules support it.
- `/health/providers` retains its existing provider list and adds `station_providers`, one entry per station actually queried. Health remains request-driven and in-memory; it resets on process restart and is not a continuous monitor.

The Cauvery dashboard now has a selectable station map/list and a detail view. It separates provider health, metadata verification, observation freshness, hydrological relationship, and threshold applicability. It identifies old readings as stale/historical and shows a source-provided missing value without painting it as current. Map markers show published coordinates only and do not draw river paths or claim flow relationships. Existing rainfall, alerts, Musiri panel, readiness, general provider health, and assistant remain in place.

To run the added tests and frontend checks from the repository root:

```powershell
cd backend
.\.venv\Scripts\python.exe -B -m unittest discover -v
cd ..\frontend
npm run lint
npm run build
```


# Phase 10: Cauvery river-network intelligence

The ignored raw NWDP/CWC resources are preserved under `backend/data/raw/` as `nwdp_cwc_river_network_raw.zip` and `nwdp_cwc_basin_boundaries_raw.zip`. They were accessed from the [CWC River Network catalog](https://nwdp.nwic.gov.in/dataset/river-line) and [NWDP Basin CWC catalog](https://nwdp.nwic.gov.in/dataset/basin-cwc). NWDP identifies CWC as the river dataset producer and describes it as a linear representation of river paths. The portal provides KML, GeoJSON, and ESRI Shapefile river resources; the downloaded raw river archive is an ESRI Shapefile ZIP and the basin archive is a ZIP containing GeoJSON. The portal resource metadata labels the license `Other (Open)`. The app retains these raw files unchanged and stores reprojected derivatives and their manifest under ignored `backend/data/processed/`.

The river shapefile's source CRS is a projected metric Lambert Conic Conformal CRS recorded in the manifest; the basin GeoJSON declares EPSG:7755. Both are transformed to WGS 84 longitude/latitude (EPSG:4326). The source river layer has 30,395 features; the Cauvery subset contains 1,043 line features and one Cauvery basin polygon. River properties include `OBJECTID`, `rivname`, `sub_basin`, `ba_name`, `length_km`, `origin`, `major_trib`, `st_pt_lat`, `st_pt_long`, `en_pt_lat`, `en_pt_long`, `UID_River`, and `Confluence`. Reach keys are local identifiers derived from `OBJECTID`, not published hydrological identifiers. The catalog describes geometry, names and labels; it does not provide a verified routable topology or flow direction. Vertex order, reach IDs, confluence text and station proximity are not used to infer flow.

`backend/river_network.py` performs point-to-line candidate matching and basin-polygon tests separately. The nearest reach is a `verified_match` only when coordinates are verified, distance is at most 250 m, the station falls inside the published Cauvery polygon, and source basin and river labels corroborate it. Matches within 2,500 m without all corroborating evidence remain `candidate_match`; metadata conflicts remain `conflicting_metadata`. The current 27 station registry records resolve to 7 verified reach matches, 18 candidate matches and 2 conflicts (K.M. VADI and Musiri). There are no unmatched records in this snapshot. Verified station-to-station connections: zero. All pairs with verified Cauvery basin labels are classified as same-basin with connection unverified; the dashboard shows no invented river connections or upstream/downstream arrows. Local keys such as `musiri` remain normalized source-name keys, not official CWC site codes.

Network endpoints are `GET /river-network/status`, `GET /river-network/stations`, `GET /river-network/relationships?station_id=...`, and `GET /river-network/geometry`. Existing station endpoints remain available. The map overlays available Cauvery lines and basin boundaries, colors gauge markers by match evidence, and retains station selection if geometry is missing. Station details show candidate reach, distance, supporting river/basin evidence, conflicts and limitations. Same-basin and proximity relationships do not imply that one gauge's reading represents another location. Flood risk remains `not_assessed`.

Run the complete deterministic backend suite, including the Phase 10 network tests, and the frontend checks from the project root:

```powershell
cd backend
.\.venv\Scripts\python.exe -B -m unittest discover -v
cd ..\frontend
npm run lint
npm run build
```

To process the preserved raw archives again, run `python river_network.py --build` from `backend`. To start the services from the project root:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

In a second terminal:

```powershell
cd frontend
npm run dev
```

# Phase 11: Evidence-based assessment readiness

`backend/assessment_readiness.py` defines a typed `AssessmentReadinessRecord` and deterministic gates for `eligible_for_observation_summary`, `eligible_for_threshold_comparison`, and `eligible_for_location_specific_assessment`. It preserves station/source identity, exact-station observation, observation and retrieval times, freshness, quality, source unit and datum state, reach match, verified station connections (if supplied by authoritative topology), threshold provenance and applicability, alert status, rainfall, elevation, conflicts, missing inputs, and source provenance. Unknown fields remain unknown. This readiness endpoint does not calculate a flood-risk score.

`GET /assessment/readiness?station_id=...` validates the registry key, fetches the latest exact-source-name observation and, only when station coordinates are verified and conflict-free, retrieves the location providers. It uses existing provider health and `collect_monitoring_data` conventions. A confirmed active SACHET alert is returned even when telemetry is missing; provider unavailability or no matching alert is never described as an all-clear. Observation-summary eligibility requires an exact registry source-name match, a finite source-valid measurement with a known unit, and freshness supported by a UTC-qualified timestamp inside the existing three-hour policy. A fresh summary is not a local flood-risk assessment.

Threshold comparison requires a fresh summary, verified official station identity, known compatible units and datum, and an officially verified threshold applicable to the exact station. No current station meets these conditions. Musiri's 82.12 m threshold remains historical reference information only; a stale 81.047 m report is not treated as current, and no threshold crossing or return is enabled. Location-specific flood assessment remains `not_assessed` because there is no validated local model and the source network has no verified topology/flow direction. A nearest reach candidate, same-basin label, rainfall, elevation, or missing warning does not become a risk score or an all-clear.

The station detail panel displays separate observation-summary, threshold-comparison and location-assessment gates; station/reach verification; official alert state; source timestamps; missing or conflicting evidence; and source links. Existing station, monitor, Musiri, validation-readiness and risk endpoints remain intact. Phase 11 tests are deterministic and mock external providers; they cover stale/fresh/missing timestamps, bad values/units, station identity, thresholds and datum, conflicts, unavailable topology/providers, alerts despite missing telemetry, Musiri thresholds, and API compatibility.

A future predictive flood-risk model remains blocked until the project has, at minimum:

- An independently verified station/site identity crosswalk and measurement datum history for each gauge.
- Current, station-specific operational thresholds with units, datum, validity periods and authoritative provenance.
- Reliable, time-bounded event labels with an explicit definition (such as observed inundation or a documented station threshold event), plus valid negative periods.
- Event timestamps with known time basis and source version/retrieval provenance.
- Evidence linking each event to the selected station/location and its measurement record, including verified hydrological connectivity where the event and gauge are at different locations.
- Sufficient independent events and non-events for a chronological, event-grouped train/validation/test strategy that prevents temporal leakage.
- A declared baseline and pre-specified metrics suited to the outcome (for example precision/recall and false-alarm rate for event detection, lead-time only when timestamps support it, and calibration only for probabilistic outputs), with uncertainty intervals and subgroup/coverage reporting.

Do not report predictive accuracy, precision/recall, lead time, calibration or false-alarm rates until those labels, links, and temporal splits are validated. Start the backend and frontend using the commands in the Phase 10 section above.


# Phase 12: Historical flood-event validation audit

`backend/historical_label_candidates.json` now inventories two intervals reported by the CWC Appraisal Report 2024, Annex XIII: Musiri above warning level (82.12 m) from 31 July 2024 19:00 to 7 August 2024 02:00, and above danger level (83.12 m) from 1 August 2024 08:00 to 3 August 2024 23:00. The report's source time basis/timezone is not stated. The CWC station metadata book identifies the named Musiri gauge as site `CW1CAL000207`; the preserved 2021–2025 telemetry has no Musiri source station and no official site-code column. These are official gauge-threshold intervals, not independently observed inundation or loss labels. See the [CWC Appraisal Report 2024, Annex XIII](https://cwc.gov.in/sites/default/files/appraisal-report-2024-publicationcompressed.pdf) and [CWC Hydro-Meteorological Observation Stations Book 2025](https://cwc.gov.in/sites/default/files/ho-metadata-book-2025final-publishedsigned.pdf).

The reproducible audit is `backend/event_linkage_audit.py`; its derived report is `backend/data/processed/cwc_cauvery_event_linkage_audit.json` and is served at `GET /validation/historical-events`. The current raw snapshot has 131,610 rows across 8 station names, 130,993 valid numeric levels, 616 invalid numeric levels, and 1 missing level. It has zero duplicate timestamp groups. Hourly-cadence comparisons show irregular intervals in every station; counts are in the report. K.M. VADI has conflicting river metadata (`Ca` and `Cauvery`). The source declares levels in meters in the column header, has no per-record unit field, leaves cross-station datum compatibility unresolved, and provides naive timestamps without timezone. Every station has irregular intervals against the expected hourly cadence. At the Phase 12 audit date, 131,609 timestamped rows are stale for current monitoring and one row has no timestamp; the latest source observation is from 30 December 2025. Historical source rows are preserved unchanged.

The audit compares each of the two event intervals with all 8 station series, for 16 pairs. Calendar-clock overlaps are recorded only as candidate evidence. Pair statuses are 0 verified, 16 unresolved, and 0 excluded; evaluation-eligibility statuses are 0 eligible, 11 candidate, 5 unresolved, and 0 excluded. The five pairs with no calendar-clock row remain unresolved because unknown timezone compatibility prevents a defensible temporal exclusion. Exclusion is reserved for duplicate event identifiers or other explicit invalidation, not a timezone-unknown gap. No pair is a verified event-to-station hydrological link: there is no official station-code crosswalk in this telemetry, no shared verified time basis, and no verified event-to-gauge river path/flow direction. Straight-line distance and shared basin labels do not establish linkage.

No baseline was evaluated. Metrics are null and predictive evaluation is `not_run`: there are no eligible pairs, no linked inundation/flood-impact labels, no independently labeled negative events, and no predeclared prediction rule or temporal evaluation split. Predictive validation remains blocked until the station identity and time basis are resolved; relevant event impact labels and negative labels are collected; hydrological linkage is verified when locations differ; and an event-level chronological evaluation design has adequate independent samples. Flood risk remains `not_assessed`.
