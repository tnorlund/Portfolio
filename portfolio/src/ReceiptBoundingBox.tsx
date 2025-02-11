import React from 'react';
import { ReceiptDetail } from './interfaces';

interface ReceiptBoundingBoxProps {
  detail: ReceiptDetail;
  width?: number;
  isSelected?: boolean;
  onClick?: () => void;
  cdn_base_url: string;
}

const ReceiptBoundingBox: React.FC<ReceiptBoundingBoxProps> = ({
  detail,
  width = 200,
  isSelected = false,
  onClick,
  cdn_base_url
}) => {
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
              strokeWidth={isSelected ? "2" : "1"}
              opacity={isSelected ? "0.8" : "0.5"}
            />
          );
        })}
      </svg>
    </div>
  );
};

export default ReceiptBoundingBox; 