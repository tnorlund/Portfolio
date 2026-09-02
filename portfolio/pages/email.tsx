import Head from "next/head";
import Link from "next/link";
import React, { useEffect, useState } from "react";
import { useInView } from "react-intersection-observer";
import ClientOnly from "../components/ClientOnly";
import AnimatedInView from "../components/ui/AnimatedInView";
import {
  EmailCodeDiagram,
  EmailCoverageChart,
  EmailForwardingRules,
  EmailFunnel,
  EmailInboxDiagram,
  EmailReplicaDiagram,
  EmailSenderCensus,
} from "../components/ui/Figures";
import { AWSLogo, GrokBotLogo, PulumiLogo } from "../components/ui/Logos";
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

const AUDIT_TABLE_STYLE: React.CSSProperties = {
  width: "100%",
  maxWidth: 640,
  margin: "1.25rem auto",
  borderCollapse: "collapse",
  fontSize: "0.95rem",
};
const CELL: React.CSSProperties = {
  padding: "0.45rem 0.6rem",
  borderBottom: "1px solid rgba(var(--text-color-rgb), 0.15)",
  textAlign: "left",
  verticalAlign: "top",
};
const NUM: React.CSSProperties = { ...CELL, textAlign: "right", whiteSpace: "nowrap" };

export default function EmailPage() {
  return (
    <div className="container" style={{ position: "relative" }}>
      <Head>
        <title>Email Receipts | Tyler Norlund</title>
        <meta
          name="description"
          content="How I give AI agents exactly the email they need and nothing else: a job bot that gets one code, a receipt reader that gets parsed rows, and the rules screen that decides."
        />
        <meta name="twitter:card" content="summary" />
      </Head>

      <h1>Introduction</h1>

      <p>
        Agents do more of my work every day. The most recent one runs my job
        search. It finds roles, fills in the application, and stops before
        the submit button so I can read it. Then Greenhouse emails me a
        verification code, and the bot needs that code to finish.
      </p>

      <p>
        So the bot needs my email. I was not going to hand a bot my inbox.
        Thirteen years of receipts, statements, and everyone I have ever
        talked to, so it can copy eight characters out of one message? No.
      </p>

      <p>
        This page is about the middle ground: giving each agent exactly the
        mail it needs, proving that is all it got, and being able to turn it
        off in one click.
      </p>

      <h1>The Job Bot Gets One Code</h1>

      <FigureBoundary name="grok-bot-logo" intrinsicSize="200px">
        <ClientOnly>
          <GrokBotLogo />
        </ClientOnly>
      </FigureBoundary>

      <p>
        Grok Bot lives in my Dock like any other app. It fills out forms,
        stops before Submit, and waits for me. The one thing it cannot do
        on its own is read the code Greenhouse emails me.
      </p>

      <p>
        iCloud has a rules screen. Each rule forwards mail from one sender to
        one address on an isolated subdomain that Amazon SES receives for.
        The five Greenhouse senders that carry verification codes go to one
        address. Those five rules are the bot&apos;s entire view of my mail,
        and I can read them on one screen.
      </p>

      <FigureBoundary
        name="email-forwarding-rules"
        intrinsicSize="620px"
        mobileIntrinsicSize="560px"
      >
        <ClientOnly>
          <EmailForwardingRules destination="ats" />
        </ClientOnly>
      </FigureBoundary>

      <p>
        On the AWS side the mail stops being an email almost immediately. SES
        checks that the message really came from Greenhouse, drops it in a
        bucket that empties after a day, and a small Lambda pulls out the
        eight-character code. The code goes into a table with a one-hour
        expiry. The bot asks a tiny read-only endpoint for the latest code.
        That is all it can ask for. It never sees a subject line, never sees
        a body, and never has my iCloud password.
      </p>

      <FigureBoundary
        name="email-code-diagram"
        intrinsicSize="230px"
        mobileIntrinsicSize="460px"
      >
        <ClientOnly>
          <EmailCodeDiagram />
        </ClientOnly>
      </FigureBoundary>

      <p>
        Everything about this path is small on purpose. One sender group.
        One field. One hour. If the bot turns out to be a bad actor, the
        worst it can do is read a code that has already expired.
      </p>

      <h1>The Receipt Reader Gets Parsed Rows</h1>

      <p>
        The same pattern already existed for a very different agent. The{" "}
        <Link href="/receipt">receipt page</Link> answers one question: how
        much did I spend on milk? It only knows about paper. Half of what I
        buy never prints a receipt. DoorDash, Apple, Amazon, Venmo, PayPal,
        Uber. Those live in my inbox too, and Claude wants to read them.
      </p>

      <h2>Thirteen Years of Email</h2>

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

      <h2>Getting the Mail to AWS</h2>

      <p>
        Exporting my mailbox by hand every few months is not a system. I
        wanted new receipts to show up on their own, so the receipt senders
        got rules too. Twenty-one of them, each forwarding one domain to the
        receipts address.
      </p>

      <FigureBoundary name="aws-logo" intrinsicSize="150px">
        <ClientOnly>
          <AnimatedInView>
            <AWSLogo />
          </AnimatedInView>
        </ClientOnly>
      </FigureBoundary>

      <p>
        SES requires TLS, runs the spam and virus scans, stamps the DMARC
        result on the message, and drops the raw email in a private S3
        bucket. Raw mail expires after 30 days. My Mac pulls it down every
        night, long before then. That is it. S3 is just the archive.
      </p>

      <FigureBoundary
        name="email-inbox-diagram"
        intrinsicSize="230px"
        mobileIntrinsicSize="350px"
      >
        <ClientOnly>
          <EmailInboxDiagram />
        </ClientOnly>
      </FigureBoundary>

      <p>
        Since it went live in July: 1,189 messages forwarded, 59 were
        receipts, and 14 failed the trust gate. Seven of those were Equinox
        marketing sent through a third party that failed DMARC alignment,
        six were flagged as spam by SES, and one was Amazon&apos;s own setup
        notice. The gate holds.
      </p>

      <h2>The Read Replica</h2>

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
        mobileIntrinsicSize="460px"
      >
        <ClientOnly>
          <EmailReplicaDiagram />
        </ClientOnly>
      </FigureBoundary>

      <p>
        On the other side is a tiny AWS Lambda. Python standard library only.
        No container, no dependencies. It downloads the copy on cold start,
        checks whether the file changed on every call, opens it read-only,
        and speaks MCP over HTTPS behind the same OAuth gateway the job bot
        uses, with its own scope.
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

      <h1>What Else Should Agents See?</h1>

      <p>
        A rules list is only a permission model if someone audits it. I have
        13 years of mail indexed, so the audit is a query, not a scroll
        through the inbox: which senders have ever produced a receipt, and
        does each one have a rule? And the other direction: which rules
        forward mail that nothing ever parses?
      </p>

      <table style={AUDIT_TABLE_STYLE}>
        <thead>
          <tr>
            <th style={CELL}>Rule</th>
            <th style={CELL}>Verdict</th>
            <th style={NUM}>Forwarded since July</th>
            <th style={NUM}>Receipts</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td style={CELL}>starbucks.com</td>
            <td style={CELL}>Add. 53 receipts, 15 in the last two years, none forwarded.</td>
            <td style={NUM}>0</td>
            <td style={NUM}>0</td>
          </tr>
          <tr>
            <td style={CELL}>github.com</td>
            <td style={CELL}>Delete. Ten receipts in seven years, same address as every notification.</td>
            <td style={NUM}>756</td>
            <td style={NUM}>0</td>
          </tr>
          <tr>
            <td style={CELL}>costco.com</td>
            <td style={CELL}>Delete. Costco email is image-only; the warehouse receipts come from an export.</td>
            <td style={NUM}>67</td>
            <td style={NUM}>0</td>
          </tr>
          <tr>
            <td style={CELL}>chase.com</td>
            <td style={CELL}>Delete. Balance alerts, and nothing reads them.</td>
            <td style={NUM}>36</td>
            <td style={NUM}>0</td>
          </tr>
        </tbody>
      </table>

      <p>
        Twenty-one rules become nineteen. Those three deletes were 859 of the
        1,189 messages, so the archive shrinks by 72 percent and nothing that
        gets parsed is lost. Seven other senders have parsers but have not
        emailed me a receipt in two years. They get a rule if they come back,
        not before. Adding a parser and adding a rule are the same moment.
      </p>

      <h1>Turning It Off</h1>

      <p>
        Every path has a switch I own. Delete a rule and that sender stops
        reaching any agent, in minutes. Delete the replica file and the
        receipt reader answers with nothing. Revoke the OAuth token and the
        endpoint stops answering at all. The bot never held a credential of
        mine, so there is nothing of mine to rotate.
      </p>

      <p>
        The milk question now has an answer for the half of my life that
        never touches paper, and the job bot gets its code. Neither of them
        can read my email. I might have a DoorDash problem.
      </p>

      <hr />

      <h1>The Boring Details</h1>

      <p>
        If you are still here, here is what is actually under the hood.
      </p>

      <p>
        SES allows one active receipt rule set per account and region, so
        both inboxes share it: one rule per recipient address. The bucket
        policy only lets that exact receipt rule write under the raw prefix.
        Nothing else in the account can. The replica Lambda&apos;s role can
        read the replica prefix and nothing else, so even raw SQL cannot
        reach a raw email.
      </p>

      <p>
        Each server has its own Cognito scope. A token minted for the receipt
        replica cannot call the code reader, and the bot&apos;s token cannot
        call the replica. The code reader&apos;s bucket keeps mail for one
        day and its table rows expire after one hour; the receipt archive
        keeps raw mail for 30 days.
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
        records, both buckets and their policies, both Lambdas, and the
        gateway routes with their own OAuth scopes.
      </p>

      <p>
        The code is on{" "}
        <a href="https://github.com/tnorlund/Portfolio">GitHub</a> if you
        want to see how the sausage gets made.
      </p>
    </div>
  );
}
