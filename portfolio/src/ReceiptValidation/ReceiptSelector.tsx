import React from 'react';
import { ReceiptDetail } from '../interfaces';
import ReceiptBoundingBox from '../ReceiptBoundingBox';
import './ReceiptSelector.css';

interface ReceiptSelectorProps {
  receiptDetails: { [key: string]: ReceiptDetail };
  selectedReceipt: string | null;
  onSelectReceipt: (receiptId: string | null) => void;
  cdn_base_url: string;
}

const ReceiptSelector: React.FC<ReceiptSelectorProps> = ({
  receiptDetails,
  selectedReceipt,
  onSelectReceipt,
  cdn_base_url,
}) => {
  console.log('Receipt Details:', Object.keys(receiptDetails).length, receiptDetails);
  
  return (
    <div className="receipt-selector-container">
      <div className="receipt-selector-content">
        <div className="receipt-selector-header">
          <h2 className="receipt-selector-title">Available Receipts</h2>
          <span className="receipt-selector-hint">Scroll horizontally to see more →</span>
        </div>
        <div className="receipt-selector-grid">
          {Object.entries(receiptDetails).map(([key, detail]) => (
            <div 
              key={key} 
              className={`receipt-item ${selectedReceipt === key ? 'selected' : ''}`}
              onClick={() => onSelectReceipt(selectedReceipt === key ? null : key)}
            >
              <ReceiptBoundingBox
                detail={detail}
                width={100}
                isSelected={selectedReceipt === key}
                onClick={() => onSelectReceipt(selectedReceipt === key ? null : key)}
                cdn_base_url={cdn_base_url}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default ReceiptSelector; 