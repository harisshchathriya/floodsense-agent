import { OfficialEvidencePanel } from "../components/DashboardPanels";
import PageHeader from "../components/PageHeader";
import { useDashboard } from "../context/DashboardContext";

export default function AlertsPage() {
  const { monitorData, monitorStatus, monitorError, requestMonitoring } = useDashboard();
  return <div className="page-stack">
    <PageHeader eyebrow="OFFICIAL SOURCES" title="Alerts and river reports" description="Published alert messages with issuer, severity, validity, timestamps, and source links. These are separate from FloodSense assessment output." action={<button type="button" className="monitor-button" onClick={requestMonitoring} disabled={monitorStatus === "loading"}>{monitorStatus === "loading" ? "Checking sources…" : "↻ Check official sources"}</button>} />
    {monitorError && <p className="state-panel state-error" role="alert">The latest source refresh failed: {monitorError}. Any previously received report is shown only if available.</p>}
    <OfficialEvidencePanel alerts={monitorData?.official_alerts} gauges={monitorData?.river_gauges} loading={monitorStatus === "loading"} />
    <p className="safety-note">A missing or unavailable alert feed is not an all-clear. Follow instructions from local authorities and emergency services.</p>
  </div>;
}
