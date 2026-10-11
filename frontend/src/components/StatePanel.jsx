export default function StatePanel({ kind = "empty", title, detail, action }) {
  return <section className={`state-panel state-${kind}`} role={kind === "error" ? "alert" : undefined}><strong>{title}</strong>{detail && <p>{detail}</p>}{action}</section>;
}
