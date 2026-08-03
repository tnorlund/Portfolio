// DEV-ONLY validation workstation. In the agentic-first loop the agent
// adjudicates the corpus and the human does exactly three things: sign off on
// batched fixes, blind-audit what the agent applied unsupervised, and settle
// the escalations no agent may decide. One screen each; nothing else.
//
// Data comes from the local shim (portfolio/dev-harness/validation_shim.py)
// via the /api/validation rewrite, which only exists in
// PHASE_DEVELOPMENT_SERVER.
import Head from "next/head";
import React, { useCallback, useState } from "react";
import AuditDeck from "../../components/dev/validation/AuditDeck";
import DigestPanel from "../../components/dev/validation/DigestPanel";
import EscalationScreen from "../../components/dev/validation/EscalationScreen";
import { HarnessScreen } from "../../components/dev/validation/types";
import styles from "../../components/dev/validation/Validation.module.css";

const SCREENS: { id: HarnessScreen; label: string; hint: string }[] = [
  { id: "digest", label: "Digest", hint: "Approve batched fixes" },
  { id: "audit", label: "Audit", hint: "Blind-check the auto-applied tier" },
  { id: "escalation", label: "Escalation", hint: "Decide what the agent could not" },
];

export default function ValidationWorkstation() {
  const [screen, setScreen] = useState<HarnessScreen>("digest");
  const [frozen, setFrozen] = useState<string[]>([]);

  // Both data screens report the freeze state; the banner is global because a
  // frozen class blocks the writer no matter which screen you are looking at.
  const onFrozen = useCallback((classes: string[]) => setFrozen(classes), []);

  return (
    <>
      <Head>
        <title>Line-item review — local harness</title>
      </Head>
      <div className={styles.layout}>
        {frozen.length > 0 ? (
          <div className={styles.freezeBanner} role="alert" data-testid="freeze-banner">
            <strong>
              Tier frozen: {frozen.join(", ")}
            </strong>
            <span>
              A blind audit disagreed with the agent on{" "}
              {frozen.length === 1 ? "this class" : "these classes"}. The
              adjudicator and writer must skip{" "}
              {frozen.length === 1 ? "it" : "them"} until the marker in
              .dev-harness/freeze/ is cleared by hand.
            </span>
          </div>
        ) : null}

        <nav className={styles.screenTabs} role="tablist" aria-label="Review screens">
          {SCREENS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              role="tab"
              id={`tab-${entry.id}`}
              aria-selected={screen === entry.id}
              aria-controls={`screen-${entry.id}`}
              data-active={screen === entry.id}
              onClick={() => setScreen(entry.id)}
            >
              <strong>{entry.label}</strong>
              <small>{entry.hint}</small>
            </button>
          ))}
        </nav>

        <div
          className={styles.screenBody}
          role="tabpanel"
          id={`screen-${screen}`}
          aria-labelledby={`tab-${screen}`}
        >
          {screen === "digest" ? (
            <DigestPanel onFrozen={onFrozen} />
          ) : screen === "audit" ? (
            <AuditDeck onFrozen={onFrozen} />
          ) : (
            <EscalationScreen />
          )}
        </div>
      </div>
    </>
  );
}
