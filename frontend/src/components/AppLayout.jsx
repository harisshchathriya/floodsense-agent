import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useDashboard } from "../context/DashboardContext";

const navigation = [
  ["/", "Overview", "◫"],
  ["/monitoring", "Live Monitoring", "⌖"],
  ["/alerts", "Alerts", "!"],
  ["/assessment", "Assessment Readiness", "✓"],
  ["/river-network", "River Network", "⌁"],
  ["/validation", "Historical Validation", "◷"],
];
const pageTitles = Object.fromEntries(navigation.map(([path, title]) => [path, title]));

export default function AppLayout() {
  const location = useLocation();
  const { selectedPosition, monitorStatus } = useDashboard();
  const currentTitle = pageTitles[location.pathname] || "Overview";
  const backendLabel = monitorStatus === "loading" ? "Checking backend" : monitorStatus === "failed" ? "Backend unavailable" : "Backend responded";
  const backendTone = monitorStatus === "failed" ? "status-down" : monitorStatus === "loading" ? "status-pending" : "status-up";

  return (
    <div className="app-layout">
      <aside className="app-sidebar">
        <Link to="/" className="app-brand" aria-label="FloodSense overview">
          <span className="app-brand-mark" aria-hidden="true">🌧</span>
          <span><strong>FloodSense</strong><small>River intelligence</small></span>
        </Link>
        <p className="sidebar-section-label">WORKSPACE</p>
        <nav className="primary-navigation" aria-label="Primary navigation">
          {navigation.map(([path, title, icon]) => <NavLink key={path} to={path} end={path === "/"} className={({ isActive }) => `navigation-link${isActive ? " active" : ""}`}><span className="navigation-icon" aria-hidden="true">{icon}</span><span>{title}</span></NavLink>)}
        </nav>
        <div className="sidebar-status"><span className={`status-indicator ${backendTone}`} /><div><b>{backendLabel}</b><small>API · 127.0.0.1:8000</small></div></div>
        <p className="sidebar-disclaimer">Awareness support only. Use official local alerts and emergency guidance for safety decisions.</p>
      </aside>

      <div className="app-main-column">
        <header className="app-topbar">
          <div className="topbar-title"><span className="muted">FloodSense</span><span aria-hidden="true">/</span><strong>{currentTitle}</strong></div>
          <div className="topbar-context"><span className={`topbar-status ${backendTone}`}><i />{monitorStatus === "success" ? "Data connected" : monitorStatus === "partial" ? "Partial data" : backendLabel}</span><span className="location-chip">⌖ {selectedPosition[0].toFixed(4)}, {selectedPosition[1].toFixed(4)}</span></div>
        </header>
        <nav className="mobile-navigation" aria-label="Mobile navigation">
          {navigation.map(([path, title, icon]) => <NavLink key={path} to={path} end={path === "/"} aria-label={title} title={title} className={({ isActive }) => `mobile-nav-link${isActive ? " active" : ""}`}><span aria-hidden="true">{icon}</span><small>{title.split(" ")[0]}</small></NavLink>)}
        </nav>
        <main className="route-content" key={location.pathname}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
