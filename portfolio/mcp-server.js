#!/usr/bin/env node

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import fs from "fs/promises";
import path from "path";
import { exec } from "child_process";
import { promisify } from "util";

const execAsync = promisify(exec);

class NextJSMCPServer {
  constructor() {
    this.server = new Server(
      {
        name: "nextjs-project-state",
        version: "0.1.0",
      },
      {
        capabilities: {
          tools: {},
        },
      }
    );

    this.setupToolHandlers();
  }

  setupToolHandlers() {
    this.server.setRequestHandler(ListToolsRequestSchema, async () => {
      return {
        tools: [
          {
            name: "get_dev_server_status",
            description:
              "Check if Next.js dev server is running and on which port",
            inputSchema: {
              type: "object",
              properties: {},
            },
          },
          {
            name: "get_build_status",
            description: "Get current build status and any errors",
            inputSchema: {
              type: "object",
              properties: {},
            },
          },
          {
            name: "get_api_endpoints",
            description: "List all available API endpoints and test them",
            inputSchema: {
              type: "object",
              properties: {},
            },
          },
          {
            name: "get_component_errors",
            description: "Check for hydration errors and component issues",
            inputSchema: {
              type: "object",
              properties: {},
            },
          },
          {
            name: "test_api_endpoint",
            description: "Test a specific API endpoint",
            inputSchema: {
              type: "object",
              properties: {
                endpoint: {
                  type: "string",
                  description: "API endpoint to test (e.g., /api/image_count)",
                },
              },
              required: ["endpoint"],
            },
          },
        ],
      };
    });

    this.server.setRequestHandler(CallToolRequestSchema, async (request) => {
      const { name, arguments: args } = request.params;

      try {
        switch (name) {
          case "get_dev_server_status":
            return await this.getDevServerStatus();
          case "get_build_status":
            return await this.getBuildStatus();
          case "get_api_endpoints":
            return await this.getApiEndpoints();
          case "get_component_errors":
            return await this.getComponentErrors();
          case "test_api_endpoint":
            return await this.testApiEndpoint(args.endpoint);
          default:
            throw new Error(`Unknown tool: ${name}`);
        }
      } catch (error) {
        return {
          content: [
            {
              type: "text",
              text: `Error: ${error.message}`,
            },
          ],
        };
      }
    });
  }

  async getDevServerStatus() {
    try {
      const { stdout } = await execAsync(
        'ps aux | grep "next dev" | grep -v grep'
      );
      const processes = stdout
        .trim()
        .split("\n")
        .filter((line) => line);

      const { stdout: portCheck } = await execAsync(
        'lsof -i :3000 2>/dev/null || echo "No process on 3000"'
      );

      return {
        content: [
          {
            type: "text",
            text: `Next.js Dev Server Status:
Running processes: ${processes.length}
Port 3000 status: ${portCheck.includes("No process") ? "Available" : "In use"}

Processes:
${processes.join("\n")}`,
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text",
            text: `Could not check dev server status: ${error.message}`,
          },
        ],
      };
    }
  }

  async getBuildStatus() {
    try {
      // Check for .next directory and build artifacts
      const nextDirExists = await fs
        .access(".next")
        .then(() => true)
        .catch(() => false);
      const packageJson = await fs.readFile("package.json", "utf8");
      const pkg = JSON.parse(packageJson);

      return {
        content: [
          {
            type: "text",
            text: `Build Status:
.next directory exists: ${nextDirExists}
Next.js version: ${pkg.dependencies?.next || "Not found"}
Build scripts: ${Object.keys(pkg.scripts || {}).join(", ")}`,
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text",
            text: `Could not check build status: ${error.message}`,
          },
        ],
      };
    }
  }

  async getApiEndpoints() {
    try {
      const apiDir = path.join(process.cwd(), "pages", "api");
      const files = await fs.readdir(apiDir);
      const endpoints = files
        .filter((file) => file.endsWith(".ts") || file.endsWith(".js"))
        .map((file) => `/api/${file.replace(/\.(ts|js)$/, "")}`);

      // Test each endpoint
      const results = [];
      for (const endpoint of endpoints) {
        try {
          const { stdout } = await execAsync(
            `curl -s -w "%{http_code}" http://localhost:3000${endpoint}`
          );
          const response = stdout.slice(0, -3);
          const statusCode = stdout.slice(-3);
          results.push(
            `${endpoint}: ${statusCode} - ${response.slice(0, 100)}...`
          );
        } catch (error) {
          results.push(`${endpoint}: Error - ${error.message}`);
        }
      }

      return {
        content: [
          {
            type: "text",
            text: `API Endpoints:
${endpoints.join("\n")}

Test Results:
${results.join("\n")}`,
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text",
            text: `Could not check API endpoints: ${error.message}`,
          },
        ],
      };
    }
  }

  async getComponentErrors() {
    // Check browser console logs if available
    // This is a simplified version - in practice you'd want to integrate with browser automation
    return {
      content: [
        {
          type: "text",
          text: `Component Error Check:
- Check browser console for hydration errors
- Look for "Text content does not match" errors
- Verify client-side only components are properly mounted
- Check for SSR/client rendering mismatches`,
        },
      ],
    };
  }

  async testApiEndpoint(endpoint) {
    try {
      const { stdout } = await execAsync(
        `curl -s -w "\\nHTTP_CODE:%{http_code}\\nTIME:%{time_total}" http://localhost:3000${endpoint}`
      );

      return {
        content: [
          {
            type: "text",
            text: `API Test Results for ${endpoint}:
${stdout}`,
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text",
            text: `Could not test endpoint ${endpoint}: ${error.message}`,
          },
        ],
      };
    }
  }

  async run() {
    const transport = new StdioServerTransport();
    await this.server.connect(transport);
  }
}

const server = new NextJSMCPServer();
server.run().catch(console.error);
