import { useEffect, useState } from "react";
import { apiGet } from "../api";
import PageHeader from "../components/PageHeader";
import StatePanel from "../components/StatePanel";
import { useDashboard } from "../context/DashboardContext";

const gates = [
  ["eligible_for_observation_summary", "Observation summary"],
  ["eligible_for_threshold_comparison", "Threshold comparison"],
  ["eligible_for_location_specific_assessment", "Location-specific assessment"],
];

export default function AssessmentPage() {
  const { selectedStationId, setSelectedStationId } = useDashboard();
  const [stations, setStations] = useState([]);
  const [readiness, setReadiness] = useState(null);
  const [stationsState, setStationsState] = useState("loading");
  const [readinessState, setReadinessState] = useState("loading");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    apiGet("/stations").then((data) => {
      if (!active) return;
      const rows = data.stations || [];
      setStations(rows);
      if (rows.length && !rows.some((item) => item.station_id === selectedStationId)) setSelectedStationId(rows[0].station_id);
      setStationsState("loaded");
    }).catch((cause) => { if (active) { setError(cause.message); setStationsState("error"); } });
    return () => { active = false; };
  }, [retry, selectedStationId, setSelectedStationId]);

  useEffect(() => {
    if (!selectedStationId) return undefined;
    let active = true;
    apiGet(`/assessment/readiness?station_id=${encodeURIComponent(selectedStationId)}`).then((data) => {
      if (active) { setReadiness(data); setReadinessState("loaded"); }
    }).catch((cause) => { if (active) { setError(cause.message); setReadinessState("error"); } });
    return () => { active = false; };
  }, [selectedStationId, retry]);

  const selected = stations.find((item) => item.station_id === selectedStationId);
  return <div className="page-stack">
    <PageHeader eyebrow="EVIDENCE GATES" title="Assessment readiness" description="Eligibility is reported separately for observation summaries, threshold comparisons, and location-specific assessment. These gates do not produce a flood-risk score." action={<label className="station-select-wrap">Selected station<select value={selectedStationId} onChange={(event) => { setReadiness(null); setReadinessState("loading"); setSelectedStationId(event.target.value); }} disabled={stationsState !== "loaded"}>{stations.map((station) => <option key={station.station_id} value={station.station_id}>{station.official_name?.value || station.station_id}</option>)}</select></label>} />
    {stationsState === "loading" && <StatePanel kind="loading" title="Loading station registry" detail="Reading registered source station names from the backend." />}
    {error && <StatePanel kind="error" title="Readiness evidence unavailable" detail={error} action={<button className="text-button" onClick={() => { setError(""); setStationsState("loading"); setReadinessState("loading"); setRetry((value) => value + 1); }}>Retry</button>} />}
    {readinessState === "loading" && <StatePanel kind="loading" title="Checking evidence gates" detail={`Retrieving readiness evidence for ${selected?.official_name?.value || selectedStationId}.`} />}
    {readiness && <>
      <section className="assessment-status-banner"><div><span>Flood-risk status</span><strong>{readiness.status || "Not assessed"}</strong></div><p>Readiness indicates which evidence summaries can be made. It does not establish safety or a flood-risk category.</p></section>
      <section className="gate-grid" aria-label="Eligibility gates">{gates.map(([key, label]) => { const gate = readiness.eligibility?.[key]; return <article className={`gate-card ${gate?.eligible ? "gate-eligible" : "gate-withheld"}`} key={key}><span>{label}</span><strong>{gate?.eligible ? "Eligible" : "Withheld"}</strong><p>{(gate?.reasons || []).join(" ") || (gate?.eligible ? "Required evidence is available." : "Eligibility evidence is unresolved.")}</p></article>; })}</section>
      <section className="panel evidence-panel"><div className="panel-heading"><div><h2>Station and observation evidence</h2><p>{readiness.selected_station?.official_name || selected?.official_name?.value || selectedStationId} · source identity and freshness are assessed independently</p></div><span className="assessment-badge unassessed">{readiness.latest_observation?.freshness_status || "Freshness unknown"}</span></div><div className="evidence-grid">
        <div><b>Station identity</b><span>{readiness.selected_station?.station_identity?.source_name_match || "Name match unknown"}</span><small>Official code: {readiness.selected_station?.station_identity?.official_station_code || "unknown"}</small></div>
        <div><b>Latest observation</b><span>{readiness.latest_observation?.value == null ? "Unavailable" : `${readiness.latest_observation.value} ${readiness.latest_observation.unit || ""}`}</span><small>{readiness.latest_observation?.observed_at || "Timestamp unknown"} · retrieved {readiness.latest_observation?.retrieved_at_utc || "unknown"}</small></div>
        <div><b>Threshold applicability</b><span>{readiness.threshold_evidence?.value == null ? "No applicable threshold" : `${readiness.threshold_evidence.value} ${readiness.threshold_evidence.unit || ""}`}</span><small>{readiness.threshold_evidence?.applicability_status || "Unknown"} · datum {readiness.threshold_evidence?.datum_status || "unknown"}</small></div>
        <div><b>River match</b><span>{readiness.river_network_evidence?.station_reach_match?.match_status || "Unknown"}</span><small>{readiness.river_network_evidence?.station_reach_match?.uncertainty || "No verified station connection is assumed."}</small></div>
        <div><b>Rainfall</b><span>{readiness.rainfall_evidence?.current?.rain_mm == null ? "Unavailable" : `${readiness.rainfall_evidence.current.rain_mm} mm`}</span><small>{readiness.rainfall_evidence?.current?.time_utc || "Observation time unknown"} · {readiness.rainfall_evidence?.source?.retrieved_at_utc || "source time unknown"}</small></div>
        <div><b>Elevation</b><span>{readiness.elevation_evidence?.elevation_m == null ? "Unavailable" : `${readiness.elevation_evidence.elevation_m} m`}</span><small>{readiness.elevation_evidence?.source?.retrieved_at_utc || "Lookup time unknown"}</small></div>
        <div><b>Official alert evidence</b><span>{readiness.official_alert_evidence?.status || "Unknown"}</span><small>{readiness.official_alert_evidence?.message || "Alert evidence is separate from assessment status."}</small></div>
        <div><b>Measurement datum</b><span>{readiness.selected_station?.reference_datum?.status || "Unknown"}</span><small>{readiness.selected_station?.reference_datum?.value || "Datum compatibility not established."}</small></div>
      </div></section>
      {(readiness.conflicts || []).length > 0 && <section className="panel evidence-panel"><h2>Conflicts</h2><ul>{readiness.conflicts.map((item, index) => <li key={`${item.field}-${index}`}><b>{item.field || "Metadata"}:</b> {item.note || item.status || JSON.stringify(item)}</li>)}</ul></section>}
      <section className="panel evidence-panel"><div className="panel-heading"><div><h2>Missing evidence and limitations</h2><p>Reasons the readiness gates may remain withheld</p></div><span className="map-tag">{readiness.missing_inputs?.length || 0} missing inputs</span></div><ul className="evidence-list">{(readiness.missing_inputs || []).map((item, index) => <li key={`${item.input}-${index}`}><b>{item.input}:</b> {item.reason}</li>)}{(readiness.limitations || []).map((item) => <li key={item}>{item}</li>)}</ul>{readiness.provenance?.length > 0 && <div className="source-links">{readiness.provenance.map((item, index) => item.url ? <a key={`${item.url}-${index}`} href={item.url} target="_blank" rel="noreferrer">{item.name || item.evidence || "Evidence source"}</a> : null)}</div>}</section>
    </>}
  </div>;
}
