import Head from "next/head";
import dynamic from "next/dynamic";
const Planner = dynamic(() => import("../components/planner/Planner"), {
  ssr: false,
});
export default function PlannerPage() {
  return (
    <>
      <Head>
        <title>Planner</title>
        <meta name="robots" content="noindex,nofollow" />
        <meta name="referrer" content="no-referrer" />
      </Head>
      <Planner />
    </>
  );
}
