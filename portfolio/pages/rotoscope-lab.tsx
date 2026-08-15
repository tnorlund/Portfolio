import dynamic from "next/dynamic";
import Head from "next/head";

const RotoscopeLab = dynamic(
  () => import("../components/rotoscope-lab/RotoscopeLab"),
  {
    ssr: false,
    loading: () => <main aria-live="polite">Loading Rotoscope Lab…</main>,
  },
);

export default function RotoscopeLabPage() {
  return (
    <>
      <Head>
        <title>Rotoscope Lab | Tyler Norlund</title>
        <meta name="robots" content="noindex,nofollow" />
      </Head>
      <RotoscopeLab />
    </>
  );
}
