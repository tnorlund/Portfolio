import React from "react";
import Head from "next/head";
import Link from "next/link";
import RotoscopePortrait from "../components/home/Rotoscope/RotoscopePortrait";
import styles from "../styles/Home.module.css";

export default function Home() {
  return (
    <div className="container">
      <Head>
        <title>Home | Tyler Norlund</title>
        <link
          rel="preload"
          as="image"
          href="/rotoscope-basins.webp"
          type="image/webp"
        />
      </Head>
      <main className={styles.main}>
        <RotoscopePortrait />
        <nav className={styles.actions} aria-label="Portfolio pages">
          <Link href="/resume">
            <button>Résumé</button>
          </Link>
          <Link href="/receipt">
            <button>Receipt</button>
          </Link>
        </nav>
      </main>
    </div>
  );
}

// Remove getServerSideProps - no longer needed for static export
