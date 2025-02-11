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
  addingTagType?: string;
  onWordTagClick?: (word: ReceiptWord, event: { clientX: number; clientY: number }) => void;
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
  addingTagType,
  onWordTagClick,
}) => {
  const [isDrawing, setIsDrawing] = useState(false);
  const [selectionBox, setSelectionBox] = useState<SelectionBox | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const startDrawing = (x: number, y: number) => {
    if (!isAddingTag || !containerRef.current) return;
    
    const rect = containerRef.current.getBoundingClientRect();
    setIsDrawing(true);
    setSelectionBox({
      startX: x - rect.left,
      startY: y - rect.top,
      endX: x - rect.left,
      endY: y - rect.top,
    });
  };

  const updateDrawing = (x: number, y: number) => {
    if (!isDrawing || !containerRef.current || !selectionBox) return;

    const rect = containerRef.current.getBoundingClientRect();
    setSelectionBox({
      ...selectionBox,
      endX: x - rect.left,
      endY: y - rect.top,
    });
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    startDrawing(e.clientX, e.clientY);
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    updateDrawing(e.clientX, e.clientY);
  };

  const handleTouchStart = (e: React.TouchEvent) => {
    e.preventDefault(); // Prevent scrolling while drawing
    const touch = e.touches[0];
    startDrawing(touch.clientX, touch.clientY);
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    e.preventDefault(); // Prevent scrolling while drawing
    const touch = e.touches[0];
    updateDrawing(touch.clientX, touch.clientY);
  };

  const endDrawing = () => {
    if (!isDrawing || !selectionBox || !onSelectionComplete) return;

    // Calculate the selection box coordinates
    const left = Math.min(selectionBox.startX, selectionBox.endX);
    const right = Math.max(selectionBox.startX, selectionBox.endX);
    const top = Math.min(selectionBox.startY, selectionBox.endY);
    const bottom = Math.max(selectionBox.startY, selectionBox.endY);

    // Find words that intersect with the selection box
    const selectedWords = detail.words.filter(word => {
      // Convert word coordinates to screen coordinates using scaleFactor
      const points = [
        { x: word.top_left.x * width, y: (1 - word.top_left.y) * height },
        { x: word.top_right.x * width, y: (1 - word.top_right.y) * height },
        { x: word.bottom_right.x * width, y: (1 - word.bottom_right.y) * height },
        { x: word.bottom_left.x * width, y: (1 - word.bottom_left.y) * height }
      ];

      // Find bounding box of the word
      const wordLeft = Math.min(...points.map(p => p.x));
      const wordRight = Math.max(...points.map(p => p.x));
      const wordTop = Math.min(...points.map(p => p.y));
      const wordBottom = Math.max(...points.map(p => p.y));

      return !(wordLeft > right || 
               wordRight < left || 
               wordTop > bottom || 
               wordBottom < top);
    });

    // Create a summary of selected words with their existing tags
    const selectionSummary = {
      selected_tag: addingTagType || null,
      selected_words: selectedWords.map(word => {
        const matchingTags = detail.word_tags.filter(tag => 
          tag.word_id === word.word_id &&
          tag.line_id === word.line_id &&
          tag.receipt_id === word.receipt_id &&
          tag.image_id === word.image_id
        );

        return {
          word: word,
          tags: matchingTags
        };
      })
    };

    console.log('Batch Update:', selectionSummary);

    onSelectionComplete(selectedWords);
    setIsDrawing(false);
    setSelectionBox(null);
  };

  const handleBoundingBoxClick = (word: ReceiptWord) => {
    if (addingTagType && onClick) {
      const matchingTags = detail.word_tags.filter(tag => 
        tag.word_id === word.word_id &&
        tag.line_id === word.line_id &&
        tag.receipt_id === word.receipt_id &&
        tag.image_id === word.image_id
      );

      console.log('Single Update test:', {
        selected_tag: addingTagType || null,
        selected_words: [{
          word: word,
          tags: matchingTags
        }]
      });

      onClick(word);
    }
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
        touchAction: isAddingTag ? 'none' : 'auto', // Prevent scrolling when in drawing mode
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={endDrawing}
      onMouseLeave={endDrawing}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={endDrawing}
      onTouchCancel={endDrawing}
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
                fill={isAddingTag ? "var(--color-yellow)" : "transparent"}
                stroke={isAddingTag ? "var(--color-yellow)" : "var(--color-red)"}
                strokeWidth={isHighlighted ? "3" : isAddingTag ? "2" : "1"}
                opacity={isAddingTag ? "0.2" : isSelected ? "0.8" : "0.5"}
                style={{ cursor: isAddingTag ? 'crosshair' : 'pointer' }}
                onClick={isAddingTag ? undefined : (e) => {  // Don't attach click handler in selection mode
                  e.stopPropagation();
                  if (onWordTagClick) {
                    onWordTagClick(word, { clientX: e.clientX, clientY: e.clientY });
                  }
                }}
              />
            );
          })}
        </svg>
      </div>
      
      {selectionBox && (
        <>
          <div
            style={{
              position: 'absolute',
              left: Math.min(selectionBox.startX, selectionBox.endX),
              top: Math.min(selectionBox.startY, selectionBox.endY),
              width: Math.abs(selectionBox.endX - selectionBox.startX),
              height: Math.abs(selectionBox.endY - selectionBox.startY),
              border: '2px solid var(--color-blue)',
              pointerEvents: 'none',
            }}
          />
          <div
            style={{
              position: 'absolute',
              left: Math.min(selectionBox.startX, selectionBox.endX) + 2,
              top: Math.min(selectionBox.startY, selectionBox.endY) + 2,
              width: Math.abs(selectionBox.endX - selectionBox.startX) + 2,
              height: Math.abs(selectionBox.endY - selectionBox.startY) + 2,
              backgroundColor: 'var(--color-blue)',
              opacity: 0.2,
              pointerEvents: 'none',
            }}
          />
        </>
      )}
    </div>
  );
};

export default ReceiptBoundingBox; 