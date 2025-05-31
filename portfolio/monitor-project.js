const fs = require("fs");
const { exec } = require("child_process");
const { promisify } = require("util");
const execAsync = promisify(exec);

async function checkProjectState() {
  console.log("\n=== Next.js Project State Check ===");
  console.log("Timestamp:", new Date().toISOString());

  try {
    // Check dev server
    const { stdout: devServer } = await execAsync(
      'ps aux | grep "next dev" | grep -v grep'
    ).catch(() => ({ stdout: "" }));
    console.log("🔧 Dev Server:", devServer ? "Running ✅" : "Not running ❌");

    // Check port 3000
    const { stdout: portCheck } = await execAsync(
      'lsof -i :3000 2>/dev/null || echo "none"'
    ).catch(() => ({ stdout: "none" }));
    console.log(
      "🌐 Port 3000:",
      portCheck.includes("none") ? "Available" : "In use ✅"
    );

    // Check API endpoints
    if (fs.existsSync("./pages/api/")) {
      const apiFiles = fs.readdirSync("./pages/api/");
      const endpoints = apiFiles.filter(
        (f) => f.endsWith(".ts") || f.endsWith(".js")
      );
      console.log("📡 API Endpoints:", endpoints.length);
      endpoints.forEach((file) => {
        const endpoint = `/api/${file.replace(/\.(ts|js)$/, "")}`;
        console.log(`   - ${endpoint}`);
      });

      // Test endpoints if server is running
      if (!portCheck.includes("none")) {
        console.log("\n🧪 Testing API Endpoints:");
        for (const file of endpoints) {
          const endpoint = `/api/${file.replace(/\.(ts|js)$/, "")}`;
          try {
            const { stdout } = await execAsync(
              `curl -s -w ",%{http_code}" http://localhost:3000${endpoint} --max-time 5`
            );
            const parts = stdout.split(",");
            const statusCode = parts[parts.length - 1];
            const response = parts.slice(0, -1).join(",").substring(0, 100);
            console.log(
              `   ${endpoint}: ${statusCode} - ${response}${
                response.length >= 100 ? "..." : ""
              }`
            );
          } catch (error) {
            console.log(`   ${endpoint}: Error - ${error.message}`);
          }
        }
      }
    }

    // Check build status
    const nextDirExists = fs.existsSync("./.next");
    console.log(
      "🏗️  Build Status:",
      nextDirExists ? "Built ✅" : "Not built ❌"
    );

    // Check package.json
    if (fs.existsSync("./package.json")) {
      const pkg = JSON.parse(fs.readFileSync("./package.json", "utf8"));
      console.log("📦 Next.js Version:", pkg.dependencies?.next || "Not found");
      console.log("🔧 Scripts:", Object.keys(pkg.scripts || {}).join(", "));
    }

    // Check for common issues
    console.log("\n🔍 Common Issues Check:");

    // Check for node_modules
    const nodeModulesExists = fs.existsSync("./node_modules");
    console.log(
      "   node_modules:",
      nodeModulesExists ? "Present ✅" : "Missing ❌ (run npm install)"
    );

    // Check for .next cache issues
    if (nextDirExists) {
      const nextSize = await execAsync(
        'du -sh .next 2>/dev/null || echo "0"'
      ).catch(() => ({ stdout: "0" }));
      console.log("   .next directory size:", nextSize.stdout.trim());
    }

    // Check for TypeScript errors (if applicable)
    if (fs.existsSync("./tsconfig.json")) {
      console.log("   TypeScript config: Present ✅");
    }
  } catch (error) {
    console.log("❌ Error checking project state:", error.message);
  }

  console.log("=== End Check ===\n");
}

// Run immediately
checkProjectState();

// Run every 30 seconds if script is kept running
if (process.argv.includes("--watch")) {
  setInterval(checkProjectState, 30000);
  console.log("👀 Watching project state... (press Ctrl+C to stop)");
}
