import { useState, useEffect, useCallback } from "react";
import { fetchReceiptDetails } from "../api";
import { ReceiptDetail } from "../interfaces";
import SelectedReceipt from './SelectedReceipt';
import ReceiptSelector from './ReceiptSelector';
import LoadingSpinner from '../components/LoadingSpinner';

const ReceiptValidation: React.FC = () => {
  const [receiptDetails, setReceiptDetails] = useState<{ [key: string]: ReceiptDetail }>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedReceipt, setSelectedReceipt] = useState<string | null>(null);
  const [currentLEK, setCurrentLEK] = useState<any>(null);
  const [hasMore, setHasMore] = useState(true);
  // Track LEK history for previous navigation
  const [lekHistory, setLekHistory] = useState<any[]>([null]);
  const [currentPage, setCurrentPage] = useState(0);

  const cdn_base_url = "https://dev.tylernorlund.com/";
  const BATCH_SIZE = 10;

  // Modify the useEffect to handle both initial load and pagination cases
  useEffect(() => {
    const receiptIds = Object.keys(receiptDetails);
    if (receiptIds.length > 0 && (!selectedReceipt || !receiptDetails[selectedReceipt])) {
      // Select first receipt if none selected or current selection is not in the new page
      setSelectedReceipt(receiptIds[0]);
    }
  }, [receiptDetails, selectedReceipt]);  // Keep both dependencies

  const loadReceipts = useCallback(async (lek: any) => {
    try {
      setLoading(true);
      setError(null);
      
      const response = await fetchReceiptDetails(BATCH_SIZE, lek);
      
      setReceiptDetails(prevDetails => {
        // Only update if the data is different
        if (JSON.stringify(prevDetails) === JSON.stringify(response.payload)) {
          return prevDetails;
        }
        return response.payload;
      });
      
      setCurrentLEK(response.last_evaluated_key);
      setHasMore(!!response.last_evaluated_key);

    } catch (err) {
      console.error('Error loading receipts:', err);
      setError(err instanceof Error ? err.message : 'An error occurred');
      setHasMore(false);
    } finally {
      setLoading(false);
    }
  }, []); // Remove selectedReceipt from dependencies

  // Initial load - add a mounted ref to prevent double calls
  useEffect(() => {
    const mounted = { current: true };
    
    if (mounted.current) {
      loadReceipts(null);
    }
    
    return () => {
      mounted.current = false;
    };
  }, [loadReceipts]);

  const handleNextPage = async () => {
    if (!hasMore || loading) return;
    
    // Save current LEK to history before loading next page
    setLekHistory(prev => [...prev, currentLEK]);
    setCurrentPage(prev => prev + 1);
    await loadReceipts(currentLEK);
  };

  const handlePreviousPage = async () => {
    if (currentPage === 0 || loading) return;
    
    // Remove current LEK from history and use the previous one
    const newHistory = [...lekHistory];
    const previousLEK = newHistory[currentPage - 1];
    newHistory.pop();
    
    setLekHistory(newHistory);
    setCurrentPage(prev => prev - 1);
    await loadReceipts(previousLEK);
  };

  useEffect(() => {
    console.log('Selected receipt changed:', selectedReceipt);
  }, [selectedReceipt]);

  const handleReceiptUpdate = (receiptId: string, newDetails: ReceiptDetail) => {
    setReceiptDetails(prev => ({
      ...prev,
      [receiptId]: newDetails
    }));
  };

  if (loading) {
    return <LoadingSpinner />;
  }

  if (error) {
    return (
      <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded">
        {error}
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col relative">
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
        onNext={handleNextPage}
        onPrevious={handlePreviousPage}
        hasMore={hasMore}
        loading={loading}
        canGoPrevious={currentPage > 0}
      />
      {loading && <LoadingSpinner />}
    </div>
  );
};

export default ReceiptValidation;