import React from 'react';
import { ReceiptDetail } from '../interfaces';
import ReceiptBoundingBox from './ReceiptBoundingBox';
import './ReceiptSelector.css';

interface ReceiptSelectorProps {
  receiptDetails: { [key: string]: ReceiptDetail };
  selectedReceipt: string | null;
  onSelectReceipt: (receiptId: string) => void;
  cdn_base_url: string;
  onNext: () => void;
  onPrevious: () => void;
  hasMore: boolean;
  loading: boolean;
  canGoPrevious: boolean;
}

const ReceiptSelector: React.FC<ReceiptSelectorProps> = ({
  receiptDetails,
  selectedReceipt,
  onSelectReceipt,
  cdn_base_url,
  onNext,
  onPrevious,
  hasMore,
  loading,
  canGoPrevious
}) => {
  const handleReceiptClick = (receiptId: string) => {
    console.log('Selecting receipt:', receiptId);
    onSelectReceipt(receiptId);
  };
  
  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const { scrollLeft, scrollWidth, clientWidth } = e.currentTarget;
    // Load more when user scrolls near the right edge
    if (scrollWidth - scrollLeft <= clientWidth * 1.5 && hasMore && !loading) {
      onNext();
    }
  };

  return (
    <div className="receipt-selector-container">
      <div 
        className="receipt-selector-content"
        onScroll={handleScroll}
      >
        <div className="receipt-selector-header">
          <button 
            onClick={onPrevious}
            className="pagination-button"
            aria-label="Previous page"
            disabled={!canGoPrevious || loading}
          >
            ← Previous
          </button>
          <button 
            onClick={onNext}
            className="pagination-button"
            aria-label="Next page"
            disabled={!hasMore || loading}
          >
            Next →
          </button>
        </div>
        <div className="receipt-selector-grid">
          {Object.entries(receiptDetails).map(([receiptId, detail]) => (
            <div 
              key={receiptId} 
              className={`receipt-item ${selectedReceipt === receiptId ? 'selected' : ''}`}
              onClick={() => handleReceiptClick(receiptId)}
              style={{ pointerEvents: 'all' }}
            >
              <div style={{ pointerEvents: 'none' }}>
                <ReceiptBoundingBox
                  detail={detail}
                  width={100}
                  isSelected={selectedReceipt === receiptId}
                  cdn_base_url={cdn_base_url}
                  highlightedWords={[]}
                />
              </div>
            </div>
          ))}
          {loading && (
            <div className="receipt-item loading">
              <div className="receipt-loading-spinner">
                <div className="spinner"></div>
              </div>
            </div>
          )}
        </div>
        {!hasMore && (
          <div className="text-center py-4 text-gray-500">
            No more receipts to load
          </div>
        )}
      </div>
    </div>
  );
};

export default ReceiptSelector; 