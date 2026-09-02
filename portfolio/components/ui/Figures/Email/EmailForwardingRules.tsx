import React from "react";
import { useViewportAnimation } from "../useDiagramOptimizations";
import { GLYPH_BASELINE, GLYPH_VIEWBOX, ICLOUD_GLYPHS } from "./icloudGlyphs";

/** One SF Symbol from the real modal, sized like iCloud sizes it. */
const Glyph: React.FC<{ name: keyof typeof ICLOUD_GLYPHS; size?: number }> = ({
  name,
  size = 16,
}) => {
  const g = ICLOUD_GLYPHS[name];
  return (
    <svg
      viewBox={g.viewBox ?? GLYPH_VIEWBOX}
      height={size}
      width={size * 1.7}
      aria-hidden="true"
      focusable="false"
      style={{ display: "block", flex: "none" }}
    >
      <g transform={`translate(${g.tx} ${GLYPH_BASELINE})`}>
        <path d={g.d} fill="currentColor" />
      </g>
    </svg>
  );
};

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

/* In iCloud's order, as of 2026-09-02. */
const RULES: Rule[] = [
  ...[
    "doordash.com", "amazon.com", "apple.com", "paypal.com", "venmo.com",
    "uber.com", "toasttab.com", "squareup.com", "chownow.com", "equinox.com",
    "github.com", "costco.com", "airbnb.com", "chase.com", "socalgas.com",
    "scewebservices.com", "stripe.com", "digitalocean.com", "ebay.com",
    "oftendining.com", "target.com",
  ].map((from) => ({ from, to: "receipts" as const })),
  ...[
    "no-reply@greenhouse.io", "no-reply@us.greenhouse-mail.io",
    "no-reply@eu.greenhouse-mail.io", "no-reply@anz.greenhouse.io",
    "login@us.greenhouse-jobs.com",
  ].map((from) => ({ from, to: "ats" as const })),
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

/* Two palettes, both sampled from icloud.com's Settings modal. The site
 * switches theme with prefers-color-scheme (see globals.css), so we do too. */
const STYLE = `
.efr { --efr-bg:#ffffff; --efr-side:#f5f5f7; --efr-row:#f5f5f7; --efr-line:#e3e3e8;
       --efr-text:#1d1d1f; --efr-muted:#6e6e73; --efr-link:#0a7aff; --efr-active:#e5e5ea;
       --efr-shadow:0 18px 50px rgba(0,0,0,.22), 0 0 0 1px rgba(0,0,0,.06); }
@media (prefers-color-scheme: dark) {
  .efr { --efr-bg:#1c1c1e; --efr-side:#1c1c1e; --efr-row:#2c2c2e; --efr-line:#3a3a3c;
         --efr-text:#f5f5f7; --efr-muted:#98989d; --efr-link:#5aa3ff; --efr-active:#3a3a3c;
         --efr-shadow:0 18px 50px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.06); }
}
.efr-modal { display:grid; grid-template-columns: 222px minmax(0,1fr); background:var(--efr-bg);
  color:var(--efr-text); border-radius:18px; overflow:hidden; box-shadow:var(--efr-shadow);
  font-family:-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size:15px; line-height:1.35; -webkit-font-smoothing:antialiased;
  opacity:0; transform:translateY(14px) scale(.97); transition:opacity .45s ease, transform .45s cubic-bezier(.2,.7,.2,1); }
.efr-side { background:var(--efr-side); padding:20px 12px 18px 18px; border-right:1px solid var(--efr-line); }
.efr-close { color:var(--efr-text); display:flex; margin:2px 0 26px 8px; }
.efr-side ul { list-style:none; margin:0; padding:0; }
.efr-side li { display:flex; align-items:center; gap:10px; margin:0; padding:5px 12px; border-radius:8px; color:var(--efr-text); font-size:15px; line-height:1.25; }
.efr-side li svg { color:var(--efr-link); }
.efr-side li[data-active="true"] { background:var(--efr-active); }
.efr-main { padding:20px 18px 16px; min-width:0; display:flex; flex-direction:column; }
.efr-head { display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--efr-line); padding-bottom:10px; }
.efr-head h3 { margin:0; font-size:20px; font-weight:700; letter-spacing:-.2px; color:var(--efr-text); }
.efr-head .efr-plus { color:var(--efr-link); display:flex; }
.efr-list { list-style:none; margin:14px 0 0; padding:0 2px 0 0; flex:1 1 auto; min-height:0; max-height:420px; overflow-y:auto; }
.efr-list li { background:var(--efr-row); border:1px solid var(--efr-line); border-radius:8px; padding:8px 10px 8px 12px;
  margin:0 0 10px; display:flex; justify-content:space-between; gap:12px; align-items:center; line-height:1.3;
  opacity:0; transform:translateY(6px); transition:opacity .35s ease, transform .35s ease, border-color .15s ease; cursor:default; }
.efr[data-in="true"] .efr-modal { opacity:1; transform:none; }
.efr[data-in="true"] .efr-list li { opacity:1; transform:none; }
.efr-list li[data-hot="true"] { border-color:var(--efr-link); }
.efr-list li b { font-weight:400; color:var(--efr-link); overflow-wrap:anywhere; }
.efr-list li .efr-handle { color:var(--efr-muted); flex:none; display:flex; }
.efr-note { border-top:1px solid var(--efr-line); margin-top:auto; padding-top:12px; color:var(--efr-muted); font-size:13px; line-height:1.45; }
.efr-note a { color:var(--efr-link); text-decoration:none; display:inline-flex; align-items:center; gap:2px; }
.efr-dest { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:16px; }
.efr-dest div { border:1.5px solid rgba(var(--text-color-rgb),.18); border-radius:12px; padding:12px 14px; font-size:13.5px;
  color:var(--text-color); transition:border-color .15s ease, background-color .15s ease; }
.efr-dest div[data-hot="true"] { border-color:var(--color-blue); background:rgba(var(--color-blue-rgb),.08); }
.efr-dest code { font-size:12.5px; }
.efr-dest p { margin:4px 0 0; opacity:.8; }
@media (max-width: 720px) {
  .efr-modal { grid-template-columns: 1fr; }
  .efr-side { display:none; }
  .efr-main { padding:18px 16px 14px; }
  .efr-list { max-height:340px; }
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
      style={{ maxWidth: 880, margin: "1em auto", width: "100%" }}
    >
      <style>{STYLE}</style>
      <div
        className="efr-modal"
        role="figure"
        aria-label="iCloud Mail rules: each rule forwards one sender to one agent-facing address"
      >
        <aside className="efr-side" aria-hidden="true">
          <div className="efr-close" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 18 18">
              <path d="M3 3l12 12M15 3L3 15" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" fill="none" />
            </svg>
          </div>
          <ul>
            {SIDEBAR.map((item) => (
              <li key={item} data-active={item === "Rules" ? "true" : undefined}>
                <Glyph name={item} size={17} />
                {item}
              </li>
            ))}
          </ul>
        </aside>
        <div className="efr-main">
          <div className="efr-head">
            <h3>Rules</h3>
            <span className="efr-plus" title="Add New Rule">
              <Glyph name="Plus" size={16} />
            </span>
          </div>
          <ul className="efr-list" onMouseLeave={() => setHot(null)}>
            {RULES.map((rule, i) => (
              <li
                key={rule.from}
                data-hot={hot === rule.to ? "true" : undefined}
                style={{ transitionDelay: `${250 + Math.min(i, 12) * 45}ms` }}
                onMouseEnter={() => setHot(rule.to)}
                onFocus={() => setHot(rule.to)}
                tabIndex={0}
              >
                <div>
                  Forward messages from <b>{rule.from}</b> to{" "}
                  <b>{ADDRESS[rule.to]}</b>
                </div>
                <span className="efr-handle">
                  <Glyph name="DragHandle" size={15} />
                </span>
              </li>
            ))}
          </ul>
          <div className="efr-note">
            Rules are applied as messages arrive. Only the first matching rule
            will be applied per message. It may take a few minutes for the
            changes to rules to take effect.{" "}
            <a
              href="https://support.apple.com/guide/icloud/set-up-filtering-rules-mm6b1a3f8a/icloud"
              target="_blank"
              rel="noopener noreferrer"
            >
              Learn more
              <Glyph name="ExternalLink" size={12} />
            </a>
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
