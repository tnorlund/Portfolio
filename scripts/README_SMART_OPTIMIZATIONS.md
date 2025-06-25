# Smart Pytest Optimization Features

## 🚀 Advanced Optimization Features Added

### **1. File Change Detection (2-5x Additional Speedup)**

The workflow now automatically detects which packages have changed and only runs tests for those packages:

```yaml
# Only run receipt_dynamo tests if receipt_dynamo code changed
if: |
  (matrix.package == 'receipt_dynamo' && needs.fast-checks.outputs.receipt_dynamo_changed == 'true') ||
  (matrix.package == 'receipt_label' && needs.fast-checks.outputs.receipt_label_changed == 'true')
```

**Benefits:**
- **Skip unchanged packages**: If you only change `receipt_label`, `receipt_dynamo` tests won't run
- **Portfolio-only changes**: TypeScript changes won't trigger Python tests
- **Documentation changes**: README updates won't run any tests
- **Workflow changes**: Always run tests to validate workflow modifications

**Expected Impact**: 2-5x speedup for most PRs that don't touch all packages

### **2. Smart Test Selection (`smart_test_runner.py`)**

New intelligent test runner that analyzes:
- **File dependencies**: Which source files each test depends on
- **Test result caching**: Skip tests that passed recently with no file changes
- **Change impact analysis**: Only run tests affected by specific changes

```bash
# Example: Only run tests that depend on changed files
python scripts/smart_test_runner.py receipt_dynamo tests/integration --dry-run

# Output:
📊 Smart Test Analysis for receipt_dynamo
   Changed files: 3
   Total test files: 127
   Tests to run: 12      # ← 90% reduction!
   Tests to skip: 115
```

**Key Features:**
- **Dependency analysis**: Parses import statements to map test → source file dependencies
- **24-hour result caching**: Skip tests that passed recently with no changes
- **Safe fallbacks**: If analysis fails, runs all tests (never skips incorrectly)
- **GitHub Actions compatible**: Handles space-separated test paths

### **3. Enhanced Caching Strategy**

Upgraded caching to include:
```yaml
path: |
  ~/.cache/pip
  ${{ env.pythonLocation }}        # ← Entire Python installation
  ~/.local/lib/python3.12/site-packages
  **/__pycache__                   # ← Compiled bytecode
  .pytest_cache/pytest-results    # ← Test result cache
```

**Benefits:**
- **Faster imports**: Cached bytecode compilation
- **Persistent test results**: Test outcome caching across runs
- **Full environment caching**: No re-installation of stable packages

### **4. Optimized Dependency Installation**

```bash
# Install core packages without dependency resolution first (faster)
pip install --no-deps pytest pytest-xdist pytest-timeout psutil moto boto3
# Then resolve dependencies
pip install pytest pytest-xdist pytest-timeout psutil moto boto3 --disable-pip-version-check
```

**Benefits:**
- **Parallel installation**: Install known packages without waiting for dependency resolution
- **Skip version checks**: Faster pip operations
- **Smart caching**: Check if packages already installed before attempting installation

## 📊 Expected Performance Improvements

| Scenario | Original Time | Current Time | With Smart Optimizations | Total Speedup |
|----------|---------------|--------------|--------------------------|---------------|
| **Full test suite** | 62.8min | 15.8min | 15.8min | 4x |
| **Single package change** | 62.8min | 15.8min | 3-8min | 8-20x |
| **Documentation only** | 62.8min | 15.8min | 30sec | 125x |
| **Small bug fix** | 62.8min | 15.8min | 2-5min | 12-30x |

## 🎯 Usage Examples

### **Local Development**
```bash
# Analyze what tests would run
python scripts/smart_test_runner.py receipt_dynamo --dry-run

# Run only tests affected by changes since main
python scripts/smart_test_runner.py receipt_dynamo

# Force run all tests (bypass smart selection)
python scripts/smart_test_runner.py receipt_dynamo --force
```

### **CI/CD Integration**
The workflow automatically:
1. **Detects changed files** using `dorny/paths-filter@v3`
2. **Skips entire packages** if unchanged
3. **Uses smart test selection** within packages that did change
4. **Falls back to full test runner** if smart selection fails

## 🛡️ Safety Features

### **Conservative Approach**
- **Default to running tests**: If change detection fails, run all tests
- **Workflow modifications**: Always run tests when `.github/workflows/` changes
- **Test file changes**: Always run a test if the test file itself changed
- **Cache misses**: Run tests if result cache is corrupted or missing

### **Debugging**
```bash
# See detailed analysis
python scripts/smart_test_runner.py receipt_dynamo --dry-run

# Force run everything for comparison
python scripts/smart_test_runner.py receipt_dynamo --force

# Check specific base reference
python scripts/smart_test_runner.py receipt_dynamo --base-ref origin/develop
```

## 🔄 Migration & Rollback

### **Current State**
- ✅ **File change detection**: Active
- ✅ **Enhanced caching**: Active  
- ✅ **Smart test selection**: Active with fallback
- ✅ **Optimized dependencies**: Active

### **Rollback Plan**
If issues arise, disable optimizations by:

1. **Remove file change detection**:
   ```yaml
   # Remove the 'if:' conditions from test jobs
   if: |
     (matrix.package == 'receipt_dynamo' && needs.fast-checks.outputs.receipt_dynamo_changed == 'true') ||
   ```

2. **Disable smart test selection**:
   ```yaml
   # Replace smart_test_runner.py with direct call to run_tests_optimized.py
   python scripts/run_tests_optimized.py \
     ${{ matrix.package }} \
     ${{ matrix.test_path }} \
   ```

3. **Revert to basic caching**:
   ```yaml
   path: ~/.cache/pip  # Remove other cache paths
   ```

All optimizations are additive and can be disabled independently without breaking existing functionality.

## 🎯 Next Steps

1. **Monitor cache hit rates** in workflow logs
2. **Track test selection accuracy** via dry-run outputs  
3. **Measure actual speedup** on real PRs
4. **Fine-tune dependency analysis** based on false positives/negatives
5. **Consider test impact analysis** for even smarter selection

The smart optimizations are designed to be transparent to developers while providing significant performance improvements for common development workflows.