import React from 'react';
import TagValidationChart from './TagValidationChart';

const TagValidationStats: React.FC = () => {
  return (
    <div>
      <h2>Tag Validation Statistics</h2>
      <TagValidationChart />
      
      {/* Optional: Add a table view below the chart */}
      <table style={{ marginTop: '20px', width: '100%' }}>
        <thead>
          <tr>
            <th>Tag</th>
            <th>Valid</th>
            <th>Invalid</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>
          {/* We'll need to move the table data into TagValidationChart 
              or create a separate component for it if still needed */}
        </tbody>
      </table>
    </div>
  );
};

export default TagValidationStats; 