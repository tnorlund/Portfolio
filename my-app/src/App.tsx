import React from 'react';
import './App.css';

function App() {
  return (
    <div className="App">
      <header className="App-header">
      <h1>Tyler Norlund</h1>
      <p>Data Person</p>
      <img src="/face.jpg" alt="Tyler Norlund" style={{
            borderRadius: '50%',
            width: '200px',       // pick whatever size you want
            height: '200px',      // same as width for a perfect circle
            objectFit: 'cover',   // ensures the image fully covers the circle
          }} />
      {/* <button>Resume</button> */}
      </header>
    </div>
  );
}

export default App;
