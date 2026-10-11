import { useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  CircleMarker,
  useMap,
  useMapEvents,
} from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./App.css";

const API_URL = "http://127.0.0.1:8000";
const TRICHY = [10.7905, 78.7047];

const prompts = {
  rainfall:
    "Retrieve the current rainfall measurements for central Tiruchirappalli. Include units, observation timestamp, location, and source. Do not infer flood risk from rainfall alone.",
  elevation:
    "Retrieve the terrain elevation for central Tiruchirappalli. Include metres, location, timestamp if available, and source. Explain that elevation alone does not establish flood risk.",
  combined:
    "Retrieve both current rainfall measurements and terrain elevation for central Tiruchirappalli. Include the values, units, timestamps, and sources. Do not infer a flood-risk level from these measurements alone.",
};

const markerIcon = L.divIcon({
  className: "floodsense-map-marker",
  html: '<span class="map-marker-pulse"><span></span></span>',
  iconSize: [26, 26],
  iconAnchor: [13, 13],
});

function MapClickHandler({ onSelect }) {
  useMapEvents({
    click(event) {
      onSelect([event.latlng.lat, event.latlng.lng]);
    },
  });
  return null;
}

function RecenterButton({ center }) {
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

function App() {
  const [selectedPosition, setSelectedPosition] = useState(TRICHY);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);

  async function askFloodSense(text) {
    const userQuestion = text.trim();
    if (!userQuestion || loading) return;
    const q = `${userQuestion}\n\nSelected monitoring coordinates: latitude ${selectedPosition[0].toFixed(6)}, longitude ${selectedPosition[1].toFixed(6)}. Use these exact coordinates for location-dependent data retrieval. State the selected coordinates in your response. Do not assume the location is central Tiruchirappalli unless the coordinates match.`;

    setMessages((old) => [...old, { role: "user", content: userQuestion }]);
    setQuestion("");
    setLoading(true);

    try {
      const response = await fetch(`${API_URL}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || `Request failed (${response.status})`);
      }
      setMessages((old) => [...old, { role: "assistant", content: data.answer }]);
    } catch (error) {
      setMessages((old) => [
        ...old,
        {
          role: "error",
          content: error.message || "Could not connect to the FloodSense API.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function submitQuestion(event) {
    event.preventDefault();
    askFloodSense(question);
  }

  function newConversation() {
    if (!loading) {
      setMessages([]);
      setQuestion("");
    }
  }

  return (
    <div className="dashboard-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-symbol">🌧</div>
          <div>
            <h1>FloodSense</h1>
            <p>Urban flood awareness</p>
          </div>
        </div>

        <button className="new-chat" onClick={newConversation} disabled={loading}>
          <span>＋</span> New conversation
        </button>

        <p className="sidebar-label">WORKSPACE</p>
        <div className="nav-item selected"><span>▦</span> Overview</div>
        <div className="nav-item"><span>⌖</span> Monitoring map</div>
        <div className="nav-item"><span>☷</span> Ask FloodSense</div>

        <div className="sidebar-bottom">
          <div className="api-status"><span className="green-dot" /><div><b>API configured</b><small>Backend: localhost:8000</small></div></div>
          <p>Awareness support only. Not an official emergency alert service.</p>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div><span className="muted">Workspace</span><span className="slash">/</span><b>Overview</b></div>
          <span className="location-chip">⌖ Selected point: {selectedPosition[0].toFixed(5)}, {selectedPosition[1].toFixed(5)}</span>
        </header>

        <div className="page-content">
          <section className="page-heading">
            <div>
              <p className="eyebrow">ENVIRONMENTAL MONITORING</p>
              <h2>Flood awareness dashboard</h2>
              <p className="heading-subtitle">Explore the monitoring location and query available rainfall and terrain data.</p>
            </div>
            <div className="data-source-chip"><span className="green-dot" /> Data tools configured</div>
          </section>

          <section className="metric-grid">
            <article className="metric-card">
              <div className="metric-top"><span>Rainfall observations</span><span className="metric-icon blue">☂</span></div>
              <strong>Live query</strong>
              <p>Retrieve precipitation and rain measurements</p>
              <button onClick={() => askFloodSense(prompts.rainfall)} disabled={loading}>Check rainfall <span>↗</span></button>
            </article>
            <article className="metric-card">
              <div className="metric-top"><span>Terrain elevation</span><span className="metric-icon teal">⌁</span></div>
              <strong>Point estimate</strong>
              <p>Retrieve elevation from the configured data source</p>
              <button onClick={() => askFloodSense(prompts.elevation)} disabled={loading}>Check elevation <span>↗</span></button>
            </article>
            <article className="metric-card">
              <div className="metric-top"><span>Flood-risk status</span><span className="metric-icon amber">ⓘ</span></div>
              <strong className="not-assessed">Not assessed</strong>
              <p>Available measurements are not a validated risk model</p>
              <span className="status-caption">Additional data required</span>
            </article>
          </section>

          <section className="content-grid">
            <article className="panel map-panel">
              <div className="panel-heading">
                <div><h3>Monitoring map</h3><p>Click anywhere on the map to select a monitoring point</p></div>
                <span className="map-tag">OSM map tiles</span>
              </div>
              <div className="map-wrap">
                <MapContainer center={TRICHY} zoom={12} scrollWheelZoom={true} className="leaflet-map">
                  <MapClickHandler onSelect={setSelectedPosition} />
                  <TileLayer
                    attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                  />
                  <CircleMarker
                    center={selectedPosition}
                    radius={22}
                    pathOptions={{ color: "#2f91bd", fillColor: "#63b9df", fillOpacity: 0.12, weight: 1 }}
                  />
                  <Marker position={selectedPosition} icon={markerIcon}>
                    <Popup>
                      <strong>FloodSense monitoring point</strong><br />
                      {selectedPosition[0].toFixed(6)}° latitude<br />
                      {selectedPosition[1].toFixed(6)}° longitude<br />
                      <small>Point location only. No flood-risk classification.</small>
                    </Popup>
                  </Marker>
                  <RecenterButton center={selectedPosition} />
                </MapContainer>
              </div>
              <div className="map-legend">
                <span><i className="legend-dot" /> Selected monitoring point</span>
                <span>Coordinates: {selectedPosition[0].toFixed(6)}, {selectedPosition[1].toFixed(6)}</span>
              </div>
            </article>

            <article className="panel assistant-panel">
              <div className="panel-heading assistant-heading">
                <div><h3>Ask FloodSense</h3><p>Rainfall and terrain data assistant</p></div>
                <span className="assistant-avatar">🌧</span>
              </div>

              <div className="assistant-body">
                {messages.length === 0 ? (
                  <div className="assistant-empty">
                    <div className="empty-icon">✳</div>
                    <h4>What would you like to know?</h4>
                    <p>Ask the agent to retrieve available measurements for the mapped location.</p>
                    <button onClick={() => askFloodSense(prompts.combined)} disabled={loading}>Get rainfall + elevation <span>↗</span></button>
                  </div>
                ) : (
                  <div className="message-list">
                    {messages.map((message, index) => (
                      <div className={`chat-message ${message.role}`} key={`${message.role}-${index}`}>
                        <b>{message.role === "user" ? "You" : message.role === "error" ? "Request error" : "FloodSense"}</b>
                        <div className="chat-message-content">
                          {message.role === "assistant" ? <ReactMarkdown>{message.content}</ReactMarkdown> : message.content}
                        </div>
                      </div>
                    ))}
                    {loading && <div className="thinking"><span /><span /><span /> Checking available data…</div>}
                  </div>
                )}
              </div>

              <form className="chat-composer" onSubmit={submitQuestion}>
                <textarea
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                      event.preventDefault();
                      submitQuestion(event);
                    }
                  }}
                  placeholder="Ask about rainfall or elevation…"
                  rows={2}
                  maxLength={1000}
                  disabled={loading}
                  aria-label="Ask FloodSense"
                />
                <div className="composer-actions">
                  <span>Enter to send · Shift+Enter for new line</span>
                  <button type="submit" disabled={loading || !question.trim()} aria-label="Send question">{loading ? "…" : "↑"}</button>
                </div>
              </form>
            </article>
          </section>

          <div className="disclaimer"><span>ⓘ</span> This map shows a configured point, not a flood boundary. Flood risk is not calculated. Use official local alerts and emergency guidance for safety decisions.</div>
        </div>
      </main>
    </div>
  );
}

export default App;
