"""Root conftest.py for pytest configuration."""

import sys
from pathlib import Path

# Add the package to the Python path for test discovery
package_dir = Path(__file__).parent
if str(package_dir) not in sys.path:
    sys.path.insert(0, str(package_dir))


def pytest_configure(config):
    """Configure pytest with custom settings."""
    # This helps with test discovery in parallel mode
    config.option.continue_on_collection_errors = True


# Configure coverage for parallel execution
def pytest_sessionstart(session):
    """Called after the Session object has been created and before performing collection."""
    # Ensure coverage is configured for parallel execution
    try:
        import coverage

        # This helps coverage work with xdist
        coverage.process_startup()
    except ImportError:
        pass
