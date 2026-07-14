import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  buildLabelBoxes,
  familiesIn,
  toCssRectInner,
} from "../AugmentationShowcase/labelGeometry";
import { LABEL_COLORS } from "../labelStyles";
import { LabelBoxOverlay, LabelLegend } from "../labelBoxOverlay";
import sharedStyles from "../labelBoxOverlay.module.css";
import {
  cloudScale,
  glyphAnchorsCloud,
  glyphDotPointsCloud,
  nodeCount,
  skeletonPathDsCloud,
} from "./geometry";
import {
  ActId,
  BOLD_WEIGHT_CALLOUT,
  CHAR_PRINT_COUNT,
  charCloudSrc,
  charPrintSrc,
  COMPOSE_GROUP_ORDER,
  FONT_CODEPOINTS,
  FontGlyphMetric,
  fontGlyphSrc,
  finalSrc,
  logoSrc,
  Merchant,
  MERCHANTS,
  MERCHANT_LABELS,
  MerchantAssets,
  RECEIPT_DIMS,
  realSrc,
  realThumbSrc,
  REAL_THUMB_COUNT,
  WEIGHT_MAX,
  WEIGHT_MIN,
  WEIGHT_STEP,
} from "./pipelineData";
import styles from "./SynthesisPipeline.module.css";

export interface ActProps {
  merchant: Merchant;
  assets: MerchantAssets;
  /** Progress within this act, 0..1. */
  progress: number;
  active: boolean;
  reducedMotion: boolean;
  dotWeight: number;
  onWeightChange: (weight: number) => void;
}

const clamp01 = (n: number): number => Math.min(1, Math.max(0, n));

/** Map a sub-range of act progress to 0..1. */
const phase = (p: number, from: number, to: number): number =>
  clamp01((p - from) / (to - from));

const AssetPending: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => (
  <div className={styles.assetPending} data-testid="asset-pending">
    {children}
  </div>
);

/**
 * Turn opaque dark-ink-on-white receipt pixels into black ink with real alpha.
 * The grayscale intensity is preserved as opacity, so antialiasing and the
 * consensus cloud survive while the receipt-paper pixels disappear entirely.
 */
export const knockOutReceiptPaper = (pixels: Uint8ClampedArray): void => {
  const paperLuminance = 220;
  const solidInkLuminance = 70;
  for (let i = 0; i < pixels.length; i += 4) {
    const luminance = Math.round(
      pixels[i] * 0.2126 + pixels[i + 1] * 0.7152 + pixels[i + 2] * 0.0722,
    );
    const normalizedInk = Math.min(
      1,
      Math.max(
        0,
        (paperLuminance - luminance) /
          (paperLuminance - solidInkLuminance),
      ),
    );
    const inkAlpha = normalizedInk ** 1.5;
    pixels[i + 3] = Math.round(pixels[i + 3] * inkAlpha);
    pixels[i] = 0;
    pixels[i + 1] = 0;
    pixels[i + 2] = 0;
  }
};

interface ReceiptInkLayerProps {
  src: string;
  className: string;
  style?: React.CSSProperties;
  ariaLabel?: string;
  testId?: string;
}

const ReceiptInkLayer: React.FC<ReceiptInkLayerProps> = ({
  src,
  className,
  style,
  ariaLabel,
  testId,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    let cancelled = false;
    const source = new Image();
    source.decoding = "async";
    source.onload = () => {
      if (cancelled) {
        return;
      }
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) {
        return;
      }
      canvas.width = source.naturalWidth;
      canvas.height = source.naturalHeight;
      ctx.drawImage(source, 0, 0);
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      knockOutReceiptPaper(imageData.data);
      ctx.putImageData(imageData, 0, 0);
    };
    source.src = src;
    return () => {
      cancelled = true;
      source.onload = null;
    };
  }, [src]);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={style}
      role={ariaLabel ? "img" : undefined}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel ? undefined : true}
      data-testid={testId}
    />
  );
};

/* ==================================================================== */
/* Act 1 — Raw material                                                  */
/* ==================================================================== */

const RawMaterialAct: React.FC<ActProps> = ({
  merchant,
  progress,
  reducedMotion,
}) => {
  const [failed, setFailed] = useState<Record<number, boolean>>({});
  const shownProgress = reducedMotion ? 1 : progress;
  const indices = Array.from({ length: REAL_THUMB_COUNT }, (_, i) => i);
  const mid = (REAL_THUMB_COUNT - 1) / 2;
  const logoVisible = shownProgress > 0.03;

  return (
    <div className={styles.rawMaterialStage} data-testid="act-raw">
      <div className={styles.rawLogoPane}>
        <svg
          role="img"
          aria-label={`${MERCHANT_LABELS[merchant]} logo`}
          viewBox="0 0 1800 468"
          preserveAspectRatio="xMidYMid meet"
          className={styles.rawLogo}
          style={
            {
              opacity: logoVisible ? 1 : 0,
              transform: logoVisible ? "translateY(0)" : "translateY(10px)",
            } as React.CSSProperties
          }
        >
          <path
            className={styles.rawLogoPrimary}
            fill="currentColor"
            fillRule="evenodd"
            clipRule="evenodd"
            d="M234 62 L233 48 L230 39 L223 33 L202 29 L202 14 L333 14 L356 17 L375 23 L391 33 L402 44 L412 62 L416 78 L415 109 L405 135 L393 150 L377 162 L345 174 L312 176 L288 170 L288 231 L290 250 L293 256 L298 260 L327 265 L327 280 L199 279 L200 265 L225 260 L230 255 L233 246 Z M1502 34 L1460 33 L1461 247 L1465 256 L1471 260 L1482 263 L1500 264 L1500 280 L1366 280 L1366 264 L1383 263 L1396 259 L1400 255 L1404 243 L1404 33 L1374 33 L1351 38 L1342 46 L1336 57 L1328 85 L1312 85 L1317 4 L1328 4 L1332 10 L1342 14 L1521 14 L1530 11 L1537 4 L1548 4 L1552 84 L1537 85 L1530 60 L1524 48 L1514 38 Z M768 157 L774 146 L785 135 L808 123 L808 125 L802 128 L786 145 L776 164 L771 185 L773 213 L782 233 L794 246 L800 249 L832 257 L861 255 L861 293 L835 293 L815 290 L794 284 L769 272 L753 261 L735 244 L722 227 L710 205 L710 202 L712 203 L715 210 L725 221 L714 196 L710 175 L712 138 L722 106 L745 66 L765 41 L783 24 L780 24 L765 35 L738 64 L719 96 L716 111 L714 111 L713 106 L713 80 L716 64 L724 44 L740 24 L762 10 L794 1 L826 0 L815 13 L807 28 L802 44 L797 78 L789 101 L782 111 L771 121 L752 132 L733 138 L731 143 L731 175 L738 203 L745 218 L757 236 L776 253 L768 243 L762 231 L757 211 L758 181 L762 167 Z M7 100 L7 76 L11 60 L24 38 L43 22 L69 11 L86 8 L122 9 L141 13 L158 20 L165 73 L165 78 L163 79 L150 81 L143 61 L133 45 L117 31 L103 26 L85 26 L74 30 L61 42 L55 58 L55 73 L62 89 L80 105 L125 128 L146 142 L164 160 L173 176 L177 191 L177 215 L175 224 L162 250 L147 265 L124 278 L108 283 L88 286 L51 284 L31 279 L11 271 L2 224 L1 200 L15 197 L26 224 L43 248 L59 261 L79 268 L101 266 L117 255 L124 242 L126 220 L121 205 L106 188 L44 152 L21 131 L11 114 Z M530 242 L534 255 L540 260 L564 264 L564 280 L440 279 L440 265 L455 263 L468 258 L473 251 L475 242 L475 50 L473 42 L468 35 L458 31 L442 29 L442 14 L577 14 L601 17 L615 21 L634 32 L645 43 L653 57 L657 71 L658 88 L652 113 L637 133 L620 145 L607 150 L607 152 L635 202 L664 245 L682 263 L696 269 L694 282 L672 283 L644 279 L631 275 L614 265 L594 240 L561 178 L555 171 L544 166 L530 166 Z M861 6 L848 2 L848 0 L861 1 Z M1070 257 L1064 251 L1054 233 L1046 203 L1044 182 L1043 47 L1040 39 L1035 34 L1027 31 L1010 29 L1010 14 L1134 14 L1134 29 L1117 31 L1109 34 L1104 39 L1101 47 L1100 179 L1105 208 L1117 234 L1127 245 L1136 251 L1157 257 L1174 257 L1189 254 L1208 243 L1218 231 L1226 215 L1233 184 L1234 83 L1230 49 L1227 42 L1219 35 L1208 31 L1191 29 L1191 14 L1294 14 L1294 29 L1279 31 L1268 36 L1260 48 L1256 79 L1256 166 L1253 198 L1243 232 L1226 257 L1213 268 L1199 276 L1179 283 L1159 286 L1140 286 L1113 282 L1090 273 L1079 266 Z M1697 198 L1677 181 L1644 164 L1618 147 L1601 130 L1589 106 L1587 93 L1588 74 L1594 55 L1601 43 L1622 23 L1633 17 L1654 10 L1667 8 L1703 9 L1722 13 L1737 18 L1739 21 L1746 78 L1731 81 L1726 66 L1713 44 L1698 31 L1684 26 L1666 26 L1649 34 L1640 45 L1637 53 L1636 74 L1642 88 L1654 100 L1671 111 L1711 131 L1728 143 L1744 159 L1753 174 L1758 192 L1758 213 L1755 227 L1745 247 L1729 264 L1705 278 L1684 284 L1649 286 L1615 280 L1592 271 L1589 260 L1581 210 L1582 200 L1594 196 L1596 197 L1600 210 L1611 231 L1622 246 L1634 257 L1651 266 L1673 268 L1682 266 L1694 259 L1704 244 L1707 222 L1705 212 Z M1799 18 L1800 41 L1795 41 L1794 25 L1789 40 L1783 40 L1779 27 L1778 40 L1773 41 L1774 18 L1782 18 L1786 32 L1791 18 Z M576 39 L571 35 L555 31 L539 32 L531 37 L530 149 L557 148 L571 144 L587 131 L594 119 L599 95 L597 70 L588 49 Z M315 32 L296 32 L291 35 L288 45 L288 152 L300 156 L318 156 L337 147 L348 134 L356 110 L356 74 L348 52 L336 39 L329 35 Z M864 292 L863 292 L863 255 L864 255 Z M1772 18 L1771 23 L1765 23 L1765 41 L1761 41 L1760 22 L1754 23 L1753 18 Z"
          />
          <path
            className={styles.rawLogoAccent}
            fill="currentColor"
            fillRule="evenodd"
            clipRule="evenodd"
            d="M342 353 L333 353 L330 356 L330 406 L346 405 L353 402 L361 393 L363 386 L363 372 L359 361 L350 354 Z M1041 457 L1044 455 L1046 448 L1045 368 L1003 464 L997 464 L959 370 L956 446 L960 456 L973 458 L973 465 L928 465 L928 459 L941 455 L944 448 L951 372 L950 357 L945 353 L935 352 L935 344 L971 345 L1007 428 L1009 428 L1045 346 L1047 344 L1082 345 L1082 352 L1071 353 L1067 356 L1066 383 L1069 453 L1073 457 L1084 458 L1084 465 L1031 465 L1031 458 Z M900 239 L911 232 L923 220 L939 195 L947 172 L950 156 L950 120 L942 88 L924 55 L896 26 L873 11 L864 8 L864 1 L892 7 L909 14 L927 24 L944 37 L965 60 L975 76 L984 96 L991 124 L992 161 L987 188 L980 208 L971 226 L960 242 L940 262 L919 276 L896 286 L864 292 L864 254 L883 249 Z M831 412 L817 404 L806 391 L803 374 L808 359 L819 348 L835 342 L854 342 L870 346 L873 374 L866 375 L859 358 L852 352 L846 350 L837 350 L830 353 L824 361 L823 371 L829 383 L860 400 L873 412 L879 427 L879 435 L875 448 L863 461 L849 467 L826 468 L806 462 L800 434 L800 430 L806 428 L816 448 L828 458 L842 460 L850 457 L855 452 L857 447 L857 433 L855 429 L847 421 Z M1414 408 L1443 446 L1454 456 L1464 458 L1464 465 L1429 465 L1384 407 L1381 407 L1381 450 L1384 456 L1397 458 L1397 465 L1344 465 L1344 458 L1356 456 L1359 450 L1359 359 L1354 353 L1343 352 L1343 345 L1396 344 L1396 352 L1384 354 L1381 360 L1381 400 L1383 400 L1403 383 L1424 358 L1423 353 L1414 352 L1414 344 L1461 344 L1461 352 L1446 354 L1436 360 L1403 393 Z M1511 353 L1509 354 L1509 398 L1537 397 L1541 392 L1542 385 L1549 384 L1550 421 L1542 422 L1540 412 L1533 408 L1509 408 L1510 452 L1512 455 L1520 457 L1547 455 L1557 444 L1561 433 L1564 433 L1568 434 L1568 440 L1562 465 L1469 465 L1469 458 L1481 457 L1486 452 L1486 358 L1482 353 L1471 352 L1471 345 L1557 344 L1559 373 L1552 374 L1548 361 L1540 354 Z M1274 353 L1265 353 L1262 356 L1262 406 L1284 403 L1292 395 L1295 386 L1295 372 L1291 361 L1280 353 Z M430 359 L425 353 L414 352 L415 344 L451 345 L488 428 L525 345 L561 344 L561 352 L550 353 L546 357 L546 396 L548 451 L553 457 L563 458 L564 465 L510 465 L511 458 L520 457 L525 452 L525 367 L483 464 L477 464 L475 460 L440 371 L438 370 L436 450 L442 457 L453 458 L453 465 L408 465 L408 458 L417 457 L423 451 L428 406 Z M771 465 L762 461 L754 453 L735 419 L729 414 L719 414 L719 451 L724 457 L734 458 L735 465 L681 465 L681 458 L693 456 L697 448 L697 361 L694 354 L681 351 L682 344 L747 345 L764 351 L771 358 L776 370 L775 386 L771 394 L763 402 L753 408 L778 448 L788 458 L795 460 L794 467 Z M1224 465 L1224 458 L1235 457 L1240 450 L1240 359 L1235 353 L1225 352 L1225 344 L1279 344 L1296 346 L1306 350 L1313 356 L1318 365 L1319 383 L1312 397 L1296 408 L1322 449 L1330 457 L1338 460 L1337 467 L1318 466 L1304 460 L1292 445 L1277 417 L1272 414 L1262 414 L1262 448 L1264 455 L1267 457 L1278 458 L1278 465 Z M613 398 L639 398 L644 394 L647 384 L654 385 L654 422 L646 422 L645 414 L641 409 L613 408 L613 447 L614 452 L619 456 L648 456 L658 449 L665 433 L672 434 L666 465 L573 465 L573 458 L585 457 L590 452 L590 357 L586 353 L576 352 L575 345 L661 344 L663 373 L659 374 L656 374 L651 359 L644 354 L615 353 L613 355 Z M292 346 L293 344 L359 345 L370 348 L380 355 L387 369 L387 383 L383 393 L374 402 L364 408 L390 449 L398 457 L406 460 L405 467 L386 466 L372 460 L363 450 L346 419 L340 414 L330 414 L330 450 L333 456 L346 458 L346 465 L292 465 L292 458 L303 457 L308 450 L308 360 L305 354 L293 352 Z M115 451 L120 457 L132 458 L133 465 L77 465 L77 458 L87 457 L92 451 L92 358 L88 353 L77 352 L77 345 L162 344 L165 367 L164 374 L157 373 L156 366 L148 355 L140 353 L115 354 L115 400 L144 399 L148 396 L151 386 L158 387 L157 424 L150 424 L149 415 L143 410 L115 409 Z M1129 427 L1121 448 L1121 455 L1133 458 L1134 465 L1092 465 L1092 458 L1103 456 L1109 449 L1149 346 L1152 343 L1160 342 L1196 444 L1204 456 L1215 458 L1215 465 L1164 465 L1164 458 L1172 457 L1175 454 L1166 425 L1129 425 Z M1135 410 L1133 414 L1162 414 L1148 374 Z M1672 342 L1674 340 L1680 340 L1682 376 L1674 375 L1671 362 L1665 355 L1657 353 L1639 353 L1639 448 L1643 456 L1657 458 L1658 465 L1599 465 L1599 458 L1613 456 L1617 448 L1617 353 L1591 355 L1585 362 L1582 375 L1574 376 L1576 340 L1582 340 L1585 344 L1671 344 Z M242 454 L243 451 L234 425 L197 425 L190 444 L189 455 L201 458 L201 465 L160 465 L160 458 L171 456 L177 449 L216 348 L218 344 L224 342 L228 343 L268 452 L274 457 L283 458 L283 465 L232 465 L232 458 L240 457 Z M752 373 L749 363 L741 355 L730 352 L720 354 L719 406 L735 405 L742 402 L750 392 Z M212 384 L201 414 L230 414 L216 374 Z M861 255 L863 255 L863 292 L861 292 Z M861 1 L863 1 L863 7 L861 6 Z"
          />
        </svg>
      </div>

      <div className={styles.thumbFan} aria-label="Real Sprouts receipt scans">
        {indices.map((i) => {
          const revealed = shownProgress > 0.08 + i * 0.04;
          const rotate = (i - mid) * 10;
          const x = (i - mid) * 46;
          const y = Math.abs(i - mid) * 18 - 12;
          const scale = i === mid ? 1 : 0.94;
          const revealY = revealed ? 0 : 20;
          const style: React.CSSProperties = {
            transform: `translate(-50%, -50%) translate(${x}px, ${
              y + revealY
            }px) rotate(${rotate}deg) scale(${scale})`,
            opacity: revealed ? 1 : 0,
            zIndex: i === mid ? indices.length + 1 : i + 1,
          };
          if (failed[i]) {
            return (
              <div
                key={i}
                className={`${styles.thumb} ${styles.thumbMissing}`}
                style={style}
              >
                scan {i + 1}
              </div>
            );
          }
          return (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={i}
              src={realThumbSrc(merchant, i)}
              alt={`Real ${MERCHANT_LABELS[merchant]} receipt scan ${i + 1}`}
              className={styles.thumb}
              style={style}
              loading="lazy"
              onError={() => setFailed((prev) => ({ ...prev, [i]: true }))}
            />
          );
        })}
      </div>
    </div>
  );
};

/* ==================================================================== */
/* Act 2 — One character: prints -> trace (with handles) -> thermal dots  */
/* ==================================================================== */

const THERMAL_STEP_UNITS = 55; // cap-unit arc-length between dot stamps
const GLYPH_DISPLAY_PX = 290; // approx on-screen height of the glyph box
const CHAR_STACK_LAYERS = 4;

/**
 * The former character / pen-path / thermal acts merged into one continuous
 * beat, all in the SAME cloudGeom frame: real prints stack into the consensus
 * cloud, the pen path draws over it WITH vector-editor handles (filled square
 * anchors, hairline handles to hollow control circles — mapped through the
 * identical cloud transform as the path), then the handles fade as the thermal
 * dots stamp along the path. The weight slider stays live throughout.
 */
const CharacterAct: React.FC<ActProps> = ({
  merchant,
  assets,
  progress,
  active,
  reducedMotion,
  dotWeight,
  onWeightChange,
}) => {
  const { skeleton, dotParams } = assets;
  const cloud = dotParams?.cloudGeom ?? null;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const p = reducedMotion ? 1 : progress;

  const geom = useMemo(() => {
    if (!skeleton || !cloud) {
      return null;
    }
    return {
      paths: skeletonPathDsCloud(skeleton, cloud),
      anchors: glyphAnchorsCloud(skeleton, cloud),
      points: glyphDotPointsCloud(skeleton, cloud, THERMAL_STEP_UNITS),
      nodes: nodeCount(skeleton),
      viewBox: { width: cloud.imageW, height: cloud.imageH },
      aspect: `${cloud.imageW} / ${cloud.imageH}`,
      pxPerUnit: cloudScale(cloud),
      stroke: cloud.capHeightPx * 0.045,
    };
  }, [skeleton, cloud]);

  // ---- Beat phases ----
  const cloudOpacity = reducedMotion ? 0.4 : phase(p, 0.1, 0.28);
  const printsOpacity = reducedMotion ? 0 : 1 - phase(p, 0.04, 0.24);
  const draw = reducedMotion ? 0 : phase(p, 0.28, 0.6); // 0..1 path draw
  const handlesOpacity = reducedMotion
    ? 0
    : phase(p, 0.42, 0.54) * (1 - phase(p, 0.62, 0.74));
  const dotReveal = reducedMotion ? 1 : phase(p, 0.62, 1);

  // Thermal dots stamp along the path as the handles fade.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !geom || !dotParams) {
      return;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return; // jsdom / no 2d context
    }
    const dpr =
      typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    canvas.width = Math.round(geom.viewBox.width * dpr);
    canvas.height = Math.round(geom.viewBox.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, geom.viewBox.width, geom.viewBox.height);
    const radius = (dotParams.dotSize / 2) * dotWeight * geom.pxPerUnit;
    const reveal = active && !reducedMotion ? dotReveal : 1;
    const count = Math.max(0, Math.round(geom.points.length * reveal));
    ctx.fillStyle =
      getComputedStyle(canvas).getPropertyValue("--text-color").trim() ||
      "#222";
    for (let i = 0; i < count && i < geom.points.length; i += 1) {
      const pt = geom.points[i];
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, Math.max(0.5, radius), 0, Math.PI * 2);
      ctx.fill();
    }
  }, [geom, dotParams, dotWeight, dotReveal, active, reducedMotion]);

  if (!geom || !dotParams) {
    return (
      <AssetPending>
        The character is mined from char_skeleton.json + dot_params.json.
      </AssetPending>
    );
  }

  const { width, height } = geom.viewBox;
  // Vector-editor handle geometry, sized in screen px (converted to viewBox
  // units so anchors/handles read at a constant on-screen size).
  const s = height / GLYPH_DISPLAY_PX;
  const anchorHalf = 1.75 * s; // ~3.5px filled square
  const handleR = 2.5 * s; // ~2.5px hollow circle
  const isBold = Math.abs(dotWeight - dotParams.weightBold) < 0.02;
  const stackP = phase(p, 0.02, 0.24);
  const activeIdx = Math.min(
    CHAR_PRINT_COUNT - 1,
    Math.floor(stackP * CHAR_PRINT_COUNT),
  );
  const printLayers = Array.from({ length: CHAR_STACK_LAYERS }, (_, d) => activeIdx - d).filter(
    (i) => i >= 0,
  );

  return (
    <div className={styles.charStageWrap} data-testid="act-character">
      <div className={styles.charGlyphBox} style={{ aspectRatio: geom.aspect }}>
        {/* Consensus cloud (the shared frame everything maps into). */}
        <ReceiptInkLayer
          src={charCloudSrc(merchant)}
          ariaLabel="Consensus letterform averaged from many prints"
          className={styles.charCloudFill}
          style={{ opacity: cloudOpacity }}
          testId="char-cloud"
        />
        {/* Real prints piling up, fading as the cloud resolves. */}
        {printsOpacity > 0.01
          ? printLayers.map((i, depth) => (
              <ReceiptInkLayer
                key={i}
                src={charPrintSrc(merchant, i)}
                className={styles.charPrintLayer}
                style={{
                  opacity: printsOpacity * (1 - depth * 0.28),
                  transform: `translate(${depth * 4}px, ${depth * -4}px)`,
                }}
              />
            ))
          : null}
        {/* Pen path + vector-editor handles, in the cloud's pixel space. */}
        <svg
          className={styles.penSvg}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {geom.paths.map((d, i) => (
            <path
              key={i}
              d={d}
              className={styles.penPath}
              pathLength={1}
              data-testid="pen-path"
              strokeWidth={geom.stroke}
              strokeDasharray={1}
              strokeDashoffset={1 - draw}
            />
          ))}
          <g style={{ opacity: handlesOpacity }}>
            {geom.anchors.handles.map((h, i) => (
              <g key={`h-${i}`}>
                <line
                  className={styles.handleLine}
                  x1={h.from.x}
                  y1={h.from.y}
                  x2={h.to.x}
                  y2={h.to.y}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
                <circle
                  className={styles.handleDot}
                  cx={h.to.x}
                  cy={h.to.y}
                  r={handleR}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              </g>
            ))}
            {geom.anchors.anchors.map((a, i) => (
              <rect
                key={`a-${i}`}
                className={styles.anchorSquare}
                data-testid="anchor-dot"
                x={a.x - anchorHalf}
                y={a.y - anchorHalf}
                width={anchorHalf * 2}
                height={anchorHalf * 2}
              />
            ))}
          </g>
        </svg>
        {/* Thermal dots stamped along the path. */}
        <canvas
          ref={canvasRef}
          className={styles.charThermalCanvas}
          data-testid="thermal-canvas"
          aria-label={`Thermal dots for ${merchant} at weight ${dotWeight.toFixed(2)}`}
        />
        <span className={styles.penBadge}>{geom.nodes} nodes</span>
      </div>
      <div className={styles.weightControl}>
        <div className={styles.weightRow}>
          <span>Weight</span>
          <input
            type="range"
            className={styles.weightSlider}
            min={WEIGHT_MIN}
            max={WEIGHT_MAX}
            step={WEIGHT_STEP}
            value={dotWeight}
            onChange={(e) => onWeightChange(Number(e.target.value))}
            aria-label="Dot weight"
            data-testid="weight-slider"
          />
          <span className={styles.weightValue}>{dotWeight.toFixed(2)}</span>
        </div>
        <span className={styles.weightLabel} data-bold={isBold}>
          {isBold
            ? `Bold — ${BOLD_WEIGHT_CALLOUT[merchant]}`
            : "Bold isn't a second font. It's one parameter."}
        </span>
      </div>
    </div>
  );
};

/* ==================================================================== */
/* Act 5 — A whole font                                                  */
/* ==================================================================== */

const FONT_GRID_COLS = 12; // matches grid-template-columns on desktop
const HERO_FLIGHT_SCALE = 5.2; // how big the hero rides in from stage center
const FONT_GRID_CAP_PERCENT = 70;
const FONT_GRID_BASELINE_PERCENT = 78.5;

const normalizedGlyphStyle = (
  metric: FontGlyphMetric | undefined,
  capHeight: number | undefined,
): React.CSSProperties | undefined => {
  if (!metric || !capHeight || capHeight <= 0) {
    return undefined;
  }
  const scale = FONT_GRID_CAP_PERCENT / capHeight;
  return {
    width: `${metric.width * scale}%`,
    height: `${metric.height * scale}%`,
    top: "auto",
    bottom: `${100 - FONT_GRID_BASELINE_PERCENT - metric.offset * scale}%`,
    transform: "translateX(-50%)",
  };
};

/** Smoothstep ease for the flight. */
const smooth = (t: number): number => t * t * (3 - 2 * t);

const WholeFontAct: React.FC<ActProps> = ({
  merchant,
  assets,
  progress,
  reducedMotion,
}) => {
  const p = reducedMotion ? 1 : progress;
  const total = FONT_CODEPOINTS.length;

  // The hero glyph handed forward from the thermal act flies into its OWN cell
  // in the atlas. Pick that cell (the skeleton's char if it's in the grid),
  // then reverse-FLIP: the cell rides in from stage center, big, and settles
  // to identity in its slot. Percent translates are relative to the cell box,
  // so this needs no DOM measurement and stays deterministic.
  const heroCp = useMemo(() => {
    const ch = assets.skeleton?.char;
    const cp = ch ? ch.codePointAt(0) : undefined;
    return cp && FONT_CODEPOINTS.includes(cp) ? cp : FONT_CODEPOINTS[0];
  }, [assets.skeleton]);
  const heroIdx = FONT_CODEPOINTS.indexOf(heroCp);
  const rows = Math.ceil(total / FONT_GRID_COLS);
  const heroCol = heroIdx % FONT_GRID_COLS;
  const heroRow = Math.floor(heroIdx / FONT_GRID_COLS);
  // Delta (in cell-box units) from the hero cell's center to the grid center.
  const dx = FONT_GRID_COLS / 2 - (heroCol + 0.5);
  const dy = rows / 2 - (heroRow + 0.5);

  const flight = smooth(phase(p, 0, 0.42)); // 0 = centered+big, 1 = in its cell

  return (
    <div className={styles.fontGrid} data-testid="act-font">
      {FONT_CODEPOINTS.map((cp, i) => {
        const isHero = cp === heroCp;
        // Non-hero cells cascade in after the hero starts landing; the hero is
        // always present because it is the object that traveled here.
        const shown = isHero || p >= 0.28 + (i / total) * 0.6;
        const heroStyle: React.CSSProperties | undefined = isHero
          ? {
              transform: `translate(${dx * (1 - flight) * 100}%, ${
                dy * (1 - flight) * 100
              }%) scale(${1 + (HERO_FLIGHT_SCALE - 1) * (1 - flight)})`,
              zIndex: flight < 1 ? 3 : undefined,
            }
          : undefined;
        const maskUrl = `url(${fontGlyphSrc(merchant, cp)})`;
        const glyphMetric = assets.fontMetrics?.glyphs[String(cp)];
        const metricStyle = normalizedGlyphStyle(
          glyphMetric,
          assets.fontMetrics?.capHeight,
        );
        return (
          <div
            key={cp}
            className={`${styles.fontCell}${isHero ? ` ${styles.fontCellHero}` : ""}`}
            data-shown={shown}
            data-hero={isHero || undefined}
            data-codepoint={cp}
            data-testid="font-cell"
            style={heroStyle}
          >
            {/* Type on the page: the alpha-mask glyph inherits the page text
                color (currentColor) on the transparent page background, in both
                themes. */}
            <div
              className={styles.fontGlyph}
              style={{
                WebkitMaskImage: maskUrl,
                maskImage: maskUrl,
                ...metricStyle,
              }}
              aria-hidden="true"
            />
          </div>
        );
      })}
    </div>
  );
};

/* ==================================================================== */
/* Act 6 — The receipt assembles (typing + LayoutLM boxes)               */
/* ==================================================================== */

const TYPE_START = 0.04;
const TYPE_END = 0.5;

/**
 * The former style / compose / print+labels acts merged into one. Phase A: the
 * receipt types itself out, header/items/summary/footer all at once — four
 * concurrent print heads revealing their words in order from final.webp. Phase
 * B: the ground-truth label boxes draw on, styled exactly like the LayoutLM
 * inference viz (LABEL_COLORS, fillOpacity 0.3, stroke 2, no vectorEffect).
 */
const AssembleAct: React.FC<ActProps> = ({
  merchant,
  assets,
  progress,
  reducedMotion,
}) => {
  const { compose, finalLabels } = assets;
  const p = reducedMotion ? 1 : progress;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [imgReady, setImgReady] = useState(false);

  const render = finalLabels?.metadata?.render;
  const renderW = render?.width ?? 760;
  const renderH = render?.height ?? 2471;
  const aspect = `${renderW} / ${renderH}`;

  // Every word's rect (labeled OR not), for the typing reveal.
  const wordRects = useMemo(() => {
    if (!finalLabels || !render) {
      return [] as Array<ReturnType<typeof toCssRectInner> | null>;
    }
    return finalLabels.bboxes.map((bb) =>
      bb && bb.length === 4 ? toCssRectInner(bb, render) : null,
    );
  }, [finalLabels, render]);

  // The four section groups (contiguous word-index ranges), typed in parallel.
  const groups = useMemo(() => {
    if (!compose) {
      return [] as number[][];
    }
    return COMPOSE_GROUP_ORDER.map((id) => compose.groups[id] ?? []).filter(
      (g) => g.length > 0,
    );
  }, [compose]);

  // Labeled boxes + families for phase B (LayoutLM-styled).
  const boxes = useMemo(
    () => (finalLabels ? buildLabelBoxes(finalLabels) : []),
    [finalLabels],
  );
  const families = useMemo(
    () => (finalLabels ? familiesIn(finalLabels) : []),
    [finalLabels],
  );

  const typingP = phase(p, TYPE_START, TYPE_END);
  const labelsShown = p >= TYPE_END + 0.06;

  // Load the printed receipt once.
  useEffect(() => {
    if (!finalLabels) {
      return;
    }
    const img = new window.Image();
    img.onload = () => {
      imgRef.current = img;
      setImgReady(true);
    };
    img.src = finalSrc(merchant);
    return () => {
      img.onload = null;
    };
  }, [merchant, finalLabels]);

  // Draw the revealed words. Each group reveals in order, all groups at once, so
  // the receipt materializes top-to-bottom in four places simultaneously; a
  // caret blinks at each section's leading edge.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return; // jsdom / no 2d context
    }
    canvas.width = renderW;
    canvas.height = renderH;
    ctx.clearRect(0, 0, renderW, renderH);
    const img = imgRef.current;
    if (!img || !imgReady) {
      return;
    }
    const t = clamp01(typingP);
    const drawWordCrop = (r: ReturnType<typeof toCssRectInner>) => {
      const sx = (r.left / 100) * renderW;
      const sy = (r.top / 100) * renderH;
      const sw = (r.width / 100) * renderW;
      const sh = (r.height / 100) * renderH;
      if (sw > 0 && sh > 0) {
        ctx.drawImage(img, sx, sy, sw, sh, sx, sy, sw, sh);
      }
    };
    groups.forEach((g) => {
      const revealed = Math.round(t * g.length);
      for (let i = 0; i < revealed; i += 1) {
        const r = wordRects[g[i]];
        if (r) {
          drawWordCrop(r);
        }
      }
    });
    // Blinking carets at each leading edge while typing.
    if (t > 0 && t < 1 && Math.floor(t * 48) % 2 === 0) {
      ctx.fillStyle =
        getComputedStyle(canvas).getPropertyValue("--color-blue").trim() ||
        "#4a90d9";
      groups.forEach((g) => {
        const revealed = Math.round(t * g.length);
        const r = wordRects[g[Math.min(g.length - 1, revealed)]];
        if (r) {
          ctx.fillRect(
            (r.left / 100) * renderW - 4,
            (r.top / 100) * renderH,
            6,
            (r.height / 100) * renderH,
          );
        }
      });
    }
  }, [typingP, imgReady, groups, wordRects, renderW, renderH]);

  if (!finalLabels || !compose) {
    return (
      <AssetPending>
        The receipt assembles from compose_steps.json + final.labels.json.
      </AssetPending>
    );
  }

  // Boxes reveal progressively (LayoutLM-style slicing — they simply appear,
  // top to bottom, no transform animation), mapped into receipt pixel space.
  const revealCount = Math.round(
    clamp01(phase(p, TYPE_END + 0.06, 0.9)) * boxes.length,
  );
  const overlayBoxes = boxes.slice(0, revealCount).map((box) => ({
    key: box.index,
    x: (box.rect.left / 100) * renderW,
    y: (box.rect.top / 100) * renderH,
    width: (box.rect.width / 100) * renderW,
    height: (box.rect.height / 100) * renderH,
    color: LABEL_COLORS[box.family] || LABEL_COLORS.O,
    testId: "final-label-box",
    family: box.family,
  }));

  return (
    <div className={styles.assembleStage} data-testid="act-assemble">
      <div
        className={`${styles.assembleReceipt} ${sharedStyles.receiptCard}`}
        style={{ aspectRatio: aspect }}
      >
        <canvas
          ref={canvasRef}
          className={styles.assembleCanvas}
          data-testid="assemble-canvas"
          aria-label={`Synthetic ${merchant} receipt assembling`}
        />
        {labelsShown ? (
          <LabelBoxOverlay
            className={styles.assembleBoxes}
            width={renderW}
            height={renderH}
            boxes={overlayBoxes}
          />
        ) : null}
      </div>
      {labelsShown ? (
        <div className={styles.assembleSide}>
          <LabelLegend families={families} />
        </div>
      ) : null}
    </div>
  );
};

/* (Acts 7 & 8 — Compose and Print+labels — merged into AssembleAct above.) */

/* ==================================================================== */
/* Act 9 — Finale: same machine, every store                            */
/* ==================================================================== */

/**
 * One merchant's finale card: a before/after PAIR. The real scan and our
 * synthesized render (both normalized to the same 760-wide box, so they align
 * 1:1) are stacked, and a divider auto-wipes left-right — as it sweeps, the
 * receipt stays continuous line-for-line, which is the proof that the synth
 * matches the real. The card renders at a common width and its own natural
 * height (Costco taller than Vons taller than Sprouts).
 */
/* Every logo is sized to the SAME visual weight (equal ink AREA) at its own
   natural aspect, so wide wordmarks (Trader Joe's, CVS) read short-and-wide and
   a squarer mark reads compact — none stretched. Area in (logo-height-unit)^2;
   the box height is clamped so tall/square marks don't push the receipt down. */
const LOGO_AREA = 1450;

const FinaleCard: React.FC<{
  merchant: Merchant;
  shown: boolean;
  wipe: number;
}> = ({ merchant, shown, wipe }) => {
  const [synthFailed, setSynthFailed] = useState(false);
  const [realFailed, setRealFailed] = useState(false);
  // Natural logo aspect, read from the mask image (works for any merchant — no
  // hardcoded per-logo dimensions needed as new merchants are added).
  const [logoAspect, setLogoAspect] = useState<number | null>(null);
  const dims = RECEIPT_DIMS[merchant];
  const logoUrl = `url(${logoSrc(merchant)})`;
  const wipePct = Math.round(clamp01(wipe) * 1000) / 10;
  const pair = !synthFailed && !realFailed;

  useEffect(() => {
    const img = new window.Image();
    img.onload = () => {
      if (img.naturalWidth && img.naturalHeight) {
        setLogoAspect(img.naturalWidth / img.naturalHeight);
      }
    };
    img.src = logoSrc(merchant);
    return () => {
      img.onload = null;
    };
  }, [merchant]);

  // Equal-area sizing: w = sqrt(AREA * aspect), h = sqrt(AREA / aspect).
  const aspect = logoAspect ?? 4;
  const logoW = Math.sqrt(LOGO_AREA * aspect);
  const logoH = Math.sqrt(LOGO_AREA / aspect);

  return (
    <figure
      className={styles.finaleCard}
      data-shown={shown}
      data-testid="finale-card"
      data-merchant={merchant}
    >
      {/* Merchant logo mark: currentColor through an alpha mask (theme-aware),
          at its natural aspect + equal ink area, centered in a fixed-height
          slot so the receipts below stay tops-aligned. */}
      <div className={styles.finaleLogoSlot}>
        <div
          className={styles.finaleLogo}
          role="img"
          aria-label={`${MERCHANT_LABELS[merchant]} logo`}
          style={{
            width: `${logoW}px`,
            height: `${logoH}px`,
            WebkitMaskImage: logoUrl,
            maskImage: logoUrl,
          }}
        />
      </div>
      <div
        className={styles.finaleFrame}
        data-testid="finale-frame"
        style={{ aspectRatio: `${dims.w} / ${dims.h}` }}
      >
        {!synthFailed ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={finalSrc(merchant)}
            alt={`Synthetic ${merchant} receipt`}
            className={styles.finaleSynth}
            loading="lazy"
            onError={() => setSynthFailed(true)}
            data-testid="finale-image"
          />
        ) : (
          <div className={styles.finaleFallback} data-testid="finale-fallback">
            {MERCHANT_LABELS[merchant]} receipt
          </div>
        )}
        {pair ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={realSrc(merchant)}
              alt={`Real ${merchant} receipt scan`}
              className={styles.finaleReal}
              loading="lazy"
              onError={() => setRealFailed(true)}
              style={{ clipPath: `inset(0 ${100 - wipePct}% 0 0)` }}
              data-testid="finale-real"
            />
            <span
              className={styles.finaleDivider}
              style={{ left: `${wipePct}%` }}
              aria-hidden="true"
            />
            <span className={`${styles.finaleChip} ${styles.finaleChipReal}`}>
              real
            </span>
            <span className={`${styles.finaleChip} ${styles.finaleChipSynth}`}>
              synth
            </span>
            {/* Mobile: one indicator that names whichever half currently
                dominates the wipe (shown via CSS on narrow cards). */}
            <span className={styles.finaleChipMobile}>
              {wipePct >= 50 ? "real" : "synth"}
            </span>
          </>
        ) : null}
      </div>
    </figure>
  );
};

const FINALE_PAN_MS = 17000; // full left->right pan duration (fits the dwell)
const FINALE_PAN_DELAY = 1200; // let the cards settle in before panning

const FinaleAct: React.FC<ActProps> = ({
  progress,
  active,
  reducedMotion,
}) => {
  const p = reducedMotion ? 1 : progress;
  // Divider oscillates across the pair; rests centered (0.5) at p=0/1 so a
  // paused finale shows a clean real|synth split.
  const wipe = 0.5 + 0.42 * Math.sin(p * Math.PI * 2);
  const rowRef = useRef<HTMLDivElement>(null);
  // Once the viewer touches the row (wheel/drag/touch) we hand over to manual
  // snap-scroll and never fight them again for this mount.
  const [manual, setManual] = useState(false);
  const autoPan = active && !manual && !reducedMotion;

  // Interaction cancels the auto-pan. A vertical mouse wheel also scrolls the
  // row horizontally (trackpad/touch do that natively); only intercept when the
  // row can scroll further, so the page still scrolls at the ends.
  useEffect(() => {
    const el = rowRef.current;
    if (!el) {
      return;
    }
    const takeOver = () => setManual(true);
    const onWheel = (e: WheelEvent) => {
      takeOver();
      const max = el.scrollWidth - el.clientWidth;
      if (max <= 1 || e.deltaY === 0 || Math.abs(e.deltaY) < Math.abs(e.deltaX)) {
        return;
      }
      const atStart = el.scrollLeft <= 0;
      const atEnd = el.scrollLeft >= max - 1;
      if ((e.deltaY < 0 && atStart) || (e.deltaY > 0 && atEnd)) {
        return; // let the page take over at the ends
      }
      e.preventDefault();
      el.scrollLeft += e.deltaY;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("pointerdown", takeOver, { passive: true });
    el.addEventListener("touchstart", takeOver, { passive: true });
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("pointerdown", takeOver);
      el.removeEventListener("touchstart", takeOver);
    };
  }, []);

  // Auto-pan: while the finale is active (and untouched), smoothly scroll the
  // row left->right through every pair, then rest at the end. Time-based so it
  // runs whether autoplay is playing or the act was jumped to.
  useEffect(() => {
    const el = rowRef.current;
    if (!el || !autoPan) {
      return;
    }
    const max = el.scrollWidth - el.clientWidth;
    if (max <= 1) {
      return; // everything fits — nothing to pan
    }
    let raf = 0;
    let start = 0;
    const ease = (t: number) => t * t * (3 - 2 * t);
    const step = (ts: number) => {
      if (!start) {
        start = ts;
      }
      const t = clamp01((ts - start - FINALE_PAN_DELAY) / FINALE_PAN_MS);
      el.scrollLeft = ease(t) * max;
      if (t < 1) {
        raf = requestAnimationFrame(step);
      }
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [autoPan]);

  return (
    <div
      ref={rowRef}
      className={styles.finaleRow}
      data-testid="act-finale"
      data-autopan={autoPan || undefined}
    >
      {MERCHANTS.map((m, i) => (
        <FinaleCard
          key={m}
          merchant={m}
          // Cards settle in quickly (not spread across the long dwell) so the
          // auto-pan reaches each one after it has appeared.
          shown={p >= (i / MERCHANTS.length) * 0.08}
          wipe={wipe}
        />
      ))}
    </div>
  );
};

/* ==================================================================== */
/* Dispatcher                                                            */
/* ==================================================================== */

const ACT_COMPONENTS: Record<ActId, React.FC<ActProps>> = {
  raw: RawMaterialAct,
  character: CharacterAct,
  font: WholeFontAct,
  assemble: AssembleAct,
  finale: FinaleAct,
};

export const ActView: React.FC<{ actId: ActId } & ActProps> = ({
  actId,
  ...props
}) => {
  const Component = ACT_COMPONENTS[actId];
  return <Component {...props} />;
};
