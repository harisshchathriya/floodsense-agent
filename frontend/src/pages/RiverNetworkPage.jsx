import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { CauveryStationExplorer } from "../components/DashboardPanels";
import PageHeader from "../components/PageHeader";
import StatePanel from "../components/StatePanel";
import { useDashboard } from "../context/DashboardContext";

const statusLabel = { verified_match: "Verified reach match", candidate_match: "Candidate match", conflicting_metadata: "Conflicting metadata", unmatched: "Unmatched", insufficient_network_data: "Network unavailable" };
export default function RiverNetworkPage() {
  const { selectedStationId, setSelectedStationId, refreshProviderHealth } = useDashboard();
  const [status, setStatus] = useState(null);
  const [stations, setStations] = useState([]);
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    Promise.all([apiGet("/river-network/status"), apiGet("/river-network/stations")]).then(([network, matches]) => {
      if (active) { setStatus(network); setStations(matches.stations || []); setState("loaded"); }
    }).catch((cause) => { if (active) { setError(cause.message); setState("error"); } });
    return () => { active = false; };
  }, [retry]);
  const count = (key) => status?.station_match_counts?.[key] ?? 0;
  return <div className="page-stack">
    <PageHeader eyebrow="CAUVERY NETWORK INTELLIGENCE" title="River network" description="Official river geometry and evidence-qualified station-to-reach matches. Reach matches do not establish a directed connection between gauges." action={<button className="musiri-refresh" type="button" onClick={() => { setError(""); setState("loading"); setRetry((value) => value + 1); }}>↻ Refresh network</button>} />
    {state === "loading" && <StatePanel kind="loading" title="Loading official network layers" detail="Reading CWC/NWDP-derived river geometry and station match evidence." />}
    {error && <StatePanel kind="error" title="Network summary unavailable" detail={error} />}
    {status && <section className="network-summary-grid"><article><span>River reaches</span><strong>{status.river_reach_count ?? "—"}</strong></article><article><span>Verified coordinate-to-reach</span><strong>{count("verified_match")}</strong></article><article><span>Candidate reaches</span><strong>{count("candidate_match")}</strong></article><article><span>Metadata conflicts</span><strong>{count("conflicting_metadata")}</strong></article><article><span>Verified station connections</span><strong>{status.verified_station_connections ?? 0}</strong></article></section>}
    <CauveryStationExplorer onProviderQuery={refreshProviderHealth} selectedStationId={selectedStationId} onSelectedStationChange={setSelectedStationId} />
    <section className="panel evidence-panel"><div className="panel-heading"><div><h2>Station-to-reach evidence</h2><p>Coordinate and metadata match state returned by the backend</p></div><span className="map-tag">{stations.length} station records</span></div>
      {stations.length ? <div className="network-table-wrap"><table className="network-table"><thead><tr><th>Station</th><th>Match status</th><th>Candidate reach</th><th>Distance</th><th>Metadata conflicts / uncertainty</th></tr></thead><tbody>{stations.map((station) => <tr key={station.station_id}><td>{station.station_id}</td><td><span className={`network-status status-${station.match_status}`}>{statusLabel[station.match_status] || station.match_status}</span></td><td>{station.candidate_reach?.river_name || "—"}</td><td>{station.candidate_reach?.distance_m == null ? "—" : `${station.candidate_reach.distance_m} m`}</td><td>{station.known_conflicts?.map((item) => `${item.field}: ${item.status}`).join("; ") || station.uncertainty || "No additional uncertainty reported."}</td></tr>)}</tbody></table></div> : state === "loaded" ? <p className="official-empty">No station match records are available.</p> : null}
    </section>
    <p className="safety-note">The published layers do not provide verified flow direction or routable station topology. No upstream/downstream relationships are invented; proximity and same-basin labels remain insufficient for hydrological linkage.</p>
  </div>;
}
