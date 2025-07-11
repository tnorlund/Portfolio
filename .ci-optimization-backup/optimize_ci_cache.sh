#!/bin/bash
"""
CI Cache Optimizer - Optimizes caching strategies for faster CI builds

This script implements intelligent caching for dependencies, builds, and test results
to significantly reduce CI execution time.

Usage:
    ./scripts/optimize_ci_cache.sh --setup
    ./scripts/optimize_ci_cache.sh --python-deps package_name
    ./scripts/optimize_ci_cache.sh --node-deps
    ./scripts/optimize_ci_cache.sh --analyze
"""

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
CACHE_VERSION="v1"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
NODE_VERSION="${NODE_VERSION:-20}"

# Helper functions
log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Generate cache key based on file content hashes
generate_cache_key() {
    local files=("$@")
    local hash_input=""
    
    for file in "${files[@]}"; do
        if [[ -f "$file" ]]; then
            hash_input+=$(md5sum "$file" | cut -d' ' -f1)
        fi
    done
    
    echo "${CACHE_VERSION}-$(echo -n "$hash_input" | md5sum | cut -d' ' -f1)"
}

# Setup cache directories and structure
setup_cache_structure() {
    log_info "Setting up cache structure..."
    
    # Create cache directories
    mkdir -p .ci-cache/{pip,npm,pytest,build,deps}
    mkdir -p .ci-cache/analysis/{timing,dependencies,coverage}
    
    # Create cache manifest
    cat > .ci-cache/manifest.json << EOF
{
    "version": "$CACHE_VERSION",
    "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "structure": {
        "pip": "Python package cache",
        "npm": "Node.js package cache", 
        "pytest": "Test result cache",
        "build": "Build artifact cache",
        "deps": "Dependency analysis cache",
        "analysis": "Performance analysis data"
    },
    "retention_days": 30
}
EOF
    
    log_success "Cache structure created"
}

# Optimize Python dependency caching
optimize_python_deps() {
    local package_name="$1"
    log_info "Optimizing Python dependencies for $package_name"
    
    if [[ ! -d "$package_name" ]]; then
        log_error "Package directory $package_name not found"
        return 1
    fi
    
    # Generate cache key based on Python version and dependency files
    local cache_key
    cache_key=$(generate_cache_key "$package_name/pyproject.toml" "requirements*.txt")
    local cache_dir=".ci-cache/pip/$cache_key"
    
    log_info "Cache key: $cache_key"
    
    if [[ -d "$cache_dir" ]]; then
        log_success "Cache hit! Restoring Python dependencies..."
        
        # Restore pip cache
        if [[ -d "$cache_dir/pip-cache" ]]; then
            mkdir -p ~/.cache/pip
            cp -r "$cache_dir/pip-cache"/* ~/.cache/pip/ 2>/dev/null || true
        fi
        
        # Restore virtual environment if it exists
        if [[ -d "$cache_dir/venv" ]]; then
            log_info "Restoring virtual environment..."
            cp -r "$cache_dir/venv" ".venv-$package_name"
            
            # Activate and verify environment
            source ".venv-$package_name/bin/activate"
            python --version
            pip list | head -10
            
            log_success "Virtual environment restored"
            return 0
        fi
    fi
    
    log_info "Cache miss. Building Python dependencies..."
    
    # Create fresh virtual environment
    python3 -m venv ".venv-$package_name"
    source ".venv-$package_name/bin/activate"
    
    # Upgrade pip for faster installs
    python -m pip install --upgrade pip wheel --disable-pip-version-check
    
    # Install dependencies
    if [[ -f "$package_name/pyproject.toml" ]]; then
        pip install -e "$package_name[test]" --disable-pip-version-check
    else
        log_warning "No pyproject.toml found, installing basic test dependencies"
        pip install pytest pytest-xdist pytest-cov --disable-pip-version-check
    fi
    
    # Cache the results
    mkdir -p "$cache_dir"
    
    # Cache pip cache directory
    if [[ -d ~/.cache/pip ]]; then
        cp -r ~/.cache/pip "$cache_dir/pip-cache"
    fi
    
    # Cache virtual environment
    cp -r ".venv-$package_name" "$cache_dir/venv"
    
    # Create cache metadata
    cat > "$cache_dir/metadata.json" << EOF
{
    "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "python_version": "$(python --version)",
    "package": "$package_name",
    "dependencies": $(pip list --format=json),
    "cache_key": "$cache_key"
}
EOF
    
    log_success "Python dependencies cached"
}

# Optimize Node.js dependency caching
optimize_node_deps() {
    log_info "Optimizing Node.js dependencies"
    
    if [[ ! -f "package-lock.json" ]]; then
        log_warning "No package-lock.json found"
        return 1
    fi
    
    local cache_key
    cache_key=$(generate_cache_key "package.json" "package-lock.json")
    local cache_dir=".ci-cache/npm/$cache_key"
    
    log_info "Cache key: $cache_key"
    
    if [[ -d "$cache_dir" ]]; then
        log_success "Cache hit! Restoring Node.js dependencies..."
        
        # Restore node_modules
        if [[ -d "$cache_dir/node_modules" ]]; then
            cp -r "$cache_dir/node_modules" .
        fi
        
        # Restore npm cache
        if [[ -d "$cache_dir/npm-cache" ]]; then
            npm config set cache "$cache_dir/npm-cache"
        fi
        
        log_success "Node.js dependencies restored"
        return 0
    fi
    
    log_info "Cache miss. Installing Node.js dependencies..."
    
    # Clean install
    npm ci --prefer-offline --no-audit
    
    # Cache the results
    mkdir -p "$cache_dir"
    
    # Cache node_modules
    cp -r node_modules "$cache_dir/"
    
    # Cache npm cache
    local npm_cache_dir
    npm_cache_dir=$(npm config get cache)
    if [[ -d "$npm_cache_dir" ]]; then
        cp -r "$npm_cache_dir" "$cache_dir/npm-cache"
    fi
    
    # Create cache metadata
    cat > "$cache_dir/metadata.json" << EOF
{
    "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "node_version": "$(node --version)",
    "npm_version": "$(npm --version)",
    "cache_key": "$cache_key"
}
EOF
    
    log_success "Node.js dependencies cached"
}

# Cache build artifacts
cache_build_artifacts() {
    local build_type="$1"
    local build_dir="$2"
    
    log_info "Caching $build_type build artifacts from $build_dir"
    
    if [[ ! -d "$build_dir" ]]; then
        log_warning "Build directory $build_dir not found"
        return 1
    fi
    
    local cache_key
    case "$build_type" in
        "nextjs")
            cache_key=$(generate_cache_key "next.config.js" "package.json" "tsconfig.json")
            ;;
        "python")
            cache_key=$(generate_cache_key "setup.py" "pyproject.toml")
            ;;
        *)
            cache_key=$(generate_cache_key "$(find . -name '*.json' -o -name '*.toml' -o -name '*.js')")
            ;;
    esac
    
    local cache_dir=".ci-cache/build/$build_type-$cache_key"
    
    if [[ -d "$cache_dir" ]]; then
        log_success "Build cache hit! Restoring artifacts..."
        cp -r "$cache_dir"/* "$build_dir/"
        return 0
    fi
    
    # Cache build artifacts after successful build
    mkdir -p "$cache_dir"
    cp -r "$build_dir"/* "$cache_dir/"
    
    log_success "$build_type build artifacts cached"
}

# Cache test results for smart test selection
cache_test_results() {
    local package_name="$1"
    local test_results_file="$2"
    
    log_info "Caching test results for $package_name"
    
    if [[ ! -f "$test_results_file" ]]; then
        log_warning "Test results file $test_results_file not found"
        return 1
    fi
    
    local cache_dir=".ci-cache/pytest/$package_name"
    mkdir -p "$cache_dir"
    
    # Store test results with timestamp
    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    cp "$test_results_file" "$cache_dir/results_$timestamp.json"
    
    # Keep only last 50 test result files
    find "$cache_dir" -name "results_*.json" -type f | sort | head -n -50 | xargs rm -f
    
    log_success "Test results cached"
}

# Analyze cache effectiveness
analyze_cache_performance() {
    log_info "Analyzing cache performance..."
    
    local analysis_file=".ci-cache/analysis/cache_analysis_$(date +%Y%m%d_%H%M%S).json"
    
    # Calculate cache hit rates
    local total_builds=0
    local cache_hits=0
    
    # Count pip cache hits
    if [[ -d ".ci-cache/pip" ]]; then
        local pip_caches
        pip_caches=$(find .ci-cache/pip -name "metadata.json" | wc -l)
        total_builds=$((total_builds + pip_caches))
        
        # Estimate cache hits based on repeated cache keys
        local unique_keys
        unique_keys=$(find .ci-cache/pip -name "metadata.json" -exec jq -r '.cache_key' {} \; | sort -u | wc -l)
        cache_hits=$((cache_hits + pip_caches - unique_keys))
    fi
    
    # Count npm cache hits
    if [[ -d ".ci-cache/npm" ]]; then
        local npm_caches
        npm_caches=$(find .ci-cache/npm -name "metadata.json" | wc -l)
        total_builds=$((total_builds + npm_caches))
        
        local unique_keys
        unique_keys=$(find .ci-cache/npm -name "metadata.json" -exec jq -r '.cache_key' {} \; | sort -u | wc -l)
        cache_hits=$((cache_hits + npm_caches - unique_keys))
    fi
    
    # Calculate cache hit rate
    local hit_rate=0
    if [[ $total_builds -gt 0 ]]; then
        hit_rate=$((cache_hits * 100 / total_builds))
    fi
    
    # Calculate cache size
    local cache_size
    cache_size=$(du -sh .ci-cache 2>/dev/null | cut -f1)
    
    # Generate analysis report
    cat > "$analysis_file" << EOF
{
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "cache_performance": {
        "total_builds": $total_builds,
        "cache_hits": $cache_hits,
        "hit_rate_percent": $hit_rate,
        "cache_size": "$cache_size"
    },
    "cache_directories": {
        "pip_caches": $(find .ci-cache/pip -maxdepth 1 -type d | wc -l),
        "npm_caches": $(find .ci-cache/npm -maxdepth 1 -type d | wc -l),
        "build_caches": $(find .ci-cache/build -maxdepth 1 -type d | wc -l),
        "test_caches": $(find .ci-cache/pytest -maxdepth 1 -type d | wc -l)
    },
    "recommendations": [
        $(if [[ $hit_rate -lt 50 ]]; then echo "\"Cache hit rate is low ($hit_rate%). Consider reviewing cache key generation.\""; fi)
        $(if [[ -n "$cache_size" && "${cache_size%M}" -gt 1000 ]]; then echo "\"Cache size is large ($cache_size). Consider implementing cleanup policies.\""; fi)
    ]
}
EOF
    
    # Display summary
    echo -e "\n${BLUE}📊 Cache Performance Analysis${NC}"
    echo -e "Cache hit rate: ${GREEN}$hit_rate%${NC}"
    echo -e "Total cache size: ${YELLOW}$cache_size${NC}"
    echo -e "Analysis saved to: $analysis_file"
    
    log_success "Cache analysis complete"
}

# Clean old cache entries
cleanup_cache() {
    local retention_days="${1:-30}"
    
    log_info "Cleaning cache entries older than $retention_days days..."
    
    # Clean old cache directories
    find .ci-cache -type d -mtime +$retention_days -exec rm -rf {} + 2>/dev/null || true
    
    # Clean old analysis files
    find .ci-cache/analysis -name "*.json" -mtime +$retention_days -delete 2>/dev/null || true
    
    log_success "Cache cleanup complete"
}

# Export cache for GitHub Actions
export_github_cache() {
    local cache_type="$1"
    local cache_key="$2"
    
    log_info "Exporting $cache_type cache for GitHub Actions"
    
    # This would integrate with GitHub Actions cache API
    # For now, we'll create tar archives
    case "$cache_type" in
        "pip")
            tar -czf "ci-cache-pip-$cache_key.tar.gz" .ci-cache/pip/
            ;;
        "npm")
            tar -czf "ci-cache-npm-$cache_key.tar.gz" .ci-cache/npm/
            ;;
        "build")
            tar -czf "ci-cache-build-$cache_key.tar.gz" .ci-cache/build/
            ;;
    esac
    
    log_success "$cache_type cache exported"
}

# Main function
main() {
    case "${1:-}" in
        "--setup")
            setup_cache_structure
            ;;
        "--python-deps")
            if [[ $# -lt 2 ]]; then
                log_error "Package name required for --python-deps"
                exit 1
            fi
            optimize_python_deps "$2"
            ;;
        "--node-deps")
            optimize_node_deps
            ;;
        "--cache-build")
            if [[ $# -lt 3 ]]; then
                log_error "Build type and directory required for --cache-build"
                exit 1
            fi
            cache_build_artifacts "$2" "$3"
            ;;
        "--cache-tests")
            if [[ $# -lt 3 ]]; then
                log_error "Package name and results file required for --cache-tests"
                exit 1
            fi
            cache_test_results "$2" "$3"
            ;;
        "--analyze")
            analyze_cache_performance
            ;;
        "--cleanup")
            cleanup_cache "${2:-30}"
            ;;
        "--export")
            if [[ $# -lt 3 ]]; then
                log_error "Cache type and key required for --export"
                exit 1
            fi
            export_github_cache "$2" "$3"
            ;;
        "--help")
            cat << EOF
CI Cache Optimizer

Usage:
    $0 --setup                              Setup cache structure
    $0 --python-deps PACKAGE               Optimize Python dependencies
    $0 --node-deps                         Optimize Node.js dependencies
    $0 --cache-build TYPE DIR               Cache build artifacts
    $0 --cache-tests PACKAGE RESULTS       Cache test results
    $0 --analyze                           Analyze cache performance
    $0 --cleanup [DAYS]                    Clean cache (default: 30 days)
    $0 --export TYPE KEY                   Export cache for CI

Examples:
    $0 --setup
    $0 --python-deps receipt_dynamo
    $0 --node-deps
    $0 --analyze
EOF
            ;;
        *)
            log_error "Unknown command: ${1:-}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
}

# Execute main function with all arguments
main "$@"