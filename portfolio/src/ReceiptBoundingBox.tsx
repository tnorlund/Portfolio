import React from 'react';
import { ReceiptDetail, ReceiptWord } from './interfaces';

interface ReceiptBoundingBoxProps {
  detail: ReceiptDetail;
  width?: number;
  isSelected?: boolean;
  onClick?: () => void;
  cdn_base_url: string;
  highlightedWords?: ReceiptWord[];
}

const ReceiptBoundingBox: React.FC<ReceiptBoundingBoxProps> = ({
  detail,
  width = 200,
  isSelected = false,
  onClick,
  cdn_base_url,
  highlightedWords = []
}): JSX.Element => {
  const { receipt, words } = detail;
  const imageUrl = cdn_base_url + receipt.cdn_s3_key;
  
  // Calculate scaling based on the original image dimensions
  const scaleFactor = width / receipt.width;
  const height = receipt.height * scaleFactor;

  return (
    <div 
      className={`cursor-pointer transition-transform ${isSelected ? 'scale-100' : 'hover:scale-105'}`}
      onClick={onClick}
    >
      <svg
        viewBox={`0 0 ${receipt.width} ${receipt.height}`}
        width={width}
        height={height}
        className={`rounded-lg ${isSelected ? 'shadow-xl' : 'shadow-md'}`}
      >
        <image
          href={imageUrl}
          x="0"
          y="0"
          width={receipt.width}
          height={receipt.height}
        />
        
        {words.map((word, idx) => {
          const isHighlighted = highlightedWords.some(hw => {
            const matches = 
              hw.word_id === word.word_id && 
              hw.line_id === word.line_id && 
              hw.receipt_id === word.receipt_id && 
              hw.image_id === word.image_id;
            return matches;
          });
          const points = `
            ${word.top_left.x * receipt.width},${(1 - word.top_left.y) * receipt.height} 
            ${word.top_right.x * receipt.width},${(1 - word.top_right.y) * receipt.height} 
            ${word.bottom_right.x * receipt.width},${(1 - word.bottom_right.y) * receipt.height} 
            ${word.bottom_left.x * receipt.width},${(1 - word.bottom_left.y) * receipt.height}
          `;
          
          return (
            <polygon
              key={idx}
              points={points}
              fill="none"
              stroke="red"
              strokeWidth={isHighlighted ? "3" : "1"}
              opacity={isSelected ? "0.8" : "0.5"}
            />
          );
        })}
      </svg>
    </div>
  );
};

export default ReceiptBoundingBox; 