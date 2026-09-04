# Sharing Next.js Project State with Cursor

There are several ways to share your Next.js project state with Cursor. Here are the options from simplest to most advanced:

## 1. **Built-in Cursor Features (Recommended)**

### Current File Context

- Cursor automatically has access to your current file and surrounding context
- Use `@files` to reference specific files
- Use `@folder` to reference entire directories

### Terminal Integration

- Cursor can see terminal output when you run commands
- Use the terminal panel to show real-time dev server logs
- Run `npm run dev` in the terminal so Cursor can see the output

### Browser Developer Tools

- Keep browser dev tools open
- Copy/paste console errors directly into Cursor chat
- Take screenshots of hydration errors for visual context

## 2. **Project State Monitoring Script**

Create a simple monitoring script that outputs project state:

```bash
# Add to package.json scripts
"monitor": "node monitor-project.js"
```

```javascript
// monitor-project.js
const fs = require("fs");
const { exec } = require("child_process");

async function checkProjectState() {
  console.log("=== Next.js Project State ===");

  // Check dev server
  exec('ps aux | grep "next dev" | grep -v grep', (error, stdout) => {
    console.log("Dev Server:", stdout ? "Running" : "Not running");
  });

  // Check API endpoints
  const apiFiles = fs.readdirSync("./pages/api/");
  console.log(
    "API Endpoints:",
    apiFiles.map((f) => `/api/${f.replace(".ts", "")}`)
  );

  // Check build status
  console.log(
    "Build artifacts:",
    fs.existsSync("./.next") ? "Present" : "Missing"
  );

  // Test API endpoints
  setTimeout(async () => {
    for (const file of apiFiles) {
      const endpoint = `/api/${file.replace(".ts", "")}`;
      try {
        const response = await fetch(`http://localhost:3000${endpoint}`);
        console.log(`${endpoint}: ${response.status}`);
      } catch (error) {
        console.log(`${endpoint}: Error - ${error.message}`);
      }
    }
  }, 1000);
}

checkProjectState();
setInterval(checkProjectState, 30000); // Check every 30 seconds
```

## 3. **MCP Integration (Advanced)**

### Setup MCP Server

1. **Install MCP SDK:**

```bash
npm install @modelcontextprotocol/sdk
```

2. **Configure Cursor:**
   Add to your Cursor settings (or create `.cursorrules`):

```json
{
  "mcp": {
    "servers": {
      "nextjs-state": {
        "command": "node",
        "args": ["mcp-server.js"],
        "cwd": "."
      }
    }
  }
}
```

3. **Use MCP Tools:**
   The MCP server I created provides these tools:

- `get_dev_server_status` - Check if dev server is running
- `get_build_status` - Check build artifacts and Next.js version
- `get_api_endpoints` - List and test all API endpoints
- `test_api_endpoint` - Test specific endpoint
- `get_component_errors` - Check for hydration errors

### Example Usage with Cursor:

```
@mcp get_dev_server_status
@mcp test_api_endpoint /api/image_count
@mcp get_component_errors
```

## 4. **Real-time Browser Integration**

For hydration errors specifically, you can:

1. **Add Error Boundary:**

```jsx
// components/ErrorBoundary.js
import { Component } from "react";

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.log("Error caught by boundary:", error, errorInfo);
    // Send to monitoring service or log to file
  }

  render() {
    if (this.state.hasError) {
      return <h2>Something went wrong: {this.state.error.message}</h2>;
    }
    return this.props.children;
  }
}
```

2. **Add Console Monitoring:**

```javascript
// Add to _app.tsx
if (typeof window !== "undefined") {
  const originalError = console.error;
  console.error = (...args) => {
    originalError(...args);
    // Could send to external service or store locally
    if (args[0].includes("Hydration")) {
      localStorage.setItem("hydrationError", JSON.stringify(args));
    }
  };
}
```

## 5. **Recommended Workflow**

For your current hydration issue:

1. **Terminal monitoring:**

```bash
npm run dev | tee dev-server.log
```

2. **Browser console:**

- Open dev tools → Console
- Enable "Preserve log"
- Refresh page and capture hydration errors

3. **Share with Cursor:**

- Copy the exact error messages
- Share the component code that's causing issues
- Include browser network tab if API calls are involved

## Quick Commands for Debugging

```bash
# Check server status
ps aux | grep "next dev"

# Test API endpoints
curl http://localhost:3000/api/image_count
curl http://localhost:3000/api/receipt_count

# Check build
ls -la .next/

# Monitor logs
tail -f dev-server.log
```

This approach gives you comprehensive project state visibility without complex setup!
