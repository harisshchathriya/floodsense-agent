import { AskFloodSensePanel, CauveryStationExplorer, HydrologicalReadinessPanel, MusiriMonitoringPanel, OfficialEvidencePanel, ProviderHealthPanel, RainfallChart, RiskAssessmentPanel, SelectedPointMap } from "../components/DashboardPanels";
import PageHeader from "../components/PageHeader";
import { useDashboard } from "../context/DashboardContext";

export default function MonitoringPage() {
  const { selectedPosition, setSelectedPosition, selectedStationId, setSelectedStationId, monitorData, monitorStatus, monitorError, requestMonitoring, monitorRefresh, providerHealthRefresh, refreshProviderHealth } = useDashboard();
  return <div className="page-stack">
    <PageHeader eyebrow="LIVE MONITORING" title="Stations and local evidence" description="Select a map point or station to inspect source observations, freshness, metadata, and location evidence." action={<button type="button" className="monitor-button" onClick={requestMonitoring} disabled={monitorStatus === "loading"}>{monitorStatus === "loading" ? "Retrieving data…" : "↻ Refresh measurements"}</button>} />
    <SelectedPointMap selectedPosition={selectedPosition} onSelect={setSelectedPosition} />
    <RiskAssessmentPanel assessment={monitorData?.assessment} monitoringStatus={monitorStatus} error={monitorError} onRetry={requestMonitoring} />
    <section className="panel rainfall-panel"><div className="panel-heading"><div><h3>Hourly rainfall trend</h3><p>Observed and forecast rainfall · millimetres</p></div><span className="map-tag">{monitorData?.rainfall?.location ? `Weather grid ${Number(monitorData.rainfall.location.latitude).toFixed(4)}, ${Number(monitorData.rainfall.location.longitude).toFixed(4)}` : "UTC"}</span></div><RainfallChart records={monitorData?.rainfall?.hourly} cutoff={monitorData?.rainfall?.current?.time_utc} loading={monitorStatus === "loading"} error={monitorData?.rainfall?.error || monitorError} /></section>
    <HydrologicalReadinessPanel report={monitorData?.validation_readiness} />
    <CauveryStationExplorer onProviderQuery={refreshProviderHealth} selectedStationId={selectedStationId} onSelectedStationChange={setSelectedStationId} />
    <section className="content-grid monitoring-support-grid"><MusiriMonitoringPanel onProviderQuery={refreshProviderHealth} /><AskFloodSensePanel /></section>
    <ProviderHealthPanel refreshSignal={`${monitorRefresh}-${monitorStatus}-${providerHealthRefresh}`} />
    <OfficialEvidencePanel alerts={monitorData?.official_alerts} gauges={monitorData?.river_gauges} loading={monitorStatus === "loading"} />
    <p className="safety-note">Station proximity and shared river labels do not establish hydrological connection. Missing observations do not mean no flood danger; risk remains not assessed when evidence is insufficient.</p>
  </div>;
}
