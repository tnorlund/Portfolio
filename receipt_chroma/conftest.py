"""Root conftest.py for pytest configuration."""

import sys
from pathlib import Path

# Add the package to the Python path for test discovery
package_dir = Path(__file__).parent
if str(package_dir) not in sys.path:
    sys.path.insert(0, str(package_dir))


# Configure coverage for parallel execution
def pytest_sessionstart(session):
    """Run after Session creation and before test collection."""
    # Ensure coverage is configured for parallel execution
    try:
        import coverage

        # This helps coverage work with xdist
        coverage.process_startup()
    except ImportError:
        pass
