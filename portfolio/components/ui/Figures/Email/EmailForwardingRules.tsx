import React from "react";
import { useViewportAnimation } from "../useDiagramOptimizations";

/**
 * A replica of the iCloud Mail "Rules" settings modal — the one screen that
 * decides which senders leave the inbox, and to which agent-facing address.
 * Two destinations, two agents, two very different apertures. Hover a rule
 * to see where that mail ends up.
 */

type Destination = "receipts" | "ats";

interface Rule {
  from: string;
  to: Destination;
}

const ADDRESS: Record<Destination, string> = {
  receipts: "receipts@in.tylernorlund.com",
  ats: "ats@in.tylernorlund.com",
};

const RULES: Rule[] = [
  { from: "apple.com", to: "receipts" },
  { from: "doordash.com", to: "receipts" },
  { from: "amazon.com", to: "receipts" },
  { from: "venmo.com", to: "receipts" },
  { from: "paypal.com", to: "receipts" },
  { from: "squareup.com", to: "receipts" },
  { from: "toasttab.com", to: "receipts" },
  { from: "uber.com", to: "receipts" },
  { from: "airbnb.com", to: "receipts" },
  { from: "equinox.com", to: "receipts" },
  { from: "stripe.com", to: "receipts" },
  { from: "socalgas.com", to: "receipts" },
  { from: "oftendining.com", to: "receipts" },
  { from: "target.com", to: "receipts" },
  { from: "no-reply@greenhouse.io", to: "ats" },
  { from: "no-reply@us.greenhouse-mail.io", to: "ats" },
  { from: "no-reply@eu.greenhouse-mail.io", to: "ats" },
  { from: "no-reply@anz.greenhouse.io", to: "ats" },
  { from: "login@us.greenhouse-jobs.com", to: "ats" },
];

const SIDEBAR = [
  "Account",
  "Categories",
  "Auto-Reply",
  "Cleanup",
  "Rules",
  "Forwarding",
  "Mailbox Behavior",
  "Import Mail",
  "Privacy & Security",
  "Viewing",
  "Composing",
];

const DESTINATIONS: {
  key: Destination;
  agent: string;
  gets: string;
  keeps: string;
}[] = [
  {
    key: "receipts",
    agent: "Claude, through the read replica",
    gets: "parsed rows: merchant, date, cents, line items",
    keeps: "the raw email stays on my Mac and ages out of S3 in 30 days",
  },
  {
    key: "ats",
    agent: "Grok Bot, through the verification-code reader",
    gets: "one eight-character code, for one hour",
    keeps: "subject and body never leave S3; the raw email expires in a day",
  },
];

/* The modal is always dark, like the real thing, so it reads as a
 * screenshot in both site themes. */
const STYLE = `
.efr { --efr-bg:#1d1d1f; --efr-side:#232325; --efr-row:#2b2b2d; --efr-line:#3a3a3c;
       --efr-text:#f2f2f4; --efr-muted:#9b9ba1; --efr-link:#5aa3ff; --efr-active:#2f2f33; }
.efr-modal { display:grid; grid-template-columns: 180px minmax(0,1fr); background:var(--efr-bg);
  color:var(--efr-text); border-radius:14px; overflow:hidden; box-shadow:0 12px 40px rgba(0,0,0,.45);
  font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size:14px; line-height:1.35; }
.efr-side { background:var(--efr-side); padding:18px 12px 14px; }
.efr-close { width:14px; height:14px; margin:0 0 14px 4px; color:var(--efr-muted); font-size:18px; line-height:14px; }
.efr-side ul { list-style:none; margin:0; padding:0; }
.efr-side li { display:flex; align-items:center; gap:8px; padding:6px 10px; border-radius:8px; color:var(--efr-text); }
.efr-side li[data-active="true"] { background:var(--efr-active); }
.efr-side li i { width:14px; height:14px; border-radius:50%; border:1.5px solid var(--efr-link); flex:none; }
.efr-main { padding:18px 18px 14px; min-width:0; display:flex; flex-direction:column; }
.efr-head { display:flex; justify-content:space-between; align-items:baseline; border-bottom:1px solid var(--efr-line); padding-bottom:8px; }
.efr-head h3 { margin:0; font-size:17px; font-weight:700; color:var(--efr-text); }
.efr-head span { color:var(--efr-link); font-size:22px; line-height:1; }
.efr-list { list-style:none; margin:12px 0 0; padding:0 4px 0 0; flex:1 1 auto; min-height:0; max-height:470px; overflow-y:auto; }
.efr-list li { background:var(--efr-row); border:1px solid var(--efr-line); border-radius:9px; padding:9px 12px;
  margin-bottom:8px; display:flex; justify-content:space-between; gap:12px; align-items:center;
  opacity:0; transform:translateY(6px); transition:opacity .35s ease, transform .35s ease, border-color .15s ease; cursor:default; }
.efr[data-in="true"] .efr-list li { opacity:1; transform:none; }
.efr-list li[data-hot="true"] { border-color:var(--efr-link); }
.efr-list li b { font-weight:400; color:var(--efr-link); overflow-wrap:anywhere; }
.efr-list li span { color:var(--efr-muted); flex:none; letter-spacing:-2px; }
.efr-note { border-top:1px solid var(--efr-line); margin-top:auto; padding-top:10px; color:var(--efr-muted); font-size:12.5px; }
.efr-dest { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:14px; }
.efr-dest div { border:1.5px solid rgba(var(--text-color-rgb),.18); border-radius:10px; padding:10px 12px; font-size:13px;
  color:var(--text-color); transition:border-color .15s ease, background-color .15s ease; }
.efr-dest div[data-hot="true"] { border-color:var(--color-blue); background:rgba(var(--color-blue-rgb),.08); }
.efr-dest code { font-size:12px; }
.efr-dest p { margin:4px 0 0; opacity:.8; }
@media (max-width: 640px) {
  .efr-modal { grid-template-columns: 1fr; }
  .efr-side { display:none; }
  .efr-list { max-height:320px; }
  .efr-dest { grid-template-columns: 1fr; }
}
`;

const EmailForwardingRules: React.FC = () => {
  const { containerRef, shouldAnimate } = useViewportAnimation(false);
  const [hot, setHot] = React.useState<Destination | null>(null);

  return (
    <div
      ref={containerRef}
      className="efr"
      data-in={shouldAnimate ? "true" : "false"}
      style={{ maxWidth: 720, margin: "1em auto", width: "100%" }}
    >
      <style>{STYLE}</style>
      <div
        className="efr-modal"
        role="figure"
        aria-label="iCloud Mail rules: each rule forwards one sender to one agent-facing address"
      >
        <aside className="efr-side" aria-hidden="true">
          <div className="efr-close">×</div>
          <ul>
            {SIDEBAR.map((item) => (
              <li key={item} data-active={item === "Rules" ? "true" : undefined}>
                <i />
                {item}
              </li>
            ))}
          </ul>
        </aside>
        <div className="efr-main">
          <div className="efr-head">
            <h3>Rules</h3>
            <span aria-hidden="true">+</span>
          </div>
          <ul className="efr-list" onMouseLeave={() => setHot(null)}>
            {RULES.map((rule, i) => (
              <li
                key={rule.from}
                data-hot={hot === rule.to ? "true" : undefined}
                style={{ transitionDelay: `${Math.min(i, 12) * 45}ms` }}
                onMouseEnter={() => setHot(rule.to)}
                onFocus={() => setHot(rule.to)}
                tabIndex={0}
              >
                <div>
                  Forward messages from <b>{rule.from}</b> to{" "}
                  <b>{ADDRESS[rule.to]}</b>
                </div>
                <span aria-hidden="true">≡</span>
              </li>
            ))}
          </ul>
          <div className="efr-note">
            Rules are applied as messages arrive. Only the first matching rule
            will be applied per message.
          </div>
        </div>
      </div>
      <div className="efr-dest">
        {DESTINATIONS.map((d) => (
          <div key={d.key} data-hot={hot === d.key ? "true" : undefined}>
            <code>{ADDRESS[d.key]}</code>
            <p>
              <strong>{d.agent}</strong> gets {d.gets}.
            </p>
            <p>And {d.keeps}.</p>
          </div>
        ))}
      </div>
    </div>
  );
};

export default EmailForwardingRules;
