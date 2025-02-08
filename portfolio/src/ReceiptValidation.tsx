import { useState } from "react";


function ReceiptValidation() {
  const [receipt, setReceipt] = useState('');
  const [isReceiptValid, setIsReceiptValid] = useState(false);

  const handleReceiptChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setReceipt(e.target.value);
  };

  const handleValidation = () => {
    setIsReceiptValid(receipt === '123456');
  };

  return (
    <div>
      <h1>Receipt Validation</h1>
    </div>
  );
}

export default ReceiptValidation;