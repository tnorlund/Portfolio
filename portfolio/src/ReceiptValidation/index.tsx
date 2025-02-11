import { useState, useEffect } from "react";
import { fetchReceiptDetails } from "../api";
import { ReceiptDetail } from "../interfaces";
import SelectedReceipt from './SelectedReceipt';
import ReceiptSelector from './ReceiptSelector';

const ReceiptValidation: React.FC = () => {
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

  const handleReceiptUpdate = (receiptId: string, newDetails: ReceiptDetail) => {
    setReceiptDetails(prev => ({
      ...prev,
      [receiptId]: newDetails
    }));
  };

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
      <SelectedReceipt
        selectedReceipt={selectedReceipt}
        receiptDetails={receiptDetails}
        cdn_base_url={cdn_base_url}
        onReceiptUpdate={handleReceiptUpdate}
      />
      <ReceiptSelector
        receiptDetails={receiptDetails}
        selectedReceipt={selectedReceipt}
        onSelectReceipt={setSelectedReceipt}
        cdn_base_url={cdn_base_url}
      />
    </div>
  );
};

export default ReceiptValidation;