import { Routes, Route, Link } from 'react-router-dom';
import './App.css';
import Resume from './Resume'; // import the Resume component
import Receipt from './Receipt';
import ReceiptValidation from './ReceiptValidation';

const isDevelopment = process.env.NODE_ENV === "development";

function App() {
  return (
    <div>
      <header>
        <h1>
          <Link to="/">Tyler Norlund</Link>
        </h1>
      </header>
      <div className="container">
      <Routes>
        <Route
          path="/"
          element={
            <main>
              <img
                src="/face.png"
                alt="Tyler Norlund"
                style={{
                  borderRadius: '50%',
                  width: '200px',
                  height: '200px',
                  objectFit: 'cover',
                  marginTop: '1rem',
                }}
              />

                <button onClick={() => window.location.href = '/resume'}>
                  Résumé
                </button>
                <button onClick={() => window.location.href = '/receipt'}>
                  Receipt
                </button>

                {isDevelopment && (
                  <button onClick={() => window.location.href = '/receipt-validation'}>
                    Receipt Validation
                  </button>
                )}
            </main>
          }
        />
        <Route path="/resume" element={<Resume />} />
        <Route path="/receipt" element={<Receipt />} />
        {isDevelopment && (<Route path="/receipt-validation" element={<ReceiptValidation />} />)}
      </Routes>
      </div>
    </div>
  );
}

export default App;