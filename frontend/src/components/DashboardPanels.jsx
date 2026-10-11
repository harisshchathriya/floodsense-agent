import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useDashboard } from "../context/DashboardContext";
import { API_URL } from "../api";
/* eslint react-refresh/only-export-components: off */
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  CircleMarker,
  GeoJSON,
  useMap,
  useMapEvents,
} from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "../App.css";

const markerIcon = L.divIcon({
  className: "floodsense-map-marker",
  html: '<span class="map-marker-pulse"><span></span></span>',
  iconSize: [26, 26],
  iconAnchor: [13, 13],
});

export function MapClickHandler({ onSelect }) {
  useMapEvents({
    click(event) {
      onSelect([event.latlng.lat, event.latlng.lng]);
    },
  });
  return null;
}

export function NetworkBounds({ data }) {
  const map = useMap();
  useEffect(() => {
    if (!data?.features?.length) return;
    const bounds = L.geoJSON(data).getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [14, 14], maxZoom: 8 });
  }, [data, map]);
  return null;
}

export function RecenterButton({ center }) {
  const map = useMap();
  return (
    <button
      type="button"
      className="map-recenter"
      onClick={() => map.setView(center, 12)}
      title="Center map on selected monitoring point"
    >
      ⌖ Recenter
    </button>
  );
}

export function RainfallChart({ records, cutoff, loading, error }) {
  const cutoffTime = Date.parse(cutoff);
  const timeline = (records || [])
    .map((record) => ({
      time: record.time_utc,
      timestamp: Date.parse(record.time_utc),
      value: Number.isFinite(record.rain_mm) ? record.rain_mm : null,
      phase: Number.isFinite(cutoffTime)
        ? Date.parse(record.time_utc) <= cutoffTime ? "observed" : "forecast"
        : "unknown",
    }))
    .filter((point) => Number.isFinite(point.timestamp))
    .sort((left, right) => left.timestamp - right.timestamp);
  const points = timeline.filter((point) => Number.isFinite(point.value));

  if (loading) return <div className="chart-message">Loading hourly rainfall…</div>;
  if (error) return <div className="chart-message">Hourly rainfall is unavailable: {error}</div>;
  if (!points.length) return <div className="chart-message">No hourly rainfall records are available for this location.</div>;

  const maxValue = Math.max(1, ...points.map((point) => point.value));
  const minTime = timeline[0].timestamp;
  const maxTime = timeline[timeline.length - 1].timestamp;
  const x = (timestamp) => 48 + (maxTime === minTime ? 348 : ((timestamp - minTime) / (maxTime - minTime)) * 696);
  const y = (value) => 202 - (value / maxValue) * 162;
  const pathSegments = [];
  let activeSegment = [];
  for (const point of timeline) {
    const previous = activeSegment[activeSegment.length - 1];
    if (!Number.isFinite(point.value)) {
      if (activeSegment.length) pathSegments.push(activeSegment);
      activeSegment = [];
      continue;
    }
    if (previous && (previous.phase !== point.phase || point.timestamp - previous.timestamp > 90 * 60 * 1000)) {
      pathSegments.push(activeSegment);
      activeSegment = [];
    }
    activeSegment.push(point);
  }
  if (activeSegment.length) pathSegments.push(activeSegment);
  const paths = pathSegments.map((segment, index) => ({
    phase: segment[0].phase,
    id: index,
    d: segment.map((point, index) => `${index ? "L" : "M"}${x(point.timestamp)},${y(point.value)}`).join(" "),
  }));
  const labels = [timeline[0], timeline[Math.floor((timeline.length - 1) / 2)], timeline[timeline.length - 1]]
    .filter((point, index, all) => all.findIndex((candidate) => candidate.timestamp === point.timestamp) === index)
    .map((point) => ({ timestamp: point.timestamp, label: new Date(point.timestamp).toISOString().slice(5, 16).replace("T", " ") }));

  return (
    <div className="chart-wrap">
      <div className="chart-legend"><span className="observed">Observed rain</span><span className="forecast">Forecast rain</span></div>
      <svg viewBox="0 0 790 260" role="img" aria-label="Hourly rain observations and forecasts in millimetres">
        <text x="16" y="22" className="chart-axis-title">mm</text>
        {[0, 1, 2, 3].map((tick) => {
          const value = maxValue * (3 - tick) / 3;
          const yPosition = 40 + tick * 54;
          return <g key={tick}><line x1="48" x2="744" y1={yPosition} y2={yPosition} className="chart-gridline" /><text x="40" y={yPosition + 4} textAnchor="end" className="chart-tick">{value.toFixed(value < 1 ? 1 : 0)}</text></g>;
        })}
        {paths.map((segment) => <path key={`${segment.phase}-${segment.id}`} d={segment.d} className={`rainfall-line ${segment.phase}`} />)}
        {points.map((point, index) => <circle key={`${point.time}-${index}`} cx={x(point.timestamp)} cy={y(point.value)} r="3" className={`rainfall-point ${point.phase}`}><title>{point.time}: {point.value} mm · {point.phase}</title></circle>)}
        {labels.map(({ timestamp, label }) => <text key={timestamp} x={x(timestamp)} y="232" textAnchor="middle" className="chart-tick">{label || "—"}</text>)}
        <text x="396" y="254" textAnchor="middle" className="chart-axis-title">Observation time (UTC)</text>
      </svg>
    </div>
  );
}

export function RiskAssessmentPanel({ assessment, monitoringStatus, error, onRetry }) {
  const features = assessment?.rainfall_features;
  const windows = features?.windows || {};
  const location = assessment?.location;

  return (
    <section className="panel assessment-panel">
      <div className="panel-heading">
        <div><h3>Explainable flood-risk assessment</h3><p>Deterministic evidence summary · no LLM-generated score</p></div>
        <span className={`assessment-badge ${assessment?.status === "not_assessed" ? "unassessed" : ""}`}>
          {monitoringStatus === "loading" ? "Assessing…" : assessment?.risk_category || (assessment ? "Not assessed" : "Awaiting data")}
        </span>
      </div>
      {monitoringStatus === "loading" ? (
        <div className="assessment-message">Retrieving provider observations and preparing rainfall features…</div>
      ) : error ? (
        <div className="assessment-message assessment-error">{error}<button onClick={onRetry}>Retry assessment</button></div>
      ) : assessment ? (
        <div className="assessment-body">
          <div className="assessment-summary">
            <strong>{assessment.explanation}</strong>
            <span>Requested point: {Number(location?.requested_latitude).toFixed(5)}, {Number(location?.requested_longitude).toFixed(5)} · Assessed {assessment.assessed_at ? `${assessment.assessed_at.replace("T", " ").slice(0, 16)} UTC` : "time unavailable"}</span>
          </div>
          <div className="rain-window-grid">
            {["1h", "3h", "24h"].map((label) => {
              const item = windows[label];
              return <div className="rain-window" key={label}>
                <span>Observed rain · {label}</span>
                <strong>{item?.complete && item.accumulation != null ? `${item.accumulation} ${item.unit}` : "Incomplete data"}</strong>
                <small>{item ? `${item.valid_observations}/${item.expected_observations} hourly observations${item.complete ? " · complete" : " · partial values are withheld as totals"}` : "Window unavailable"}</small>
              </div>;
            })}
          </div>
          <div className="assessment-facts">
            <span><b>Short-term trend</b> {features?.trend?.status === "available" ? `${features.trend.direction} (${features.trend.change_mm_per_hour} mm/hour)` : "Insufficient consecutive hourly observations"}</span>
            <span><b>Observed / forecast records</b> {features?.observed_record_count ?? 0} / {features?.forecast_record_count ?? 0}</span>
            <span><b>Rainfall freshness</b> {features?.is_stale ? "Stale · over 3 hours old" : "Within 3-hour freshness window"}</span>
            <span><b>Latest usable observation</b> {features?.latest_usable_observation_utc || "Unavailable"}</span>
            <span><b>Weather-grid point</b> {location?.rainfall_provider_coordinates ? `${Number(location.rainfall_provider_coordinates.latitude).toFixed(4)}, ${Number(location.rainfall_provider_coordinates.longitude).toFixed(4)}` : "Unavailable"}</span>
            <span><b>Elevation provider point</b> {location?.elevation_provider_coordinates ? `${Number(location.elevation_provider_coordinates.latitude).toFixed(4)}, ${Number(location.elevation_provider_coordinates.longitude).toFixed(4)}` : "Unavailable"}</span>
          </div>
          <details className="assessment-details">
            <summary>Evidence and reasons ({(assessment.evidence || []).length})</summary>
            <ul>{(assessment.evidence || []).map((item, index) => <li key={`${item.name}-${index}`}><b>{item.name}</b>: {item.value ?? "Unavailable"}{item.unit ? ` ${item.unit}` : ""}{item.complete === false ? ` · partial (${item.coverage})` : ""}{item.source ? ` · ${item.source}` : ""}{item.observed_at_utc ? ` · ${item.observed_at_utc} UTC` : ""}</li>)}</ul>
            <ul>{(assessment.reasons || []).map((reason) => <li key={reason}>{reason}</li>)}</ul>
          </details>
          <details className="assessment-details">
            <summary>Missing data and limitations ({(assessment.missing_inputs || []).length})</summary>
            <ul>{(assessment.missing_inputs || []).map((item, index) => <li key={`${item.input}-${index}`}><b>{item.input}</b>: {item.reason}</li>)}</ul>
            <ul>{(assessment.limitations || []).map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
          </details>
        </div>
      ) : (
        <div className="assessment-message">Start monitoring to retrieve evidence and assess data coverage. Flood risk will remain unclassified unless verified inputs and defensible local rules are available.</div>
      )}
    </section>
  );
}

export function OfficialEvidencePanel({ alerts, gauges, loading }) {
  const alertRecords = alerts?.alerts || [];
  const stations = gauges?.stations || [];
  const providerState = (provider) => provider?.status === "available"
    ? "Available"
    : provider?.status === "unavailable" || provider?.error
      ? "Unavailable"
      : "Not reported";

  return (
    <section className="panel official-panel">
      <div className="panel-heading">
        <div><h3>Official alerts and river-level intelligence</h3><p>NDMA SACHET public alerts · CWC telemetry published by NWIC</p></div>
        <span className="map-tag">{loading ? "Refreshing" : "Source status shown below"}</span>
      </div>
      <div className="official-grid">
        <article className="official-section">
          <div className="official-section-title"><h4>NDMA SACHET alerts</h4><span className={`provider-state ${alerts?.status === "available" ? "provider-ok" : "provider-unavailable"}`}>{providerState(alerts)}</span></div>
          {alerts?.status === "unavailable" || alerts?.error ? (
            <p className="official-empty">Alert data is unavailable. This does not mean there are no active alerts. {alerts.error || "Retry monitoring to try again."}</p>
          ) : alertRecords.length ? alertRecords.map((alert, index) => (
            <div className="official-record" key={`${alert.identifier || alert.headline || "alert"}-${index}`}>
              <div className="official-record-head"><strong>{alert.headline || alert.event || "Official alert"}</strong><span>{alert.severity || "Severity not supplied"}</span></div>
              <p>{alert.description || "No additional alert wording supplied."}</p>
              <small>{alert.sender || "Issuing authority not supplied"}{alert.cap_status ? ` · CAP status: ${alert.cap_status}` : ""}{alert.area_description ? ` · ${alert.area_description}` : ""}</small>
              <small>Validity: {alert.validity_status === "active" ? "Active within published period" : alert.validity_status === "expired" ? "Expired" : alert.validity_status === "cancelled" ? "Cancellation message" : alert.validity_status === "updated_status_unresolved" ? "Update received; prior message relationship unresolved" : "Could not verify from published fields"}{alert.effective_utc ? ` · From ${alert.effective_utc}` : ""}{alert.expires_utc ? ` · Until ${alert.expires_utc}` : ""}</small>
              {alert.instruction && <p className="official-instruction">{alert.instruction}</p>}
              <small>{alert.match_reason}</small>
              {alert.link && <a href={alert.link} target="_blank" rel="noreferrer">Open source alert</a>}
            </div>
          )) : (
            <p className="official-empty">{alerts?.status === "available" ? alerts.message || "No matching alert record was found in the retrieved feed. This is not an all-clear." : "Start monitoring to check the official alert feed."}</p>
          )}
          {alerts?.retrieved_at_utc && <small className="source-footnote">Checked {alerts.retrieved_at_utc} · {alerts.source}</small>}
        </article>

        <article className="official-section">
          <div className="official-section-title"><h4>CWC river gauges · Cauvery dataset</h4><span className={`provider-state ${gauges?.status === "available" ? "provider-ok" : "provider-unavailable"}`}>{providerState(gauges)}</span></div>
          {gauges?.status === "unavailable" || gauges?.error ? (
            <p className="official-empty">Gauge data is unavailable. No reading is assumed. {gauges.error || "Retry monitoring to try again."}</p>
          ) : stations.length ? stations.map((station) => (
            <div className="official-record gauge-record" key={`${station.station}-${station.station_location?.latitude}-${station.station_location?.longitude}`}>
              <div className="official-record-head"><strong>{station.station}</strong><span>{station.observation?.value ?? "—"} {station.observation?.unit || ""}</span></div>
              <p>{station.river || "River not supplied"}{station.district ? ` · ${station.district}` : ""}{station.state ? `, ${station.state}` : ""}</p>
              <small>Gauge point: {station.station_location?.latitude}, {station.station_location?.longitude} · {station.distance_from_selected_point_km} km straight-line distance</small>
              <small>Observation: {station.observation?.observed_at_utc || station.observation?.observed_at || "Timestamp unavailable"} · {station.is_stale === true ? "Stale" : station.is_stale === false ? "Within 3 hours" : "Freshness unknown (source timezone not documented)"}</small>
              <small>{station.match_reason}</small>
            </div>
          )) : (
            <p className="official-empty">{gauges?.status === "available" ? "No georeferenced station record was found." : "Start monitoring to retrieve CWC gauge observations."}</p>
          )}
          <p className="official-caveat">{gauges?.message || "Only Cauvery CWC telemetry is connected. Nearest-station distance does not establish upstream, downstream, or basin relevance."}</p>
          {gauges?.retrieved_at_utc && <small className="source-footnote">Retrieved {gauges.retrieved_at_utc} · {gauges.source}</small>}
        </article>
      </div>
    </section>
  );
}

export function MusiriMonitoringPanel({ onProviderQuery }) {
  const [data, setData] = useState(null);
  const [state, setState] = useState("loading");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    fetch(`${API_URL}/stations/musiri/status`)
      .then(async (response) => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `Request failed (${response.status})`);
        return result;
      })
      .then((result) => { if (active) { setData(result); setState("loaded"); onProviderQuery(); } })
      .catch((error) => { if (active) { setData({ error: error.message }); setState("error"); onProviderQuery(); } });
    return () => { active = false; };
  }, [refresh, onProviderQuery]);
  const observation = data?.observation;
  const station = data?.station;
  const monitoring = data?.threshold_monitoring;
  return (
    <section className="panel musiri-panel">
      <div className="panel-heading"><div><h3>Musiri river monitoring</h3><p>Exact MUSIRI row · CWC Cauvery telemetry via NWIC</p></div>
        <button className="musiri-refresh" type="button" onClick={() => { setState("loading"); setRefresh((value) => value + 1); }} disabled={state === "loading"}>{state === "loading" ? "Refreshing…" : "↻ Refresh"}</button></div>
      <div className="musiri-body">
        <div className="musiri-summary"><div><span>Latest reported level</span><strong>{observation?.value == null ? "—" : `${observation.value} ${observation.unit || "m"}`}</strong></div>
          <span className="assessment-badge unassessed">{state === "loading" ? "Loading" : monitoring?.monitoring_status || (state === "error" ? "Unavailable" : "Unknown")}</span></div>
        {state === "error" && <p className="official-empty">Musiri data could not be retrieved. No other station is substituted. {data?.error}</p>}
        {data?.status === "unavailable" && <p className="official-empty">{data.message} {data.error || ""}</p>}
        {station && <div className="musiri-details">
          <span><b>Feed station:</b> {station.name} · site code not supplied by telemetry</span>
          <span><b>CWC inventory reference:</b> CW1CAL000207 (crosswalk not verified)</span>
          <span><b>Location:</b> {station.latitude}, {station.longitude} · {station.river}, {station.district}, {station.state}</span>
          <span><b>Observation time:</b> {observation?.observed_at || "Unavailable"} · {observation?.timezone_verified ? "timezone supplied" : "timezone not stated"}</span>
          <span><b>Retrieved:</b> {data.retrieved_at_utc || "Unavailable"} · <b>Freshness:</b> {data.freshness?.status || "unknown"}</span>
          <span><b>Documented warning reference:</b> 82.12 m · CWC Appraisal Report 2024 · reference only</span>
          <p className="official-caveat">{monitoring?.monitoring_status === "stale_data" ? "The latest observation is stale; no threshold conclusion is made." : monitoring?.explanation || "Threshold result is unavailable."} {station.identity_note || ""} Raw datum fields: zero gauge {observation?.raw_zero_of_gauge ?? "not supplied"}; mean sea level {observation?.raw_mean_sea_level_field ?? "not supplied"}.</p>
        </div>}
        <small className="source-footnote">{data?.source_url ? <a href={data.source_url} target="_blank" rel="noreferrer">Open CWC/NWIC telemetry source</a> : "CWC/NWIC telemetry source"} · Not a forecast or official public warning. Flood risk remains not assessed.</small>
      </div>
    </section>
  );
}

export function ProviderHealthPanel({ refreshSignal }) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    fetch(`${API_URL}/health/providers`)
      .then(async (response) => {
        const value = await response.json();
        if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
        return value;
      })
      .then((value) => { if (active) { setReport(value); setState("loaded"); } })
      .catch(() => { if (active) setState("error"); });
    return () => { active = false; };
  }, [refresh, refreshSignal]);
  const dateLabel = (value) => value ? new Date(value).toLocaleString() : "—";
  const freshnessLabel = (value) => ({ fresh: "Fresh", stale: "Stale", unknown: "Unknown", degraded: "Mixed / degraded", not_applicable: "Not applicable" }[value] || "Unknown");
  return (
    <section className="panel provider-health-panel">
      <div className="panel-heading"><div><h3>Data-source health</h3><p>Reachability and observation freshness, tracked when requests run</p></div>
        <button className="musiri-refresh" type="button" onClick={() => setRefresh((value) => value + 1)}>{state === "loading" ? "Checking…" : "↻ Refresh health"}</button></div>
      <div className="provider-health-body">
        <div className="provider-health-grid">
          {(report?.providers || []).map((provider) => (
            <article className="provider-health-card" key={provider.key}>
              <div className="provider-health-title"><strong>{provider.name}</strong><span className={`health-pill health-${provider.status}`}>{provider.status}</span></div>
              <span className="provider-health-source">{provider.source}</span>
              <span><b>Last success</b> {dateLabel(provider.last_successful_retrieval_at)}</span>
              <span><b>Last attempt</b> {dateLabel(provider.last_attempted_at)} · {provider.last_result_status}</span>
              <span><b>Observation freshness</b> {freshnessLabel(provider.observation_freshness)}</span>
              {provider.last_error_category && <span className="provider-health-error"><b>Latest error</b> {provider.last_error_category}</span>}
              {provider.recovered_since_failure === true && <span className="provider-health-recovery">Recovered after a previous failure</span>}
              {provider.recovered_since_failure === false && provider.last_result_status === "unavailable" && <span className="provider-health-error">Latest request failed</span>}
              <small>{provider.status_reason}</small>
            </article>
          ))}
          {!report && state === "loading" && <p className="official-empty">Loading provider health…</p>}
          {!report && state === "error" && <p className="official-empty">Provider health is unavailable. No provider is assumed healthy.</p>}
        </div>
        <p className="provider-health-note">{report?.message || "Health is request-driven; no background monitoring is running."} {state === "error" && report ? "The latest refresh failed; showing the last received health snapshot." : ""}</p>
      </div>
    </section>
  );
}

export function CauveryStationExplorer({ onProviderQuery, selectedStationId, onSelectedStationChange }) {
  const [stations, setStations] = useState([]);
  const [internalSelectedId, setInternalSelectedId] = useState("musiri");
  const selectedId = selectedStationId ?? internalSelectedId;
  const setSelectedId = onSelectedStationChange ?? setInternalSelectedId;
  const [observations, setObservations] = useState(null);
  const [readiness, setReadiness] = useState(null);
  const [readinessError, setReadinessError] = useState(null);
  const [loadingReadiness, setLoadingReadiness] = useState(true);
  const [networkGeometry, setNetworkGeometry] = useState(null);
  const [networkStatus, setNetworkStatus] = useState(null);
  const [stationRelationships, setStationRelationships] = useState([]);
  const [networkError, setNetworkError] = useState(null);
  const [loadingStations, setLoadingStations] = useState(true);
  const [loadingObservations, setLoadingObservations] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    fetch(`${API_URL}/stations`)
      .then(async (response) => {
        const value = await response.json();
        if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
        return value;
      })
      .then((value) => {
        if (!active) return;
        setStations(value.stations || []);
        setNetworkStatus(value.river_network || null);
        if (!(value.stations || []).some((item) => item.station_id === selectedId) && value.stations?.length) {
          setSelectedId(value.stations[0].station_id);
        }
        setLoadingStations(false);
      })
      .catch((cause) => { if (active) { setError(cause.message); setLoadingStations(false); } });
    return () => { active = false; };
  }, [selectedId, setSelectedId]);

  useEffect(() => {
    let active = true;
    fetch(`${API_URL}/river-network/geometry`)
      .then(async (response) => {
        const value = await response.json();
        if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
        return value;
      })
      .then((value) => { if (active) setNetworkGeometry(value); })
      .catch((cause) => { if (active) setNetworkError(cause.message); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    fetch(`${API_URL}/stations/${encodeURIComponent(selectedId)}/observations?limit=12`)
      .then(async (response) => {
        const value = await response.json();
        if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
        return value;
      })
      .then((value) => { if (active) { setObservations(value); setStations((items) => items.map((station) => station.station_id === selectedId ? { ...station, provider_health: value.provider_health } : station)); setError(null); setLoadingObservations(false); onProviderQuery(); } })
      .catch((cause) => { if (active) { setError(cause.message); setLoadingObservations(false); onProviderQuery(); } });
    return () => { active = false; };
  }, [selectedId, refresh, onProviderQuery]);

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    fetch(`${API_URL}/assessment/readiness?station_id=${encodeURIComponent(selectedId)}`)
      .then(async (response) => {
        const value = await response.json();
        if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
        return value;
      })
      .then((value) => { if (active) { setReadiness(value); setLoadingReadiness(false); onProviderQuery(); } })
      .catch((cause) => { if (active) { setReadinessError(cause.message); setLoadingReadiness(false); onProviderQuery(); } });
    return () => { active = false; };
  }, [selectedId, refresh, onProviderQuery]);

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    fetch(`${API_URL}/river-network/relationships?station_id=${encodeURIComponent(selectedId)}`)
      .then(async (response) => {
        const value = await response.json();
        if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
        return value;
      })
      .then((value) => { if (active) setStationRelationships(value.relationships || []); })
      .catch(() => { if (active) setStationRelationships([]); });
    return () => { active = false; };
  }, [selectedId]);

  const selected = stations.find((station) => station.station_id === selectedId);
  const shownStations = stations.filter((station) => String(station.official_name?.value || "").toLowerCase().includes(search.toLowerCase()));
  const latest = observations?.latest_observation;
  const lat = selected?.latitude?.value;
  const lon = selected?.longitude?.value;
  const sourceCoordinateCount = stations.filter((station) => Number.isFinite(station.latitude?.value) && Number.isFinite(station.longitude?.value)).length;
  const mapCenter = selected && Number.isFinite(lat) && Number.isFinite(lon) ? [lat, lon] : [11.75, 77.25];
  const relationshipLabel = (status) => ({
    verified_river_network_connection: "Verified river-network connection",
    verified_connected_reaches: "Verified connected reaches · direction unknown",
    verified_upstream_downstream: "Verified upstream/downstream relationship",
    same_basin_no_verified_river_connection: "Same basin · river connection unverified",
    proximity_only: "Proximity only",
    insufficient_geographic_data: "Insufficient geographic data",
  }[status] || "Relationship unknown");
  const matchLabel = (status) => ({
    verified_match: "Verified station-coordinate to reach match",
    candidate_match: "Candidate reach · not verified",
    conflicting_metadata: "Conflicting station metadata · candidate only",
    unmatched: "No reach match",
    insufficient_network_data: "Network data unavailable",
  }[status] || "Match status unknown");
  const relatedStations = stationRelationships
    .filter((item) => item.connection_status !== "unknown")
    .sort((left, right) => {
      const rank = (status) => status.startsWith("verified_") ? 0 : status === "same_basin_no_verified_river_connection" ? 1 : 2;
      return rank(left.connection_status) - rank(right.connection_status)
        || (left.distance_km ?? Infinity) - (right.distance_km ?? Infinity);
    })
    .slice(0, 5);
  const chooseStation = (stationId) => {
    setSelectedId(stationId);
    setLoadingObservations(true);
    setObservations(null);
    setError(null);
    setReadiness(null);
    setReadinessError(null);
    setLoadingReadiness(true);
  };

  return (
    <section className="panel cauvery-explorer">
      <div className="panel-heading">
        <div><h3>Cauvery station registry</h3><p>{stations.length ? `${stations.length} source station names across preserved CWC telemetry files` : "CWC/NWIC station metadata and observations"}</p></div>
        <button className="musiri-refresh" type="button" onClick={() => { setLoadingObservations(true); setLoadingReadiness(true); setReadiness(null); setReadinessError(null); setRefresh((value) => value + 1); }} disabled={loadingObservations}>{loadingObservations ? "Retrieving…" : "↻ Refresh selected station"}</button>
      </div>
      <div className="cauvery-explorer-layout">
        <div className="station-map-column">
          <div className="station-map-legend">
            <span><i className="network-line-legend" /> CWC river geometry <i className="basin-line-legend" /> Cauvery basin</span>
            <span>{sourceCoordinateCount} gauges · {networkStatus?.river_reach_count ?? 0} reaches</span>
          </div>
          <MapContainer key={`station-map-${selectedId}`} center={mapCenter} zoom={selected ? 7 : 6} scrollWheelZoom className="station-leaflet-map">
            <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
            {networkGeometry?.features?.length > 0 && <>
              <GeoJSON
                data={networkGeometry}
                style={(feature) => feature?.properties?.geometry_role === "basin_boundary"
                  ? { color: "#718c79", weight: 1.5, fillColor: "#8ca98f", fillOpacity: 0.08 }
                  : { color: "#3e8fa8", weight: 1.6, opacity: 0.8 }}
                onEachFeature={(feature, layer) => {
                  if (feature?.properties?.geometry_role === "river_reach") {
                    const properties = feature.properties;
                    layer.bindTooltip(`${properties.river_name || "Named river reach"}${properties.sub_basin ? ` · ${properties.sub_basin}` : ""}`);
                  }
                }}
              />
              <NetworkBounds data={networkGeometry} />
            </>}
            {stations.filter((station) => Number.isFinite(station.latitude?.value) && Number.isFinite(station.longitude?.value)).map((station) => (
              <CircleMarker
                key={station.station_id}
                center={[station.latitude.value, station.longitude.value]}
                radius={station.station_id === selectedId ? 9 : 6}
                pathOptions={{ color: station.station_id === selectedId ? "#b46b23" : station.river_network_match?.match_status === "conflicting_metadata" ? "#bd5147" : station.river_network_match?.match_status === "verified_match" ? "#34845d" : "#267c9f", fillColor: station.station_id === selectedId ? "#e6a45d" : station.river_network_match?.match_status === "conflicting_metadata" ? "#bd5147" : station.river_network_match?.match_status === "verified_match" ? "#49a875" : "#4dabc5", fillOpacity: 0.9, weight: 2 }}
                eventHandlers={{ click: () => chooseStation(station.station_id) }}
              ><Popup><strong>{station.official_name.value}</strong><br />{matchLabel(station.river_network_match?.match_status)}<br /><button type="button" onClick={() => chooseStation(station.station_id)}>View station</button></Popup></CircleMarker>
            ))}
          </MapContainer>
          <p className="station-map-note">{networkError ? `Official river geometry unavailable: ${networkError}.` : networkStatus?.status === "available" ? "CWC lines and basin boundaries are reprojected to WGS 84. Marker colors show reach-match evidence. No verified station-to-station topology or flow direction is published; no connections or arrows are drawn." : "Loading official Cauvery river geometry; station selection and details remain available."} Source coordinates and candidates do not establish that a gauge reading represents a nearby location.</p>
        </div>
        <aside className="station-list-column">
          <label className="station-search-label" htmlFor="station-search">Browse stations</label>
          <input id="station-search" className="station-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filter station names" />
          <div className="station-list" role="listbox" aria-label="Cauvery telemetry stations">
            {shownStations.map((station) => (
              <button type="button" role="option" aria-selected={station.station_id === selectedId} className={`station-list-item ${station.station_id === selectedId ? "selected" : ""}`} key={station.station_id} onClick={() => chooseStation(station.station_id)}>
                <span><b>{station.official_name.value}</b><small>{matchLabel(station.river_network_match?.match_status)} · {station.snapshot_availability?.status === "historical_snapshot_only" ? "historical snapshot only" : "in 2026–2030 snapshot"}</small></span>
                <i className={`metadata-dot metadata-${station.metadata_verification_status}`} title={`Metadata ${station.metadata_verification_status}`} />
              </button>
            ))}
            {!shownStations.length && <p className="official-empty">No station name matches this filter.</p>}
          </div>
        </aside>
      </div>
      {loadingStations && <p className="official-empty station-message">Loading the CWC station registry…</p>}
      {error && <p className="official-empty station-message">Station information could not be retrieved. {error}</p>}
      {selected && <div className="station-detail">
        <div className="station-detail-heading"><div><span className="eyebrow">SELECTED SOURCE STATION</span><h4>{selected.official_name.value}</h4></div><span className={`health-pill health-${observations?.status === "unavailable" ? "unavailable" : observations?.freshness?.status === "stale" ? "stale" : observations?.freshness?.status === "fresh" ? "healthy" : "unknown"}`}>{loadingObservations ? "loading" : observations?.availability === "historical_only" ? "historical only" : observations?.freshness?.status || observations?.status || "unknown"}</span></div>
        <div className="station-facts">
          <span><b>Station identifier</b>{selected.station_id} · local name key; official site code {selected.station_id_verification_status}</span>
          <span><b>Source coordinates</b>{lat ?? "not available"}, {lon ?? "not available"} · {selected.latitude?.status === "conflicting" ? "conflicting with inventory" : selected.latitude?.status || "unknown"}</span>
          <span><b>Metadata verification</b>{selected.metadata_verification_status}{selected.known_conflicts?.length ? ` · ${selected.known_conflicts.map((item) => item.field).join(", ")} conflict` : ""}</span>
          <span><b>Provider health</b>{observations?.provider_health?.status || selected.provider_health?.status || "unknown"} · request-driven, in memory</span>
          <span><b>Observation freshness</b>{observations?.freshness?.status || "unknown"}{observations?.freshness?.note ? ` · ${observations.freshness.note}` : ""}</span>
          <span><b>Hydrological relationship</b>{relationshipLabel(selected.hydrological_relationship?.status)}</span>
          <span><b>River-network match</b>{matchLabel(selected.river_network_match?.match_status)}{selected.river_network_match?.candidate_reach ? ` · ${selected.river_network_match.candidate_reach.river_name} (${selected.river_network_match.candidate_reach.distance_m} m)` : ""}</span>
          <span><b>Match evidence</b>{selected.river_network_match?.river_name_evidence?.name_match ? "River name and basin labels corroborate the coordinate match." : "Basin geometry and distance identify a candidate only."} Official station code remains {selected.station_id_verification_status || "unverified"}.</span>
          <span><b>Threshold applicability</b>{selected.threshold?.applicability_status === "unknown" ? "Unknown · reference only" : selected.threshold?.applicability_status || "Not available"}{selected.threshold?.value != null ? ` · ${selected.threshold.value} ${selected.threshold.unit} (not decision eligible)` : ""}</span>
          <span><b>Preserved coverage</b>{selected.first_observation || "—"} to {selected.last_observation || "—"} · source timezone unknown</span>
          <span><b>Saved dataset presence</b>{selected.snapshot_availability?.status === "historical_snapshot_only" ? "Historical file only" : "Included in 2026–2030 snapshot"} · live feed checked separately</span>
        </div>
        <div className="station-observation-summary">
          <strong>{latest?.value == null ? "No numeric level in latest record" : `${latest.value} ${latest.unit}`}</strong>
          <span>{latest ? `Latest reported ${observations?.freshness?.status === "stale" ? "(stale; not current)" : observations?.availability === "historical_only" ? "(historical snapshot; not current)" : observations?.freshness?.status === "fresh" ? "(within application freshness window)" : "(freshness unknown)"}` : "No observation is currently available for this station."}</span>
          <span><b>Observed:</b> {latest?.observed_at || "—"} {latest?.observed_at_utc ? `(${latest.observed_at_utc} UTC)` : "· timezone not verified"}</span>
          <span><b>Retrieved:</b> {observations?.retrieved_at_utc || "—"} · <b>Source:</b> {observations?.source || selected.source}</span>
          {observations?.message && <small>{observations.message}</small>}
        </div>
        <section className="assessment-readiness-panel" aria-live="polite">
          <div className="assessment-readiness-heading"><div><b>Assessment readiness</b><small>Evidence gates only · flood risk remains not assessed</small></div><span className="readiness-pill">{loadingReadiness ? "Checking evidence…" : readiness?.status === "not_assessed" ? "Not assessed" : "Unknown"}</span></div>
          {readinessError && <p className="assessment-readiness-note">Readiness evidence is unavailable: {readinessError}</p>}
          {readiness && <>
            <div className="assessment-readiness-grid">
              <span><b>Observation summary</b>{readiness.eligibility?.eligible_for_observation_summary?.eligible ? "Eligible · fresh exact-station record" : "Withheld · " + (readiness.eligibility?.eligible_for_observation_summary?.reasons?.[0] || "evidence unknown")}</span>
              <span><b>Threshold comparison</b>{readiness.eligibility?.eligible_for_threshold_comparison?.eligible ? "Eligible" : "Not eligible · " + (readiness.threshold_evidence?.applicability_status || "unknown")}</span>
              <span><b>Location-specific assessment</b>Not assessed</span>
              <span><b>Official alert evidence</b>{readiness.official_alert_evidence?.status === "confirmed_official_alert" ? `Confirmed official alert · ${readiness.official_alert_evidence.active_matched_count}` : readiness.official_alert_evidence?.status === "unknown" ? "Unknown · alert provider unavailable" : "No matching alert found · not an all-clear"}</span>
              <span><b>Station identity</b>{readiness.selected_station?.station_identity?.source_name_match || "Unknown"} source-name match · official site code {readiness.selected_station?.station_identity?.official_station_code || "unknown"}</span>
              <span><b>River match</b>{matchLabel(readiness.river_network_evidence?.station_reach_match?.match_status)}</span>
              <span><b>Threshold</b>{readiness.threshold_evidence?.value == null ? "Not available" : `${readiness.threshold_evidence.value} ${readiness.threshold_evidence.unit || ""} · ${readiness.threshold_evidence.applicability_status || "unknown"}`}</span>
              <span><b>Observation time</b>{readiness.latest_observation?.observed_at || "Unknown"} · retrieved {readiness.latest_observation?.retrieved_at_utc || "Unknown"}</span>
            </div>
            <div className="assessment-readiness-context">
              <span><b>Rainfall</b>{readiness.rainfall_evidence?.current?.rain_mm == null ? "Unknown or unavailable" : `${readiness.rainfall_evidence.current.rain_mm} mm`} · observed {readiness.rainfall_evidence?.current?.time_utc || "timestamp unknown"} · retrieved {readiness.rainfall_evidence?.source?.retrieved_at_utc || "unknown"}</span>
              <span><b>Elevation</b>{readiness.elevation_evidence?.elevation_m == null ? "Unknown or unavailable" : `${readiness.elevation_evidence.elevation_m} m`} · lookup retrieved {readiness.elevation_evidence?.source?.retrieved_at_utc || "unknown"}</span>
            </div>
            {readiness.official_alert_evidence?.alerts?.length > 0 && <div className="assessment-alert-records">{readiness.official_alert_evidence.alerts.slice(0, 3).map((alert, index) => <article key={`${alert.link || alert.name || "alert"}-${index}`}><b>{alert.name || alert.headline || "Official alert"}{alert.severity ? ` · ${alert.severity}` : ""}</b><span>{alert.wording || alert.instruction || "Issuer alert text unavailable."}</span><small>Validity: {alert.validity_status || "unknown"} · effective {alert.effective_utc || "unknown"} · expires {alert.expires_utc || "unknown"}</small></article>)}</div>}
            {readiness.eligibility?.eligible_for_location_specific_assessment?.reasons?.length > 0 && <ul className="assessment-readiness-reasons">{readiness.eligibility.eligible_for_location_specific_assessment.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
            {readiness.missing_inputs?.length > 0 && <details className="assessment-readiness-missing"><summary>Missing or unresolved evidence ({readiness.missing_inputs.length})</summary><ul>{readiness.missing_inputs.map((item, index) => <li key={`${item.input}-${index}`}>{item.input}: {item.reason}</li>)}</ul></details>}
            <div className="assessment-readiness-sources">Sources: {readiness.provenance?.filter((item) => item.url).map((item) => <a key={`${item.evidence}-${item.url}`} href={item.url} target="_blank" rel="noreferrer">{item.name}</a>)}</div>
          </>}
          {!readiness && loadingReadiness && <p className="assessment-readiness-note">Checking station observation and location evidence. Provider availability alone does not establish eligibility.</p>}
        </section>
        <div className="network-relationship-summary">
          <strong>Other station relevance</strong>
          {relatedStations.length ? relatedStations.map((item) => {
            const peerId = item.station_a_id === selectedId ? item.station_b_id : item.station_a_id;
            const peer = stations.find((station) => station.station_id === peerId);
            return <span key={peerId}><b>{peer?.official_name?.value || peerId}</b> · {relationshipLabel(item.connection_status)}{item.distance_km != null ? ` · ${item.distance_km} km straight-line` : ""}</span>;
          }) : <span>No verified connection is available. Nearby gauges remain proximity candidates only.</span>}
        </div>
        {selected.known_conflicts?.length > 0 && <ul className="station-conflicts">{selected.known_conflicts.map((conflict, index) => <li key={`${conflict.field}-${index}`}>{conflict.field}: {conflict.status} · {conflict.note || JSON.stringify(conflict.telemetry || conflict.values)}</li>)}</ul>}
        <details className="station-record-list"><summary>Recent station records ({observations?.observations?.length || 0})</summary>
          {observations?.observations?.length ? observations.observations.map((item, index) => <div className="station-observation-row" key={`${item.observed_at}-${index}`}><b>{item.value == null ? "Missing / invalid level" : `${item.value} ${item.unit}`}</b><span>{item.observed_at || "Timestamp invalid"} · {item.quality_status} · {item.record_type}</span><small>Raw level: {item.raw_value ?? "not supplied"} · zero gauge: {item.raw_zero_of_gauge ?? "not supplied"} · mean sea level: {item.raw_mean_sea_level ?? "not supplied"}</small></div>) : <p className="official-empty">No station records were returned. The source may be unavailable or this station may not be included in its current resource.</p>}
        </details>
      </div>}
      <p className="official-caveat station-bottom-note">Provider health is request-driven and in-memory. Metadata verification, observation freshness, river-network evidence, and threshold applicability are separate dimensions. No Cauvery-wide flood-risk score is calculated; risk remains not assessed unless location-specific evidence and validated rules support it.</p>
    </section>
  );
}

export function HydrologicalReadinessPanel({ report }) {
  if (!report) return null;
  const gauges = report.geography?.gauge_connection_status || [];
  const labels = {
    proximity_only: "Proximity only · connection unverified",
    same_basin_unverified_connection: "Same basin · river connection unverified",
    verified_connection: "Verified river connection",
    insufficient_geographic_data: "Insufficient geographic data",
  };
  return (
    <section className="panel hydro-readiness-panel">
      <div className="panel-heading">
        <div><h3>Hydrological validation readiness</h3><p>Historical evidence and gauge relevance for this selected point</p></div>
        <span className="assessment-badge unassessed">{report.validation_status === "ready" ? "Ready to evaluate" : "Not ready"}</span>
      </div>
      <div className="hydro-readiness-body">
        {gauges.length ? gauges.map((gauge, index) => (
          <div className="hydro-gauge-row" key={`${gauge.station}-${index}`}>
            <strong>{gauge.station || "CWC gauge candidate"}</strong>
            <span>{gauge.distance_km == null ? "Distance unavailable" : `${gauge.distance_km} km away`}</span>
            <span>{gauge.freshness_status === "fresh" ? "Fresh observation" : gauge.freshness_status === "stale" ? "Stale observation" : "Freshness unknown"}</span>
            <b>{labels[gauge.connection?.status] || "Connection unavailable"}</b>
          </div>
        )) : <p className="official-empty">No gauge candidate is available for this point.</p>}
        <div className="hydro-history-status"><b>Historical gauge sources:</b> CWC/NWDP records cataloged (1961–2025), not yet ingested · <b>Flood history:</b> CWC aggregated affected-area assessment (1986–2022), not event labels</div>
        <p className="official-caveat">No real historical validation has been completed. Current telemetry snapshots and official alert feeds are not event labels.</p>
        <details className="assessment-details">
          <summary>What is missing ({(report.missing_evidence || []).length})</summary>
          <ul>{(report.missing_evidence || []).map((item) => <li key={item}>{item}</li>)}</ul>
        </details>
      </div>
    </section>
  );
}



export function SelectedPointMap({ selectedPosition, onSelect }) {
  return (
    <section className="panel map-panel">
      <div className="panel-heading"><div><h3>Monitoring location</h3><p>Click the map to select a point for location evidence</p></div><span className="map-tag">OpenStreetMap</span></div>
      <div className="map-wrap">
        <MapContainer center={selectedPosition} zoom={12} scrollWheelZoom className="leaflet-map">
          <MapClickHandler onSelect={onSelect} />
          <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
          <CircleMarker center={selectedPosition} radius={22} pathOptions={{ color: "#2f91bd", fillColor: "#63b9df", fillOpacity: 0.12, weight: 1 }} />
          <Marker position={selectedPosition} icon={markerIcon}><Popup><strong>FloodSense monitoring point</strong><br />{selectedPosition[0].toFixed(6)}° latitude<br />{selectedPosition[1].toFixed(6)}° longitude<br /><small>Point location only. No flood-risk classification.</small></Popup></Marker>
          <RecenterButton center={selectedPosition} />
        </MapContainer>
      </div>
      <div className="map-legend"><span><i className="legend-dot" /> Selected monitoring point</span><span>Coordinates: {selectedPosition[0].toFixed(6)}, {selectedPosition[1].toFixed(6)}</span></div>
    </section>
  );
}

export function AskFloodSensePanel() {
  const { selectedPosition, messages, assistantLoading: loading, askFloodSense, clearAssistantChat } = useDashboard();
  const [question, setQuestion] = useState("");
  const submit = (event) => { event.preventDefault(); if (question.trim()) { askFloodSense(question); setQuestion(""); } };
  const suggestions = [
    { label: "Get rainfall + elevation", prompt: "Retrieve both current rainfall measurements and terrain elevation. Include units, observation timestamps, location, and sources. Do not infer flood risk from these measurements alone." },
    { label: "Why is risk not assessed?", prompt: "Explain what ‘flood risk not assessed’ means in FloodSense. Use only the information supplied here: this dashboard can summarize rainfall and terrain measurements, but does not have enough verified local evidence or a validated model for a flood-risk category. Explain that missing data is not an all-clear." },
    { label: "What can rainfall tell me?", prompt: "Explain what rainfall measurements can and cannot tell me about flooding. Keep the explanation concise, distinguish observed data from forecasts, and do not infer that a location is safe or in danger from rainfall alone." },
  ];
  return (
    <section className="panel assistant-panel">
      <div className="panel-heading assistant-heading"><div><h3>Ask FloodSense</h3><p>Understand available measurements</p></div><div className="assistant-heading-actions">{messages.length > 0 && <button type="button" className="assistant-clear" onClick={clearAssistantChat} disabled={loading}>New chat</button>}<span className="assistant-avatar" aria-hidden="true">🌧</span></div></div>
      <div className="assistant-body">
        {!messages.length ? <div className="assistant-empty"><div className="empty-icon" aria-hidden="true">✳</div><h4>How can I help?</h4><p>Ask about rainfall or elevation for the selected map point, or choose a question below.</p><div className="assistant-suggestions">{suggestions.map((item) => <button type="button" key={item.label} onClick={() => askFloodSense(item.prompt)} disabled={loading}>{item.label}<span aria-hidden="true">↗</span></button>)}</div></div> : <div className="message-list" aria-live="polite">{messages.map((message, index) => <div className={`chat-message ${message.role}`} key={`${message.role}-${index}`}><b>{message.role === "user" ? "You" : message.role === "error" ? "Request error" : "FloodSense"}</b><div className="chat-message-content">{message.role === "assistant" ? <ReactMarkdown>{message.content}</ReactMarkdown> : message.content}</div></div>)}{loading && <div className="thinking" role="status"><span /><span /><span /> Checking available data…</div>}</div>}
      </div>
      <p className="assistant-boundary">Awareness support only. It explains measurements; it does not issue official alerts or determine whether a place is safe.</p>
      <form className="chat-composer" onSubmit={submit}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) submit(event); }} placeholder={`Ask about this point: ${selectedPosition[0].toFixed(4)}, ${selectedPosition[1].toFixed(4)}`} rows={2} maxLength={1000} disabled={loading} aria-label="Ask FloodSense" /><div className="composer-actions"><span>Enter to send · Shift+Enter for new line</span><button type="submit" disabled={loading || !question.trim()} aria-label="Send question">{loading ? "…" : "↑"}</button></div></form>
    </section>
  );
}
