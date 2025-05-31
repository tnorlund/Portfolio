import React from "react";
import Link from "next/link";

export default function Home() {
  return (
    <div className="container">
      <main>
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
        />

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
