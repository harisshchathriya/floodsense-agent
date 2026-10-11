/* eslint react-refresh/only-export-components: off */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { apiGet } from "../api";

const DashboardContext = createContext(null);

export function DashboardProvider({ children }) {
  const [selectedPosition, setSelectedPosition] = useState([10.7905, 78.7047]);
  const [selectedStationId, setSelectedStationId] = useState("musiri");
  const [monitorData, setMonitorData] = useState(null);
  const [monitorStatus, setMonitorStatus] = useState("loading");
  const [monitorError, setMonitorError] = useState("");
  const [monitorRefresh, setMonitorRefresh] = useState(0);
  const [providerHealthRefresh, setProviderHealthRefresh] = useState(0);
  const [messages, setMessages] = useState([]);
  const [assistantLoading, setAssistantLoading] = useState(false);
  const requestId = useRef(0);

  const refreshProviderHealth = useCallback(() => setProviderHealthRefresh((value) => value + 1), []);
  const requestMonitoring = useCallback(() => setMonitorRefresh((value) => value + 1), []);

  useEffect(() => {
    const currentRequest = ++requestId.current;
    const controller = new AbortController();
    setMonitorData(null);
    setMonitorError("");
    setMonitorStatus("loading");
    const timer = window.setTimeout(async () => {
      try {
        const data = await apiGet("/assess-risk", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ latitude: Number(selectedPosition[0]), longitude: Number(selectedPosition[1]) }),
          signal: controller.signal,
        });
        if (currentRequest !== requestId.current) return;
        setMonitorData(data);
        setMonitorStatus(data.data_status);
      } catch (error) {
        if (currentRequest !== requestId.current || error.name === "AbortError") return;
        setMonitorError(error.message || "Could not connect to the FloodSense API.");
        setMonitorStatus("failed");
      }
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [selectedPosition, monitorRefresh]);

  const askFloodSense = useCallback(async (text) => {
    const userQuestion = text.trim();
    if (!userQuestion || assistantLoading) return;
    const prompt = `${userQuestion}\n\nSelected monitoring coordinates: latitude ${selectedPosition[0].toFixed(6)}, longitude ${selectedPosition[1].toFixed(6)}. Use these exact coordinates for location-dependent data retrieval. State the selected coordinates in your response. Do not assume the location is central Tiruchirappalli unless the coordinates match.`;
    setMessages((old) => [...old, { role: "user", content: userQuestion }]);
    setAssistantLoading(true);
    try {
      const data = await apiGet("/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: prompt, latitude: Number(selectedPosition[0]), longitude: Number(selectedPosition[1]) }),
      });
      setMessages((old) => [...old, { role: "assistant", content: data.answer }]);
    } catch (error) {
      setMessages((old) => [...old, { role: "error", content: error.message || "Could not connect to the FloodSense API." }]);
    } finally { setAssistantLoading(false); }
  }, [assistantLoading, selectedPosition]);

  const clearAssistantChat = useCallback(() => {
    if (!assistantLoading) setMessages([]);
  }, [assistantLoading]);

  const value = {
    selectedPosition, setSelectedPosition, selectedStationId, setSelectedStationId,
    monitorData, monitorStatus, monitorError, requestMonitoring, monitorRefresh,
    providerHealthRefresh, refreshProviderHealth, messages, assistantLoading, askFloodSense, clearAssistantChat,
  };
  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

export function useDashboard() {
  const value = useContext(DashboardContext);
  if (!value) throw new Error("useDashboard must be used within DashboardProvider");
  return value;
}
