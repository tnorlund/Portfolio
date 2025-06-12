Approach 1: Perpendicular Projection (Most Promising)
Instead of finding extremes along the primary axis, find lines that are furthest left/right when projected onto the secondary axis (perpendicular to text direction). This would give us lines that are actually at the true left/right edges of the receipt content.
Apply to PhotoReceipt...
"
Approach 2: Statistical Consensus
Analyze the distribution of ALL line edges and use percentiles:
Left boundary: 5th percentile of all left edges
Right boundary: 95th percentile of all right edges
This filters out outliers and finds the "typical" boundaries
Approach 3: Rotated Bounding Box
Create a properly rotated bounding rectangle around all line content:
Use the average text angle to rotate coordinate system
Find min/max in rotated coordinates
Transform back to original coordinates
Approach 4: Edge Density Analysis
Find regions with highest density of line starts/ends:
Histogram of left edge positions
Histogram of right edge positions
Use peaks to identify true boundaries
