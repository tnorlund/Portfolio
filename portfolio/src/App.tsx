import { Routes, Route, Link } from 'react-router-dom';
import './App.css';
import Resume from './Resume'; // import the Resume component

function App() {
  return (
    <div>
      <header>
        <h1>
          <Link to="/">Tyler Norlund</Link>
        </h1>
      </header>
      <div className="resume-container">
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
            </main>
          }
        />
        <Route path="/resume" element={<Resume />} />
      </Routes>
      </div>
    </div>
  );
}

export default App;