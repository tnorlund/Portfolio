import Head from "next/head";
import Link from "next/link";
import React, { useEffect, useState } from "react";
import { useInView } from "react-intersection-observer";
import ClientOnly from "../components/ClientOnly";
import AnimatedInView from "../components/ui/AnimatedInView";
import {
  EmailCoverageChart,
  EmailForwardingRules,
  EmailFunnel,
  EmailInboxDiagram,
  EmailReplicaDiagram,
  EmailSenderCensus,
} from "../components/ui/Figures";
import { AWSLogo, PulumiLogo } from "../components/ui/Logos";
import styles from "../styles/Receipt.module.css";

interface FigureBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  name: string;
  intrinsicSize: string;
  mobileIntrinsicSize?: string;
}

const FIGURE_LAZY_ROOT_MARGIN = "1000px 0px";

const FigureBoundary = ({
  children,
  fallback = null,
  name,
  intrinsicSize,
  mobileIntrinsicSize = intrinsicSize,
}: FigureBoundaryProps) => {
  const { ref, inView } = useInView({
    rootMargin: FIGURE_LAZY_ROOT_MARGIN,
    triggerOnce: true,
    fallbackInView: true,
  });
  const [shouldRender, setShouldRender] = useState(false);

  useEffect(() => {
    if (inView) {
      setShouldRender(true);
    }
  }, [inView]);

  return (
    <div
      ref={ref}
      className={styles.figureBoundary}
      data-figure-boundary={name}
      data-lazy-pending={shouldRender ? undefined : "true"}
      style={
        {
          "--figure-intrinsic-size": intrinsicSize,
          "--figure-mobile-intrinsic-size": mobileIntrinsicSize,
        } as React.CSSProperties
      }
    >
      {shouldRender ? children : fallback}
    </div>
  );
};

export default function EmailPage() {
  return (
    <div className="container" style={{ position: "relative" }}>
      <Head>
        <title>Email Receipts | Tyler Norlund</title>
        <meta
          name="description"
          content="Thirteen years of email receipts, parsed into one SQLite file, replicated to AWS, and read by agents over MCP."
        />
        <meta name="twitter:card" content="summary" />
      </Head>

      <h1>Introduction</h1>

      <p>
        The <Link href="/receipt">receipt page</Link> answers one question: how much
        did I spend on milk? It only knows about paper. Half of what I buy
        never prints a receipt. DoorDash, Apple, Amazon, Venmo, PayPal, Uber.
        Those live in my inbox.
      </p>

      <p>
        So I taught my laptop to read my email too.
      </p>

      <h1>Thirteen Years of Email</h1>

      <p>
        I exported everything. 162,333 messages going back to 2013. Most of
        it is noise. About 30,000 came from senders that have ever sent me a
        receipt, and a little over 4,000 of those actually were receipts.
      </p>

      <FigureBoundary
        name="email-funnel"
        intrinsicSize="200px"
        mobileIntrinsicSize="200px"
      >
        <ClientOnly>
          <EmailFunnel />
        </ClientOnly>
      </FigureBoundary>

      <p>
        Then the de-duping. Apple sends the same receipt twice. DoorDash sends
        an estimate, then a final. After collapsing those I have 3,845 unique
        receipts and 4,851 line items. The same SQLite file also holds a
        snapshot of the 1,023 paper receipts and 3,516 card transactions from
        the bank. 376 of those transactions have a matching receipt so far.
      </p>

      <h2>Every Sender Formats It Differently</h2>

      <p>
        DoorDash puts the restaurant in the subject. Apple hides the total in
        a table that is split across three cells. Venmo forgets the year.
        Each sender group gets its own small parser. Regular expressions, no
        AI. They pull merchant, date, and total 97 to 100 percent of the time.
      </p>

      <FigureBoundary
        name="email-sender-census"
        intrinsicSize="220px"
        mobileIntrinsicSize="220px"
      >
        <ClientOnly>
          <EmailSenderCensus />
        </ClientOnly>
      </FigureBoundary>

      <p>
        Some hard truths came out of this. The last four digits of the card
        are almost never in the email, so matching to the bank has to lean on
        amount, date, and merchant. DoorDash totals since 2023 are estimates
        because the tip lands later, so I match against a band instead of a
        number. Amazon stopped listing items in its emails from 2020 to
        mid-2023. And Costco email is useless: 3 receipts in 2,256 messages.
        Costco arrives on paper.
      </p>

      <h2>Matching to the Bank</h2>

      <p>
        The number I actually care about is coverage. Of the purchases on my
        card each month, how many can I point to a receipt for?
      </p>

      <FigureBoundary
        name="email-coverage"
        intrinsicSize="400px"
        mobileIntrinsicSize="420px"
      >
        <ClientOnly>
          <EmailCoverageChart />
        </ClientOnly>
      </FigureBoundary>

      <p>
        It is low. That is the point. The metric exists to be embarrassed by.
        The business card buys things that never email a receipt, so its line
        sinks toward zero. The personal card bounces around depending on how
        many paper receipts I bothered to photograph that month.
      </p>

      <h1>Getting the Mail to AWS</h1>

      <p>
        Exporting my mailbox by hand every few months is not a system. I
        wanted new receipts to show up on their own.
      </p>

      <FigureBoundary name="aws-logo" intrinsicSize="150px">
        <ClientOnly>
          <AnimatedInView>
            <AWSLogo />
          </AnimatedInView>
        </ClientOnly>
      </FigureBoundary>

      <p>
        iCloud has a rules screen. Each rule forwards mail from one sender to
        one address on an isolated subdomain that Amazon SES receives for.
        Receipt senders go to the receipts address. The five Greenhouse
        senders that carry job-application codes go to a different address
        that a different agent reads. Nothing else leaves my inbox. That list
        is the whole permission model, and I can read it on one screen.
      </p>

      <FigureBoundary
        name="email-forwarding-rules"
        intrinsicSize="620px"
        mobileIntrinsicSize="760px"
      >
        <ClientOnly>
          <EmailForwardingRules />
        </ClientOnly>
      </FigureBoundary>

      <p>
        SES requires TLS, runs the spam and virus scans, stamps the DMARC
        result on the message, and drops the raw email in a private S3
        bucket. Raw mail expires after 30 days. My Mac pulls it down every
        night, long before then. That is it. S3 is just the archive.
      </p>

      <FigureBoundary name="email-inbox-diagram" intrinsicSize="220px">
        <ClientOnly>
          <EmailInboxDiagram />
        </ClientOnly>
      </FigureBoundary>

      <p>
        Since it went live in July: 1,189 messages forwarded, 14 failed the
        trust gate, and 59 were receipts. 756 of them were GitHub
        notifications, because GitHub sends its ten receipts a year from the
        same address as everything else it says. That rule is gone now.
      </p>

      <h1>The Read Replica</h1>

      <p>
        The SQLite file on my Mac is the primary. Every night a job pulls the
        new raw mail down from S3, parses it with the one and only set of
        parsers, and reconciles it against the bank. Then it takes a
        consistent copy of the file and uploads it back to S3 next to a
        manifest: a checksum, row counts, and when it was published.
      </p>

      <FigureBoundary
        name="email-replica-diagram"
        intrinsicSize="230px"
        mobileIntrinsicSize="230px"
      >
        <ClientOnly>
          <EmailReplicaDiagram />
        </ClientOnly>
      </FigureBoundary>

      <p>
        On the other side is a tiny AWS Lambda. Python standard library only.
        No container, no dependencies. It downloads the copy on cold start,
        checks whether the file changed on every call, opens it read-only,
        and speaks MCP over HTTPS behind the same OAuth gateway my other
        agent tools use.
      </p>

      <p>
        Now Claude on my phone, a scheduled agent, or Claude Code on any
        machine can ask the same ten read-only questions the local server
        answers. Summaries, one receipt, search, merchants, spend by month,
        coverage, the unmatched worklist, status, and raw SQL when none of
        those fit. Writes stay on the Mac. Confirming a match or tagging a
        transaction happens where the primary lives. The replica lags by a
        day, and the manifest tells the agent exactly how stale it is.
      </p>

      <h2>What I Deleted</h2>

      <p>
        The first version was fancier. A Lambda woke up on every object that
        landed in S3, ran a second copy of every parser, and wrote a JSON
        file that nothing ever read. Two copies of 5,000 lines of regular
        expressions drifting apart. 92 percent of what it parsed was not a
        receipt.
      </p>

      <p>
        I deleted the Lambda, its dead-letter queue, the alarm, the retry
        config, and the S3 trigger. SES to S3 is now just mail in a bucket.
        Fewer moving parts, same data, and one parser to fix when Apple
        changes its template again.
      </p>

      <p>
        The milk question now has an answer for the half of my life that
        never touches paper. I might have a DoorDash problem.
      </p>

      <hr />

      <h1>The Boring Details</h1>

      <p>
        If you are still here, here is what is actually under the hood.
      </p>

      <p>
        SES allows one active receipt rule set per account and region, so
        the other inbox I run for job-application verification codes shares
        this one. The bucket policy only lets that exact receipt rule write
        under the raw prefix. Nothing else in the account can.
      </p>

      <p>
        Money is integer cents everywhere. Ingest is idempotent on the
        Message-ID and on a hash of the content, so re-running over an
        overlapping export adds nothing. The raw SQL tool only accepts
        SELECT and WITH and caps results at 500 rows.
      </p>

      <p>
        The API Gateway integration window is 29 seconds, so the Lambda
        times out at 25. The database is about 20 MB, 7 MB gzipped, and is
        downloaded once per cold start.
      </p>

      <FigureBoundary name="pulumi-logo" intrinsicSize="150px">
        <ClientOnly>
          <AnimatedInView>
            <PulumiLogo />
          </AnimatedInView>
        </ClientOnly>
      </FigureBoundary>

      <p>
        Pulumi in Python defines all of it: the SES identity and DKIM
        records, the bucket and its policy, the Lambda, and the gateway
        route with its own OAuth scope.
      </p>

      <p>
        The code is on{" "}
        <a href="https://github.com/tnorlund/Portfolio">GitHub</a> if you
        want to see how the sausage gets made.
      </p>
    </div>
  );
}
