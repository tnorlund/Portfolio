#!/bin/bash

# Comprehensive Development Workflow Script
# Provides guided workflow for cost-efficient development
# Usage: ./scripts/dev_workflow.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Use environment variable if set, otherwise default to $HOME/.github-runners
# To use a custom location, set: export GITHUB_RUNNERS_DIR="/path/to/runners"
RUNNER_BASE="${GITHUB_RUNNERS_DIR:-$HOME/.github-runners}"

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
    echo " 15) 🖥️  Start all self-hosted runners"
    echo " 16) 🛑 Stop all self-hosted runners"
    echo " 17) 📋 Check all runner status"
    echo " 18) ⚡ Quick setup additional runners"
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
            echo -e "${GREEN}🖥️  Starting all self-hosted runners...${NC}"

            # Start runner 1 (original)
            local runner1_dir="${RUNNER_BASE}/actions-runner"
            if [ -d "$runner1_dir" ]; then
                cd "$runner1_dir"
                if [ ! -f "runner.pid" ] || ! ps -p $(cat runner.pid 2>/dev/null) > /dev/null 2>&1; then
                    nohup ./run.sh > runner.log 2>&1 &
                    echo $! > runner.pid
                    echo -e "${GREEN}✅ Runner 1 started (PID: $(cat runner.pid))${NC}"
                else
                    echo -e "${YELLOW}⚠️  Runner 1 already running${NC}"
                fi
            else
                echo -e "${BLUE}ℹ️  Runner 1 not configured yet (directory not found: $runner1_dir)${NC}"
            fi

            # Start additional runners
            for i in {2..4}; do
                local runner_dir="${RUNNER_BASE}/actions-runner-${i}"
                if [ -d "$runner_dir" ]; then
                    cd "$runner_dir"
                    if [ ! -f "runner.pid" ] || ! ps -p $(cat runner.pid 2>/dev/null) > /dev/null 2>&1; then
                        nohup ./run.sh > runner.log 2>&1 &
                        echo $! > runner.pid
                        echo -e "${GREEN}✅ Runner $i started (PID: $(cat runner.pid))${NC}"
                    else
                        echo -e "${YELLOW}⚠️  Runner $i already running${NC}"
                    fi
                else
                    echo -e "${BLUE}ℹ️  Runner $i not configured yet${NC}"
                fi
            done

            cd "$PROJECT_ROOT"
            echo -e "${GREEN}🎉 All available runners started!${NC}"
            ;;
        16)
            echo -e "${GREEN}🛑 Stopping all self-hosted runners...${NC}"

            # Stop all runners
            for i in {1..4}; do
                local runner_dir="${RUNNER_BASE}/actions-runner$([ $i -eq 1 ] && echo "" || echo "-$i")"
                if [ -d "$runner_dir" ] && [ -f "$runner_dir/runner.pid" ]; then
                    local pid=$(cat "$runner_dir/runner.pid")
                    if ps -p $pid > /dev/null 2>&1; then
                        kill $pid 2>/dev/null || true
                        echo -e "${GREEN}✅ Runner $i stopped (PID: $pid)${NC}"
                    fi
                    rm -f "$runner_dir/runner.pid"
                fi
            done

            # Also kill any stray runner processes
            pkill -f "Runner.Listener" 2>/dev/null || true
            echo -e "${GREEN}🎉 All runners stopped!${NC}"
            ;;
        17)
            echo -e "${GREEN}📋 Checking all runner status...${NC}"
            echo ""

            local active_count=0
            for i in {1..4}; do
                local runner_dir="${RUNNER_BASE}/actions-runner$([ $i -eq 1 ] && echo "" || echo "-$i")"
                if [ -d "$runner_dir" ]; then
                    if [ -f "$runner_dir/runner.pid" ]; then
                        local pid=$(cat "$runner_dir/runner.pid")
                        if ps -p $pid > /dev/null 2>&1; then
                            echo -e "${GREEN}✅ Runner $i: ACTIVE (PID: $pid)${NC}"
                            ((active_count++))
                        else
                            echo -e "${RED}❌ Runner $i: STOPPED (stale PID)${NC}"
                            rm -f "$runner_dir/runner.pid"
                        fi
                    else
                        echo -e "${YELLOW}⚠️  Runner $i: NOT RUNNING${NC}"
                    fi
                else
                    echo -e "${BLUE}ℹ️  Runner $i: NOT CONFIGURED${NC}"
                fi
            done

            echo ""
            echo -e "${BLUE}Total active runners: $active_count${NC}"
            if [ $active_count -eq 0 ]; then
                echo -e "${YELLOW}💡 Use option 15 to start runners${NC}"
            elif [ $active_count -eq 1 ]; then
                echo -e "${YELLOW}💡 Consider configuring more runners for parallelization${NC}"
            else
                echo -e "${GREEN}🚀 Great! Multiple runners for parallel execution${NC}"
            fi
            ;;
        18)
            echo -e "${GREEN}⚡ Running quick setup for additional runners...${NC}"
            if [ -f "$PROJECT_ROOT/scripts/quick_runner_setup.sh" ]; then
                "$PROJECT_ROOT/scripts/quick_runner_setup.sh"
            else
                echo -e "${RED}❌ Setup script not found${NC}"
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
    echo -e "${BLUE}Enter your choice (0-18):${NC}"
    read -r choice
    echo ""

    run_action "$choice"

    echo ""
    echo -e "${BLUE}Press Enter to continue...${NC}"
    read -r
    clear
done
