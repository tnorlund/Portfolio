// ReceiptGrid.tsx
import React, { useEffect, useState } from 'react';
import { fetchReceiptDetails } from './api';
import { ReceiptDetailsApiResponse, Receipt, ReceiptWord } from './interfaces';

interface ReceiptEntry {
  receipt: Receipt;
  words: ReceiptWord[];
}

const ReceiptGrid: React.FC = () => {
  const [receiptEntries, setReceiptEntries] = useState<ReceiptEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchReceiptDetails(5)
      .then((data: ReceiptDetailsApiResponse) => {
        // Transform the payload into an array of receipt entries
        const entries: ReceiptEntry[] = Object.values(data.payload)
          .filter((entry) => entry.receipt !== undefined)
          .map((entry) => ({
            receipt: entry.receipt!,
            words: entry.words || [],
          }));
        setReceiptEntries(entries);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return <div>Loading receipts...</div>;
  }

  if (error) {
    return <div>Error loading receipts: {error}</div>;
  }

  return (
    <div style={styles.gridContainer}>
      {receiptEntries.map(({ receipt, words }) => (
        <div key={`${receipt.image_id}-${receipt.id}`} style={styles.card}>
          <h3 style={styles.cardTitle}>
            {receipt.image_id} - {receipt.id}
          </h3>
          <div style={styles.svgContainer}>
            <svg
              viewBox={`0 0 ${receipt.width} ${receipt.height}`}
              preserveAspectRatio="xMidYMid meet"
              xmlns="http://www.w3.org/2000/svg"
              style={styles.svg}
            >
              {/* Render the receipt image normally */}
              <image
                width={receipt.width}
                height={receipt.height}
                preserveAspectRatio="xMidYMid meet"
                xlinkHref={`https://dev.tylernorlund.com/${receipt.cdn_s3_key}`}
              />
              {/*
                Wrap the polygons in a group that applies a transform so that
                the API's coordinate system (with y=0 at the bottom) maps correctly.
                The transform translates the origin to the bottom of the image and then
                flips vertically.
              */}
              <g transform={`translate(0, ${receipt.height}) scale(1, -1)`}>
                {words.map((word) => {
                  // Use the four corner coordinates from the API directly.
                  // (These coordinates are assumed to be in the original image coordinate system.)
                  const points = `
                    ${word.top_left.x},${word.top_left.y} 
                    ${word.top_right.x},${word.top_right.y} 
                    ${word.bottom_right.x},${word.bottom_right.y} 
                    ${word.bottom_left.x},${word.bottom_left.y}
                  `;
                  return (
                    <polygon
                      key={word.id}
                      points={points}
                      stroke="red"
                      fill="transparent"
                      strokeWidth="2"
                    />
                  );
                })}
              </g>
            </svg>
          </div>
        </div>
      ))}
    </div>
  );
};

const styles: { [key: string]: React.CSSProperties } = {
  gridContainer: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))',
    gap: '1rem',
    padding: '1rem',
  },
  card: {
    padding: '1rem',
    border: '1px solid var(--test-color)',
    borderRadius: '8px',
    backgroundColor: 'var(--background-color)',
    maxWidth: '300px',
    margin: '0 auto',
  },
  cardTitle: {
    textAlign: 'center',
    marginBottom: '0.5rem',
  },
  svgContainer: {
    width: '100%',
    overflow: 'hidden',
  },
  svg: {
    width: '100%',
    height: 'auto',
    display: 'block',
  },
};

export default ReceiptGrid;