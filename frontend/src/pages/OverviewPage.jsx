import { Link } from "react-router-dom";
import { AskFloodSensePanel, OfficialEvidencePanel, ProviderHealthPanel } from "../components/DashboardPanels";
import PageHeader from "../components/PageHeader";
import { useDashboard } from "../context/DashboardContext";

export default function OverviewPage() {
  const { monitorData, monitorStatus, monitorError, requestMonitoring, monitorRefresh, providerHealthRefresh } = useDashboard();
  const rainfall = monitorData?.rainfall?.current;
  const elevation = monitorData?.elevation?.elevation_m;
  const assessment = monitorData?.assessment;
  return <div className="page-stack">
    <PageHeader eyebrow="CAUVERY BASIN · LIVE OVERVIEW" title="Flood awareness overview" description="A concise view of available official alerts, data-source health, and current monitoring evidence." action={<button type="button" className="monitor-button" onClick={requestMonitoring} disabled={monitorStatus === "loading"}>{monitorStatus === "loading" ? "Refreshing evidence…" : "↻ Refresh evidence"}</button>} />
    <section className="summary-grid" aria-label="Current monitoring summary">
      <article className="summary-card"><span>Current rainfall</span><strong>{monitorStatus === "loading" ? "Loading" : rainfall?.rain_mm == null ? "Unavailable" : `${rainfall.rain_mm} mm`}</strong><small>{rainfall?.time_utc ? `Observed ${rainfall.time_utc} UTC` : monitorData?.rainfall?.error || "No measurement available"}</small></article>
      <article className="summary-card"><span>Terrain elevation</span><strong>{monitorStatus === "loading" ? "Loading" : elevation == null ? "Unavailable" : `${elevation} m`}</strong><small>{monitorData?.elevation?.source || monitorData?.elevation?.error || "No measurement available"}</small></article>
      <article className="summary-card summary-caution"><span>Flood-risk status</span><strong>{assessment?.risk_category || "Not assessed"}</strong><small>{assessment?.explanation || "Missing data is not evidence of safety. Risk remains unassessed without sufficient local evidence and validated rules."}</small></article>
    </section>
    {monitorError && <p className="state-panel state-error" role="alert">Monitoring evidence could not be retrieved: {monitorError}</p>}
    <section className="quick-links" aria-label="Detailed pages"><Link to="/monitoring"><span>⌖</span><b>Live monitoring</b><small>Stations, map, observations, rainfall and elevation</small></Link><Link to="/assessment"><span>✓</span><b>Assessment readiness</b><small>Eligibility gates, missing evidence and conflicts</small></Link><Link to="/validation"><span>◷</span><b>Historical validation</b><small>Event links, coverage and predictive-evaluation blockers</small></Link></section>
    <OfficialEvidencePanel alerts={monitorData?.official_alerts} gauges={monitorData?.river_gauges} loading={monitorStatus === "loading"} />
    <ProviderHealthPanel refreshSignal={`${monitorRefresh}-${monitorStatus}-${providerHealthRefresh}`} />
    <AskFloodSensePanel />
    <p className="safety-note">Official alerts remain distinct from model output. This dashboard does not replace emergency instructions from local authorities.</p>
  </div>;
}
