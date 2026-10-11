import { BrowserRouter, Route, Routes } from "react-router-dom";
import AppLayout from "./components/AppLayout";
import { DashboardProvider } from "./context/DashboardContext";
import OverviewPage from "./pages/OverviewPage";
import MonitoringPage from "./pages/MonitoringPage";
import AlertsPage from "./pages/AlertsPage";
import AssessmentPage from "./pages/AssessmentPage";
import RiverNetworkPage from "./pages/RiverNetworkPage";
import HistoricalValidationPage from "./pages/HistoricalValidationPage";
import "./layout.css";

function NotFoundPage() {
  return <div className="page-stack"><section className="route-heading"><div><p className="eyebrow">FLOODSENSE</p><h1>Page not found</h1><p>Choose a page from the navigation to continue.</p></div></section></div>;
}

export default function App() {
  return <BrowserRouter><DashboardProvider><Routes><Route element={<AppLayout />}>
    <Route index element={<OverviewPage />} />
    <Route path="monitoring" element={<MonitoringPage />} />
    <Route path="alerts" element={<AlertsPage />} />
    <Route path="assessment" element={<AssessmentPage />} />
    <Route path="river-network" element={<RiverNetworkPage />} />
    <Route path="validation" element={<HistoricalValidationPage />} />
    <Route path="*" element={<NotFoundPage />} />
  </Route></Routes></DashboardProvider></BrowserRouter>;
}
