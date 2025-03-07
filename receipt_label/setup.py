from setuptools import setup, find_packages

setup(
    name="receipt_label",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "requests>=2.31.0",
        "python-dateutil>=2.8.2",
        "typing-extensions>=4.8.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "black>=23.7.0",
            "isort>=5.12.0",
            "mypy>=1.5.1",
        ],
    },
    python_requires=">=3.8",
) 