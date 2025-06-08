import React from "react";
import Head from "next/head";
import Link from "next/link";

export default function Home() {
  return (
    <div className="container">
      <Head>
        <title>Home | Tyler Norlund</title>
        {/* Preload critical above-the-fold image */}
        <link rel="preload" as="image" href="/face.avif" type="image/avif" />
        <link rel="preload" as="image" href="/face.webp" type="image/webp" />
      </Head>
      <main>
        <picture>
          <source srcSet="/face.avif" type="image/avif" />
          <source srcSet="/face.webp" type="image/webp" />
          <img
            src="/face.png"
            alt="Tyler Norlund"
            style={{
              borderRadius: "50%",
              width: "200px",
              height: "200px",
              objectFit: "cover",
              marginTop: "1rem",
            }}
            loading="eager"
            decoding="async"
          />
        </picture>

        <Link href="/resume">
          <button>Résumé</button>
        </Link>
        <Link href="/receipt">
          <button>Receipt</button>
        </Link>
      </main>
    </div>
  );
}

// Remove getServerSideProps - no longer needed for static export
