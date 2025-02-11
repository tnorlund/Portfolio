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
    <div className="w-full flex flex-col">
      <h1 className="text-2xl font-bold p-4">Receipt Validation</h1>
      
      {/* Main container */}
      <div style={{ display: 'flex', position: 'relative' }}>
        {/* Left panel - Will determine height */}
        <div style={{ 
          width: '50%',
          backgroundColor: 'red',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center'
        }}>
          {selectedReceipt && receiptDetails[selectedReceipt] ? (
            <ReceiptBoundingBox
              detail={receiptDetails[selectedReceipt]}
              width={450}
              isSelected={true}
              cdn_base_url={cdn_base_url}
            />
          ) : (
            <div>Select a receipt</div>
          )}
        </div>
        
        {/* Right panel - Should scroll */}
        <div style={{ 
          width: '50%',
          backgroundColor: 'blue',
          position: 'absolute',
          right: 0,
          top: 0,
          bottom: 0,
          overflowY: 'auto'
        }}>
          <div style={{ padding: '20px' }}>
            <h2 style={{ color: 'white', marginBottom: '1rem' }}>Receipt Details</h2>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
            <div style={{ color: 'white', height: '200px'}}>Content here will scroll</div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SelectedReceipt; 