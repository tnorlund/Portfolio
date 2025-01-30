from setuptools import setup, find_packages

setup(
    name="dynamo",
    version="1",
    packages=find_packages(where="dynamo"),
    package_dir={"": "dynamo"},
    install_requires=["requests"],
    extras_require={
        "test": [
            "pytest",
            "pytest-mock",
            "pytest-cov",
            "moto",
            "pytest-cov",
        ]
    },
)
