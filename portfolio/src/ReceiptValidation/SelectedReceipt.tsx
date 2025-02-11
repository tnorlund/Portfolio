import React from 'react';
import { ReceiptDetail } from '../interfaces';
import ReceiptBoundingBox from '../ReceiptBoundingBox';

interface SelectedReceiptProps {
  selectedReceipt: string | null;
  receiptDetails: { [key: string]: ReceiptDetail };
  cdn_base_url: string;
}

const SelectedReceipt: React.FC<SelectedReceiptProps> = ({
  selectedReceipt,
  receiptDetails,
  cdn_base_url,
}) => {
  return (
    <div className="flex-grow border-b bg-gray-50">
      <div className="max-w-6xl mx-auto p-4">
        <h1 className="text-2xl font-bold mb-4">Receipt Validation</h1>
        <div className="flex justify-center items-center" style={{ minHeight: '60vh' }}>
          {selectedReceipt && receiptDetails[selectedReceipt] ? (
            <ReceiptBoundingBox
              detail={receiptDetails[selectedReceipt]}
              width={400}
              isSelected={true}
              cdn_base_url={cdn_base_url}
            />
          ) : (
            <div className="text-gray-500 bg-white p-8 rounded-lg shadow-sm">
              Select a receipt below to view details
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default SelectedReceipt; 