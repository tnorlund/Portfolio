from setuptools import setup, find_packages

setup(
    name="receipt_label",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "python-dotenv>=0.19.0",
        "requests>=2.26.0",
        "boto3>=1.18.0",
        "pydantic>=1.8.0",
        "typing-extensions>=4.0.0",
        "python-dateutil>=2.8.2",
        "regex>=2021.8.3",
    ],
    extras_require={
        "dev": [
            "pytest>=6.2.5",
            "pytest-cov>=2.12.1",
            "black>=21.7b0",
            "isort>=5.9.3",
            "mypy>=0.910",
            "flake8>=3.9.2",
        ],
    },
    python_requires=">=3.8",
    author="Your Name",
    author_email="your.email@example.com",
    description="A package for labeling and validating receipt data",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/receipt_label",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
