import { Head, Html, Main, NextScript } from "next/document";

const GA_MEASUREMENT_ID =
    process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID?.trim();
const GTM_ID = process.env.NEXT_PUBLIC_GTM_ID?.trim();

export default function Document() {
    return (
        <Html lang="en">
            <Head>
                <meta
                    name="viewport"
                    content="width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes"
                />
                {GTM_ID && (
                    <script
                        dangerouslySetInnerHTML={{
                            __html: `
(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
})(window,document,'script','dataLayer','${GTM_ID}');
`,
                        }}
                    />
                )}
                {GA_MEASUREMENT_ID && (
                    <>
                        <script
                            async
                            src={`https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`}
                        />
                        <script
                            dangerouslySetInnerHTML={{
                                __html: `
window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());
gtag('config', '${GA_MEASUREMENT_ID}');
`,
                            }}
                        />
                    </>
                )}
            </Head>
            <body>
                {GTM_ID && (
                    <noscript>
                        <iframe
                            src={`https://www.googletagmanager.com/ns.html?id=${GTM_ID}`}
                            height="0"
                            width="0"
                            style={{
                                display: "none",
                                visibility: "hidden",
                            }}
                        />
                    </noscript>
                )}
                <Main />
                <NextScript />
            </body>
        </Html>
    );
}
