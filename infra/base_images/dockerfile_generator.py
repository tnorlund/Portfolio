"""
Dockerfile generator for base images based on package dependencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import List


def generate_dockerfile(
    package_names: List[str],
    output_path: Path,
    python_version: str = "3.12",
) -> None:
    """Generate a Dockerfile for a base image containing multiple packages.

    Args:
        package_names: List of package names in build order (dependencies first)
        output_path: Path where Dockerfile should be written
        python_version: Python version to use
    """
    # Map package names to directory names
    package_dir_map = {
        "receipt-dynamo": "receipt_dynamo",
        "receipt_label": "receipt_label",
        "receipt-chroma": "receipt_chroma",
        "receipt_upload": "receipt_upload",
        "receipt_places": "receipt_places",
        "receipt-agent": "receipt_agent",
    }

    # Determine if we need C++ compiler (for packages with native extensions)
    needs_cpp = any(
        pkg in ["receipt-chroma", "receipt_label", "receipt-agent"]
        for pkg in package_names
    )

    dockerfile_lines = [
        f"ARG PYTHON_VERSION={python_version}",
        "FROM public.ecr.aws/lambda/python:${PYTHON_VERSION}",
        "",
        "# Install system dependencies",
    ]

    if needs_cpp:
        dockerfile_lines.append(
            "RUN dnf install -y gcc gcc-c++ python3-devel && \\"
        )
    else:
        dockerfile_lines.append("RUN dnf install -y gcc python3-devel && \\")

    dockerfile_lines.extend(
        [
            "    dnf clean all && \\",
            "    rm -rf /var/cache/dnf",
            "",
        ]
    )

    # Copy packages
    for pkg_name in package_names:
        dir_name = package_dir_map.get(pkg_name, pkg_name.replace("-", "_"))
        dockerfile_lines.extend(
            [
                f"# Copy {pkg_name} package",
                f"COPY {dir_name} /tmp/{dir_name}",
            ]
        )

    dockerfile_lines.append("")

    # Install packages
    install_commands = []
    for pkg_name in package_names:
        dir_name = package_dir_map.get(pkg_name, pkg_name.replace("-", "_"))
        # receipt_label needs [full] extras
        if pkg_name == "receipt_label":
            install_cmd = f'pip install --no-cache-dir "/tmp/{dir_name}[full]"'
        else:
            install_cmd = f"pip install --no-cache-dir /tmp/{dir_name}"

        if pkg_name == package_names[-1]:
            # Last package - also clean up
            install_commands.append(f"{install_cmd} && \\")
            install_commands.append(f"    rm -rf /tmp/{dir_name}")
        else:
            install_commands.append(f"{install_cmd} && \\")

    dockerfile_lines.append("RUN " + " \\\n    ".join(install_commands))
    dockerfile_lines.append("")
    dockerfile_lines.append("# This is a base image, no CMD needed")

    # Write Dockerfile
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(dockerfile_lines) + "\n")
