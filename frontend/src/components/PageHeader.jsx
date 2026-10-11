export default function PageHeader({ eyebrow = "FLOODSENSE", title, description, action }) {
  return <section className="route-heading"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1>{description && <p>{description}</p>}</div>{action}</section>;
}
