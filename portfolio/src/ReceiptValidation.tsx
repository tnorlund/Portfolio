import { useState, useEffect } from "react";
import { fetchReceiptDetails } from "./api";
import { ReceiptDetail } from "./interfaces";
import ReceiptBoundingBox from './ReceiptBoundingBox';

function ReceiptValidation() {
  const [receiptDetails, setReceiptDetails] = useState<{ [key: string]: ReceiptDetail }>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedReceipt, setSelectedReceipt] = useState<string | null>(null);

  const cdn_base_url = "https://dev.tylernorlund.com/";

  useEffect(() => {
    const loadReceiptDetails = async () => {
      try {
        setLoading(true);
        setError(null);
        const response = await fetchReceiptDetails(10);
        setReceiptDetails(response.payload);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'An error occurred');
      } finally {
        setLoading(false);
      }
    };

    loadReceiptDetails();
  }, []);

  if (loading) {
    return <div className="text-center py-4">Loading...</div>;
  }

  if (error) {
    return (
      <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded">
        {error}
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col">
      {/* Top Section - Selected Receipt */}
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

      {/* Bottom Section - Receipt Selector */}
      <div className="h-[200px] bg-white border-t shadow-inner">
        <div className="max-w-6xl mx-auto p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-700">Available Receipts</h2>
            <span className="text-xs text-gray-500">Scroll horizontally to see more →</span>
          </div>
          <div style={{display: 'flex', overflowX: 'auto', gap: '10px', padding: '10px'}}>
            {Object.entries(receiptDetails).map(([key, detail]) => (
              <div 
                key={key} 
                className={`
                  flex flex-col items-center gap-1 p-2 rounded-lg flex-shrink-0
                  ${selectedReceipt === key ? 'bg-blue-50' : 'hover:bg-gray-50'}
                  transition-colors duration-200
                `}
              >
                <ReceiptBoundingBox
                  detail={detail}
                  width={100}
                  isSelected={selectedReceipt === key}
                  onClick={() => setSelectedReceipt(selectedReceipt === key ? null : key)}
                  cdn_base_url={cdn_base_url}
                />
                <span className="text-xs text-gray-600">
                  Receipt {detail.receipt.receipt_id}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default ReceiptValidation;