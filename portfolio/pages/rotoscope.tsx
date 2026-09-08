import Head from "next/head";
import React from "react";
import RotoscopeExplainer from "../components/rotoscope/RotoscopeExplainer";
import styles from "../styles/Rotoscope.module.css";

export default function RotoscopePage() {
  return (
    <div className={styles.page}>
      <Head>
        <title>How the Rotoscope Works | Tyler Norlund</title>
        <meta
          name="description"
          content="A visual, plain-language explanation of a best-features rotoscoping algorithm."
        />
        <meta name="twitter:card" content="summary" />
      </Head>
      <main className={`container ${styles.article}`}>
        <RotoscopeExplainer />
      </main>
    </div>
  );
}
