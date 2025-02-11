import React, { useState, useRef } from 'react';
import { ReceiptDetail, ReceiptWord } from './interfaces';

interface SelectionBox {
  startX: number;
  startY: number;
  endX: number;
  endY: number;
}

interface ReceiptBoundingBoxProps {
  detail: ReceiptDetail;
  width: number;
  isSelected: boolean;
  cdn_base_url: string;
  highlightedWords: ReceiptWord[];
  onClick?: (word: ReceiptWord) => void;
  onSelectionComplete?: (words: ReceiptWord[]) => void;
  isAddingTag?: boolean;
}

const ReceiptBoundingBox: React.FC<ReceiptBoundingBoxProps> = ({
  detail,
  width,
  isSelected,
  cdn_base_url,
  highlightedWords,
  onClick,
  onSelectionComplete,
  isAddingTag,
}) => {
  const [isDrawing, setIsDrawing] = useState(false);
  const [selectionBox, setSelectionBox] = useState<SelectionBox | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = (e: React.MouseEvent) => {
    if (!isAddingTag || !containerRef.current) return;
    
    const rect = containerRef.current.getBoundingClientRect();
    setIsDrawing(true);
    setSelectionBox({
      startX: e.clientX - rect.left,
      startY: e.clientY - rect.top,
      endX: e.clientX - rect.left,
      endY: e.clientY - rect.top,
    });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDrawing || !containerRef.current || !selectionBox) return;

    const rect = containerRef.current.getBoundingClientRect();
    setSelectionBox({
      ...selectionBox,
      endX: e.clientX - rect.left,
      endY: e.clientY - rect.top,
    });
  };

  const handleMouseUp = () => {
    if (!isDrawing || !selectionBox || !onSelectionComplete) return;

    // Calculate the selection box coordinates
    const left = Math.min(selectionBox.startX, selectionBox.endX);
    const right = Math.max(selectionBox.startX, selectionBox.endX);
    const top = Math.min(selectionBox.startY, selectionBox.endY);
    const bottom = Math.max(selectionBox.startY, selectionBox.endY);

    // Find words that intersect with the selection box
    const selectedWords = detail.words.filter(word => {
      const box = word.bounding_box;
      const wordLeft = box.x * width;
      const wordRight = (box.x + box.width) * width;
      const wordTop = box.y * width;
      const wordBottom = (box.y + box.height) * width;

      return !(wordLeft > right || 
               wordRight < left || 
               wordTop > bottom || 
               wordBottom < top);
    });

    onSelectionComplete(selectedWords);
    setIsDrawing(false);
    setSelectionBox(null);
  };

  const { receipt, words } = detail;
  const imageUrl = cdn_base_url + receipt.cdn_s3_key;
  
  // Calculate scaling based on the original image dimensions
  const scaleFactor = width / receipt.width;
  const height = receipt.height * scaleFactor;

  return (
    <div 
      ref={containerRef}
      style={{ 
        position: 'relative',
        cursor: isAddingTag ? 'crosshair' : 'default',
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      <div 
        className={`cursor-pointer transition-transform ${isSelected ? 'scale-100' : 'hover:scale-105'}`}
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
                fill={isAddingTag ? "rgba(255, 255, 0, 0.2)" : "none"}
                stroke={isAddingTag ? "yellow" : "red"}
                strokeWidth={isHighlighted ? "3" : isAddingTag ? "2" : "1"}
                opacity={isAddingTag ? "0.8" : isSelected ? "0.8" : "0.5"}
                style={{ cursor: isAddingTag ? 'pointer' : 'default' }}
                onClick={(e) => {
                  if (onClick && isAddingTag) {
                    e.stopPropagation();
                    onClick(word);
                  }
                }}
              />
            );
          })}
        </svg>
      </div>
      
      {selectionBox && (
        <div
          style={{
            position: 'absolute',
            left: Math.min(selectionBox.startX, selectionBox.endX),
            top: Math.min(selectionBox.startY, selectionBox.endY),
            width: Math.abs(selectionBox.endX - selectionBox.startX),
            height: Math.abs(selectionBox.endY - selectionBox.startY),
            border: '2px solid var(--text-color)',
            backgroundColor: 'rgba(255, 255, 255, 0.1)',
            pointerEvents: 'none',
          }}
        />
      )}
    </div>
  );
};

export default ReceiptBoundingBox; 