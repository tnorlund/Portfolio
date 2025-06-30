#!/bin/bash

# Comprehensive Development Workflow Script
# Provides guided workflow for cost-efficient development
# Usage: ./scripts/dev_workflow.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m'

echo -e "${PURPLE}🚀 GitHub Actions Cost-Optimized Development Workflow${NC}"
echo -e "${BLUE}=====================================================${NC}"
echo ""

# Function to show menu
show_menu() {
    echo "Select your development workflow:"
    echo ""
    echo "Quick Development (Low CI cost):"
    echo "  1) 🎨 Format code only"
    echo "  2) 🧪 Run quick local tests (receipt_dynamo)"
    echo "  3) 🧪 Run quick local tests (receipt_label)" 
    echo "  4) 📦 Quick check + format (all packages)"
    echo ""
    echo "Comprehensive Testing (Pre-push validation):"
    echo "  5) 🔍 Full unit tests (receipt_dynamo)"
    echo "  6) 🔍 Full integration tests (receipt_dynamo group-1)"
    echo "  7) 🔍 Full integration tests (receipt_dynamo group-2)"
    echo "  8) 🔍 Full integration tests (receipt_dynamo group-3)"
    echo "  9) 🔍 Full integration tests (receipt_dynamo group-4)"
    echo " 10) 🔍 Full tests (receipt_label unit)"
    echo " 11) 🔍 Full tests (receipt_label integration)"
    echo ""
    echo "Git Workflow Helpers:"
    echo " 12) 📝 Create draft PR (no CI cost)"
    echo " 13) ✅ Mark PR ready for review (triggers CI)"
    echo " 14) 📊 Check GitHub Actions usage"
    echo ""
    echo "Runner Management:"
    echo " 15) 🖥️  Start self-hosted runner"
    echo " 16) 🛑 Stop self-hosted runner"
    echo " 17) 📋 Check runner status"
    echo ""
    echo " 0) 🚪 Exit"
}

# Function to estimate CI cost
estimate_ci_cost() {
    local action=$1
    case $action in
        "draft_pr") echo "~$0 (draft PRs don't trigger expensive CI)" ;;
        "ready_pr") echo "~$0.96 (8 jobs × 2min = 16min × $0.008)" ;;
        "full_pr") echo "~$2.40 (6 jobs × 20min = 120min × $0.008)" ;;
        *) echo "~$0 (local only)" ;;
    esac
}

# Function to run action
run_action() {
    local choice=$1
    case $choice in
        1)
            echo -e "${GREEN}🎨 Formatting code...${NC}"
            ./scripts/format_code.sh
            echo -e "${GREEN}💰 Cost: $(estimate_ci_cost "local")${NC}"
            ;;
        2)
            echo -e "${GREEN}🧪 Running quick tests for receipt_dynamo...${NC}"
            ./scripts/local_ci_check.sh receipt_dynamo
            echo -e "${GREEN}💰 Cost: $(estimate_ci_cost "local")${NC}"
            ;;
        3)
            echo -e "${GREEN}🧪 Running quick tests for receipt_label...${NC}"
            ./scripts/local_ci_check.sh receipt_label
            echo -e "${GREEN}💰 Cost: $(estimate_ci_cost "local")${NC}"
            ;;
        4)
            echo -e "${GREEN}📦 Running quick checks for all packages...${NC}"
            echo "Checking receipt_dynamo..."
            ./scripts/local_ci_check.sh receipt_dynamo
            echo ""
            echo "Checking receipt_label..."
            ./scripts/local_ci_check.sh receipt_label
            echo ""
            echo "Formatting all code..."
            ./scripts/format_code.sh
            echo -e "${GREEN}💰 Cost: $(estimate_ci_cost "local")${NC}"
            ;;
        5)
            echo -e "${GREEN}🔍 Running full unit tests for receipt_dynamo...${NC}"
            ./scripts/local_full_test.sh receipt_dynamo unit
            echo -e "${GREEN}💰 Cost: $(estimate_ci_cost "local")${NC}"
            ;;
        6|7|8|9)
            local group=$((choice - 5))
            echo -e "${GREEN}🔍 Running full integration tests for receipt_dynamo group-$group...${NC}"
            ./scripts/local_full_test.sh receipt_dynamo integration "group-$group"
            echo -e "${GREEN}💰 Cost: $(estimate_ci_cost "local")${NC}"
            ;;
        10)
            echo -e "${GREEN}🔍 Running full unit tests for receipt_label...${NC}"
            ./scripts/local_full_test.sh receipt_label unit
            echo -e "${GREEN}💰 Cost: $(estimate_ci_cost "local")${NC}"
            ;;
        11)
            echo -e "${GREEN}🔍 Running full integration tests for receipt_label...${NC}"
            ./scripts/local_full_test.sh receipt_label integration
            echo -e "${GREEN}💰 Cost: $(estimate_ci_cost "local")${NC}"
            ;;
        12)
            echo -e "${GREEN}📝 Creating draft PR...${NC}"
            if command -v gh &> /dev/null; then
                echo "Enter PR title:"
                read -r pr_title
                gh pr create --draft --title "WIP: $pr_title" --body "Draft PR for development. Ready for review when complete."
                echo -e "${GREEN}✅ Draft PR created!${NC}"
                echo -e "${GREEN}💰 Estimated CI cost: $(estimate_ci_cost "draft_pr")${NC}"
            else
                echo -e "${YELLOW}⚠️  GitHub CLI not installed. Install with: brew install gh${NC}"
            fi
            ;;
        13)
            echo -e "${GREEN}✅ Marking PR ready for review...${NC}"
            if command -v gh &> /dev/null; then
                gh pr ready
                echo -e "${GREEN}✅ PR marked ready!${NC}"
                echo -e "${YELLOW}💰 Estimated CI cost: $(estimate_ci_cost "ready_pr")${NC}"
            else
                echo -e "${YELLOW}⚠️  GitHub CLI not installed. Install with: brew install gh${NC}"
            fi
            ;;
        14)
            echo -e "${GREEN}📊 Checking GitHub Actions usage...${NC}"
            if command -v gh &> /dev/null; then
                echo "Opening GitHub billing page..."
                open "https://github.com/settings/billing"
            else
                echo "Visit: https://github.com/settings/billing"
            fi
            ;;
        15)
            echo -e "${GREEN}🖥️  Starting self-hosted runner...${NC}"
            cd /Users/tnorlund/GitHub/actions-runner
            nohup ./run.sh > runner.log 2>&1 &
            echo $! > runner.pid
            echo -e "${GREEN}✅ Runner started in background (PID: $(cat runner.pid))${NC}"
            echo "Log file: /Users/tnorlund/GitHub/actions-runner/runner.log"
            cd "$PROJECT_ROOT"
            ;;
        16)
            echo -e "${GREEN}🛑 Stopping self-hosted runner...${NC}"
            if [ -f "/Users/tnorlund/GitHub/actions-runner/runner.pid" ]; then
                kill $(cat /Users/tnorlund/GitHub/actions-runner/runner.pid) 2>/dev/null || true
                rm -f /Users/tnorlund/GitHub/actions-runner/runner.pid
                echo -e "${GREEN}✅ Runner stopped${NC}"
            else
                echo -e "${YELLOW}⚠️  No runner PID file found${NC}"
            fi
            ;;
        17)
            echo -e "${GREEN}📋 Checking runner status...${NC}"
            if [ -f "/Users/tnorlund/GitHub/actions-runner/runner.pid" ]; then
                local pid=$(cat /Users/tnorlund/GitHub/actions-runner/runner.pid)
                if ps -p $pid > /dev/null 2>&1; then
                    echo -e "${GREEN}✅ Runner is running (PID: $pid)${NC}"
                    echo "Recent log entries:"
                    tail -5 /Users/tnorlund/GitHub/actions-runner/runner.log 2>/dev/null || echo "No log file found"
                else
                    echo -e "${RED}❌ Runner not running (stale PID file)${NC}"
                    rm -f /Users/tnorlund/GitHub/actions-runner/runner.pid
                fi
            else
                echo -e "${YELLOW}⚠️  Runner not running${NC}"
            fi
            ;;
        0)
            echo -e "${GREEN}👋 Goodbye!${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Invalid choice${NC}"
            ;;
    esac
}

# Main loop
while true; do
    show_menu
    echo ""
    echo -e "${BLUE}Enter your choice (0-17):${NC}"
    read -r choice
    echo ""
    
    run_action "$choice"
    
    echo ""
    echo -e "${BLUE}Press Enter to continue...${NC}"
    read -r
    clear
done